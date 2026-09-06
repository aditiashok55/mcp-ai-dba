"""Index usage — pairs with table stats for the missing-index scenario."""
from __future__ import annotations

from pydantic import BaseModel, Field

from mcp_server.config import MAX_ROWS
from mcp_server.db import connect
from mcp_server.tools.base import dba_tool


class IndexUsageInput(BaseModel):
    model_config = {"extra": "forbid"}

    schema_name: str = Field(default="public", min_length=1, max_length=63)
    limit: int = Field(default=100, ge=1, le=MAX_ROWS)


_SQL = """
SELECT
    s.schemaname                             AS schema,
    s.relname                                AS table,
    s.indexrelname                           AS index,
    s.idx_scan                               AS scans,
    s.idx_tup_read                           AS tuples_read,
    s.idx_tup_fetch                          AS tuples_fetched,
    pg_size_pretty(pg_relation_size(s.indexrelid)) AS index_size,
    pg_relation_size(s.indexrelid)           AS index_bytes,
    t.seq_scan                               AS table_seq_scans,
    t.idx_scan                               AS table_idx_scans
FROM pg_stat_user_indexes AS s
JOIN pg_stat_user_tables  AS t
  ON t.relid = s.relid
WHERE s.schemaname = %(schema)s
ORDER BY s.idx_scan ASC, pg_relation_size(s.indexrelid) DESC
LIMIT %(limit)s;
"""


@dba_tool(name="get_index_usage", input_model=IndexUsageInput)
def get_index_usage(*, params: IndexUsageInput) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(_SQL, {"schema": params.schema_name, "limit": params.limit})
        rows = cur.fetchall()
    return {"rows": rows}
