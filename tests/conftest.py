"""Pytest fixtures shared across the suite.

The unit tests never hit a real database — they patch
:func:`mcp_server.db.connect` with a fake connection/cursor pair.
Integration tests (in ``tests/integration``) that require Docker are
skipped when ``AI_DBA_INTEGRATION`` is not set.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest


class FakeCursor:
    """Minimal ``psycopg`` cursor stand-in.

    Tests register ``(sql_substring, rows)`` pairs. Entries are
    consumed in order — an execute matches the first *unconsumed*
    entry whose substring is present. This lets multi-query tools
    be scripted without collisions on common substrings like
    ``current_database()``.
    """

    def __init__(self, script: list[tuple[str, list[dict[str, Any]]]]) -> None:
        self._script = list(script)
        self._pos = 0
        self._pending: list[dict[str, Any]] = []
        self.executed: list[tuple[str, Any]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> None:  # noqa: D401
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))
        for i in range(self._pos, len(self._script)):
            needle, rows = self._script[i]
            if needle in sql:
                self._pending = list(rows)
                self._pos = i + 1
                return
        self._pending = []

    def fetchone(self) -> dict[str, Any] | None:
        return self._pending[0] if self._pending else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._pending)


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.rolled_back = False
        self.committed = False

    def cursor(self) -> FakeCursor:
        return self._cursor

    def rollback(self) -> None:
        self.rolled_back = True

    def commit(self) -> None:
        self.committed = True


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch):
    """Return a factory that patches ``connect`` with a scripted cursor."""

    def _factory(script: list[tuple[str, list[dict[str, Any]]]]) -> FakeCursor:
        cursor = FakeCursor(script)
        conn = FakeConnection(cursor)

        @contextmanager
        def _connect() -> Iterator[FakeConnection]:
            yield conn

        # Patch every import site.
        import mcp_server.tools.active_connections as m1
        import mcp_server.tools.connection as m2
        import mcp_server.tools.explain_query as m3
        import mcp_server.tools.health as m4
        import mcp_server.tools.index_usage as m5
        import mcp_server.tools.locks as m6
        import mcp_server.tools.slow_queries as m7
        import mcp_server.tools.table_statistics as m8

        for mod in (m1, m2, m3, m4, m5, m6, m7, m8):
            monkeypatch.setattr(mod, "connect", _connect)

        return cursor

    return _factory


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.getenv("AI_DBA_INTEGRATION"):
        return
    skip = pytest.mark.skip(reason="set AI_DBA_INTEGRATION=1 to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


# Silence audit log noise during tests.
@pytest.fixture(autouse=True)
def _quiet_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    import logging
    logging.getLogger("ai_dba.audit").setLevel(logging.CRITICAL)


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Give every test a fresh, empty rate limiter."""
    from mcp_server import rate_limit
    rate_limit.limiter._buckets.clear()  # noqa: SLF001 - test fixture


_ = MagicMock  # re-exported for tests that want ad-hoc mocks
