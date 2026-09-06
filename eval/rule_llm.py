"""Deterministic rule-based "LLM" for offline evals.

Runs the same agent loop as a real LLM would — same tool-use turn
shape, same final answer format — but its "reasoning" is a hard-coded
tree: call a fixed sequence of diagnostic tools, then emit a
diagnosis whose findings are derived from what came back.

This exists so the eval harness can validate *itself* (harness plumbing,
scoring, chaos scripts, correlation-ID propagation) without an LLM
key. Real-LLM runs use the same harness with ``build_llm()`` instead.
"""
from __future__ import annotations

import json
from typing import Any

from agent.llm import FinalAnswer, ToolCall, ToolResult, Turn


class RuleBasedLLM:
    """Fixed diagnostic playbook, one tool per turn, then a diagnosis."""

    #: Order matters — mirrors what a real DBA would check first.
    PLAYBOOK: tuple[str, ...] = (
        "get_database_health",
        "get_active_connections",
        "get_slow_queries",
        "get_lock_information",
    )

    def __init__(self) -> None:
        self._observed: list[dict[str, Any]] = []
        self._step = 0

    # ------------------------------------------------------------------ #
    # LLM protocol
    # ------------------------------------------------------------------ #

    def start(self, **_: Any) -> None:
        return None

    def submit_tool_results(self, results: list[ToolResult]) -> None:
        for r in results:
            try:
                self._observed.append(json.loads(r.content))
            except json.JSONDecodeError:
                continue

    def next_turn(self) -> Turn:
        if self._step < len(self.PLAYBOOK):
            tool = self.PLAYBOOK[self._step]
            self._step += 1
            return Turn(
                tool_calls=[ToolCall(id=f"toolu_{self._step}", name=tool, arguments={})]
            )

        return Turn(final=FinalAnswer(text=self._build_diagnosis()))

    # ------------------------------------------------------------------ #

    def _build_diagnosis(self) -> str:
        # Collect every correlation ID we saw.
        ids: list[str] = []
        for r in self._observed:
            cid = r.get("correlation_id")
            if isinstance(cid, str):
                # sanitize wrapped it in <untrusted>…</untrusted>.
                ids.append(
                    cid.replace("<untrusted>", "").replace("</untrusted>", "")
                )

        findings: list[dict[str, Any]] = []

        # --- rule 1: long-running / slow queries --------------------------
        for r in self._observed:
            for key in ("active_slow_queries", "historic_slow_queries"):
                if r.get(key):
                    findings.append(
                        {
                            "finding": "Long-running slow queries detected",
                            "evidence": ids,
                            "confidence": "high",
                            "recommended_action": (
                                "Investigate long-running sleep / unindexed scans."
                            ),
                        }
                    )
                    break

        # --- rule 2: lock contention -------------------------------------
        for r in self._observed:
            rows = r.get("rows")
            # locks tool returns rows with blocked_pid/blocking_pid
            if isinstance(rows, list) and any(
                "blocked_pid" in row for row in rows if isinstance(row, dict)
            ):
                findings.append(
                    {
                        "finding": "Lock contention between sessions",
                        "evidence": ids,
                        "confidence": "high",
                        "recommended_action": (
                            "Kill the blocking session or investigate the "
                            "transaction holding the lock."
                        ),
                    }
                )
                break

        # --- rule 3: idle-in-transaction leak ----------------------------
        for r in self._observed:
            h = r.get("health")
            if isinstance(h, dict):
                idle_tx = h.get("idle_in_transaction")
                # sanitize wraps ints as ints (not strings), so no unwrap
                if isinstance(idle_tx, int) and idle_tx >= 3:
                    findings.append(
                        {
                            "finding": (
                                f"{idle_tx} connections are idle in transaction — "
                                "likely a connection leak."
                            ),
                            "evidence": ids,
                            "confidence": "high",
                            "recommended_action": (
                                "Track application code paths that BEGIN but "
                                "do not COMMIT."
                            ),
                        }
                    )
                    break

        if not findings:
            findings.append(
                {
                    "finding": "No abnormal signals across health, connections, slow queries, or locks.",
                    "evidence": ids or [],
                    "confidence": "medium",
                    "recommended_action": "No action required.",
                }
            )

        diagnosis = {
            "summary": "Rule-based playbook completed; findings derived from tool output.",
            "findings": findings,
            "all_tool_calls": ids,
        }
        return json.dumps(diagnosis)
