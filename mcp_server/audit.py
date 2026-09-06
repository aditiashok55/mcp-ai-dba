"""Structured audit logging with correlation IDs.

Every tool invocation emits exactly one JSON audit record — regardless
of success or failure — via :func:`audit`. Correlation IDs are stored
in a :class:`contextvars.ContextVar` so they thread through nested
calls without needing to be passed explicitly.

The output is deliberately line-delimited JSON so it can be shipped
straight into Loki / CloudWatch / Datadog without a parser.
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

_logger = logging.getLogger("ai_dba.audit")
if not _logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


def new_correlation_id() -> str:
    """Generate and set a fresh correlation ID for this context."""
    cid = uuid.uuid4().hex[:12]
    _correlation_id.set(cid)
    return cid


def get_correlation_id() -> str:
    return _correlation_id.get()


class _Timer:
    def __init__(self) -> None:
        self._start = time.perf_counter()

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self._start) * 1000, 2)


def timer() -> _Timer:
    return _Timer()


def audit(
    *,
    tool: str,
    params: dict[str, Any],
    status: str,
    duration_ms: float,
    row_count: int | None = None,
    error: str | None = None,
) -> None:
    """Emit a single structured audit record.

    Parameters are keyword-only to make call sites self-documenting.
    """
    record = {
        "event": "tool_call",
        "correlation_id": get_correlation_id(),
        "tool": tool,
        "params": params,
        "status": status,
        "duration_ms": duration_ms,
        "row_count": row_count,
        "error": error,
    }
    _logger.info(json.dumps(record, default=str))
