"""List current connections with state — surfaces connection leaks."""
from __future__ import annotations

from pydantic import BaseModel, Field

from mcp_server.config import MAX_ROWS
from mcp_server.db import connect
from mcp_server.tools.base import dba_tool


class ActiveConnectionsInput(BaseModel):
    """Bounded, validated input for :func:`get_active_connections`."""

    model_config = {"extra": "forbid"}

    limit: int = Field(default=50, ge=1, le=MAX_ROWS)
    include_idle: bool = Field(default=True)


_SQL = """
SELECT
    pid,
    usename        AS username,
    application_name,
    client_addr::text,
    state,
    wait_event_type,
    wait_event,
    (now() - xact_start)::text  AS xact_age,
    (now() - query_start)::text AS query_age,
    LEFT(query, 500)            AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND (%(include_idle)s OR state <> 'idle')
ORDER BY query_start ASC NULLS LAST
LIMIT %(limit)s;
"""


@dba_tool(
    name="get_active_connections",
    input_model=ActiveConnectionsInput,
)
def get_active_connections(*, params: ActiveConnectionsInput) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            _SQL,
            {"include_idle": params.include_idle, "limit": params.limit},
        )
        rows = cur.fetchall()
    return {"rows": rows}
