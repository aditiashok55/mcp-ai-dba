from prometheus_client import REGISTRY

from mcp_server.metrics import (
    record,
    tool_calls_in_flight,
    tool_calls_total,
)


def _sample(name: str, **labels: str) -> float:
    for metric in REGISTRY.collect():
        for s in metric.samples:
            if s.name == name and all(s.labels.get(k) == v for k, v in labels.items()):
                return s.value
    return 0.0


def test_record_increments_counter_and_histogram():
    before = _sample("aidba_tool_calls_total", tool="unit_test", status="ok")
    record("unit_test", "ok", 12.5)
    after = _sample("aidba_tool_calls_total", tool="unit_test", status="ok")
    assert after == before + 1

    hist_count = _sample(
        "aidba_tool_call_duration_seconds_count", tool="unit_test", status="ok"
    )
    assert hist_count >= 1


def test_in_flight_gauge_is_wired_by_decorator(fake_db):
    """@dba_tool should inc/dec the gauge around the call."""
    fake_db([("SELECT 1", [{"ok": 1}])])

    # before
    before = _sample("aidba_tool_calls_in_flight", tool="check_database_connection")

    from mcp_server.tools.connection import check_database_connection
    out = check_database_connection()
    assert out["status"] == "ok"

    after = _sample("aidba_tool_calls_in_flight", tool="check_database_connection")
    # Should return to the pre-call value.
    assert after == before


def test_decorator_records_success_metric(fake_db):
    fake_db([("SELECT 1", [{"ok": 1}])])
    before = _sample(
        "aidba_tool_calls_total", tool="check_database_connection", status="ok"
    )

    from mcp_server.tools.connection import check_database_connection
    check_database_connection()

    after = _sample(
        "aidba_tool_calls_total", tool="check_database_connection", status="ok"
    )
    assert after == before + 1
