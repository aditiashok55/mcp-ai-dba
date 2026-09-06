from mcp_server.audit import audit, get_correlation_id, new_correlation_id


def test_new_correlation_id_changes_and_is_readable():
    cid1 = new_correlation_id()
    assert cid1 == get_correlation_id()
    cid2 = new_correlation_id()
    assert cid1 != cid2
    assert get_correlation_id() == cid2


def test_audit_emits_json(capsys, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="ai_dba.audit")

    new_correlation_id()
    audit(
        tool="check_database_connection",
        params={"x": 1},
        status="ok",
        duration_ms=1.23,
        row_count=0,
    )
    # Record should have been emitted; content is JSON-serialised.
    assert any("tool_call" in rec.message for rec in caplog.records)
