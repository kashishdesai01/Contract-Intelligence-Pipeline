"""Bounded local HTTP measurement, not a production capacity claim."""

import concurrent.futures
import json
import platform
import statistics
import time
from pathlib import Path

import httpx

from cip.corpus import generate
from cip.store import Store


def run() -> dict:
    texts, gold = generate(seed=314159, families=40)
    org, key = Store().create_tenant()
    headers = {"X-API-Key": key}
    latencies = []
    started = time.monotonic()

    def upload(item: tuple[str, str]) -> float:
        name, text = item
        with httpx.Client(base_url="http://127.0.0.1:58080", headers=headers, timeout=20) as client:
            before = time.monotonic()
            response = client.post(
                "/v1/documents", files={"file": (name + ".txt", text, "text/plain")}
            )
            response.raise_for_status()
            return (time.monotonic() - before) * 1000

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        latencies = list(pool.map(upload, texts.items()))
    admission_seconds = time.monotonic() - started
    with httpx.Client(base_url="http://127.0.0.1:58080", headers=headers, timeout=20) as client:
        requested = client.post("/v1/snapshots", json={})
        requested.raise_for_status()
        job_id = requested.json()["job_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            response = client.get(f"/v1/jobs/{job_id}")
            response.raise_for_status()
            job = response.json()
            if job["state"] in {"succeeded", "dead"}:
                break
            time.sleep(1)
        assert job["state"] == "succeeded", job
        sid = job["result"]["snapshot_id"]
        promoted = client.post(f"/v1/snapshots/{sid}/promote", json={"expected_active": None})
        promoted.raise_for_status()
        queried = client.post(
            "/v1/query", json={"predicates": [{"field": "payment_days", "op": "gt", "value": 45}]}
        )
        queried.raise_for_status()
        answer = queried.json()
        expected = sum(values["after_payment"] > 45 for values in gold.values())
        assert answer["count"] == expected and answer["coverage"]["complete"]
    return {
        "mode": "local_http_offline_rules",
        "documents": len(texts),
        "families": len(gold),
        "concurrency": 8,
        "workers": 2,
        "machine": platform.platform(),
        "python": platform.python_version(),
        "admission_seconds": round(admission_seconds, 3),
        "admission_ms": {
            "p50": round(statistics.median(latencies), 3),
            "p95": round(sorted(latencies)[75], 3),
        },
        "end_to_end_seconds": round(time.monotonic() - started, 3),
        "aggregate_expected": expected,
        "aggregate_actual": answer["count"],
        "snapshot_id": sid,
        "model_calls": 0,
        "limit": "Single local bounded run; no real-contract or production capacity claim",
    }


if __name__ == "__main__":
    report = run()
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/benchmark.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
