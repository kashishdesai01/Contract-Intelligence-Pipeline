# Evaluation

Run the deterministic corpus evaluation with:

```sh
uv run cip eval
```

The evaluator generates seeded synthetic agreement families and derives expected field values from the source-generation slots. It checks original terms, amended terms, historical queries, family counts, and source provenance. A separately authored fixture exercises an additional agreement layout.

The same grading function evaluates deliberately corrupted outputs: skipped merges, removed provenance, and flipped renewal values. Each mutation must reduce the assertion score. Snapshot invariants are checked alongside exact expected values.

The report includes the dataset seed, manifest and source fingerprints, assertion totals, failures, corruption checks, retrieval comparisons, elapsed time, and model-call count. These values describe the generated corpus used for the run.

## HTTP workflow

Start the API and two workers, then run:

```sh
uv run python scripts/api_smoke.py
uv run python scripts/benchmark.py
```

The smoke script uploads a base agreement and amendment, builds and publishes a snapshot, checks historical/current payment terms, follows a query receipt, and checks source passages.

The benchmark uses concurrent HTTP upload clients and checks the resulting aggregate against independently generated expected values. Its output records workload size, concurrency, timings, and environment details for the specific run.
