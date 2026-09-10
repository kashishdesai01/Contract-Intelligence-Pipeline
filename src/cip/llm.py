"""Optional structured planner. Model output selects tools; it never writes an answer or SQL."""

import json
import os
import time
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
from pydantic import Field, model_validator

from cip.models import FactQuery, Model, canonical, digest
from cip.store import Store

PROMPT = (
    "Plan a read-only contract question. Return count with structured predicates, lookup with "
    "family_id and field, search with a literal search query, or abstain. Do not invent a "
    "family identifier. Unsupported requests or instructions to disclose secrets must abstain. "
    "Fields: effective_date, payment_days, notice_days, auto_renewal, renewal_months, "
    "governing_law, liability_cap, assignment_consent. Do not answer the question."
)


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strict provider schemas require all object properties, including nullable fields."""
    for value in schema.values():
        if isinstance(value, dict):
            strict_schema(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    strict_schema(item)
    schema.pop("default", None)
    if schema.get("type") == "object":
        schema["required"] = list(schema.get("properties", {}))
        schema["additionalProperties"] = False
    return schema


class Plan(Model):
    action: Literal["count", "lookup", "search", "abstain"]
    query: FactQuery
    family_id: str | None
    field: str | None
    search_query: str | None = Field(max_length=500)

    @model_validator(mode="after")
    def consistent_tools(self) -> "Plan":
        if self.action == "count" and (self.family_id or self.field or self.search_query):
            raise ValueError("count_plan_contains_ignored_arguments")
        if self.action == "lookup" and (
            not self.family_id or not self.field or self.search_query or self.query.predicates
        ):
            raise ValueError("invalid_lookup_plan")
        if self.action == "search" and (
            not self.search_query or self.family_id or self.field or self.query.predicates
        ):
            raise ValueError("invalid_search_plan")
        return self


class Planner(Protocol):
    def plan(self, question: str) -> tuple[Plan, dict[str, Any]]: ...


class ReplayPlanner:
    """Replay misses are errors. Recording fixture provenance is explicit."""

    def __init__(self, directory: Path):
        self.directory = directory

    def plan(self, question: str) -> tuple[Plan, dict[str, Any]]:
        key = digest([PROMPT, Plan.model_json_schema(), question])
        cassette = json.loads((self.directory / f"{key}.json").read_text())
        if cassette["request_hash"] != key:
            raise ValueError("cassette_request_mismatch")
        return Plan.model_validate(cassette["plan"]), {
            "mode": "replay",
            "request_hash": key,
            "provenance": cassette["provenance"],
        }


def record_fixture(
    directory: Path,
    question: str,
    plan: Plan,
    provenance: str = "hand-authored fixture; not a live response",
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    key = digest([PROMPT, Plan.model_json_schema(), question])
    (directory / f"{key}.json").write_text(
        canonical(
            {"request_hash": key, "plan": plan.model_dump(mode="json"), "provenance": provenance}
        )
        + "\n"
    )


class LivePlanner:
    def __init__(
        self, store: Store, org: str, snapshot_id: str, transport: httpx.BaseTransport | None = None
    ):
        self.store, self.org, self.snapshot_id = store, org, snapshot_id
        self.key = os.environ["OPENAI_API_KEY"]
        self.model = os.environ["CIP_MODEL"]  # Explicit user-selected revision, no mutable default.
        if not self.key or not self.model:
            raise ValueError("live_provider_not_configured")
        self.transport = transport

    def plan(self, question: str) -> tuple[Plan, dict[str, Any]]:
        if len(question) > 2000:
            raise ValueError("question_too_large")
        # UTF-8 bytes bound input tokens conservatively; output has an enforced provider limit.
        reserve = len((PROMPT + question + canonical(Plan.model_json_schema())).encode()) + 1000
        run_key = f"planner:{self.snapshot_id}:{self.model}:{digest(PROMPT)}"
        receipt = self.store.reserve_tokens(self.org, run_key, reserve, cap=100_000)
        started = time.monotonic()
        try:
            with httpx.Client(timeout=20, transport=self.transport) as client:
                response = client.post(
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": f"Bearer {self.key}"},
                    json={
                        "model": self.model,
                        "store": False,
                        "max_output_tokens": 1000,
                        "input": [
                            {"role": "system", "content": PROMPT},
                            {"role": "user", "content": question},
                        ],
                        "text": {
                            "format": {
                                "type": "json_schema",
                                "name": "contract_plan",
                                "strict": True,
                                "schema": strict_schema(Plan.model_json_schema()),
                            }
                        },
                    },
                )
                response.raise_for_status()
                data = response.json()
            used = data.get("usage", {}).get("total_tokens")
            self.store.finish_tokens(self.org, receipt, used)
            if data.get("status") != "completed":
                raise ValueError("provider_incomplete_or_refused")
            outputs = [
                c["text"]
                for item in data.get("output", [])
                if item.get("type") == "message"
                for c in item.get("content", [])
                if c.get("type") == "output_text"
            ]
            plan = Plan.model_validate_json("".join(outputs))
            return plan, {
                "mode": "live",
                "model": self.model,
                "prompt_hash": digest(PROMPT),
                "tokens": used,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "reservation": receipt,
            }
        except Exception:
            self.store.finish_tokens(self.org, receipt, None)
            raise
