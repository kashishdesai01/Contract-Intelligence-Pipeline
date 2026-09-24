"""Exercise the deployed containers over HTTP. No API key is written to output."""

import json
import time
from pathlib import Path

import httpx

from cip.corpus import amendment, base
from cip.store import Store


def run() -> dict:
    org, key = Store().create_tenant()
    sources = {}
    with httpx.Client(
        base_url="http://127.0.0.1:58080", headers={"X-API-Key": key}, timeout=10
    ) as c:
        assert c.get("/readyz").status_code == 200
        for name, text in [("base", base()), ("amendment", amendment())]:
            uploaded = c.post("/v1/documents", files={"file": (f"{name}.txt", text, "text/plain")})
            assert uploaded.status_code == 202, uploaded.text
            sources[uploaded.json()["document_id"]] = text
        built = c.post("/v1/snapshots", json={})
        assert built.status_code == 202, built.text
        job_id = built.json()["job_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = c.get(f"/v1/jobs/{job_id}").json()
            if job["state"] in {"succeeded", "dead"}:
                break
            time.sleep(0.1)
        assert job["state"] == "succeeded", job
        sid = job["result"]["snapshot_id"]
        assert (
            c.post(f"/v1/snapshots/{sid}/promote", json={"expected_active": None}).status_code
            == 200
        )
        family = c.get("/v1/families").json()["families"][0]["id"]
        before = c.post(
            "/v1/ask",
            json={
                "action": "lookup",
                "family_id": family,
                "field": "payment_days",
                "query": {"as_of": "2025-03-01"},
            },
        ).json()
        after = c.post(
            "/v1/ask", json={"action": "lookup", "family_id": family, "field": "payment_days"}
        ).json()
        assert before["value"] == 30 and after["value"] == 60
        diff = c.get(
            f"/v1/families/{family}/diff",
            params={"from_date": "2025-03-01", "to_date": "2025-07-01"},
        ).json()
        assert len(diff["changes"]) == 1
        query = c.post(
            "/v1/ask",
            json={"query": {"predicates": [{"field": "payment_days", "op": "gt", "value": 45}]}},
        ).json()
        assert query["count"] == 1
        receipt = query["citations"][0]["receipt_id"]
        assert c.get(f"/v1/receipts/{receipt}").status_code == 200
        assert c.get("/v1/verify").json()["errors"] == []
        retrieved = c.post("/v1/search", json={"query": "Invoices payable days"}).json()["results"][
            0
        ]
        assert retrieved["quote_origin"] == "composite"
        assert all(e["quote"] in sources[e["source_id"]] for e in retrieved["evidence"])
        return {
            "org_id": org,
            "snapshot_id": sid,
            "http_smoke_passed": True,
            "payment_before": 30,
            "payment_after": 60,
            "changed_clauses": 1,
            "aggregate_count": query["count"],
            "receipt_resolved": True,
            "retrieval_provenance_checked": True,
            "live_model_calls": 0,
        }


if __name__ == "__main__":
    report = run()
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/http-smoke.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
