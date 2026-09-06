DO $$ BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cip_runtime') THEN
    CREATE ROLE cip_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
  END IF;
END $$;
GRANT USAGE ON SCHEMA public TO cip_runtime;
GRANT SELECT ON schema_migrations TO cip_runtime;
GRANT SELECT, INSERT, UPDATE ON tenants,documents,jobs,job_dependencies,snapshots,
  audit_events,query_receipts,llm_reservations TO cip_runtime;
GRANT USAGE, SELECT ON SEQUENCE audit_events_id_seq TO cip_runtime;
INSERT INTO schema_migrations VALUES(3);

