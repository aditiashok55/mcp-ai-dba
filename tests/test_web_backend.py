"""FastAPI backend tests via TestClient — mocks the DB with fake_db.

The backend re-uses the same agent loop as the CLI, so these tests
double as integration tests for the wiring between web layer and
tool layer.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from web.backend import app


client = TestClient(app)


def test_list_tools_returns_allowlist():
    r = client.get("/api/tools")
    assert r.status_code == 200
    tools = r.json()
    names = {t["name"] for t in tools}
    assert "check_database_connection" in names
    assert "explain_slow_query" in names
    # every tool must have a description + input_schema
    for t in tools:
        assert t["description"]
        assert t["input_schema"]["type"] == "object"


def test_diagnose_rule_llm_end_to_end(fake_db):
    # Script covers the rule-LLM playbook's four tool calls.
    fake_db(
        [
            ("current_database()", [{"database": "ai_dba", "idle_in_transaction": 0}]),
            ("FROM pg_stat_activity", [{"pid": 1}]),
            ("FROM pg_stat_activity", []),
            ("FROM pg_extension", []),
            ("pg_blocking_pids", []),
        ]
    )
    r = client.post(
        "/api/diagnose",
        json={"question": "how healthy is the DB?", "llm": "rule"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["diagnosis"]["findings"]
    # correlation IDs surface on every finding
    assert all(f["evidence"] for f in body["diagnosis"]["findings"])
    assert len(body["tool_call_ids"]) >= 1


def test_diagnose_rejects_empty_question():
    r = client.post("/api/diagnose", json={"question": ""})
    assert r.status_code == 422


def test_diagnose_rejects_unknown_llm():
    r = client.post(
        "/api/diagnose", json={"question": "hi", "llm": "gpt-99"}
    )
    assert r.status_code == 422


def test_diagnose_anthropic_without_key_returns_400(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post(
        "/api/diagnose", json={"question": "hi", "llm": "anthropic"}
    )
    assert r.status_code == 400
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_diagnose_openai_without_key_returns_400(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = client.post("/api/diagnose", json={"question": "hi", "llm": "openai"})
    assert r.status_code == 400
    assert "OPENAI_API_KEY" in r.json()["detail"]


def test_providers_endpoint(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = client.get("/api/providers")
    assert r.status_code == 200
    body = r.json()
    assert body["rule"]["available"] is True
    assert body["anthropic"]["available"] is False
    assert body["openai"]["available"] is False
    assert "ollama" in body


def test_index_page_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "AI-DBA" in r.text
