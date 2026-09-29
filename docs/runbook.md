# Operations

## Services

```sh
docker compose -f deploy/compose.yaml up --build -d --scale worker=2
docker compose -f deploy/compose.yaml ps
docker compose -f deploy/compose.yaml logs -f api worker
```

Stop the services with `docker compose -f deploy/compose.yaml down`. Named volumes retain PostgreSQL and Redis data. Adding `-v` removes those volumes.

## Database migrations

The Compose migration service applies versioned SQL under an advisory transaction lock. The API and workers use the `cip_runtime` role; the migration service uses the database owner. When running processes directly, apply migrations with `uv run cip migrate` before starting the API or worker.

Configure `POSTGRES_PASSWORD` and `CIP_RUNTIME_PASSWORD` before initializing a new database. Configure locally run processes with `DATABASE_URL` and `REDIS_URL`.

## Health and logs

- `/healthz`: process liveness.
- `/readyz`: schema availability and Redis reachability.
- `/metrics`: authenticated HTTP counters and latency.

Workers emit structured events with job IDs, kinds, and outcomes. Inspect processing with `GET /v1/jobs` and `GET /v1/jobs/{id}`. Retry a dead job with `POST /v1/jobs/{id}/retry` after addressing its error.

## Recovery

Jobs and source bytes are stored in PostgreSQL. Workers recreate missing Redis consumer groups and reconcile due database jobs back into the stream. Expired leases can be claimed with new ownership tokens. Retry attempts use backoff and jitter; dependent jobs track prerequisite outcomes.

Preserve PostgreSQL volumes and configure database backups and restore procedures for the deployment environment. Redis also stores expiring browser sessions; clearing it signs browsers out while retaining PostgreSQL documents and snapshots.

## Publication and rollback

Build a snapshot, inspect its result and review queue, then promote it with `expected_active` set to the current snapshot ID. Use null for the first publication. A concurrent publication returns HTTP 409.

To roll back, promote a retained previous snapshot using the current ID as `expected_active`. Existing artifacts remain immutable.

## Sessions and access

Create a tenant key with `cip create-tenant`. Direct API clients send it as `X-API-Key`. The frontend exchanges it for an eight-hour HttpOnly session. Cookie-authenticated changes require a matching Origin; Disconnect revokes the session.

Serve external traffic through a TLS ingress. HTTPS responses set Secure cookies. Keep database and Redis access on a private network and supply credentials through deployment secrets. The Compose defaults bind to loopback for local development.
