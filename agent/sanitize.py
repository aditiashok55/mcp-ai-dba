"""Defense against second-order prompt injection.

Tool outputs can contain arbitrary strings from the database (query
text captured in ``pg_stat_activity``, table names, etc.). If we feed
those verbatim back into the LLM planner, an attacker who can plant
text in the DB can plant instructions in the agent.

This module wraps every string leaf of a tool result so the LLM
sees the payload as *data*, not *instructions*.
"""
from __future__ import annotations

import re
from typing import Any

# Patterns that look like injected LLM instructions. We don't try to
# be exhaustive — this is a signal, not a filter. The primary defense
# is the ``<untrusted>`` wrapping below; this is just observability.
_SUSPICIOUS = re.compile(
    r"(?i)(ignore (?:all )?previous|system prompt|you are now|"
    r"assistant:|<\|.*?\|>|call the tool)",
)

_MAX_STR = 2_000  # per-string cap; prevents runaway context blow-up


def _wrap(text: str) -> str:
    # Truncate first, then wrap. The tags make the LLM treat the
    # content as inert data (a well-documented mitigation).
    if len(text) > _MAX_STR:
        text = text[:_MAX_STR] + "…[truncated]"
    return f"<untrusted>{text}</untrusted>"


def sanitize(value: Any) -> Any:
    """Recursively wrap string leaves inside a tool result."""
    if isinstance(value, str):
        return _wrap(value)
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items()}
    return value


def contains_suspicious(value: Any) -> bool:
    """Return True if any string leaf looks like an injected instruction."""
    if isinstance(value, str):
        return bool(_SUSPICIOUS.search(value))
    if isinstance(value, list):
        return any(contains_suspicious(v) for v in value)
    if isinstance(value, dict):
        return any(contains_suspicious(v) for v in value.values())
    return False
