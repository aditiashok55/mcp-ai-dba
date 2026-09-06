"""LLM adapter — provider-agnostic tool-calling interface.

The agent loop treats every LLM through the same small stateful
protocol so we don't hard-code any one vendor's message shape:

    llm.start(system, user, tools)
    while not done:
        turn = llm.next_turn()
        if turn.final: return turn.final
        results = execute(turn.tool_calls)
        llm.submit_tool_results(results)

Each adapter owns its own on-the-wire message accumulator and
converts our neutral :class:`ToolCall` / :class:`ToolResult` into
its provider's native format.

Adapters shipped:

* :class:`AnthropicLLM` — Claude (``ANTHROPIC_API_KEY``).
* :class:`OpenAILLM`    — GPT-4o / gpt-4o-mini / any OpenAI-compatible
  endpoint (``OPENAI_API_KEY``, optional ``OPENAI_BASE_URL``).
* :class:`OllamaLLM`    — local models via ``http://localhost:11434``
  (llama3.1, qwen2.5, etc.). No key needed.
* :class:`FakeLLM`      — scripted turns for tests.

The rule-based offline "LLM" is separate at
:class:`eval.rule_llm.RuleBasedLLM`.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# Neutral message primitives — every adapter converts these to/from native.
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class FinalAnswer:
    text: str  # JSON matching :class:`agent.schemas.Diagnosis`


@dataclass
class ToolResult:
    tool_use_id: str
    content: str  # JSON-encoded, already sanitized


@dataclass
class Turn:
    tool_calls: list[ToolCall] = field(default_factory=list)
    final: FinalAnswer | None = None


class LLM(Protocol):
    def start(
        self, *, system: str, user: str, tools: list[dict[str, Any]]
    ) -> None: ...

    def next_turn(self) -> Turn: ...

    def submit_tool_results(self, results: list[ToolResult]) -> None: ...


# ---------------------------------------------------------------------------
# FakeLLM — scripted turns for tests.
# ---------------------------------------------------------------------------


class FakeLLM:
    """Replays a scripted list of turns regardless of inputs.

    Kept simple: it does not track a real transcript; tests either
    ignore ``submit_tool_results`` or spy on it manually.
    """

    def __init__(self, script: list[Turn]) -> None:
        self._script = list(script)
        self.results_log: list[list[ToolResult]] = []

    def start(self, **_: Any) -> None:  # noqa: D401
        return None

    def next_turn(self) -> Turn:
        if not self._script:
            raise RuntimeError("FakeLLM script exhausted")
        return self._script.pop(0)

    def submit_tool_results(self, results: list[ToolResult]) -> None:
        self.results_log.append(results)


# ---------------------------------------------------------------------------
# AnthropicLLM — Claude via messages API.
# ---------------------------------------------------------------------------


class AnthropicLLM:
    def __init__(self, model: str | None = None) -> None:
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "anthropic SDK not installed. `pip install anthropic`."
            ) from exc
        self._model = model or os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
        self._system = ""
        self._tools: list[dict[str, Any]] = []
        self._messages: list[dict[str, Any]] = []
        self._pending_tool_uses: list[dict[str, Any]] = []

    def start(
        self, *, system: str, user: str, tools: list[dict[str, Any]]
    ) -> None:
        self._system = system
        # Anthropic tool defs: {name, description, input_schema}.
        self._tools = tools
        self._messages = [{"role": "user", "content": user}]
        self._pending_tool_uses = []

    def next_turn(self) -> Turn:
        from anthropic import Anthropic

        resp = Anthropic().messages.create(
            model=self._model,
            max_tokens=1024,
            system=self._system,
            tools=self._tools,
            messages=self._messages,
        )

        # Record the full assistant response so tool_result can reference it.
        assistant_content: list[dict[str, Any]] = []
        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []
        for block in resp.content:
            if block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=block.input or {})
                )
                assistant_content.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input or {},
                    }
                )
            elif block.type == "text":
                text_parts.append(block.text)
                assistant_content.append({"type": "text", "text": block.text})

        self._messages.append({"role": "assistant", "content": assistant_content})

        if tool_calls:
            return Turn(tool_calls=tool_calls)
        return Turn(final=FinalAnswer(text="\n".join(text_parts).strip()))

    def submit_tool_results(self, results: list[ToolResult]) -> None:
        self._messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.tool_use_id,
                        "content": r.content,
                    }
                    for r in results
                ],
            }
        )


# ---------------------------------------------------------------------------
# OpenAILLM — GPT-4o / any OpenAI-compatible endpoint.
# ---------------------------------------------------------------------------


class OpenAILLM:
    """Uses the OpenAI chat.completions API (works with Azure OpenAI,
    LM Studio, vLLM, etc. via ``OPENAI_BASE_URL``).
    """

    def __init__(self, model: str | None = None) -> None:
        try:
            import openai  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "openai SDK not installed. `pip install openai`."
            ) from exc
        self._model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self._base_url = os.getenv("OPENAI_BASE_URL")
        self._system = ""
        self._tools: list[dict[str, Any]] = []
        self._messages: list[dict[str, Any]] = []

    def _client(self):
        from openai import OpenAI

        kwargs: dict[str, Any] = {}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return OpenAI(**kwargs)

    def start(
        self, *, system: str, user: str, tools: list[dict[str, Any]]
    ) -> None:
        self._system = system
        # OpenAI tool defs: {"type":"function","function":{"name","description","parameters":<schema>}}
        self._tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]
        self._messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def next_turn(self) -> Turn:
        resp = self._client().chat.completions.create(
            model=self._model,
            messages=self._messages,
            tools=self._tools,
            max_tokens=1024,
        )
        msg = resp.choices[0].message

        # Persist the assistant message so tool results can reference the ids.
        self._messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in (msg.tool_calls or [])
                ]
                if msg.tool_calls
                else None,
            }
        )

        if msg.tool_calls:
            tool_calls: list[ToolCall] = []
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(
                    ToolCall(id=tc.id, name=tc.function.name, arguments=args)
                )
            return Turn(tool_calls=tool_calls)

        return Turn(final=FinalAnswer(text=(msg.content or "").strip()))

    def submit_tool_results(self, results: list[ToolResult]) -> None:
        for r in results:
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": r.tool_use_id,
                    "content": r.content,
                }
            )


# ---------------------------------------------------------------------------
# OllamaLLM — local models via HTTP.
# ---------------------------------------------------------------------------


class OllamaLLM:
    """Talks to a local Ollama server (default http://localhost:11434).

    Uses the /api/chat endpoint with the tool-calling extension. Works
    with any Ollama model that supports tools (llama3.1, qwen2.5,
    mistral-nemo, firefunction-v2, etc.). Tool-call quality is
    noticeably worse on smaller local models — treat as
    zero-cost-but-lower-fidelity.
    """

    def __init__(self, model: str | None = None) -> None:
        self._model = model or os.getenv("OLLAMA_MODEL", "llama3.1")
        self._host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        self._system = ""
        self._tools: list[dict[str, Any]] = []
        self._messages: list[dict[str, Any]] = []

    def start(
        self, *, system: str, user: str, tools: list[dict[str, Any]]
    ) -> None:
        self._system = system
        # Ollama accepts OpenAI-style function schema.
        self._tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]
        self._messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def next_turn(self) -> Turn:
        import urllib.request

        payload = json.dumps(
            {
                "model": self._model,
                "messages": self._messages,
                "tools": self._tools,
                "stream": False,
            }
        ).encode()
        req = urllib.request.Request(
            f"{self._host}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())

        msg = body.get("message", {}) or {}
        raw_calls = msg.get("tool_calls") or []

        # Persist assistant turn.
        self._messages.append(
            {
                "role": "assistant",
                "content": msg.get("content", "") or "",
                "tool_calls": raw_calls or None,
            }
        )

        if raw_calls:
            tool_calls: list[ToolCall] = []
            for i, tc in enumerate(raw_calls):
                fn = tc.get("function", {}) or {}
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                # Ollama may not emit an id; synthesise a stable one.
                tid = tc.get("id") or f"ollama_{i}_{fn.get('name', 'tool')}"
                tool_calls.append(
                    ToolCall(id=tid, name=fn.get("name", ""), arguments=args or {})
                )
            return Turn(tool_calls=tool_calls)

        return Turn(final=FinalAnswer(text=(msg.get("content") or "").strip()))

    def submit_tool_results(self, results: list[ToolResult]) -> None:
        for r in results:
            self._messages.append(
                {
                    "role": "tool",
                    "content": r.content,
                    # older Ollama ignores tool_call_id; keep for parity
                    "tool_call_id": r.tool_use_id,
                }
            )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


PROVIDERS = ("anthropic", "openai", "ollama")


def build_llm(kind: str | None = None) -> LLM:
    """Return an LLM adapter.

    Resolution order for ``kind``:

    1. Explicit argument (``"anthropic"`` / ``"openai"`` / ``"ollama"``).
    2. ``AI_DBA_LLM`` env var.
    3. ``"auto"`` — pick the first provider whose credentials look set.
    """
    kind = (kind or os.getenv("AI_DBA_LLM") or "auto").lower()

    if kind == "auto":
        if os.getenv("ANTHROPIC_API_KEY"):
            kind = "anthropic"
        elif os.getenv("OPENAI_API_KEY"):
            kind = "openai"
        elif os.getenv("OLLAMA_HOST") or _ollama_reachable():
            kind = "ollama"
        else:
            raise RuntimeError(
                "No LLM configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, "
                "or run Ollama locally (or set AI_DBA_LLM explicitly)."
            )

    if kind == "anthropic":
        return AnthropicLLM()
    if kind == "openai":
        return OpenAILLM()
    if kind == "ollama":
        return OllamaLLM()
    raise ValueError(f"unknown LLM provider {kind!r}; expected one of {PROVIDERS}")


def _ollama_reachable(host: str | None = None, timeout: float = 0.2) -> bool:
    """Return True if a local Ollama server is answering /api/tags."""
    import urllib.error
    import urllib.request

    host = (host or os.getenv("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


__all__ = [
    "AnthropicLLM",
    "FakeLLM",
    "FinalAnswer",
    "LLM",
    "OllamaLLM",
    "OpenAILLM",
    "PROVIDERS",
    "ToolCall",
    "ToolResult",
    "Turn",
    "build_llm",
]
