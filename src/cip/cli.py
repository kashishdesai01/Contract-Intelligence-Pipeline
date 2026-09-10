"""A small, explicit operational surface. Live calls require separate opt-in."""

import json
from pathlib import Path
from typing import Any

import typer

from cip.corpus import amendment, base
from cip.domain import verify_snapshot
from cip.models import FactQuery, Predicate
from cip.observability import configure_logging
from cip.query import lookup, query_facts
from cip.store import Store
from cip.worker import Worker

app = typer.Typer(no_args_is_help=True)


def emit(value: Any) -> None:
    typer.echo(json.dumps(value, default=str, indent=2))


@app.command()
def migrate() -> None:
    Store().migrate()
    emit({"schema": 3})


@app.command("create-tenant")
def create_tenant() -> None:
    org, key = Store().create_tenant()
    emit({"org_id": org, "api_key": key, "note": "Store this key; only its hash is retained."})


@app.command()
def worker() -> None:
    configure_logging()
    Worker(Store()).run()


@app.command()
def demo(output: Path = Path("artifacts/demo.json")) -> None:
    """Run an isolated tenant through real DB/Redis with no model calls."""
    db = Store()
    with db.connection() as conn:
        if not conn.execute("SELECT 1 FROM schema_migrations WHERE version=3").fetchone():
            raise RuntimeError("run_cip_migrate_first")
    org, _ = db.create_tenant()
    first = db.upload(org, base().encode(), "text/plain")
    second = db.upload(org, amendment().encode(), "text/plain")
    job = db.request_snapshot(org)
    outcome = Worker(db).drain(org, job)
    if outcome["state"] != "succeeded":
        raise RuntimeError("demo_pipeline_failed")
    sid = outcome["result"]["snapshot_id"]
    db.promote(org, sid, None)
    snapshot = db.snapshot(org)
    assert snapshot is not None
    family = snapshot[1].families[0]
    from datetime import date

    before = lookup(snapshot[1], family.id, "payment_days", date(2025, 3, 1))
    after = lookup(snapshot[1], family.id, "payment_days", None)
    counts = query_facts(
        snapshot[1], FactQuery(predicates=[Predicate(field="payment_days", op="gt", value=45)])
    )
    receipt = db.save_query(org, sid, counts)
    repeat = db.request_snapshot(org)
    assert repeat == job
    conflict_doc = amendment(
        number=2, operation=('Replace Section 2 with:\n"Invoices are payable within 90 days."')
    )
    db.upload(org, conflict_doc.encode(), "text/plain")
    conflicted_job = db.request_snapshot(org)
    conflicted = Worker(db).drain(org, conflicted_job)
    conflicted_sid = conflicted["result"]["snapshot_id"]
    conflict_snapshot = db.snapshot(org, conflicted_sid)
    assert conflict_snapshot is not None
    conflicts = conflict_snapshot[1].families[0].versions[-1].conflicts
    active_snapshot = db.snapshot(org)
    assert active_snapshot is not None
    report = {
        "mode": "offline_supported_grammar",
        "infrastructure": "real_postgres_and_redis",
        "documents": [first["document_id"], second["document_id"]],
        "snapshot_id": sid,
        "payment_before": before,
        "payment_after": after,
        "aggregate": counts,
        "receipt_id": receipt,
        "same_input_same_job": repeat == job,
        "conflicts": [c.model_dump() for c in conflicts],
        "conflict_abstention": lookup(conflict_snapshot[1], family.id, "payment_days", None),
        "unpublished_generation_keeps_active": active_snapshot[0] == sid,
        "verify_errors": verify_snapshot(snapshot[1]),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    emit(report)


@app.command("eval")
def evaluate(output: Path = Path("artifacts/evaluation.json")) -> None:
    from cip.evals import run

    report = run()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    emit(report)
    if not report["passed"]:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
