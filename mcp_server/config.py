"""Runtime configuration for the AI-DBA MCP server.

All values are read from environment variables (see ``.env.example``).
No secrets are hard-coded; defaults only apply to the local Docker
environment defined in ``docker-compose.yml``.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Env var {name} must be an integer, got {raw!r}") from exc


# --- Database connection ---------------------------------------------------
DB_HOST: str = os.getenv("DB_HOST", "localhost")
DB_PORT: int = _int("DB_PORT", 5432)
DB_NAME: str = os.getenv("DB_NAME", "ai_dba")
DB_USER: str = os.getenv("DB_USER", "ai_dba_readonly")
DB_PASSWORD: str = os.getenv("DB_PASSWORD", "readonly_password")

# --- Execution guards ------------------------------------------------------
# Hard per-statement timeout enforced by Postgres itself.
STATEMENT_TIMEOUT_MS: int = _int("STATEMENT_TIMEOUT_MS", 3000)

# Global cap on rows returned by any single tool. Individual tools may
# request a smaller limit, but never a larger one.
MAX_ROWS: int = _int("MAX_ROWS", 200)

# Identifies our sessions in ``pg_stat_activity`` — useful for the agent
# to filter itself out of diagnostic queries.
APPLICATION_NAME: str = os.getenv("APPLICATION_NAME", "ai-dba-mcp")

# --- Rate limiting ---------------------------------------------------------
# Token bucket per (tool_name). Defaults tuned for a single interactive
# agent — bump for load tests.
RATE_LIMIT_CAPACITY: int = _int("RATE_LIMIT_CAPACITY", 10)
RATE_LIMIT_REFILL_PER_SEC: float = float(os.getenv("RATE_LIMIT_REFILL_PER_SEC", "2.0"))

# --- Observability ---------------------------------------------------------
# Prometheus /metrics endpoint. Disabled in tests.
METRICS_ENABLED: bool = os.getenv("METRICS_ENABLED", "1") not in ("0", "false", "False")
METRICS_PORT: int = _int("METRICS_PORT", 9108)
