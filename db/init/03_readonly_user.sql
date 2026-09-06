CREATE ROLE ai_dba_readonly
WITH LOGIN
PASSWORD 'readonly_password';

GRANT CONNECT ON DATABASE ai_dba TO ai_dba_readonly;

GRANT USAGE ON SCHEMA public TO ai_dba_readonly;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO ai_dba_readonly;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT SELECT ON TABLES TO ai_dba_readonly;

-- pg_stat_statements is created in the "public" schema by default on
-- PG 16, but the view itself is owned by superuser. Grant SELECT so
-- the read-only role can inspect historic slow queries.
GRANT SELECT ON pg_stat_statements TO ai_dba_readonly;

-- Membership in pg_read_all_stats lets the role see other users'
-- queries in pg_stat_activity (otherwise the ``query`` column is
-- masked). Without this, get_active_connections / get_slow_queries
-- would be effectively blind.
GRANT pg_read_all_stats TO ai_dba_readonly;
