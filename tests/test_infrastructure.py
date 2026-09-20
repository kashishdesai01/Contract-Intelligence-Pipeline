import concurrent.futures
import multiprocessing
import time
from dataclasses import replace
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from cip.api import create_app
from cip.corpus import amendment, base
from cip.llm import LivePlanner
from cip.store import ConflictError, LostLease, Store
from cip.worker import Worker

pytestmark = pytest.mark.integration


def build(db, org):
    db.upload(org, base().encode(), "text/plain")
    db.upload(org, amendment().encode(), "text/plain")
    job = db.request_snapshot(org)
    outcome = Worker(db).drain(org, job)
    assert outcome["state"] == "succeeded"
    return job, outcome["result"]["snapshot_id"]


def test_duplicate_admission_and_one_owner(db, tenant):
    org, _ = tenant

    def upload(_):
        return db.upload(org, base().encode(), "text/plain")

    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        rows = list(pool.map(upload, range(4)))
    assert len({r["document_id"] for r in rows}) == 1
    assert len({r["job_id"] for r in rows}) == 1
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        claims = list(pool.map(db.claim, [rows[0]["job_id"]] * 4))
    assert sum(c is not None for c in claims) == 1


def test_stale_owner_cannot_commit_heartbeat_or_fail(db, tenant):
    org, _ = tenant
    row = db.upload(org, base().encode(), "text/plain")
    a = db.claim(row["job_id"])
    with db.connection() as conn:
        conn.execute(
            "UPDATE jobs SET locked_until=clock_timestamp()-interval '1 second' WHERE id=%s",
            (row["job_id"],),
        )
    b = db.claim(row["job_id"])
    assert a["claim_token"] != b["claim_token"]
    assert not db.heartbeat(row["job_id"], str(a["claim_token"]))
    assert not db.fail(a, "stale")
    with pytest.raises(LostLease):
        db.complete(a, {"stale": True})
    Worker(db).execute(b)
    assert db.get_job(org, row["job_id"])["state"] == "succeeded"


def test_due_retry_and_exhaustion(db, tenant):
    org, _ = tenant
    row = db.upload(org, base().encode(), "text/plain")
    a = db.claim(row["job_id"])
    assert db.fail(a, "timeout")
    assert db.claim(row["job_id"]) is None
    with db.connection() as conn:
        conn.execute(
            "UPDATE jobs SET run_after=clock_timestamp(),max_attempts=2 WHERE id=%s",
            (row["job_id"],),
        )
    b = db.claim(row["job_id"])
    assert db.fail(b, "timeout")
    assert db.get_job(org, row["job_id"])["state"] == "dead"
    assert db.retry(org, row["job_id"])


def test_broker_stream_loss_reconciles_jobs(db, tenant):
    org, _ = tenant
    row = db.upload(org, base().encode(), "text/plain")
    worker = Worker(Store(replace(db.settings, dispatch_seconds=0)))
    worker.relay()
    worker.redis.delete("cip:work")  # Dedicated test stream only; never FLUSHALL.
    outcome = worker.drain(org, row["job_id"])
    assert outcome["state"] == "succeeded"


def test_snapshot_promotion_cas_rollback_and_tenant_fks(db, tenant):
    org, _ = tenant
    job, sid = build(db, org)
    db.promote(org, sid, None)
    assert db.request_snapshot(org) == job
    changed = db.request_snapshot(org, version="revision-two")
    assert changed != job
    outcome = Worker(db).drain(org, changed)
    next_sid = outcome["result"]["snapshot_id"]
    assert db.snapshot(org)[0] == sid
    db.promote(org, next_sid, sid)
    with pytest.raises(ConflictError):
        db.promote(org, sid, sid)
    db.promote(org, sid, next_sid)
    other, _ = db.create_tenant()
    assert db.snapshot(other, sid) is None
    with pytest.raises(ConflictError):
        db.promote(other, sid, None)


