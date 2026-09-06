CREATE TABLE IF NOT EXISTS schema_migrations(version integer PRIMARY KEY);
CREATE TABLE tenants(
  id text PRIMARY KEY, key_hash text NOT NULL UNIQUE,
  active_snapshot uuid, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE documents(
  id text PRIMARY KEY, org_id text NOT NULL REFERENCES tenants(id), sha256 text NOT NULL,
  media text NOT NULL, body bytea NOT NULL CHECK(octet_length(body) <= 512000),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(org_id, sha256), UNIQUE(org_id,id)
);
CREATE TABLE jobs(
  id uuid PRIMARY KEY, org_id text NOT NULL REFERENCES tenants(id), kind text NOT NULL,
  subject text NOT NULL, input_hash text NOT NULL, input jsonb NOT NULL,
  state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','succeeded','dead')),
  attempts integer NOT NULL DEFAULT 0, max_attempts integer NOT NULL DEFAULT 5,
  run_after timestamptz NOT NULL DEFAULT clock_timestamp(),
  locked_until timestamptz, claim_token uuid, dispatched_at timestamptz,
  result jsonb, error text, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK(attempts >= 0 AND max_attempts > 0),
  CHECK((state = 'running') = (claim_token IS NOT NULL AND locked_until IS NOT NULL)),
  CHECK(state <> 'succeeded' OR result IS NOT NULL),
  UNIQUE(org_id,kind,subject,input_hash), UNIQUE(org_id,id)
);
CREATE INDEX jobs_due ON jobs(run_after,dispatched_at) WHERE state IN ('queued','running');
CREATE TABLE job_dependencies(
  org_id text NOT NULL, job_id uuid NOT NULL, dependency_id uuid NOT NULL,
  PRIMARY KEY(job_id, dependency_id), CHECK(job_id <> dependency_id),
  FOREIGN KEY(org_id,job_id) REFERENCES jobs(org_id,id),
  FOREIGN KEY(org_id,dependency_id) REFERENCES jobs(org_id,id)
);
CREATE TABLE snapshots(
  id uuid PRIMARY KEY, org_id text NOT NULL REFERENCES tenants(id),
  job_id uuid NOT NULL UNIQUE, payload jsonb NOT NULL, content_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(org_id,id),
  FOREIGN KEY(org_id,job_id) REFERENCES jobs(org_id,id)
);
ALTER TABLE tenants ADD CONSTRAINT active_snapshot_tenant
  FOREIGN KEY(id,active_snapshot) REFERENCES snapshots(org_id,id);
CREATE TABLE audit_events(
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id text NOT NULL REFERENCES tenants(id), actor text NOT NULL,
  action text NOT NULL, detail jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE query_receipts(
  id uuid PRIMARY KEY, org_id text NOT NULL, snapshot_id uuid NOT NULL,
  payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  FOREIGN KEY(org_id,snapshot_id) REFERENCES snapshots(org_id,id)
);
CREATE TABLE llm_reservations(
  id uuid PRIMARY KEY, org_id text NOT NULL REFERENCES tenants(id),
  run_key text NOT NULL, reserved_tokens integer NOT NULL CHECK(reserved_tokens > 0),
  used_tokens integer CHECK(used_tokens >= 0), state text NOT NULL DEFAULT 'reserved'
    CHECK(state IN ('reserved','completed','unknown')),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO schema_migrations VALUES(1);

