"""End-to-end agent-loop tests using scripted stateful LLMs + mocked DB."""
from __future__ import annotations

import json

from agent.llm import FinalAnswer, ToolCall, ToolResult, Turn
from agent.loop import run


class ScriptedLLM:
    """Stateful LLM stub matching the new :class:`agent.llm.LLM` protocol."""

    def __init__(self) -> None:
        self.step = 0
        self.last_results: list[ToolResult] = []

    # -- interface ---------------------------------------------------------

    def start(self, **_):
        return None

    def submit_tool_results(self, results):
        self.last_results = results

    # -- subclasses override -----------------------------------------------

    def next_turn(self):  # pragma: no cover - overridden
        raise NotImplementedError


def _unwrap(s: str) -> str:
    return s.replace("<untrusted>", "").replace("</untrusted>", "")


def test_agent_gathers_evidence_and_returns_diagnosis(fake_db):
    fake_db([("SELECT 1", [{"ok": 1}])])

    captured: dict[str, str] = {}

    class LLM(ScriptedLLM):
        def next_turn(self):
            self.step += 1
            if self.step == 1:
                return Turn(
                    tool_calls=[
                        ToolCall(id="toolu_1", name="check_database_connection", arguments={})
                    ]
                )
            # Turn 2: the loop just called submit_tool_results with the
            # real correlation IDs — extract one and cite it.
            data = json.loads(self.last_results[0].content)
            cid = _unwrap(data["correlation_id"])
            captured["cid"] = cid
            return Turn(
                final=FinalAnswer(
                    text=json.dumps(
                        {
                            "summary": "DB is reachable.",
                            "findings": [
                                {
                                    "finding": "connection ok",
                                    "evidence": [cid],
                                    "confidence": "high",
                                    "recommended_action": "no action",
                                }
                            ],
                            "all_tool_calls": [cid],
                        }
                    )
                )
            )

    result = run("is the DB up?", llm=LLM(), max_steps=5)

    assert result.error is None, result.error
    assert result.diagnosis is not None
    assert result.diagnosis.findings[0].evidence == [captured["cid"]]
    assert captured["cid"] in result.tool_call_ids


def test_agent_surfaces_real_ids_when_llm_fabricates(fake_db):
    fake_db([("SELECT 1", [{"ok": 1}])])

    class LLM(ScriptedLLM):
        def next_turn(self):
            self.step += 1
            if self.step == 1:
                return Turn(
                    tool_calls=[
                        ToolCall(id="toolu_1", name="check_database_connection", arguments={})
                    ]
                )
            # Everything the LLM cites is invented.
            return Turn(
                final=FinalAnswer(
                    text=json.dumps(
                        {
                            "summary": "made up",
                            "findings": [
                                {
                                    "finding": "fabricated",
                                    "evidence": ["not-a-real-id"],
                                    "confidence": "high",
                                    "recommended_action": "n/a",
                                }
                            ],
                            "all_tool_calls": ["not-a-real-id"],
                        }
                    )
                )
            )

    result = run("q", llm=LLM(), max_steps=5)
    # Schema alone can't detect the lie — but the loop returns the
    # ground-truth ids so downstream callers can cross-check.
    assert result.diagnosis is not None
    llm_claimed = set(result.diagnosis.all_tool_calls)
    real = set(result.tool_call_ids)
    assert not (llm_claimed <= real), (
        "loop must expose real IDs so caller can detect fabrication"
    )
