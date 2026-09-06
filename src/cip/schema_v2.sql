CREATE FUNCTION cip_immutable_artifact() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable_artifact'; END $$;
CREATE TRIGGER immutable_snapshot BEFORE UPDATE OR DELETE ON snapshots
  FOR EACH ROW EXECUTE FUNCTION cip_immutable_artifact();
CREATE TRIGGER immutable_document BEFORE UPDATE OR DELETE ON documents
  FOR EACH ROW EXECUTE FUNCTION cip_immutable_artifact();
CREATE FUNCTION cip_immutable_job_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.input IS DISTINCT FROM NEW.input OR OLD.input_hash <> NEW.input_hash
    OR OLD.org_id <> NEW.org_id OR OLD.kind <> NEW.kind OR OLD.subject <> NEW.subject
    OR (OLD.state='succeeded' AND (NEW.state <> 'succeeded' OR OLD.result IS DISTINCT FROM NEW.result))
  THEN RAISE EXCEPTION 'immutable_job_identity_or_result'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER immutable_job_identity BEFORE UPDATE ON jobs
  FOR EACH ROW EXECUTE FUNCTION cip_immutable_job_identity();
CREATE INDEX jobs_org_cursor ON jobs(org_id,created_at,id);
CREATE INDEX reservations_run ON llm_reservations(org_id,run_key);
CREATE INDEX audit_org ON audit_events(org_id,created_at,id);
INSERT INTO schema_migrations VALUES(2);

