"""Postgres owns bytes, obligations, leases, snapshots and audit history."""

import hashlib
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import files
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from cip.config import Settings
from cip.domain import PIPELINE_VERSION, verify_snapshot
from cip.models import Snapshot, canonical, digest


class LostLease(RuntimeError):
    pass


class ConflictError(RuntimeError):
    pass


class Store:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.load()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        with psycopg.connect(
            self.settings.database_url,
            row_factory=dict_row,
            connect_timeout=5,
            options="-c statement_timeout=5000",
        ) as conn:
            yield conn

    def migrate(self) -> None:
        with self.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(73412801)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations(version integer PRIMARY KEY)"
            )
            if not conn.execute("SELECT 1 FROM schema_migrations WHERE version=1").fetchone():
                conn.execute(files("cip").joinpath("schema.sql").read_text())
            if not conn.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone():
                conn.execute(files("cip").joinpath("schema_v2.sql").read_text())
            if not conn.execute("SELECT 1 FROM schema_migrations WHERE version=3").fetchone():
                conn.execute(files("cip").joinpath("schema_v3.sql").read_text())
                password = os.getenv("CIP_RUNTIME_PASSWORD", "cip-local-runtime")
                if os.getenv("CIP_ENV") == "production" and not os.getenv("CIP_RUNTIME_PASSWORD"):
                    raise ValueError("runtime_password_required")
                conn.execute(
                    sql.SQL("ALTER ROLE cip_runtime PASSWORD {}").format(sql.Literal(password))
                )

    def create_tenant(self) -> tuple[str, str]:
        org, key = str(uuid4()), "cip_" + secrets.token_urlsafe(32)
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO tenants(id,key_hash) VALUES(%s,%s)",
                (org, hashlib.sha256(key.encode()).hexdigest()),
            )
        return org, key

    def authenticate(self, key: str) -> str | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT id FROM tenants WHERE key_hash=%s",
                (hashlib.sha256(key.encode()).hexdigest(),),
            ).fetchone()
            return row["id"] if row else None

    @staticmethod
    def enqueue(
        conn: Any,
        org: str,
        kind: str,
        subject: str,
        data: dict[str, Any],
        dependencies: list[str] | None = None,
    ) -> str:
        input_hash = digest(data)
        row = conn.execute(
            "INSERT INTO jobs(id,org_id,kind,subject,input_hash,input) VALUES(%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(org_id,kind,subject,input_hash) DO UPDATE "
            "SET input_hash=EXCLUDED.input_hash "
            "RETURNING id",
            (uuid4(), org, kind, subject, input_hash, Jsonb(data)),
        ).fetchone()
        job_id = str(row["id"])
        for dependency in dependencies or []:
            conn.execute(
                "INSERT INTO job_dependencies VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (org, job_id, dependency),
            )
        return job_id

    def upload(self, org: str, body: bytes, media: str) -> dict[str, str]:
        sha = hashlib.sha256(body).hexdigest()
        document = digest([org, sha])
        with self.connection() as conn:
            conn.execute("SELECT id FROM tenants WHERE id=%s FOR UPDATE", (org,))
            exists = conn.execute(
                "SELECT id FROM documents WHERE org_id=%s AND id=%s", (org, document)
            ).fetchone()
            quota = conn.execute(
                "SELECT count(*) AS n,COALESCE(sum(octet_length(body)),0) AS size "
                "FROM documents WHERE org_id=%s",
                (org,),
            ).fetchone()
            if not exists and (quota["n"] >= 100 or quota["size"] + len(body) > 5_000_000):
                raise ValueError("tenant_document_quota_exhausted")
            conn.execute(
                "INSERT INTO documents(id,org_id,sha256,media,body) VALUES(%s,%s,%s,%s,%s) "
                "ON CONFLICT(org_id,sha256) DO NOTHING",
                (document, org, sha, media, body),
            )
            stored = conn.execute(
                "SELECT media FROM documents WHERE org_id=%s AND id=%s", (org, document)
            ).fetchone()
            job = self.enqueue(
                conn,
                org,
                "analyze",
                document,
                {
                    "document_id": document,
                    "media": stored["media"],
                    "sha256": sha,
                    "version": PIPELINE_VERSION,
                },
            )
        return {"document_id": document, "job_id": job}

    def request_snapshot(
        self,
        org: str,
        version: str = PIPELINE_VERSION,
        resolutions: dict[str, str] | None = None,
        exclusions: dict[str, str] | None = None,
    ) -> str:
        with self.connection() as conn:
            docs = conn.execute(
                "SELECT id,sha256,media,octet_length(body) AS size "
                "FROM documents WHERE org_id=%s ORDER BY id LIMIT 101",
                (org,),
            ).fetchall()
            if not docs or len(docs) > 100:
                raise ValueError("snapshot_requires_1_to_100_documents")
            if sum(d["size"] for d in docs) > 5_000_000:
                raise ValueError("snapshot_total_bytes_limit")
            exclusions = exclusions or {}
            if any(
                key not in {d["id"] for d in docs} or not reason.strip() or len(reason) > 500
                for key, reason in exclusions.items()
            ):
                raise ValueError("invalid_scope_exclusion")
            all_hashes = {d["id"]: d["sha256"] for d in docs}
            docs = [d for d in docs if d["id"] not in exclusions]
            if not docs:
                raise ValueError("scope_requires_at_least_one_document")
            dependencies = [
                self.enqueue(
                    conn,
                    org,
                    "analyze",
                    d["id"],
                    {
                        "document_id": d["id"],
                        "sha256": d["sha256"],
                        "media": d["media"],
                        "version": PIPELINE_VERSION,
                    },
                )
                for d in docs
            ]
            data = {
                "version": version,
                "pipeline": PIPELINE_VERSION,
                "dependencies": dependencies,
                "resolutions": resolutions or {},
                "source_hashes": {d["id"]: d["sha256"] for d in docs},
                "exclusions": exclusions,
                "excluded_hashes": {key: all_hashes[key] for key in exclusions},
            }
            job = self.enqueue(conn, org, "snapshot", org, data, dependencies)
            conn.execute(
                "INSERT INTO audit_events(org_id,actor,action,detail) VALUES(%s,%s,%s,%s)",
                (
                    org,
                    "api-key",
                    "snapshot_requested",
                    Jsonb({"job": job, "resolutions": resolutions or {}, "exclusions": exclusions}),
                ),
            )
        return job

    def claim(self, job_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            return conn.execute(
                "UPDATE jobs j SET state='running',attempts=attempts+1,claim_token=%s, "
                "locked_until=clock_timestamp() + %s * interval '1 second' "
                "WHERE j.id=%s AND run_after<=clock_timestamp() AND attempts<max_attempts "
                "AND (state='queued' OR (state='running' AND locked_until<clock_timestamp())) "
                "AND NOT EXISTS(SELECT 1 FROM job_dependencies d JOIN jobs p "
                "ON p.id=d.dependency_id WHERE d.job_id=j.id AND p.state<>'succeeded') "
                "RETURNING j.*",
                (uuid4(), self.settings.lease_seconds, UUID(job_id)),
            ).fetchone()

    def heartbeat(self, job_id: str, token: str) -> bool:
        with self.connection() as conn:
            return (
                conn.execute(
                    "UPDATE jobs SET locked_until=clock_timestamp()+%s * interval '1 second' "
                    "WHERE id=%s AND claim_token=%s AND state='running' "
                    "AND locked_until>clock_timestamp() RETURNING id",
                    (self.settings.lease_seconds, job_id, token),
                ).fetchone()
                is not None
            )

    def complete(
        self, job: dict[str, Any], result: dict[str, Any], snapshot: Snapshot | None = None
    ) -> None:
        with self.connection() as conn:
            owned = conn.execute(
                "SELECT id FROM jobs WHERE id=%s AND claim_token=%s AND state='running' "
                "AND locked_until>clock_timestamp() FOR UPDATE",
                (job["id"], job["claim_token"]),
            ).fetchone()
            if not owned:
                raise LostLease("lease_lost")
            if snapshot:
                if len(canonical(snapshot).encode()) > 8_000_000:
                    raise ValueError("snapshot_artifact_size_limit")
                if verify_snapshot(snapshot):
                    raise ValueError("snapshot_invariants_failed")
                sid = uuid4()
                conn.execute(
                    "INSERT INTO snapshots(id,org_id,job_id,payload,content_hash) "
                    "VALUES(%s,%s,%s,%s,%s)",
                    (
                        sid,
                        job["org_id"],
                        job["id"],
                        Jsonb(snapshot.model_dump(mode="json")),
                        digest(snapshot),
                    ),
                )
                result = {"snapshot_id": str(sid), "content_hash": digest(snapshot)}
            conn.execute(
                "UPDATE jobs SET state='succeeded',result=%s,claim_token=NULL, "
                "locked_until=NULL,error=NULL WHERE id=%s",
                (Jsonb(result), job["id"]),
            )

    def fail(self, job: dict[str, Any], error: str, retryable: bool = True) -> bool:
        delay = min(60, 2 ** job["attempts"]) + secrets.randbelow(1000) / 1000
        with self.connection() as conn:
            row = conn.execute(
                "UPDATE jobs SET state=CASE WHEN attempts>=max_attempts OR NOT %s THEN 'dead' "
                "ELSE 'queued' END,run_after=clock_timestamp()+%s * interval '1 second', "
                "claim_token=NULL,locked_until=NULL,dispatched_at=NULL,error=%s "
                "WHERE id=%s AND claim_token=%s AND state='running' "
                "AND locked_until>clock_timestamp() RETURNING id",
                (retryable, delay, error[:100], job["id"], job["claim_token"]),
            ).fetchone()
            return row is not None

    def dispatchable(self) -> list[str]:
        with self.connection() as conn:
            conn.execute(
                "UPDATE jobs SET state='dead',claim_token=NULL,locked_until=NULL, "
                "error='attempts_exhausted' WHERE state='running' "
                "AND locked_until<clock_timestamp() AND attempts>=max_attempts"
            )
            conn.execute(
                "UPDATE jobs j SET state='dead',error='dependency_dead' WHERE state='queued' "
                "AND EXISTS(SELECT 1 FROM job_dependencies d JOIN jobs p ON p.id=d.dependency_id "
                "WHERE d.job_id=j.id AND p.state='dead')"
            )
            rows = conn.execute(
                "SELECT j.id FROM jobs j WHERE run_after<=clock_timestamp() "
                "AND attempts<max_attempts "
                "AND (state='queued' OR (state='running' AND locked_until<clock_timestamp())) "
                "AND (dispatched_at IS NULL OR "
                "dispatched_at<clock_timestamp()-%s*interval '1 second') "
                "AND NOT EXISTS(SELECT 1 FROM job_dependencies d JOIN jobs p "
                "ON p.id=d.dependency_id "
                "WHERE d.job_id=j.id AND p.state<>'succeeded') ORDER BY created_at,id LIMIT 100",
                (self.settings.dispatch_seconds,),
            ).fetchall()
            return [str(row["id"]) for row in rows]

    def dispatched(self, job_id: str) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE jobs SET dispatched_at=clock_timestamp() WHERE id=%s", (job_id,))

    def get_job(self, org: str, job_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT id,kind,state,attempts,result,error FROM jobs WHERE org_id=%s AND id=%s",
                (org, job_id),
            ).fetchone()

    def document(self, org: str, doc_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM documents WHERE org_id=%s AND id=%s", (org, doc_id)
            ).fetchone()

    def dependency_results(self, org: str, ids: list[str]) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT id,result FROM jobs WHERE org_id=%s AND id=ANY(%s::uuid[]) "
                "AND state='succeeded'",
                (org, ids),
            ).fetchall()
            by_id = {str(row["id"]): row["result"] for row in rows}
            if len(by_id) != len(ids):
                raise ValueError("missing_dependencies")
            return [by_id[i] for i in ids]

    def snapshot(self, org: str, sid: str | None = None) -> tuple[str, Snapshot] | None:
        with self.connection() as conn:
            if sid is None:
                row = conn.execute(
                    "SELECT s.id,s.payload FROM tenants t JOIN snapshots s "
                    "ON s.id=t.active_snapshot AND s.org_id=t.id WHERE t.id=%s",
                    (org,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id,payload FROM snapshots WHERE org_id=%s AND id=%s", (org, sid)
                ).fetchone()
            return (str(row["id"]), Snapshot.model_validate(row["payload"])) if row else None

    def snapshot_build_options(self, org: str, sid: str) -> dict[str, dict[str, str]]:
        """Restore reviewed decisions from this immutable snapshot's captured build input."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT j.input FROM snapshots s JOIN jobs j "
                "ON j.id=s.job_id AND j.org_id=s.org_id WHERE s.org_id=%s AND s.id=%s",
                (org, sid),
            ).fetchone()
            if row is None:
                raise ConflictError("snapshot_missing")
            return {
                name: dict(row["input"].get(name, {})) for name in ("resolutions", "exclusions")
            }

    def promote(self, org: str, sid: str, expected: str | None) -> None:
        with self.connection() as conn:
            row = conn.execute(
                "UPDATE tenants SET active_snapshot=%s WHERE id=%s "
                "AND active_snapshot IS NOT DISTINCT FROM %s::uuid "
                "AND EXISTS(SELECT 1 FROM snapshots WHERE org_id=%s AND id=%s) "
                "RETURNING id",
                (sid, org, expected, org, sid),
            ).fetchone()
            if not row:
                raise ConflictError("active_snapshot_changed_or_missing")
            conn.execute(
                "INSERT INTO audit_events(org_id,actor,action,detail) VALUES(%s,%s,%s,%s)",
                (org, "api-key", "snapshot_promoted", Jsonb({"from": expected, "to": sid})),
            )

    def retry(self, org: str, job_id: str) -> bool:
        with self.connection() as conn:
            row = conn.execute(
                "UPDATE jobs SET state='queued',attempts=0,error=NULL, "
                "run_after=clock_timestamp(),dispatched_at=NULL "
                "WHERE org_id=%s AND id=%s AND state='dead' RETURNING id",
                (org, job_id),
            ).fetchone()
            if row:
                conn.execute(
                    "INSERT INTO audit_events(org_id,actor,action,detail) VALUES(%s,%s,%s,%s)",
                    (org, "api-key", "job_retried", Jsonb({"job": job_id})),
                )
            return row is not None

    def reserve_tokens(self, org: str, run_key: str, tokens: int, cap: int) -> str:
        with self.connection() as conn:
            conn.execute("SELECT id FROM tenants WHERE id=%s FOR UPDATE", (org,))
            used = conn.execute(
                "SELECT COALESCE(sum(COALESCE(used_tokens,reserved_tokens)),0) AS n "
                "FROM llm_reservations WHERE org_id=%s AND run_key=%s",
                (org, run_key),
            ).fetchone()["n"]
            if tokens <= 0 or used + tokens > cap:
                raise ValueError("token_budget_exhausted")
            receipt = uuid4()
            conn.execute(
                "INSERT INTO llm_reservations(id,org_id,run_key,reserved_tokens) "
                "VALUES(%s,%s,%s,%s)",
                (receipt, org, run_key, tokens),
            )
            return str(receipt)

    def finish_tokens(self, org: str, receipt: str, used: int | None) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE llm_reservations SET used_tokens=%s,state=%s "
                "WHERE org_id=%s AND id=%s AND state='reserved'",
                (used, "completed" if used is not None else "unknown", org, receipt),
            )

    def save_query(self, org: str, sid: str, payload: dict[str, Any]) -> str:
        receipt = str(uuid4())
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO query_receipts(id,org_id,snapshot_id,payload) VALUES(%s,%s,%s,%s)",
                (receipt, org, sid, Jsonb(payload)),
            )
        return receipt
