"""RuleBasedLLM + agent loop, against a mocked DB.

Proves the harness plumbing: chaos → tool output → rule reasoning →
Diagnosis → scorer. No real Postgres or LLM required.
"""
from __future__ import annotations

from agent.loop import run
from eval.rule_llm import RuleBasedLLM
from eval.scorer import score_diagnosis


def test_rule_llm_detects_slow_queries(fake_db):
    # Playbook order: health, active_conns, slow, locks
    fake_db(
        [
            # get_database_health
            ("current_database()", [{"database": "ai_dba", "idle_in_transaction": 0}]),
            # get_active_connections
            ("FROM pg_stat_activity", [{"pid": 42, "state": "active"}]),
            # get_slow_queries — active
            ("FROM pg_stat_activity", [{"pid": 42, "duration_ms": 9000, "query": "SELECT pg_sleep(30)"}]),
            # get_slow_queries — extension check
            ("FROM pg_extension", []),
            # get_lock_information
            ("pg_blocking_pids", []),
        ]
    )

    result = run("investigate", llm=RuleBasedLLM(), max_steps=8)
    assert result.error is None, result.error
    assert result.diagnosis is not None

    s = score_diagnosis("slow_query", ("slow", "long-running"), result.diagnosis)
    assert s.passed, f"expected match, got {s}"


def test_rule_llm_detects_lock_contention(fake_db):
    fake_db(
        [
            ("current_database()", [{"database": "ai_dba", "idle_in_transaction": 0}]),
            ("FROM pg_stat_activity", [{"pid": 1}]),
            ("FROM pg_stat_activity", []),  # no active slow queries
            ("FROM pg_extension", []),
            (
                "pg_blocking_pids",
                [{"blocked_pid": 7, "blocking_pid": 9, "blocked_query": "UPDATE …"}],
            ),
        ]
    )

    result = run("investigate", llm=RuleBasedLLM(), max_steps=8)
    assert result.diagnosis is not None

    s = score_diagnosis(
        "lock_contention", ("lock", "block", "contention"), result.diagnosis
    )
    assert s.passed


def test_rule_llm_healthy_baseline(fake_db):
    fake_db(
        [
            ("current_database()", [{"database": "ai_dba", "idle_in_transaction": 0}]),
            ("FROM pg_stat_activity", []),
            ("FROM pg_stat_activity", []),
            ("FROM pg_extension", []),
            ("pg_blocking_pids", []),
        ]
    )
    result = run("investigate", llm=RuleBasedLLM(), max_steps=8)
    assert result.diagnosis is not None
    assert "no abnormal" in result.diagnosis.findings[0].finding.lower()
