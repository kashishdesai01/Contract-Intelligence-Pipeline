"""Authenticated, tenant-scoped API. Every read binds once to a published snapshot."""

import hashlib
import logging
import secrets
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import psycopg
import redis
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from prometheus_client import Counter, Histogram, generate_latest
from pydantic import Field, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.staticfiles import StaticFiles

from cip.domain import PIPELINE_VERSION, as_of, verify_snapshot
from cip.llm import LivePlanner
from cip.models import FIELDS, FactQuery, Model, digest
from cip.observability import configure_logging
from cip.query import lookup, query_facts, search
from cip.security import BodyLimit
from cip.store import ConflictError, Store

REQUESTS = Counter("cip_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("cip_http_seconds", "HTTP latency", ["route"])


class BuildRequest(Model):
    version: str = Field(default=PIPELINE_VERSION, min_length=1, max_length=80)
    resolutions: dict[str, Literal["skip"]] = Field(default_factory=dict, max_length=100)
    exclusions: dict[str, str] = Field(default_factory=dict, max_length=100)


class PromoteRequest(Model):
    expected_active: UUID | None


class SearchRequest(Model):
    query: str = Field(min_length=1, max_length=500)
    as_of: date | None = None
    limit: int = Field(default=10, ge=1, le=20)
    method: Literal["bm25", "trigram", "hybrid"] = "hybrid"


class AskRequest(Model):
    question: str = Field(default="", max_length=2000)
    mode: Literal["structured", "live"] = "structured"
    action: Literal["count", "lookup", "search"] = "count"
    query: FactQuery = Field(default_factory=FactQuery)
    family_id: str | None = None
    field: str | None = None

    @model_validator(mode="after")
    def consistent_request(self) -> "AskRequest":
        if self.mode == "live":
            if not self.question.strip():
                raise ValueError("live_question_required")
            if self.action != "count" or self.query != FactQuery() or self.family_id or self.field:
                raise ValueError("live_mode_accepts_a_question_without_ignored_typed_arguments")
            return self
        if self.action == "count" and (self.question or self.family_id or self.field):
            raise ValueError("count_requires_a_typed_query_not_a_natural_language_question")
        if self.action == "lookup" and (
            not self.family_id or self.field not in FIELDS or self.question or self.query.predicates
        ):
            raise ValueError("lookup_requires_family_and_supported_field_without_ignored_filters")
        if self.action == "search" and (
            not self.question.strip() or self.family_id or self.field or self.query.predicates
        ):
            raise ValueError("search_requires_text_without_ignored_filters")
        return self


def create_app(store: Store | None = None) -> FastAPI:
    db = store or Store()
    app = FastAPI(title="Contract Intelligence Pipeline", version="0.1.0")
    app.add_middleware(BodyLimit)
    configure_logging()
    log = logging.getLogger("cip.api")
    rate: Any = redis.Redis.from_url(
        db.settings.redis_url, socket_timeout=2, socket_connect_timeout=2
    )

    def session_key(token: str) -> str:
        return "cip:session:" + hashlib.sha256(token.encode()).hexdigest()

    def browser_origin(request: Request) -> None:
        origin = request.headers.get("origin")
        if (origin and origin != str(request.base_url).rstrip("/")) or request.headers.get(
            "sec-fetch-site"
        ) == "cross-site":
            raise HTTPException(403, "cross_origin_request_rejected")

    def auth(request: Request, x_api_key: Annotated[str | None, Header()] = None) -> str:
        try:
            if x_api_key:
                org = db.authenticate(x_api_key)
            else:
                token = request.cookies.get("cip_session", "")
                raw = rate.get(session_key(token)) if token else None
                org = raw.decode() if raw else None
                if org and request.method not in {"GET", "HEAD", "OPTIONS"}:
                    browser_origin(request)
                    if request.headers.get("origin") != str(request.base_url).rstrip("/"):
                        raise HTTPException(403, "same_origin_required")
            if org is None:
                raise HTTPException(401, "invalid_api_key")
            used = rate.eval(
                "local n=redis.call('INCR',KEYS[1]); "
                "if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n",
                1,
                f"cip:api-rate:{org}",
            )
            if int(used) > 120:
                raise HTTPException(429, "request_rate_exceeded")
        except redis.RedisError:
            raise HTTPException(503, "rate_limiter_unavailable") from None
        return org

    Org = Annotated[str, Depends(auth)]

    @app.post("/v1/session")
    def sign_in(org: Org, request: Request, response: Response) -> dict[str, str]:
        browser_origin(request)
        token = secrets.token_urlsafe(32)
        try:
            old = request.cookies.get("cip_session")
            with rate.pipeline(transaction=True) as pipe:
                if old:
                    pipe.delete(session_key(old))
                pipe.set(session_key(token), org, ex=28_800)
                pipe.execute()
        except redis.RedisError:
            raise HTTPException(503, "session_store_unavailable") from None
        response.set_cookie(
            "cip_session",
            token,
            max_age=28_800,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        return {"org_id": org}

    @app.get("/v1/session")
    def session_status(org: Org, response: Response) -> dict[str, str]:
        response.headers["Cache-Control"] = "no-store"
        return {"org_id": org}

    @app.delete("/v1/session", status_code=204)
    def sign_out(request: Request) -> Response:
        browser_origin(request)
        token = request.cookies.get("cip_session")
        if token:
            if request.headers.get("origin") != str(request.base_url).rstrip("/"):
                raise HTTPException(403, "same_origin_required")
            try:
                rate.delete(session_key(token))
            except redis.RedisError:
                raise HTTPException(503, "session_store_unavailable") from None
        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        response.delete_cookie("cip_session", path="/", httponly=True, samesite="strict")
        return response

    @app.middleware("http")
    async def observe(request: Request, call_next: Callable[..., Any]) -> Response:
        started = time.monotonic()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        LATENCY.labels(route).observe(time.monotonic() - started)
        request_id = str(uuid4())
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/v1/"):
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers["Cache-Control"] = "no-cache"
        if response.headers.get("content-type", "").startswith(
            "text/html"
        ) and request.url.path in {"/", "/index.html"}:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            )
            response.headers["Referrer-Policy"] = "no-referrer"
        log.info(
            "http_request",
            extra={"request_id": request_id, "route": route, "status": response.status_code},
        )
        return response

    @app.exception_handler(HTTPException)
    async def problem(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            {"type": "about:blank", "title": str(exc.detail), "status": exc.status_code},
            status_code=exc.status_code,
            media_type="application/problem+json",
        )

    @app.exception_handler(ConflictError)
    async def conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(
            {"type": "about:blank", "title": str(exc), "status": 409},
            status_code=409,
            media_type="application/problem+json",
        )

    @app.exception_handler(psycopg.Error)
    async def database_unavailable(_: Request, __: psycopg.Error) -> JSONResponse:
        return JSONResponse(
            {"type": "about:blank", "title": "database_unavailable", "status": 503},
            status_code=503,
            media_type="application/problem+json",
        )

    def active(org: str) -> Any:
        result = db.snapshot(org)
        if result is None:
            raise HTTPException(409, "no_published_snapshot")
        return result

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/readyz")
    def ready() -> dict[str, str]:
        try:
            with db.connection() as conn:
                row = conn.execute(
                    "SELECT version FROM schema_migrations WHERE version=3"
                ).fetchone()
                if row is None:
                    raise HTTPException(503, "schema_not_ready")
            rate.ping()
        except (psycopg.Error, redis.RedisError):
            raise HTTPException(503, "dependencies_unavailable") from None
        return {"status": "ready"}

    @app.get("/metrics")
    def metrics(org: Org) -> Response:
        return Response(generate_latest(), media_type="text/plain; version=0.0.4")

    @app.post("/v1/documents", status_code=202)
    async def upload(org: Org, file: Annotated[UploadFile, File()]) -> dict[str, str]:
        media = file.content_type or ""
        if media not in {
            "text/plain",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }:
            raise HTTPException(415, "unsupported_media_type")
        body = await file.read(db.settings.max_upload_bytes + 1)
        await file.close()
        if len(body) > db.settings.max_upload_bytes:
            raise HTTPException(413, "upload_too_large")
        try:
            return await run_in_threadpool(db.upload, org, body, media)
        except ValueError as exc:
            raise HTTPException(413, str(exc)) from None

    @app.get("/v1/documents/{document_id}")
    def document(org: Org, document_id: str) -> dict[str, Any]:
        row = db.document(org, document_id)
        if not row:
            raise HTTPException(404, "document_not_found")
        return {key: row[key] for key in ("id", "sha256", "media", "created_at")}

    @app.post("/v1/snapshots", status_code=202)
    def build(org: Org, body: BuildRequest) -> dict[str, str]:
        try:
            return {
                "job_id": db.request_snapshot(
                    org, body.version, dict(body.resolutions), body.exclusions
                )
            }
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/v1/jobs/{job_id}")
    def job(org: Org, job_id: UUID) -> dict[str, Any]:
        result = db.get_job(org, str(job_id))
        if result is None:
            raise HTTPException(404, "job_not_found")
        return result

    @app.get("/v1/jobs")
    def jobs(org: Org, after: UUID | None = None) -> dict[str, Any]:
        with db.connection() as conn:
            if after:
                cursor = conn.execute(
                    "SELECT created_at,id FROM jobs WHERE org_id=%s AND id=%s", (org, after)
                ).fetchone()
                if not cursor:
                    raise HTTPException(404, "cursor_not_found")
                rows = conn.execute(
                    "SELECT id,kind,state,attempts,created_at FROM jobs "
                    "WHERE org_id=%s AND (created_at,id)>(%s,%s) ORDER BY created_at,id LIMIT 21",
                    (org, cursor["created_at"], cursor["id"]),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id,kind,state,attempts,created_at FROM jobs "
                    "WHERE org_id=%s ORDER BY created_at,id LIMIT 21",
                    (org,),
                ).fetchall()
        return {"jobs": rows[:20], "next": str(rows[19]["id"]) if len(rows) > 20 else None}

    @app.post("/v1/jobs/{job_id}/retry")
    def retry(org: Org, job_id: UUID) -> dict[str, bool]:
        if not db.retry(org, str(job_id)):
            raise HTTPException(409, "job_missing_or_not_dead")
        return {"queued": True}

    @app.get("/v1/snapshots/current")
    def current(org: Org) -> dict[str, Any]:
        sid, snapshot = active(org)
        return {
            "snapshot_id": sid,
            "content": snapshot.model_dump(mode="json"),
            "build_options": db.snapshot_build_options(org, sid),
        }

    @app.get("/v1/snapshots/{snapshot_id}")
    def snapshot_get(org: Org, snapshot_id: UUID) -> dict[str, Any]:
        result = db.snapshot(org, str(snapshot_id))
        if result is None:
            raise HTTPException(404, "snapshot_not_found")
        return {"snapshot_id": result[0], "content": result[1].model_dump(mode="json")}

    @app.post("/v1/snapshots/{snapshot_id}/promote")
    def promote(org: Org, snapshot_id: UUID, body: PromoteRequest) -> dict[str, str]:
        db.promote(
            org, str(snapshot_id), str(body.expected_active) if body.expected_active else None
        )
        return {"active_snapshot": str(snapshot_id)}

    @app.get("/v1/families/{family_id}/composite")
    def composite(org: Org, family_id: str, as_of_date: date | None = None) -> dict[str, Any]:
        sid, snapshot = active(org)
        family = next((f for f in snapshot.families if f.id == family_id), None)
        version = as_of(family, as_of_date) if family else None
        if not version:
            raise HTTPException(404, "composite_not_found")
        return {
            "snapshot_id": sid,
            "family_id": family_id,
            "composite": version.model_dump(mode="json"),
        }

    @app.get("/v1/families")
    def families(org: Org) -> dict[str, Any]:
        sid, snapshot = active(org)
        return {
            "snapshot_id": sid,
            "families": [
                {
                    "id": f.id,
                    "agreement": f.agreement,
                    "parties": f.parties,
                    "versions": len(f.versions),
                }
                for f in snapshot.families
            ],
        }

    @app.get("/v1/families/{family_id}/timeline")
    def timeline(org: Org, family_id: str) -> dict[str, Any]:
        sid, snapshot = active(org)
        family = next((f for f in snapshot.families if f.id == family_id), None)
        if family is None:
            raise HTTPException(404, "family_not_found")
        return {
            "snapshot_id": sid,
            "versions": [
                {"as_of": v.as_of, "conflicts": len(v.conflicts)} for v in family.versions
            ],
        }

    @app.get("/v1/families/{family_id}/diff")
    def diff(org: Org, family_id: str, from_date: date, to_date: date) -> dict[str, Any]:
        sid, snapshot = active(org)
        family = next((f for f in snapshot.families if f.id == family_id), None)
        old = as_of(family, from_date) if family else None
        new = as_of(family, to_date) if family else None
        if not old or not new:
            raise HTTPException(404, "version_not_found")
        before, after = {c.key: c for c in old.clauses}, {c.key: c for c in new.clauses}
        changes = [
            {"clause_key": k, "before": before[k].model_dump(), "after": after[k].model_dump()}
            for k in sorted(before.keys() & after.keys())
            if before[k].text != after[k].text or before[k].disputed != after[k].disputed
        ]
        return {"snapshot_id": sid, "changes": changes, "conflicts": new.conflicts}

    @app.get("/v1/review")
    def review(org: Org) -> dict[str, Any]:
        sid, snapshot = active(org)
        conflicts = [
            {
                **c.model_dump(),
                "family_id": f.id,
                "as_of": v.as_of,
                "skip_resolution_key": digest([c.source_id, c.target]),
            }
            for f in snapshot.families
            for v in f.versions
            for c in v.conflicts
        ]
        return {"snapshot_id": sid, "documents": snapshot.review, "conflicts": conflicts}

    @app.post("/v1/query")
    def facts(org: Org, body: FactQuery) -> dict[str, Any]:
        sid, snapshot = active(org)
        result = {"snapshot_id": sid, **query_facts(snapshot, body)}
        result["receipt_id"] = db.save_query(org, sid, result)
        return result

    @app.get("/v1/receipts/{receipt_id}")
    def receipt(org: Org, receipt_id: UUID) -> dict[str, Any]:
        with db.connection() as conn:
            row = conn.execute(
                "SELECT payload FROM query_receipts WHERE org_id=%s AND id=%s", (org, receipt_id)
            ).fetchone()
        if not row:
            raise HTTPException(404, "receipt_not_found")
        return row["payload"]

    @app.post("/v1/search")
    def search_api(org: Org, body: SearchRequest) -> dict[str, Any]:
        sid, snapshot = active(org)
        return {
            "snapshot_id": sid,
            "results": search(snapshot, body.query, body.as_of, body.limit, body.method),
        }

    @app.get("/v1/deviations")
    def deviation_api(org: Org, as_of_date: date | None = None) -> dict[str, Any]:
        sid, snapshot = active(org)
        results: list[dict[str, Any]] = []
        for family in snapshot.families:
            composite = as_of(family, as_of_date)
            if composite:
                results.extend(
                    {**d, "family_id": family.id, "as_of": composite.as_of}
                    for d in composite.deviations
                )
        return {"snapshot_id": sid, "results": results}

    @app.post("/v1/ask")
    def ask(org: Org, body: AskRequest) -> dict[str, Any]:
        sid, snapshot = active(org)
        action, query = body.action, body.query
        family_id, field, question = body.family_id, body.field, body.question
        trace: dict[str, Any] = {"mode": "structured", "snapshot_id": sid}
        if body.mode == "live":
            try:
                plan, trace = LivePlanner(db, org, sid).plan(body.question)
                action, query = plan.action, plan.query  # type: ignore[assignment]
                family_id, field, question = plan.family_id, plan.field, plan.search_query or ""
            except Exception:
                return {
                    "snapshot_id": sid,
                    "abstained": True,
                    "citations": [],
                    "answer": "Planner unavailable or output invalid.",
                    "trace": trace,
                }
        trace["tool"] = action
        if action == "count":
            result = {"snapshot_id": sid, **query_facts(snapshot, query)}
            receipt_id = db.save_query(org, sid, result)
            partial = not result["coverage"]["complete"]
            answer = f"{result['count']} matching agreement families in the evaluated population."
            if partial:
                answer += " Coverage is incomplete; this is a partial count."
            return {
                "snapshot_id": sid,
                "answer": answer,
                "abstained": False,
                "citations": [{"receipt_id": receipt_id}],
                "coverage": result["coverage"],
                "trace": trace,
                "count": result["count"],
            }
        if action == "lookup" and family_id and field in FIELDS:
            return {
                "snapshot_id": sid,
                **lookup(snapshot, family_id, field, query.as_of),
                "trace": trace,
            }
        if action == "search":
            results = search(snapshot, question, query.as_of, limit=min(query.limit, 20))
            return {
                "snapshot_id": sid,
                "answer": "Composite passages with original-source evidence; no legal conclusion.",
                "abstained": not results,
                "citations": results,
                "trace": trace,
            }
        return {
            "snapshot_id": sid,
            "answer": "Unsupported question.",
            "abstained": True,
            "citations": [],
            "trace": trace,
        }

    @app.get("/v1/verify")
    def verify(org: Org) -> dict[str, Any]:
        sid, snapshot = active(org)
        return {"snapshot_id": sid, "errors": verify_snapshot(snapshot)}

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "web", html=True), name="frontend")
    return app


app = create_app()
