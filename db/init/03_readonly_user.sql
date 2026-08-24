CREATE ROLE ai_dba_readonly
WITH LOGIN
PASSWORD 'readonly_password';

GRANT CONNECT ON DATABASE ai_dba TO ai_dba_readonly;

GRANT USAGE ON SCHEMA public
TO ai_dba_readonly;

GRANT SELECT ON ALL TABLES IN SCHEMA public
TO ai_dba_readonly;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT SELECT ON TABLES
TO ai_dba_readonly;