def _claim_and_pause(settings, job_id, ready):
    store = Store(settings)
    job = store.claim(job_id)
    ready.send(bool(job))
    time.sleep(120)


def test_actual_process_crash_recovery(db, tenant):
    org, _ = tenant
    row = db.upload(org, base().encode(), "text/plain")
    short = Store(replace(db.settings, lease_seconds=1))
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_claim_and_pause, args=(short.settings, row["job_id"], child))
    process.start()
    try:
        assert parent.poll(10) and parent.recv()
        process.kill()
        process.join(5)
        time.sleep(1.1)
        assert Worker(short).drain(org, row["job_id"])["state"] == "succeeded"
    finally:
        if process.is_alive():
            process.kill()
        process.join(5)


def test_http_tenant_isolation_receipts_and_abstention(db, tenant):
    org, key = tenant
    _, sid = build(db, org)
    db.promote(org, sid, None)
    client = TestClient(create_app(db))
    assert client.get("/v1/snapshots/current").status_code == 401
    auth = {"X-API-Key": key}
    assert client.get("/readyz").status_code == 200
    response = client.post(
        "/v1/ask",
        headers=auth,
        json={
            "action": "count",
            "query": {"predicates": [{"field": "payment_days", "op": "gt", "value": 45}]},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    receipt = response.json()["citations"][0]["receipt_id"]
    assert client.get(f"/v1/receipts/{receipt}", headers=auth).status_code == 200
    other, other_key = db.create_tenant()
    other_auth = {"X-API-Key": other_key}
    assert client.get(f"/v1/receipts/{receipt}", headers=other_auth).status_code == 404
    assert client.get(f"/v1/snapshots/{sid}", headers=other_auth).status_code == 404
    assert (
        client.post(
            "/v1/query", headers=auth, json={"predicates": [{"field": "org_id", "value": other}]}
        ).status_code
        == 422
    )
    family_id = db.snapshot(org)[1].families[0].id
    answer = client.post(
        "/v1/ask",
        headers=auth,
        json={"action": "lookup", "family_id": family_id, "field": "payment_days"},
    ).json()
    assert answer["value"] == 60 and not answer["abstained"]
    assert client.get("/metrics", headers=auth).status_code == 200


def test_budget_reservations_are_atomic_and_survive_redis_loss(db, tenant):
    org, _ = tenant

    def reserve(_):
        try:
            return db.reserve_tokens(org, "test-run", 60, 100)
        except ValueError:
            return None

    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        receipts = list(pool.map(reserve, range(4)))
    assert sum(r is not None for r in receipts) == 1
    with pytest.raises(ValueError):
        db.reserve_tokens(org, "test-run", 60, 100)


def test_live_adapter_against_mock_transport_only(db, tenant, monkeypatch):
    org, _ = tenant
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("CIP_MODEL", "test-model-revision")

    def respond(request):
        assert request.url == "https://api.openai.com/v1/responses"
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"total_tokens": 30},
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"action":"count","query":'
                                '{"predicates":[],"as_of":null,"limit":100},'
                                '"family_id":null,"field":null,"search_query":null}',
                            }
                        ],
                    }
                ],
            },
        )

    planner = LivePlanner(db, org, str(uuid4()), httpx.MockTransport(respond))
    plan, trace = planner.plan("How many agreements?")
    assert plan.action == "count" and trace["tokens"] == 30


def test_unparsed_document_blocks_trusted_scope(db, tenant):
    org, _ = tenant
    db.upload(org, base().encode(), "text/plain")
    db.upload(org, b"An amendment with no reliable reference or effective date", "text/plain")
    outcome = Worker(db).drain(org, db.request_snapshot(org))
    snapshot = db.snapshot(org, outcome["result"]["snapshot_id"])[1]
    assert snapshot.review
    assert all(f.status == "disputed" for f in snapshot.families[0].versions[-1].facts.values())


