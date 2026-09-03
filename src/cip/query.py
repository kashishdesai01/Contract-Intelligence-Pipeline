"""Snapshot-bound structured queries, coverage, retrieval and deterministic answers."""

import math
import re
from collections import Counter, defaultdict
from datetime import date
from typing import Any

from cip.domain import as_of
from cip.models import FactQuery, Snapshot, digest


def query_facts(snapshot: Snapshot, query: FactQuery) -> dict[str, Any]:
    matches = []
    excluded: dict[str, int] = Counter()
    evaluated = 0
    for family in snapshot.families:
        composite = as_of(family, query.as_of)
        if composite is None:
            excluded["not_effective"] += 1
            continue
        if composite.conflicts:
            excluded["conflicted"] += 1
            continue
        if any(composite.facts[p.field].status != "ok" for p in query.predicates):
            excluded["unknown_required_fact"] += 1
            continue
        evaluated += 1
        hit = True
        for p in query.predicates:
            value = composite.facts[p.field].value
            # bool is an int subclass; reject type confusion rather than coercing it.
            if type(value) is not type(p.value):
                hit = False
                continue
            if p.op == "eq":
                valid = value == p.value
            elif p.op == "ne":
                valid = value != p.value
            elif (
                p.op in {"gt", "gte", "lt", "lte"}
                and isinstance(value, int)
                and isinstance(p.value, int)
            ):
                valid = {
                    "gt": value > p.value,
                    "gte": value >= p.value,
                    "lt": value < p.value,
                    "lte": value <= p.value,
                }[p.op]
            else:
                valid = False
            hit = hit and valid
        if hit:
            matches.append(
                {
                    "family_id": family.id,
                    "agreement": family.agreement,
                    "as_of": composite.as_of.isoformat(),
                    "facts": {
                        p.field: composite.facts[p.field].model_dump() for p in query.predicates
                    },
                }
            )
    return {
        "query": query.model_dump(mode="json"),
        "unit": "agreement_family",
        "count": len(matches),
        "rows": matches[: query.limit],
        "truncated": len(matches) > query.limit,
        "coverage": {
            "families": len(snapshot.families),
            "evaluated": evaluated,
            "excluded": dict(excluded),
            "unlinked_documents": len(snapshot.review),
            "reviewer_excluded_documents": len(snapshot.exclusions),
            "complete": not snapshot.review
            and not snapshot.exclusions
            and not any(key != "not_effective" for key in excluded),
        },
    }


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def search(
    snapshot: Snapshot,
    query: str,
    when: date | None = None,
    limit: int = 10,
    method: str = "hybrid",
) -> list[dict[str, Any]]:
    """BM25 and character-trigram cosine, fused by RRF. This is not neural retrieval."""
    candidates: list[dict[str, Any]] = []
    for family in snapshot.families:
        composite = as_of(family, when)
        if composite is None or composite.conflicts:
            continue
        for clause in composite.clauses:
            candidates.append(
                {
                    "family_id": family.id,
                    "agreement": family.agreement,
                    "clause_key": clause.key,
                    "source_id": clause.source_id,
                    "text": clause.text,
                    "quote_origin": "composite",
                    "evidence": [e.model_dump() for e in clause.evidence],
                    "provenance": clause.history,
                    "as_of": composite.as_of.isoformat(),
                }
            )
    if not candidates:
        return []
    tokenized = [_tokens(c["text"]) for c in candidates]
    lengths = [len(t) for t in tokenized]
    average = max(1, sum(lengths) / len(lengths))
    lexical = []
    fuzzy = []
    qtokens = set(_tokens(query))
    qgrams = Counter(query.lower()[i : i + 3] for i in range(max(0, len(query) - 2)))
    for i, tokens in enumerate(tokenized):
        counts = Counter(tokens)
        score = 0.0
        for token in qtokens:
            df = sum(token in t for t in tokenized)
            idf = math.log(1 + (len(candidates) - df + 0.5) / (df + 0.5))
            tf = counts[token]
            score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * lengths[i] / average))
        lexical.append((score, i))
        text = candidates[i]["text"].lower()
        grams = Counter(text[j : j + 3] for j in range(max(0, len(text) - 2)))
        dot = sum(v * grams[k] for k, v in qgrams.items())
        norm = math.sqrt(sum(v * v for v in qgrams.values()) * sum(v * v for v in grams.values()))
        fuzzy.append((dot / norm if norm else 0.0, i))
    fused: dict[int, float] = defaultdict(float)
    lists = [lexical] if method == "bm25" else [fuzzy] if method == "trigram" else [lexical, fuzzy]
    for ranking in lists:
        for rank, (score, i) in enumerate(sorted(ranking, key=lambda x: (-x[0], x[1])), 1):
            if score > 0:
                fused[i] += 1 / (60 + rank)
    return [
        {**candidates[i], "score": score, "retrieval": method}
        for i, score in sorted(fused.items(), key=lambda x: (-x[1], x[0]))[:limit]
    ]


def lookup(snapshot: Snapshot, family_id: str, field: str, when: date | None) -> dict[str, Any]:
    family = next((f for f in snapshot.families if f.id == family_id), None)
    composite = as_of(family, when) if family else None
    fact = composite.facts.get(field) if composite else None
    if not fact or fact.status != "ok" or composite is None:
        return {
            "answer": "Required evidence is unavailable or disputed.",
            "abstained": True,
            "citations": [],
            "value": None,
        }
    return {
        "answer": f"{field}: {fact.value}.",
        "scope_exclusions": snapshot.exclusions,
        "abstained": False,
        "value": fact.value,
        "citations": [fact.model_dump()],
        "as_of": composite.as_of.isoformat(),
    }


def query_identity(snapshot_id: str, query: FactQuery) -> str:
    return digest([snapshot_id, query])
