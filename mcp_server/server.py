"""FastMCP server entrypoint.

Every tool is a thin wrapper that delegates to the implementation in
``mcp_server.tools`` — the wrappers exist so FastMCP can introspect
type hints for tool discovery. All cross-cutting concerns
(validation, audit, error handling) live in :mod:`mcp_server.tools.base`.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from mcp_server.metrics import ensure_started as _start_metrics
from mcp_server.tools import (
    active_connections,
    connection,
    explain_query,
    health,
    index_usage,
    locks,
    slow_queries,
    table_statistics,
)

mcp = FastMCP("AI-DBA")


@mcp.tool()
def check_database_connection() -> dict:
    """Verify that the PostgreSQL database is reachable."""
    return connection.check_database_connection()


@mcp.tool()
def get_database_health() -> dict:
    """Return version, uptime, and connection-count vitals."""
    return health.get_database_health()


@mcp.tool()
def get_active_connections(limit: int = 50, include_idle: bool = True) -> dict:
    """List current DB connections with state, wait event, and age."""
    return active_connections.get_active_connections(
        limit=limit, include_idle=include_idle
    )


@mcp.tool()
def get_slow_queries(min_duration_ms: int = 500, limit: int = 20) -> dict:
    """Return currently-running slow queries plus historic aggregates."""
    return slow_queries.get_slow_queries(
        min_duration_ms=min_duration_ms, limit=limit
    )


@mcp.tool()
def get_lock_information(limit: int = 20) -> dict:
    """Return blocking / blocked query pairs for lock contention analysis."""
    return locks.get_lock_information(limit=limit)


@mcp.tool()
def explain_slow_query(queryid: int) -> dict:
    """Run EXPLAIN on a query looked up by pg_stat_statements queryid."""
    return explain_query.explain_slow_query(queryid=queryid)


@mcp.tool()
def get_table_statistics(schema_name: str = "public", limit: int = 50) -> dict:
    """Return table sizes, dead-tuple bloat, and vacuum recency."""
    return table_statistics.get_table_statistics(
        schema_name=schema_name, limit=limit
    )


@mcp.tool()
def get_index_usage(schema_name: str = "public", limit: int = 100) -> dict:
    """Return index scan counts and sizes — surfaces missing/unused indexes."""
    return index_usage.get_index_usage(schema_name=schema_name, limit=limit)


if __name__ == "__main__":
    _start_metrics()
    mcp.run()