def test_artifacts_and_completed_results_are_database_immutable(db, tenant):
    org, _ = tenant
    job, sid = build(db, org)
    for sql, params in [
        ("UPDATE snapshots SET payload='{}' WHERE id=%s", (sid,)),
        ("UPDATE jobs SET result='{}' WHERE id=%s", (job,)),
        ("UPDATE documents SET body='corrupt' WHERE org_id=%s", (org,)),
    ]:
        with pytest.raises(psycopg.Error), db.connection() as conn:
            conn.execute(sql, params)


def test_failed_new_generation_leaves_old_snapshot_published(db, tenant):
    org, _ = tenant
    _, sid = build(db, org)
    db.promote(org, sid, None)
    bad = db.request_snapshot(org, resolutions={"unknown-operation": "skip"})
    assert Worker(db).drain(org, bad)["state"] == "dead"
    assert db.snapshot(org)[0] == sid


def test_http_body_bounds_and_typed_filters(db, tenant):
    _, key = tenant
    client = TestClient(create_app(db))
    auth = {"X-API-Key": key}
    assert (
        client.post(
            "/v1/documents",
            headers=auth,
            files={"file": ("large.txt", b"x" * 700_000, "text/plain")},
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/v1/query",
            headers=auth,
            json={"predicates": [{"field": "payment_days", "value": True}]},
        ).status_code
        == 422
    )


def test_pipeline_fingerprint_is_part_of_job_identity(db, tenant, monkeypatch):
    org, _ = tenant
    first = db.upload(org, base().encode(), "text/plain")
    monkeypatch.setattr("cip.store.PIPELINE_VERSION", "another-parser-revision")
    changed = db.upload(org, base().encode(), "text/plain")
    assert first["document_id"] == changed["document_id"]
    assert first["job_id"] != changed["job_id"]


def test_captured_manifest_excludes_concurrent_later_upload(db, tenant):
    org, _ = tenant
    db.upload(org, base().encode(), "text/plain")
    requested = db.request_snapshot(org)
    db.upload(org, amendment().encode(), "text/plain")
    outcome = Worker(db).drain(org, requested)
    snapshot = db.snapshot(org, outcome["result"]["snapshot_id"])[1]
    assert len(snapshot.source_hashes) == 1
    assert snapshot.families[0].versions[-1].facts["payment_days"].value == 30


def test_runtime_role_has_required_privileges_but_no_ddl(db):
    from cip.config import Settings

    runtime = Store(
        Settings(database_url="postgresql://cip_runtime:cip-local-runtime@127.0.0.1:55432/cip")
    )
    org, _ = runtime.create_tenant()
    _, sid = build(runtime, org)
    runtime.promote(org, sid, None)
    assert runtime.snapshot(org)[0] == sid
    with pytest.raises(psycopg.Error), runtime.connection() as conn:
        conn.execute("CREATE TABLE runtime_must_not_create(id integer)")


def test_scope_review_exclusion_recovers_with_visible_coverage(db, tenant):
    from cip.models import FactQuery
    from cip.query import query_facts

    org, _ = tenant
    db.upload(org, base().encode(), "text/plain")
    unrelated = db.upload(org, b"Unsupported unrelated synthetic attachment", "text/plain")
    outcome = Worker(db).drain(
        org,
        db.request_snapshot(
            org, exclusions={unrelated["document_id"]: "Reviewed: unrelated synthetic attachment"}
        ),
    )
    snapshot = db.snapshot(org, outcome["result"]["snapshot_id"])[1]
    assert snapshot.families[0].versions[-1].facts["payment_days"].status == "ok"
    coverage = query_facts(snapshot, FactQuery())["coverage"]
    assert coverage["reviewer_excluded_documents"] == 1 and not coverage["complete"]
    with pytest.raises(ValueError, match="invalid_scope_exclusion"):
        db.request_snapshot(org, exclusions={"other-tenant-document": "Unrelated"})


