"""Composite vitals — version, uptime, connection counts."""
from __future__ import annotations

from mcp_server.db import connect
from mcp_server.tools.base import dba_tool


_HEALTH_SQL = """
SELECT
    current_database()                              AS database,
    version()                                       AS version,
    current_setting('max_connections')::int         AS max_connections,
    (SELECT count(*) FROM pg_stat_activity)         AS current_connections,
    (SELECT count(*) FROM pg_stat_activity
       WHERE state = 'active')                      AS active_connections,
    (SELECT count(*) FROM pg_stat_activity
       WHERE state = 'idle')                        AS idle_connections,
    (SELECT count(*) FROM pg_stat_activity
       WHERE state = 'idle in transaction')         AS idle_in_transaction,
    (now() - pg_postmaster_start_time())::text      AS uptime;
"""


@dba_tool(name="get_database_health")
def get_database_health() -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(_HEALTH_SQL)
        row = cur.fetchone() or {}
    return {"health": row}
