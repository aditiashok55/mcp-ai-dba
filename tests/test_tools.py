"""Tool-level tests with a mocked DB."""
from __future__ import annotations

from mcp_server.tools.active_connections import get_active_connections
from mcp_server.tools.connection import check_database_connection
from mcp_server.tools.explain_query import explain_slow_query
from mcp_server.tools.health import get_database_health
from mcp_server.tools.index_usage import get_index_usage
from mcp_server.tools.locks import get_lock_information
from mcp_server.tools.slow_queries import get_slow_queries
from mcp_server.tools.table_statistics import get_table_statistics


def test_check_connection_ok(fake_db):
    fake_db([("SELECT 1", [{"ok": 1}])])
    out = check_database_connection()
    assert out["status"] == "ok"
    assert out["database_reachable"] is True


def test_health_returns_row(fake_db):
    fake_db([("current_database()", [{"database": "ai_dba", "version": "PG"}])])
    out = get_database_health()
    assert out["status"] == "ok"
    assert out["health"]["database"] == "ai_dba"


def test_active_connections_respects_limit(fake_db):
    cursor = fake_db([("pg_stat_activity", [{"pid": 1}, {"pid": 2}])])
    out = get_active_connections(limit=5, include_idle=False)
    assert out["status"] == "ok"
    assert out["rows"] == [{"pid": 1}, {"pid": 2}]
    # confirm binds went through as named params
    _, params = cursor.executed[-1]
    assert params["limit"] == 5
    assert params["include_idle"] is False


def test_slow_queries_handles_missing_extension(fake_db):
    fake_db([
        ("FROM pg_stat_activity", [{"pid": 1, "duration_ms": 900}]),
        ("FROM pg_extension", []),  # extension not installed
    ])
    out = get_slow_queries(min_duration_ms=100, limit=10)
    assert out["status"] == "ok"
    assert out["pg_stat_statements_available"] is False
    assert out["historic_slow_queries"] == []


def test_locks_returns_pairs(fake_db):
    fake_db([("pg_blocking_pids", [{"blocked_pid": 5, "blocking_pid": 9}])])
    out = get_lock_information(limit=10)
    assert out["rows"][0]["blocked_pid"] == 5


def test_explain_reports_missing_extension(fake_db):
    fake_db([("FROM pg_extension", [])])
    out = explain_slow_query(queryid=1234)
    assert out["status"] == "ok"
    assert out["explain"] is None
    assert "pg_stat_statements" in out["error"]


def test_explain_reports_missing_queryid(fake_db):
    fake_db([
        ("FROM pg_extension", [{"1": 1}]),
        ("FROM pg_stat_statements", []),
    ])
    out = explain_slow_query(queryid=9999)
    assert out["error"] == "queryid not found"


def test_table_statistics(fake_db):
    fake_db([("pg_stat_user_tables", [{"table": "orders", "dead_pct": 12.5}])])
    out = get_table_statistics(schema_name="public", limit=10)
    assert out["rows"][0]["table"] == "orders"


def test_index_usage(fake_db):
    fake_db([("pg_stat_user_indexes", [{"index": "orders_pkey", "scans": 0}])])
    out = get_index_usage()
    assert out["rows"][0]["scans"] == 0
