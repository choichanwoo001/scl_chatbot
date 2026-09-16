-- Run after Alembic as the migration owner, in the default app schema.
-- Set passwords separately in Supabase/psql; never commit them in this file.
-- If DATABASE_SCHEMA differs, review and replace every app schema reference.
BEGIN;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scl_app') THEN
        CREATE ROLE scl_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scl_ingest') THEN
        CREATE ROLE scl_ingest LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
    END IF;
END $$;

REVOKE ALL ON SCHEMA app FROM PUBLIC;
GRANT USAGE ON SCHEMA app TO scl_app, scl_ingest;
GRANT SELECT ON ALL TABLES IN SCHEMA app TO scl_app;
GRANT INSERT, UPDATE, DELETE ON app.handoff_requests, app.chat_feedback, app.faq_candidates, app.chat_sessions TO scl_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO scl_ingest;
REVOKE INSERT, UPDATE, DELETE ON app.alembic_version FROM scl_ingest;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO scl_app, scl_ingest;

-- Applies to future objects created by this migration owner only.
ALTER DEFAULT PRIVILEGES IN SCHEMA app REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA app REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA app GRANT SELECT ON TABLES TO scl_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA app GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO scl_ingest;
ALTER DEFAULT PRIVILEGES IN SCHEMA app GRANT USAGE, SELECT ON SEQUENCES TO scl_app, scl_ingest;
COMMIT;
