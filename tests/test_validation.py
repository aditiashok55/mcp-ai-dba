"""Adversarial input tests — the validation layer must reject junk."""
from __future__ import annotations

from mcp_server.tools.active_connections import get_active_connections
from mcp_server.tools.slow_queries import get_slow_queries
from mcp_server.tools.table_statistics import get_table_statistics


def test_rejects_negative_limit(fake_db):
    fake_db([])
    out = get_active_connections(limit=-1)
    assert out["status"] == "invalid_input"


def test_rejects_limit_over_cap(fake_db):
    fake_db([])
    out = get_active_connections(limit=10_000)
    assert out["status"] == "invalid_input"


def test_rejects_unknown_kwarg(fake_db):
    fake_db([])
    out = get_slow_queries(min_duration_ms=100, limit=5, drop_table=True)
    assert out["status"] == "invalid_input"


def test_rejects_empty_schema(fake_db):
    fake_db([])
    out = get_table_statistics(schema_name="", limit=10)
    assert out["status"] == "invalid_input"


def test_error_returns_envelope_not_exception(fake_db, monkeypatch):
    # Force the tool body to raise, ensure decorator swallows into envelope.
    from mcp_server.tools import connection as mod

    def boom():  # signature matches undecorated func
        raise RuntimeError("db exploded")

    # Replace the decorated function's inner call by patching connect
    # to raise instead.
    from contextlib import contextmanager

    @contextmanager
    def bad_connect():
        raise RuntimeError("db exploded")
        yield  # pragma: no cover

    monkeypatch.setattr(mod, "connect", bad_connect)
    out = mod.check_database_connection()
    assert out["status"] == "error"
    assert "db exploded" in out["error"]
