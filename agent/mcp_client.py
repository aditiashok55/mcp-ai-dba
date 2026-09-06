"""In-process MCP-style tool client.

The tools already enforce the trust boundary in
:mod:`mcp_server.tools.base` (validation, rate limit, audit, error
envelope). An out-of-process stdio client would add IPC overhead
without changing what the LLM can do.

For external MCP clients (Claude Desktop, etc.) the same tools are
exposed via ``python -m mcp_server.server``. This module is what our
own agent loop uses.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

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


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    invoke: Callable[..., dict]


def _spec(
    name: str,
    description: str,
    properties: dict[str, dict[str, Any]],
    required: list[str],
    invoke: Callable[..., dict],
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        invoke=invoke,
    )


# The allowlist. Adding a tool here is the *only* way it becomes
# visible to the agent — matches the "MCP client-side allowlist as a
# second check" defense-in-depth from the architecture doc §5.
_TOOLS: list[ToolSpec] = [
    _spec(
        "check_database_connection",
        "Verify the database is reachable. No parameters.",
        {},
        [],
        connection.check_database_connection,
    ),
    _spec(
        "get_database_health",
        "Return version, uptime, and connection-count vitals.",
        {},
        [],
        health.get_database_health,
    ),
    _spec(
        "get_active_connections",
        "List current sessions with state, wait event, and age.",
        {
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            "include_idle": {"type": "boolean", "default": True},
        },
        [],
        active_connections.get_active_connections,
    ),
    _spec(
        "get_slow_queries",
        "Return currently-running slow queries plus historic aggregates.",
        {
            "min_duration_ms": {"type": "integer", "minimum": 0, "default": 500},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
        },
        [],
        slow_queries.get_slow_queries,
    ),
    _spec(
        "get_lock_information",
        "Return blocking/blocked query pairs for lock-contention analysis.",
        {"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}},
        [],
        locks.get_lock_information,
    ),
    _spec(
        "explain_slow_query",
        "Run EXPLAIN on a query identified by its pg_stat_statements queryid. "
        "Never accepts free-text SQL.",
        {"queryid": {"type": "integer"}},
        ["queryid"],
        explain_query.explain_slow_query,
    ),
    _spec(
        "get_table_statistics",
        "Return table sizes, dead-tuple bloat, and vacuum recency.",
        {
            "schema_name": {"type": "string", "default": "public"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        },
        [],
        table_statistics.get_table_statistics,
    ),
    _spec(
        "get_index_usage",
        "Return index scan counts and sizes; surfaces missing/unused indexes.",
        {
            "schema_name": {"type": "string", "default": "public"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100},
        },
        [],
        index_usage.get_index_usage,
    ),
]

_TOOLS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in _TOOLS}


def list_tools() -> list[ToolSpec]:
    return list(_TOOLS)


def anthropic_tool_defs() -> list[dict[str, Any]]:
    """Deprecated alias for :func:`tool_defs`. Kept for BC."""
    return tool_defs()


def tool_defs() -> list[dict[str, Any]]:
    """Neutral tool definitions ``{name, description, input_schema}``.

    Each adapter in :mod:`agent.llm` converts these to its provider's
    native shape (Anthropic uses them as-is; OpenAI / Ollama wrap them
    in a ``function`` object).
    """
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in _TOOLS
    ]


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Invoke a tool by name. Unknown tools return an error envelope."""
    spec = _TOOLS_BY_NAME.get(name)
    if spec is None:
        return {
            "status": "unknown_tool",
            "tool": name,
            "error": f"tool {name!r} not in allowlist",
        }
    return spec.invoke(**arguments)
