-- Enable extensions used by the diagnostic tools.
-- Runs before schema/seed because init scripts execute in filename order.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
