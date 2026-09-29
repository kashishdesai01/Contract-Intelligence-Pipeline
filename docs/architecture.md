# Contracts and design decisions

## Durable admission and processing

An upload transaction stores bytes and inserts an analyze job. The database unique key `(org, kind, subject, input_hash)` retains the identity fence. Jobs are the outbox; Redis contains job identifiers, never the authoritative job state. Reconciliation republishes due queued jobs and expired running jobs, regardless of prior dispatch history, at a bounded interval. Redis trimming can discard hints because database obligations remain.

Transitions:

| From | Trigger | To |
|---|---|---|
| queued | Due, attempts remain, all dependencies succeeded | running with a new token |
| running | Valid owner completes atomically | succeeded with immutable result |
| running | Valid owner has a retryable failure | queued with exponential backoff and jitter |
| running | Permanent failure or attempts exhausted | dead |
| running | Lease expires and attempts remain | running with a new owner's token |
| running | Lease expires and attempts exhausted | dead through reconciliation |
| queued | A prerequisite is dead | dead with dependency reason |
| dead | Authenticated explicit retry | queued, reset attempts, audit entry |

Heartbeat, retry/failure, and completion all require the current token and an unexpired lease. Completion locks the job row before writing outputs. This prevents a takeover between ownership validation and commit. A completed job cannot be claimed again. A repeated identical request uses the retained result. Audit records may grow; canonical business artifacts remain unchanged.

A worker holds no database transaction during parsing or model calls. It renews its lease in a separate thread and discards output if ownership is lost. Worker death leaves a reclaimable obligation. Redis pending-entry ownership is independent of the Postgres fence; a busy or redundant hint may be acknowledged because reconciliation still tracks the job.

## Snapshot publication

A snapshot request captures document content identities and prerequisite job IDs in a transaction. The builder consumes only those immutable results. Its artifact contains family assignments, historical composites, facts, deviations, source texts/hashes and review outcomes. Invariant validation happens before the artifact and the successful job state commit together.

Build completion does not publish. Promotion changes one tenant pointer with compare-and-swap against the caller's expected active snapshot. Tenant-consistent foreign keys prevent pointing at another tenant's artifact. Rollback is the same operation. A query resolves this pointer once and uses that artifact throughout. Query receipts persist the snapshot ID, query, population/coverage and result.

## Supported interpretation and uncertainty

Malformed sources suspend trusted answers conservatively. A reviewer can explicitly exclude a source with a reason in a new snapshot request; the source is retained, its hash and the reason are audited, and coverage remains partial. Ambiguous amendment references suspend candidate families from the amendment's effective date. Neither case is silently treated as an unrelated document.

The first release uses an intentionally closed sentence grammar. A recognizable number or exact quotation is insufficient to accept a fact. Unrecognized substantive sentences make semantic fields unknown. More than one evidence candidate remains unknown. No global clause cache is used: meanings are recomputed from each composite, so changes elsewhere do not leave cached facts stale.

Amendment payloads are taken verbatim from supported operation syntax. Replacements use the current clause state; `replace_text` requires one occurrence. Same-date overlaps are detected before authoritative results. Any unresolved composite conflict makes all its facts disputed: this conservative closure accounts for definitions and cross-references through a conservative dependency policy. Conflict history persists in descendants. Review may skip a specified operation; it creates a new build input and an auditable snapshot rather than editing a published artifact.

The `quote` on an amended fact is a derived composite quote. Its `evidence` list carries exact source passages. Full replacement resets current evidence to the replacing instrument; lineage retains preceding source IDs. Partial replacement retains original and amendment evidence. Before-base queries return no composite.

## Search and assistant choices

BM25 plus character-trigram cosine provides a CPU-only lexical baseline with RRF. It is bounded, deterministic, and needs no model download. Search excludes conflicted composites and returns passages, not inferred legal conclusions.

The assistant exposes count, lookup and search. Structured mode is deterministic; optional live mode asks a provider for a schema-constrained plan, validates it, executes one allowlisted tool against one snapshot, and renders the answer in code. Contract text never enters the planner prompt. Counting is a set operation on agreement families. Unknown required facts and disputed composites are excluded with explicit coverage. Query receipts support computed quantities that never occur verbatim in source text.

Token reservations are serialized on the tenant row and survive Redis loss. Unknown provider outcomes retain their reservation. This limits accounted token allocation, not an invoice or exactly-once provider execution. The optional adapter has no automatic retry loop or infinite repair behavior.

## Storage and evolution

Input identities include a source fingerprint of the parser/merger and artifact schemas. A changed fingerprint creates new analysis identities instead of reusing prior output. Snapshot labels are separate from the implementation fingerprint. Workers reject queued inputs from an incompatible version. API/worker containers use the `cip_runtime` database role; DDL runs through the separate migration service. Immutable snapshots, source bytes and completed job results are enforced by database triggers.

The schema migration is versioned and serialized by an advisory transaction lock. Runtime schema updates are explicit CLI operations, never implicit on HTTP startup. The small schema is shipped as package data and exercised from the built image. New migrations must be additive and reversible at the application compatibility boundary; do not rewrite applied SQL.

Input versioning must change with parser, validator, schema or operation semantics. Old results and snapshots are retained. There is no mutable model default. Duplicate document bytes admitted with conflicting media metadata retain the original interpretation. Durable identity fences, source bytes and snapshots have no automatic deletion in this release; storage retention and deletion are operational decisions.