def test_reconnect_restores_review_options_without_cross_tenant_access(db, tenant):
    from cip.models import digest

    org, key = tenant
    db.upload(org, base().encode(), "text/plain")
    amended = db.upload(org, amendment().encode(), "text/plain")
    unsupported = db.upload(org, b"Reviewed unrelated attachment", "text/plain")
    options = {
        "resolutions": {digest([amended["document_id"], "2"]): "skip"},
        "exclusions": {unsupported["document_id"]: "Reviewed: unrelated attachment"},
    }
    requested = db.request_snapshot(org, **options)
    sid = Worker(db).drain(org, requested)["result"]["snapshot_id"]
    db.promote(org, sid, None)
    client = TestClient(create_app(db))
    restored = client.get("/v1/snapshots/current", headers={"X-API-Key": key}).json()
    assert restored["build_options"] == options
    assert restored["snapshot_id"] == sid
    assert (
        restored["content"]["families"][0]["versions"][-1]["facts"]["payment_days"]["value"] == 30
    )
    other, _ = db.create_tenant()
    with pytest.raises(ConflictError, match="snapshot_missing"):
        db.snapshot_build_options(other, sid)
    # A later build uses the restored decisions instead of resurrecting skipped sources.
    db.upload(org, base("Later agreement").encode(), "text/plain")
    rebuilt = db.request_snapshot(org, **restored["build_options"])
    next_sid = Worker(db).drain(org, rebuilt)["result"]["snapshot_id"]
    assert db.snapshot_build_options(org, next_sid) == options


def test_browser_session_persistence_expiry_csrf_and_logout(db, tenant):
    import hashlib

    import redis

    org, key = tenant
    _, sid = build(db, org)
    db.promote(org, sid, None)
    client = TestClient(create_app(db))
    origin = {"Origin": "http://testserver"}
    assert client.get("/v1/session").status_code == 401
    assert (
        client.post(
            "/v1/session", headers={"X-API-Key": key, "Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    signed = client.post("/v1/session", headers={"X-API-Key": key, **origin})
    assert signed.status_code == 200 and signed.json()["org_id"] == org
    cookie = signed.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=28800" in cookie
    token = client.cookies.get("cip_session")
    broker = redis.Redis.from_url(db.settings.redis_url)
    redis_key = "cip:session:" + hashlib.sha256(token.encode()).hexdigest()
    assert 0 < broker.ttl(redis_key) <= 28800
    assert client.get("/v1/snapshots/current").json()["snapshot_id"] == sid
    assert client.get("/v1/session", headers={"X-API-Key": "invalid"}).status_code == 401
    assert client.post("/v1/query", json={}).status_code == 403
    assert (
        client.post("/v1/query", json={}, headers={"Origin": "https://evil.test"}).status_code
        == 403
    )
    assert client.post("/v1/query", json={}, headers=origin).status_code == 200
    assert client.get("/v1/session").headers["cache-control"] == "no-store"
    assert client.delete("/v1/session", headers=origin).status_code == 204
    assert not broker.exists(redis_key)
    assert client.get("/v1/session", headers={"Cookie": f"cip_session={token}"}).status_code == 401
    # Expiry is enforced by the server, independently of the browser cookie lifetime.
    client.post("/v1/session", headers={"X-API-Key": key, **origin})
    token = client.cookies.get("cip_session")
    broker.expire("cip:session:" + hashlib.sha256(token.encode()).hexdigest(), -1)
    assert client.get("/v1/session").status_code == 401
    secure = TestClient(create_app(db), base_url="https://testserver")
    response = secure.post(
        "/v1/session", headers={"X-API-Key": key, "Origin": "https://testserver"}
    )
    assert "secure" in response.headers["set-cookie"].lower()
    assert secure.get("/v1/session").json()["org_id"] == org
    other, other_key = db.create_tenant()
    secure.post("/v1/session", headers={"X-API-Key": other_key, "Origin": "https://testserver"})
    assert secure.get("/v1/session").json()["org_id"] == other
    assert secure.get(f"/v1/snapshots/{sid}").status_code == 404
