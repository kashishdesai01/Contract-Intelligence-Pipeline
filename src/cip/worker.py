"""Redis delivers hints. Database reconciliation is the liveness authority."""

import logging
import secrets
import signal
import threading
import time
import zipfile
from typing import Any
from uuid import UUID

import psycopg
import redis
from prometheus_client import Counter

from cip.domain import PIPELINE_VERSION, analyze, build_snapshot, parse_bytes
from cip.models import Conflict, Instrument, digest
from cip.store import LostLease, Store

LOG = logging.getLogger(__name__)
OUTCOMES = Counter("cip_jobs_total", "Worker attempts", ["kind", "outcome"])
STREAM = "cip:work"
GROUP = "cip-workers"


class Worker:
    def __init__(self, store: Store, client: Any | None = None):
        self.store = store
        self.redis: Any = client or redis.Redis.from_url(
            store.settings.redis_url, decode_responses=True
        )
        self.consumer = f"worker-{UUID(int=secrets.randbits(128))}"
        self.stopping = threading.Event()

    def group(self) -> None:
        try:
            self.redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def relay(self) -> int:
        self.group()
        count = 0
        for job_id in self.store.dispatchable():
            self.redis.xadd(STREAM, {"job_id": job_id}, maxlen=10000, approximate=True)
            self.store.dispatched(job_id)
            count += 1
        return count

    def execute(self, job: dict[str, Any]) -> None:
        stopped = threading.Event()
        lost = threading.Event()

        def renew() -> None:
            while not stopped.wait(max(0.1, self.store.settings.lease_seconds / 3)):
                try:
                    if not self.store.heartbeat(str(job["id"]), str(job["claim_token"])):
                        lost.set()
                        return
                except Exception:
                    lost.set()
                    return

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            data = job["input"]
            if data.get("pipeline", data.get("version")) != PIPELINE_VERSION:
                raise ValueError("incompatible_worker_version")
            if job["kind"] == "analyze":
                document = self.store.document(job["org_id"], data["document_id"])
                if not document:
                    raise ValueError("missing_document")
                try:
                    text = parse_bytes(bytes(document["body"]), document["media"])
                    instrument = analyze(text, document["id"])
                    result = {
                        "instrument": instrument.model_dump(mode="json"),
                        "status": "ok",
                        "text": text,
                    }
                except (ValueError, UnicodeError, zipfile.BadZipFile):
                    result = {
                        "status": "needs_review",
                        "source_id": document["id"],
                        "reason": "unsupported_or_malformed_document",
                    }
                if lost.is_set():
                    raise LostLease("lease_lost")
                self.store.complete(job, result)
            elif job["kind"] == "snapshot":
                results = self.store.dependency_results(job["org_id"], data["dependencies"])
                instruments = [
                    Instrument.model_validate(r["instrument"])
                    for r in results
                    if r["status"] == "ok"
                ]
                snapshot = build_snapshot(
                    instruments, data["source_hashes"], data["version"], data["resolutions"]
                )
                snapshot.exclusions = data.get("exclusions", {})
                snapshot.review.extend(
                    {"source_id": r["source_id"], "reason": r["reason"]}
                    for r in results
                    if r["status"] != "ok"
                )
                if any(r["status"] != "ok" for r in results):
                    # A malformed document may be an amendment with an unknown date or target.
                    # It cannot be safely dismissed as unrelated to otherwise trusted families.
                    unknown_source = next(r["source_id"] for r in results if r["status"] != "ok")
                    for family in snapshot.families:
                        for composite in family.versions:
                            composite.conflicts.append(
                                Conflict(
                                    id=digest([unknown_source, "unparsed-source"]),
                                    source_id=unknown_source,
                                    target="*",
                                    kind="unparsed_source",
                                    detail="Scope contains an unparsed document.",
                                )
                            )
                            composite.deviations = []
                            for fact in composite.facts.values():
                                fact.status, fact.reason = "disputed", "unparsed_scope_source"
                if lost.is_set():
                    raise LostLease("lease_lost")
                self.store.complete(job, {}, snapshot)
            else:
                raise ValueError("unknown_job_kind")
            OUTCOMES.labels(job["kind"], "succeeded").inc()
        except LostLease:
            OUTCOMES.labels(job["kind"], "lost_lease").inc()
        except Exception as exc:
            # Exceptions may contain contract text or secrets; retain only the class name.
            self.store.fail(job, type(exc).__name__, retryable=not isinstance(exc, ValueError))
            OUTCOMES.labels(job["kind"], "failed").inc()
            LOG.warning("job_failed", extra={"job_id": str(job["id"]), "kind": job["kind"]})
        finally:
            stopped.set()
            thread.join(timeout=2)

    def once(self, block: int = 100) -> bool:
        self.relay()
        # Reclaimed hints may refer to busy jobs: ack is safe because DB obligations persist.
        reclaimed = self.redis.xautoclaim(
            STREAM, GROUP, self.consumer, min_idle_time=1000, start_id="0-0", count=10
        )
        messages = reclaimed[1]
        if not messages:
            batches = self.redis.xreadgroup(
                GROUP, self.consumer, {STREAM: ">"}, count=1, block=block
            )
            messages = batches[0][1] if batches else []
        for message_id, values in messages:
            try:
                job = self.store.claim(values["job_id"])
            except (ValueError, KeyError):
                self.redis.xack(STREAM, GROUP, message_id)
                LOG.warning("invalid_delivery_hint")
                continue
            if job:
                self.execute(job)
            self.redis.xack(STREAM, GROUP, message_id)
        return bool(messages)

    def run(self) -> None:
        def stop(*_: Any) -> None:
            self.stopping.set()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not self.stopping.is_set():
            try:
                self.once(1000)
            except (redis.RedisError, psycopg.Error, OSError):
                LOG.warning("delivery_unavailable")
                self.stopping.wait(1)

    def drain(self, org: str, job_id: str, timeout: float = 30) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.store.get_job(org, job_id)
            if result and result["state"] in {"succeeded", "dead"}:
                return result
            self.once()
        raise TimeoutError("pipeline_drain_timeout")
