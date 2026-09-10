"""Slot gold plus separately authored examples. Scores describe the supported grammar only."""

import hashlib
import time
from datetime import date
from importlib.resources import files
from typing import Any

from cip.corpus import generate
from cip.domain import analyze, build_snapshot, verify_snapshot
from cip.models import FactQuery, Family, Predicate, digest
from cip.query import query_facts, search


def family_checks(family: Family, expected: dict[str, Any]) -> dict[str, bool]:
    before, after = family.versions[0], family.versions[-1]
    return {
        "payment_before": before.facts["payment_days"].status == "ok"
        and before.facts["payment_days"].value == expected["before_payment"],
        "payment_after": after.facts["payment_days"].status == "ok"
        and after.facts["payment_days"].value == expected["after_payment"],
        "notice": after.facts["notice_days"].status == "ok"
        and after.facts["notice_days"].value == expected["notice_days"],
        "renewal": after.facts["auto_renewal"].status == "ok"
        and after.facts["auto_renewal"].value == expected["auto_renewal"],
        "provenance": len(after.facts["payment_days"].evidence) == 2,
        "deviation": any(d["rule"] == "payment_days" for d in after.deviations),
    }


def run(seed: int = 193, families: int = 40) -> dict[str, Any]:
    texts, gold = generate(seed, families)
    started = time.perf_counter()
    instruments = [analyze(text, digest(text)) for text in texts.values()]
    snapshot = build_snapshot(instruments, {i.id: digest(i.text) for i in instruments})
    assertions: list[bool] = []
    failures: list[dict[str, str]] = []
    timings = []
    retrieval: dict[str, list[bool]] = {method: [] for method in ("bm25", "trigram", "hybrid")}
    for family in snapshot.families:
        checks = family_checks(family, gold[family.agreement])
        assertions.extend(checks.values())
        failures.extend(
            {"family": family.agreement, "check": k} for k, ok in checks.items() if not ok
        )
    for method in retrieval:
        hits = search(snapshot, "Invoices payable days", limit=10, method=method)
        # There are equally relevant payment clauses in different families.
        retrieval[method].append(bool(hits) and hits[0]["clause_key"] == "2")
    for _ in range(20):
        query_started = time.perf_counter()
        receipt = query_facts(
            snapshot, FactQuery(predicates=[Predicate(field="payment_days", op="gt", value=45)])
        )
        timings.append((time.perf_counter() - query_started) * 1000)
    assertions.append(receipt["count"] == families)
    historical = query_facts(
        snapshot,
        FactQuery(
            as_of=date(2025, 3, 1), predicates=[Predicate(field="payment_days", op="gt", value=45)]
        ),
    )
    assertions.append(historical["count"] == 0)
    invariant_errors = verify_snapshot(snapshot)
    # Apply corruption to artifacts and rerun the same grading function; never change gold.
    baseline = sum(sum(family_checks(f, gold[f.agreement]).values()) for f in snapshot.families)
    drops = {}
    for mutation in ("skip_merge", "drop_provenance", "flip_renewal"):
        mutated = snapshot.model_copy(deep=True)
        for family in mutated.families:
            facts = family.versions[-1].facts
            if mutation == "skip_merge":
                facts["payment_days"].value = family.versions[0].facts["payment_days"].value
            elif mutation == "drop_provenance":
                facts["payment_days"].evidence = []
            else:
                facts["auto_renewal"].value = not facts["auto_renewal"].value
        mutated_score = sum(
            sum(family_checks(f, gold[f.agreement]).values()) for f in mutated.families
        )
        drops[mutation] = baseline - mutated_score
    canaries = {key: drop > 0 for key, drop in drops.items()}
    # Independently authored input is packaged, and labels are separate from the generator.
    independent = files("cip").joinpath("fixtures/independent.txt").read_text()
    sample = analyze(independent, digest(independent))
    challenge = build_snapshot([sample], {sample.id: digest(independent)})
    labels = {
        "payment_days": 75,
        "notice_days": 15,
        "auto_renewal": False,
        "renewal_months": 6,
        "governing_law": "Oregon",
        "liability_cap": "unlimited",
        "assignment_consent": False,
        "effective_date": "2025-01-01",
    }
    challenge_pass = all(
        challenge.families[0].versions[0].facts[k].value == value for k, value in labels.items()
    )
    return {
        "mode": "offline_rules",
        "dataset": "synthetic-slot-holdout-v1",
        "seed": seed,
        "families": families,
        "documents": len(texts),
        "manifest_hash": digest(texts),
        "implementation_sha256": hashlib.sha256(
            files("cip").joinpath("domain.py").read_bytes()
        ).hexdigest(),
        "assertions": len(assertions),
        "correct": sum(assertions),
        "failures": failures,
        "independent_example_passed": challenge_pass,
        "canaries_detected": canaries,
        "canary_assertion_drops": drops,
        "invariant_errors": invariant_errors,
        "retrieval_top1_supported_clause": {k: sum(v) / len(v) for k, v in retrieval.items()},
        "query_latency_ms": {
            "p50": round(sorted(timings)[10], 3),
            "p95": round(sorted(timings)[18], 3),
        },
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "model_calls": 0,
        "model_quality_measured": False,
        "limits": [
            "Template-generated synthetic inputs; not real-contract accuracy",
            "Independent example was authored separately, not lawyer reviewed",
            "Retrieval is lexical; no neural embedding quality claim",
        ],
        "passed": all(assertions)
        and challenge_pass
        and all(canaries.values())
        and not invariant_errors,
    }
