"""Restricted EXPLAIN — the LLM never supplies free-text SQL.

The tool accepts a ``queryid`` produced by ``pg_stat_statements``,
looks up the stored (already-normalized) text, and runs a plain
``EXPLAIN`` — never ``EXPLAIN ANALYZE`` — inside a transaction that
is always rolled back.

Design constraints (see architecture doc §5, §5.5):

* No free-text SQL crosses the trust boundary.
* No side-effects: ``EXPLAIN ANALYZE`` executes the plan; we do not.
* Read-only session already enforced by :func:`mcp_server.db.connect`.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from mcp_server.db import connect
from mcp_server.tools.base import dba_tool


class ExplainInput(BaseModel):
    model_config = {"extra": "forbid"}

    # queryid is a signed 64-bit int in pg_stat_statements.
    queryid: int = Field(..., description="pg_stat_statements.queryid")


_LOOKUP_SQL = """
SELECT query
FROM pg_stat_statements
WHERE queryid = %(queryid)s
LIMIT 1;
"""


def _extension_available(cur) -> bool:
    cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements';")
    return cur.fetchone() is not None


@dba_tool(name="explain_slow_query", input_model=ExplainInput)
def explain_slow_query(*, params: ExplainInput) -> dict:
    with connect() as conn, conn.cursor() as cur:
        if not _extension_available(cur):
            return {
                "explain": None,
                "error": "pg_stat_statements extension is not installed",
            }

        cur.execute(_LOOKUP_SQL, {"queryid": params.queryid})
        row = cur.fetchone()
        if not row:
            return {"explain": None, "error": "queryid not found"}

        query_text: str = row["query"]

        # Normalized statements from pg_stat_statements contain
        # placeholders like $1, $2 — EXPLAIN without ANALYZE handles
        # them via a prepared plan. We wrap and roll back defensively.
        try:
            cur.execute(f"EXPLAIN (FORMAT JSON) {query_text}")
            plan = cur.fetchone()
        finally:
            conn.rollback()

    return {"query": query_text, "explain": plan}
