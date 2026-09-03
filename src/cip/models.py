"""Typed artifacts are also the wire contract. Unknown never means false."""

import hashlib
import json
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FIELDS = (
    "effective_date",
    "payment_days",
    "notice_days",
    "auto_renewal",
    "renewal_months",
    "governing_law",
    "liability_cap",
    "assignment_consent",
)


def canonical(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Model):
    source_id: str
    quote: str


class Clause(Model):
    key: str
    text: str
    source_id: str
    start: int
    end: int
    history: list[str] = Field(default_factory=list)
    disputed: bool = False
    evidence: list[Evidence] = Field(default_factory=list)


class Fact(Model):
    field: str
    value: str | int | bool | None = None
    status: Literal["ok", "unknown", "invalid", "disputed"] = "unknown"
    clause_key: str | None = None
    source_id: str | None = None
    quote: str | None = None
    quote_origin: Literal["composite", "source_header"] = "composite"
    reason: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class Operation(Model):
    kind: Literal["replace_clause", "replace_text"]
    target: str
    new_text: str
    old_text: str | None = None
    source_id: str
    evidence: str
    effective_date: date


class Instrument(Model):
    id: str
    kind: Literal["base", "amendment"]
    agreement: str
    parties: list[str]
    effective_date: date
    number: int | None = None
    base_date: date | None = None
    clauses: list[Clause]
    operations: list[Operation] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=list)
    text: str


class Conflict(Model):
    id: str
    source_id: str
    target: str
    kind: str
    detail: str


class Composite(Model):
    as_of: date
    clauses: list[Clause]
    facts: dict[str, Fact]
    conflicts: list[Conflict]
    deviations: list[dict[str, Any]]


class Family(Model):
    id: str
    agreement: str
    parties: list[str]
    base_id: str
    versions: list[Composite]


class Snapshot(Model):
    version: str
    families: list[Family]
    review: list[dict[str, str]]
    source_hashes: dict[str, str]
    source_texts: dict[str, str] = Field(default_factory=dict)
    exclusions: dict[str, str] = Field(default_factory=dict)


class Predicate(Model):
    field: Literal[
        "effective_date",
        "payment_days",
        "notice_days",
        "auto_renewal",
        "renewal_months",
        "governing_law",
        "liability_cap",
        "assignment_consent",
    ]
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte"] = "eq"
    value: str | int | bool

    @model_validator(mode="after")
    def valid_types(self) -> "Predicate":
        numeric = self.field in {"payment_days", "notice_days", "renewal_months"}
        boolean = self.field in {"auto_renewal", "assignment_consent"}
        if numeric and type(self.value) is not int:
            raise ValueError("numeric_field_requires_integer")
        if boolean and (type(self.value) is not bool or self.op not in {"eq", "ne"}):
            raise ValueError("boolean_field_requires_boolean_equality")
        if (
            not numeric
            and not boolean
            and (type(self.value) is not str or self.op not in {"eq", "ne"})
        ):
            raise ValueError("string_field_requires_string_equality")
        if self.field == "effective_date":
            self.value = date.fromisoformat(str(self.value)).isoformat()
        return self


class FactQuery(Model):
    predicates: list[Predicate] = Field(default_factory=list, max_length=8)
    as_of: date | None = None
    limit: int = Field(default=100, ge=1, le=100)
