"""Lock contention — blocking / blocked query pairs."""
from __future__ import annotations

from pydantic import BaseModel, Field

from mcp_server.config import MAX_ROWS
from mcp_server.db import connect
from mcp_server.tools.base import dba_tool


class LockInfoInput(BaseModel):
    model_config = {"extra": "forbid"}

    limit: int = Field(default=20, ge=1, le=MAX_ROWS)


# Uses pg_blocking_pids (PG 9.6+) to correlate blocked <-> blockers
# without a self-join on pg_locks.
_SQL = """
SELECT
    blocked.pid                                        AS blocked_pid,
    blocked.usename                                    AS blocked_user,
    LEFT(blocked.query, 300)                           AS blocked_query,
    blocked.wait_event_type,
    blocked.wait_event,
    EXTRACT(MILLISECOND FROM (now() - blocked.query_start))::int
                                                       AS blocked_duration_ms,
    blocking.pid                                       AS blocking_pid,
    blocking.usename                                   AS blocking_user,
    blocking.state                                     AS blocking_state,
    LEFT(blocking.query, 300)                          AS blocking_query
FROM pg_stat_activity AS blocked
JOIN LATERAL unnest(pg_blocking_pids(blocked.pid)) AS blocker_pid ON TRUE
JOIN pg_stat_activity AS blocking ON blocking.pid = blocker_pid
WHERE blocked.datname = current_database()
ORDER BY blocked.query_start ASC
LIMIT %(limit)s;
"""


@dba_tool(name="get_lock_information", input_model=LockInfoInput)
def get_lock_information(*, params: LockInfoInput) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(_SQL, {"limit": params.limit})
        rows = cur.fetchall()
    return {"rows": rows}
