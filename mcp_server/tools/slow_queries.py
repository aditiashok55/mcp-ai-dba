"""Currently-running long queries — the centerpiece diagnostic tool.

Reads from ``pg_stat_activity`` (always available). If the
``pg_stat_statements`` extension is installed, also returns aggregate
statistics for historically slow statements.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from mcp_server.config import MAX_ROWS
from mcp_server.db import connect
from mcp_server.tools.base import dba_tool


class SlowQueriesInput(BaseModel):
    model_config = {"extra": "forbid"}

    min_duration_ms: int = Field(default=500, ge=0, le=3_600_000)
    limit: int = Field(default=20, ge=1, le=MAX_ROWS)


_ACTIVE_SQL = """
SELECT
    pid,
    usename                                              AS username,
    state,
    wait_event_type,
    wait_event,
    EXTRACT(MILLISECOND FROM (now() - query_start))::int AS duration_ms,
    LEFT(query, 500)                                     AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND state <> 'idle'
  AND pid <> pg_backend_pid()
  AND query_start IS NOT NULL
  AND (now() - query_start) >= (%(min_duration_ms)s || ' milliseconds')::interval
ORDER BY query_start ASC
LIMIT %(limit)s;
"""

_HISTORIC_SQL = """
SELECT
    queryid::text,
    calls,
    ROUND(mean_exec_time::numeric, 2) AS mean_ms,
    ROUND(total_exec_time::numeric, 2) AS total_ms,
    rows,
    LEFT(query, 500) AS query
FROM pg_stat_statements
WHERE mean_exec_time >= %(min_duration_ms)s
ORDER BY mean_exec_time DESC
LIMIT %(limit)s;
"""


def _has_extension(cur, name: str) -> bool:
    cur.execute("SELECT 1 FROM pg_extension WHERE extname = %s;", (name,))
    return cur.fetchone() is not None


@dba_tool(name="get_slow_queries", input_model=SlowQueriesInput)
def get_slow_queries(*, params: SlowQueriesInput) -> dict:
    bind = {"min_duration_ms": params.min_duration_ms, "limit": params.limit}
    with connect() as conn, conn.cursor() as cur:
        cur.execute(_ACTIVE_SQL, bind)
        active = cur.fetchall()

        historic: list[dict] = []
        pg_stat_statements_available = _has_extension(cur, "pg_stat_statements")
        if pg_stat_statements_available:
            cur.execute(_HISTORIC_SQL, bind)
            historic = cur.fetchall()

    return {
        "active_slow_queries": active,
        "historic_slow_queries": historic,
        "pg_stat_statements_available": pg_stat_statements_available,
        # keep a `rows` key so audit row_count still means something
        "rows": active + historic,
    }
