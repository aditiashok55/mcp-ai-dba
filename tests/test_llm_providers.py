"""Tests for the multi-provider LLM adapters + factory.

Real network calls to Anthropic / OpenAI / Ollama are mocked. The
goal is to prove the adapters convert between our neutral
``ToolCall`` / ``ToolResult`` types and each provider's native
message shape correctly.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent import llm as llm_mod
from agent.llm import (
    OllamaLLM,
    OpenAILLM,
    PROVIDERS,
    ToolResult,
    build_llm,
)


# ---------------------------------------------------------------------------
# Factory dispatch
# ---------------------------------------------------------------------------


def test_build_llm_explicit_kind(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    llm = build_llm("anthropic")
    assert type(llm).__name__ == "AnthropicLLM"


def test_build_llm_reads_ai_dba_llm_env(monkeypatch):
    monkeypatch.setenv("AI_DBA_LLM", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    llm = build_llm()
    assert type(llm).__name__ == "OpenAILLM"


def test_build_llm_auto_picks_anthropic_first(monkeypatch):
    monkeypatch.delenv("AI_DBA_LLM", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-a")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-b")
    assert type(build_llm()).__name__ == "AnthropicLLM"


def test_build_llm_auto_falls_back_to_openai(monkeypatch):
    monkeypatch.delenv("AI_DBA_LLM", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-b")
    assert type(build_llm()).__name__ == "OpenAILLM"


def test_build_llm_auto_no_credentials_raises(monkeypatch):
    monkeypatch.delenv("AI_DBA_LLM", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    with patch.object(llm_mod, "_ollama_reachable", return_value=False):
        with pytest.raises(RuntimeError):
            build_llm()


def test_build_llm_unknown_kind():
    with pytest.raises(ValueError):
        build_llm("hal9000")


def test_providers_constant_matches_factory():
    assert set(PROVIDERS) == {"anthropic", "openai", "ollama"}


# ---------------------------------------------------------------------------
# OpenAI adapter — tool-use round trip
# ---------------------------------------------------------------------------


def test_openai_adapter_round_trip():
    llm = OpenAILLM()
    llm.start(
        system="sys",
        user="hi",
        tools=[
            {
                "name": "check_database_connection",
                "description": "probe",
                "input_schema": {"type": "object", "properties": {}, "required": []},
            }
        ],
    )

    # Fabricate an OpenAI-shaped response: assistant asks to call a tool.
    fake_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="check_database_connection", arguments="{}"),
    )
    fake_msg = SimpleNamespace(content=None, tool_calls=[fake_call])
    fake_choice = SimpleNamespace(message=fake_msg)
    fake_resp = SimpleNamespace(choices=[fake_choice])

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = fake_resp
    with patch.object(llm, "_client", return_value=mock_client):
        turn = llm.next_turn()

    assert turn.final is None
    assert len(turn.tool_calls) == 1
    tc = turn.tool_calls[0]
    assert tc.id == "call_1"
    assert tc.name == "check_database_connection"
    assert tc.arguments == {}

    # Submit a tool result — must land as a role="tool" message with the id.
    llm.submit_tool_results([ToolResult(tool_use_id="call_1", content='{"ok":1}')])
    tail = llm._messages[-1]  # noqa: SLF001 - test
    assert tail["role"] == "tool"
    assert tail["tool_call_id"] == "call_1"
    assert tail["content"] == '{"ok":1}'


def test_openai_adapter_final_answer():
    llm = OpenAILLM()
    llm.start(system="s", user="u", tools=[])
    fake_msg = SimpleNamespace(content='{"summary": "ok"}', tool_calls=[])
    fake_resp = SimpleNamespace(choices=[SimpleNamespace(message=fake_msg)])
    with patch.object(llm, "_client") as m:
        m.return_value.chat.completions.create.return_value = fake_resp
        turn = llm.next_turn()
    assert turn.tool_calls == []
    assert turn.final is not None
    assert turn.final.text == '{"summary": "ok"}'


# ---------------------------------------------------------------------------
# Ollama adapter — HTTP round trip mocked
# ---------------------------------------------------------------------------


class _FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_ollama_adapter_round_trip(monkeypatch):
    llm = OllamaLLM(model="llama3.1")
    llm.start(
        system="sys",
        user="hi",
        tools=[
            {
                "name": "check_database_connection",
                "description": "probe",
                "input_schema": {"type": "object", "properties": {}, "required": []},
            }
        ],
    )

    payload = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "check_database_connection",
                        "arguments": {},
                    }
                }
            ],
        }
    }

    def fake_urlopen(req, timeout=None):
        assert req.full_url.endswith("/api/chat")
        return _FakeHTTPResponse(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    turn = llm.next_turn()
    assert turn.final is None
    assert len(turn.tool_calls) == 1
    tc = turn.tool_calls[0]
    assert tc.name == "check_database_connection"
    # Ollama gave no id — adapter must synthesize one so tool_result can ref.
    assert tc.id and tc.id.startswith("ollama_")

    llm.submit_tool_results([ToolResult(tool_use_id=tc.id, content='{"ok":1}')])
    tail = llm._messages[-1]  # noqa: SLF001
    assert tail["role"] == "tool"
    assert tail["content"] == '{"ok":1}'


def test_ollama_adapter_stringified_arguments(monkeypatch):
    """Some Ollama builds emit arguments as a JSON string; adapter must parse."""
    llm = OllamaLLM()
    llm.start(system="", user="", tools=[])
    payload = {
        "message": {
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "get_slow_queries",
                        "arguments": '{"limit": 5}',
                    }
                }
            ],
        }
    }
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout=None: _FakeHTTPResponse(payload)
    )
    turn = llm.next_turn()
    assert turn.tool_calls[0].arguments == {"limit": 5}
