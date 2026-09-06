"""Prometheus metrics for the MCP tool layer.

Wired from :mod:`mcp_server.tools.base` — every tool call produces
one increment of ``tool_calls_total`` and one observation of
``tool_call_duration_seconds``. ``tool_calls_in_flight`` is a gauge
around the call so overlapping requests are visible on the dashboard.

A metrics HTTP endpoint is started lazily by :func:`ensure_started`
(called from :mod:`mcp_server.server` at boot). Disabled by default in
tests via ``METRICS_ENABLED=0``.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from mcp_server.config import METRICS_ENABLED, METRICS_PORT

if TYPE_CHECKING:  # pragma: no cover
    pass


tool_calls_total = Counter(
    "aidba_tool_calls_total",
    "Total tool calls, labelled by tool and terminal status.",
    labelnames=("tool", "status"),
)

tool_call_duration_seconds = Histogram(
    "aidba_tool_call_duration_seconds",
    "Wall-clock duration of a tool call.",
    labelnames=("tool", "status"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

tool_calls_in_flight = Gauge(
    "aidba_tool_calls_in_flight",
    "Number of tool calls currently executing.",
    labelnames=("tool",),
)


_started = False
_lock = threading.Lock()


def ensure_started() -> None:
    """Start the Prometheus HTTP endpoint exactly once, if enabled."""
    global _started
    if not METRICS_ENABLED:
        return
    with _lock:
        if _started:
            return
        start_http_server(METRICS_PORT)
        _started = True


def record(tool: str, status: str, duration_ms: float) -> None:
    """Emit the three metrics for a single completed tool call."""
    tool_calls_total.labels(tool=tool, status=status).inc()
    tool_call_duration_seconds.labels(tool=tool, status=status).observe(
        duration_ms / 1000.0
    )
