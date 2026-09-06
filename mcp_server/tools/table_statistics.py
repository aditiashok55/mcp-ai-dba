"""Table sizes, dead-tuple bloat, vacuum/analyze recency."""
from __future__ import annotations

from pydantic import BaseModel, Field

from mcp_server.config import MAX_ROWS
from mcp_server.db import connect
from mcp_server.tools.base import dba_tool


class TableStatsInput(BaseModel):
    model_config = {"extra": "forbid"}

    schema_name: str = Field(default="public", min_length=1, max_length=63)
    limit: int = Field(default=50, ge=1, le=MAX_ROWS)


_SQL = """
SELECT
    schemaname                                       AS schema,
    relname                                          AS table,
    n_live_tup                                       AS live_rows,
    n_dead_tup                                       AS dead_rows,
    CASE WHEN n_live_tup > 0
         THEN ROUND(100.0 * n_dead_tup / n_live_tup, 2)
         ELSE 0 END                                  AS dead_pct,
    pg_size_pretty(pg_total_relation_size(relid))    AS total_size,
    pg_total_relation_size(relid)                    AS total_bytes,
    last_vacuum,
    last_autovacuum,
    last_analyze,
    last_autoanalyze
FROM pg_stat_user_tables
WHERE schemaname = %(schema)s
ORDER BY pg_total_relation_size(relid) DESC
LIMIT %(limit)s;
"""


@dba_tool(name="get_table_statistics", input_model=TableStatsInput)
def get_table_statistics(*, params: TableStatsInput) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(_SQL, {"schema": params.schema_name, "limit": params.limit})
        rows = cur.fetchall()
    return {"rows": rows}
