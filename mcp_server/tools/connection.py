"""Trivial reachability probe — proves the full pipeline works."""
from __future__ import annotations

from mcp_server.db import connect
from mcp_server.tools.base import dba_tool


@dba_tool(name="check_database_connection")
def check_database_connection() -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 AS ok;")
        row = cur.fetchone()
    return {"database_reachable": row is not None and row.get("ok") == 1}
