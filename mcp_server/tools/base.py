"""Reusable plumbing for every diagnostic tool.

The :func:`dba_tool` decorator centralizes:

* pydantic input validation (rejects unknown / malformed params early),
* structured audit logging with correlation IDs,
* uniform error envelopes (tools never raise across the MCP boundary),
* a per-call timer.

Individual tool modules only have to implement the SQL — not the
cross-cutting concerns.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from mcp_server.audit import audit, get_correlation_id, new_correlation_id, timer
from mcp_server.metrics import record as record_metric, tool_calls_in_flight
from mcp_server.rate_limit import limiter

TInput = TypeVar("TInput", bound=BaseModel)


def dba_tool(
    name: str, input_model: type[BaseModel] | None = None
) -> Callable[[Callable[..., dict]], Callable[..., dict]]:
    """Decorate a tool function with validation, audit, and error handling.

    The wrapped function is called with a validated pydantic model
    instance (if ``input_model`` is provided) and must return a
    JSON-serializable ``dict``. If it raises, the decorator converts
    the exception into a structured error envelope.
    """

    def decorator(fn: Callable[..., dict]) -> Callable[..., dict]:
        @wraps(fn)
        def wrapper(**kwargs: Any) -> dict:
            new_correlation_id()
            t = timer()

            # 0. Rate limit — cheap first check, before validation.
            if not limiter.try_acquire(name):
                duration = t.elapsed_ms()
                audit(
                    tool=name,
                    params=kwargs,
                    status="rate_limited",
                    duration_ms=duration,
                    error="per-tool rate limit exceeded",
                )
                record_metric(name, "rate_limited", duration)
                return {
                    "status": "rate_limited",
                    "tool": name,
                    "correlation_id": get_correlation_id(),
                    "error": "per-tool rate limit exceeded",
                }

            # 1. Validate inputs.
            if input_model is not None:
                try:
                    validated = input_model(**kwargs)
                except ValidationError as ve:
                    duration = t.elapsed_ms()
                    audit(
                        tool=name,
                        params=kwargs,
                        status="invalid_input",
                        duration_ms=duration,
                        error=str(ve),
                    )
                    record_metric(name, "invalid_input", duration)
                    return {
                        "status": "invalid_input",
                        "tool": name,
                        "correlation_id": get_correlation_id(),
                        "error": ve.errors(),
                    }
                call_kwargs: dict[str, Any] = {"params": validated}
                logged_params = validated.model_dump()
            else:
                call_kwargs = {}
                logged_params = kwargs

            # 2. Execute (gauge tracks concurrent in-flight calls).
            tool_calls_in_flight.labels(tool=name).inc()
            try:
                try:
                    result = fn(**call_kwargs)
                except Exception as exc:  # noqa: BLE001 - boundary layer
                    duration = t.elapsed_ms()
                    audit(
                        tool=name,
                        params=logged_params,
                        status="error",
                        duration_ms=duration,
                        error=repr(exc),
                    )
                    record_metric(name, "error", duration)
                    return {
                        "status": "error",
                        "tool": name,
                        "correlation_id": get_correlation_id(),
                        "error": str(exc),
                    }

                # 3. Audit success.
                duration = t.elapsed_ms()
                rows = result.get("rows") if isinstance(result, dict) else None
                row_count = len(rows) if isinstance(rows, list) else None
                audit(
                    tool=name,
                    params=logged_params,
                    status="ok",
                    duration_ms=duration,
                    row_count=row_count,
                )
                record_metric(name, "ok", duration)
                return {
                    "status": "ok",
                    "tool": name,
                    "correlation_id": get_correlation_id(),
                    **result,
                }
            finally:
                tool_calls_in_flight.labels(tool=name).dec()

        return wrapper

    return decorator
