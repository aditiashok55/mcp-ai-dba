# CLAUDE.md — AI-DBA project reference

This file is the single source of truth for what has been built in this
repo, why it looks the way it does, and where to make changes. Read
this first before editing anything.

Companion documents:
- `AI_DBA_Phase_0_Architecture.md` — the original design doc (why this
  project exists, the trust-boundary model, V1/V2 scope).
- `README.md` — user-facing setup instructions.

---

## 1. What this project is

An **AI-DBA**: an LLM-driven agent that investigates PostgreSQL
operational problems (slow queries, lock contention, connection
leaks, missing indexes) by calling a **finite, named, read-only** set
of diagnostic tools — never by generating raw SQL that gets executed
as-is.

The core architectural constraint (design doc §1):

> The LLM never talks to the database directly, and never generates
> arbitrary SQL that gets executed as-is. The LLM only calls named
> tools with typed parameters. Every tool internally runs a fixed,
> parameterized SQL statement.

Every design decision downstream flows from that one line.

---

## 2. Layout

```
mcp-ai-dba/
├── AI_DBA_Phase_0_Architecture.md   # original design doc
├── CLAUDE.md                        # ← this file
├── README.md                        # user-facing setup
├── docker-compose.yml               # postgres + prometheus + grafana
├── pyproject.toml                   # pytest config
├── requirements.txt                 # pinned deps
├── .env.example
│
├── db/
│   ├── init/                        # runs on first `docker compose up`
│   │   ├── 00_extensions.sql        # pg_stat_statements
│   │   ├── 01_schema.sql            # customers/orders/products/order_items
│   │   ├── 02_seed.sql
│   │   └── 03_readonly_user.sql     # ai_dba_readonly role
│   └── chaos/                       # legacy SQL stubs (not used)
│
├── mcp_server/                      # ← the tool provider (trust boundary)
│   ├── config.py                    # env-driven config + guards
│   ├── db.py                        # connect() context manager
│   ├── audit.py                     # structured JSON log + correlation IDs
│   ├── rate_limit.py                # per-tool token bucket
│   ├── metrics.py                   # Prometheus counters/hist/gauge
│   ├── server.py                    # FastMCP entrypoint
│   └── tools/
│       ├── base.py                  # @dba_tool decorator (the core)
│       ├── connection.py
│       ├── health.py
│       ├── active_connections.py
│       ├── slow_queries.py
│       ├── locks.py
│       ├── explain_query.py
│       ├── table_statistics.py
│       └── index_usage.py
│
├── agent/                           # ← the LLM loop
│   ├── schemas.py                   # Diagnosis + Finding pydantic
│   ├── sanitize.py                  # <untrusted> wrapping
│   ├── mcp_client.py                # in-process tool allowlist
│   ├── llm.py                       # AnthropicLLM + FakeLLM + Protocol
│   ├── loop.py                      # plan→call→observe→diagnose
│   └── cli.py                       # `python -m agent.cli "..."`
│
├── eval/                            # ← chaos + eval harness
│   ├── chaos.py                     # background-thread injectors
│   ├── rule_llm.py                  # deterministic offline "LLM"
│   ├── scorer.py                    # keyword-based Score
│   └── runner.py                    # `python -m eval.runner`
│
├── web/                             # ← FastAPI + vanilla JS UI
│   ├── backend.py                   # /api/tools + /api/diagnose
│   └── static/
│       ├── index.html
│       ├── styles.css
│       └── app.js
│
├── observability/
│   ├── prometheus/prometheus.yml
│   └── grafana/
│       ├── provisioning/
│       │   ├── datasources/prometheus.yml
│       │   └── dashboards/dashboards.yml
│       └── dashboards/ai_dba.json
│
├── scripts/
│   └── smoke_web.sh                 # 3-curl UI smoke test
│
└── tests/                           # 43 tests, all mocked, no DB needed
    ├── conftest.py                  # FakeCursor / FakeConnection
    ├── test_audit.py
    ├── test_tools.py
    ├── test_validation.py
    ├── test_rate_limit.py
    ├── test_metrics.py
    ├── test_agent_loop.py
    ├── test_agent_safety.py
    ├── test_scorer.py
    ├── test_eval_harness.py
    └── test_web_backend.py
```

---

## 3. The four systems

The repo is four independently-testable systems layered on top of each
other. Each has one file/module that owns the contract; edits should
respect those seams.

### 3.1 MCP server (the trust boundary)

**Owns**: DB access, validation, audit, rate limit, metrics.
**Never**: talks to an LLM, accepts free-text SQL, opens a raw
`psycopg.connect`.

- Every tool goes through `@dba_tool` (`mcp_server/tools/base.py`).
  That decorator is where **every cross-cutting concern lives** —
  rate limit, pydantic validation, execution, audit, metrics,
  error envelope. Adding a concern? Add it here, once.
- Every DB connection goes through `connect()`
  (`mcp_server/db.py`). It sets `statement_timeout`,
  `default_transaction_read_only`, `application_name`, and
  `dict_row`.
- Every tool response includes: `{status, tool, correlation_id, ...}`.
  The `correlation_id` is what the agent later cites as evidence.

### 3.2 Agent (the LLM loop)

**Owns**: the plan → call → observe → diagnose cycle.
**Never**: talks to the DB directly.

- `agent/loop.py::run()` is the loop. It calls the LLM through a
  **stateful, provider-neutral** protocol
  (`start` → `next_turn` → `submit_tool_results`) so no single
  provider's message shape leaks into the loop code.
- The LLM adapter (`agent/llm.py`) is a small `Protocol` with four
  implementations:
  - `AnthropicLLM` — Claude (`ANTHROPIC_API_KEY`)
  - `OpenAILLM`    — GPT-4o / any OpenAI-compatible endpoint via
    `OPENAI_BASE_URL` (Azure, LM Studio, vLLM)
  - `OllamaLLM`    — local models (Llama 3.1, qwen2.5, etc.), no key
  - `FakeLLM`      — scripted turns for tests

  Each adapter owns its own native transcript and converts our neutral
  `ToolCall` / `ToolResult` types to the provider's wire format.
  A fifth "LLM" — `eval/rule_llm.py::RuleBasedLLM` — is a
  deterministic playbook used by the eval harness and offline UI runs.
- `agent/llm.py::build_llm(kind=None)` is the factory. Resolution:
  explicit arg → `AI_DBA_LLM` env → `auto` (first provider with
  credentials).
- `agent/mcp_client.py::tool_defs()` returns the *neutral*
  `{name, description, input_schema}` shape; adapters wrap it as
  needed. `anthropic_tool_defs()` remains as a deprecated alias.
- Correlation IDs are captured from every tool response into
  `RunResult.tool_call_ids`. The loop exposes those so callers can
  detect LLM fabrication (the LLM's `all_tool_calls` might lie; the
  loop's list is the ground truth).

### 3.3 Eval harness (chaos + scoring)

**Owns**: reproducible failure injection and accuracy measurement.

- `eval/chaos.py` — three background-thread scenarios that connect
  as the **admin role** (they need to hold locks / stay idle-in-txn,
  which the read-only role can't do). Each scenario declares
  `expected_findings` — the keywords a correct diagnosis should
  contain.
- `eval/scorer.py` — bag-of-keywords over concatenated finding text,
  case-insensitive. Returns a `Score` with matched/missing.
- `eval/runner.py` — CLI. Refuses to run without
  `AI_DBA_INTEGRATION=1` because chaos injectors need a real DB.
  Exits non-zero if overall mean score < 0.5.

### 3.4 Web UI

**Owns**: the "so a human can actually use this" surface.

- `web/backend.py` is a thin FastAPI wrapper around `agent.loop.run`.
  All security invariants are preserved because we reuse the loop.
- The frontend is vanilla HTML + CSS + one JS file (no build step,
  no framework). Every string leaf from the API is
  `<untrusted>`-unwrapped before rendering.

---

## 4. Invariants — do not break these

1. **The LLM never sees a raw connection and never supplies SQL.**
   `explain_slow_query` is the canonical proof: it accepts a
   `queryid`, not a query.
2. **All DB access goes through `mcp_server.db.connect`.** A tool
   that opens its own `psycopg.connect` bypasses statement_timeout
   and read-only mode.
3. **All tools go through `@dba_tool`.** A tool that raises past the
   decorator is a bug. The decorator produces the `correlation_id`
   the agent needs to cite.
4. **No write tools. Ever, in V1.** If added, they need explicit
   human-approval and their own decorator (not `@dba_tool`).
5. **`extra="forbid"` on every pydantic input model.** This is what
   makes prompt injection via extra kwargs a non-issue.
6. **`Diagnosis` schema validates evidence.** Any finding whose
   `evidence` cites a correlation ID not in `all_tool_calls`
   fails `model_validate`. Cross-check `RunResult.tool_call_ids`
   (the real ones) against the LLM's `all_tool_calls` if you want
   to detect fabrication.
7. **`EXPLAIN`, never `EXPLAIN ANALYZE`.** `ANALYZE` executes the
   plan — that's a side effect and a security hole.

---

## 5. Tools implemented (V1)

| # | Tool | Reads from | Notes |
|---|---|---|---|
| 1 | `check_database_connection` | — | Reachability probe. |
| 2 | `get_database_health` | `pg_stat_activity`, `pg_postmaster_start_time`, `current_setting` | Composite. |
| 3 | `get_active_connections` | `pg_stat_activity` | Filters out our own backend PID. |
| 4 | `get_slow_queries` | `pg_stat_activity` + `pg_stat_statements` (optional) | Graceful when extension missing. |
| 5 | `get_lock_information` | `pg_stat_activity` + `pg_blocking_pids` | Blocked/blocker pairs. |
| 6 | `explain_slow_query` | `pg_stat_statements` | Looks up text by `queryid`. `EXPLAIN` only, always rolled back. |
| 7 | `get_table_statistics` | `pg_stat_user_tables` | Sizes, dead-tuple %, vacuum recency. |
| 8 | `get_index_usage` | `pg_stat_user_indexes` | Unused / missing-index candidates. |

Deferred: `get_recent_errors` (needs log parsing) and
`find_similar_incidents` (needs `pgvector`, V2).

---

## 6. Configuration surface

Every knob is env-driven. Defaults in `mcp_server/config.py`.

| Env var | Default | Purpose |
|---|---|---|
| `DB_HOST` / `DB_PORT` / `DB_NAME` | localhost / 5432 / ai_dba | Postgres connection. |
| `DB_USER` / `DB_PASSWORD` | `ai_dba_readonly` / `readonly_password` | Read-only role from `db/init/03_readonly_user.sql`. |
| `STATEMENT_TIMEOUT_MS` | 3000 | Per-query timeout (PG-enforced). |
| `MAX_ROWS` | 200 | Ceiling for any tool's `limit` param. |
| `APPLICATION_NAME` | `ai-dba-mcp` | So the agent can filter itself out. |
| `RATE_LIMIT_CAPACITY` | 10 | Token bucket per tool. |
| `RATE_LIMIT_REFILL_PER_SEC` | 2.0 | Refill rate. |
| `METRICS_ENABLED` | `1` | Set `0` in tests. |
| `METRICS_PORT` | 9108 | `/metrics` endpoint. |
| `ANTHROPIC_API_KEY` | (unset) | Required for Claude. |
| `ANTHROPIC_MODEL` | `claude-3-5-haiku-latest` | Cheap model is plenty for tool-calling. |
| `OPENAI_API_KEY` | (unset) | Required for OpenAI. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Or any tool-calling model. |
| `OPENAI_BASE_URL` | (unset) | Point at Azure OpenAI / LM Studio / vLLM. |
| `OLLAMA_HOST` | `http://localhost:11434` | Local server. |
| `OLLAMA_MODEL` | `llama3.1` | Must support tool-calling. |
| `AI_DBA_LLM` | `auto` | `auto` / `anthropic` / `openai` / `ollama`. |
| `AI_DBA_INTEGRATION` | (unset) | `1` unlocks integration + eval runs. |
| `WEB_PORT` | 8000 | UI port. |
| `AI_DBA_ADMIN_URL` | `postgresql://admin:admin_password@localhost:5432/ai_dba` | Chaos injectors use this. |

---

## 7. How to run

### Local dev

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
docker compose up -d              # postgres + prometheus + grafana
```

### The four ways to use the system

| Interface | Command | When |
|---|---|---|
| **Tests** | `METRICS_ENABLED=0 pytest` | Always. 43/43 must pass. |
| **CLI agent** | `python -m agent.cli "why is DB slow?"` | Quick queries. Needs `ANTHROPIC_API_KEY`. |
| **Web UI** | `python -m web.backend` → http://127.0.0.1:8000 | Demos, screenshots, exploratory. |
| **MCP server** | `python -m mcp_server.server` | To use with Claude Desktop / another external MCP client. |
| **Eval harness** | `AI_DBA_INTEGRATION=1 python -m eval.runner --all --runs 3` | Measure diagnosis accuracy. |

### Observability

- Prometheus: http://localhost:9090
- Grafana:    http://localhost:3000 (anon Viewer; `admin`/`admin` to edit)
- Dashboard "AI-DBA Tools" is auto-provisioned.

Metrics exposed: `aidba_tool_calls_total`,
`aidba_tool_call_duration_seconds`, `aidba_tool_calls_in_flight`.

---

## 8. Testing conventions

- Tests never hit a real database. `tests/conftest.py::fake_db`
  monkey-patches `connect` in every tool module with a scripted
  `FakeCursor` that consumes `(sql_substring, rows)` pairs **in
  order** (not first-match — this matters when tools issue multiple
  queries).
- The rate limiter is reset between tests via an autouse fixture.
- Audit logging is silenced via an autouse fixture.
- Integration tests (chaos + real DB) are marked `integration` and
  skipped unless `AI_DBA_INTEGRATION=1`.
- Metrics tests require `METRICS_ENABLED=0` (otherwise pytest
  tries to bind port 9108).

Run everything: `METRICS_ENABLED=0 pytest`

---

## 9. Adding a new tool — checklist

1. Create `mcp_server/tools/<name>.py`.
2. Define a pydantic `Input` model with `model_config = {"extra": "forbid"}`.
3. Write the SQL as a module-level string constant. Use `%(name)s`
   placeholders, never f-string interpolation.
4. Wrap the function with `@dba_tool(name="...", input_model=...)`.
5. The function body should be:
   ```python
   with connect() as conn, conn.cursor() as cur:
       cur.execute(_SQL, {...})
       rows = cur.fetchall()
   return {"rows": rows}
   ```
6. Register the tool in **two** places:
   - `mcp_server/server.py` (external MCP clients)
   - `agent/mcp_client.py::_TOOLS` (internal agent)
   Both include the JSON schema. This is the "defense-in-depth
   allowlist" from the design doc §5.
7. Add a happy-path test in `tests/test_tools.py`.
8. Add at least one adversarial test in `tests/test_validation.py`
   (bad limit, unknown kwarg, etc.).

---

## 10. Gotchas (things that will bite you)

- **`mcp` 2.x moved `FastMCP` out of `mcp.server.fastmcp`.**
  Requirements pin `mcp>=1.0,<2.0`. Don't unpin.
- **`pg_stat_statements` needs `shared_preload_libraries`** and a PG
  restart. Set in `docker-compose.yml`. If you upgrade from an older
  volume: `docker compose down -v && docker compose up -d`.
- **`pg_stat_activity.query` is masked** for non-owners unless the
  role is in `pg_read_all_stats`. Granted in `03_readonly_user.sql`.
- **The venv had to be recreated once** because Homebrew bumped
  Python from 3.13 to 3.14 mid-install. If `from mcp.server.fastmcp
  import FastMCP` starts failing, check for mixed site-packages dirs.
- **Grafana's `host.docker.internal`** works on Docker Desktop
  (macOS/Windows) out of the box. On Linux, `extra_hosts:` with
  `host-gateway` makes it work there too — already in the compose.
- **`RuleBasedLLM` runs four tools per diagnosis.** Rate limit
  capacity is 10 by default, so it fits — but if you crank the
  playbook, bump `RATE_LIMIT_CAPACITY`.
- **The FastAPI `TestClient` needs `httpx`** (already in
  requirements). Without it, tests fail at collection.

---

## 11. Roadmap status

Phase 0 architecture doc uses these phase numbers; this project matches.

| Phase | Status |
|---|---|
| 1 — Architecture doc | ✅ (design doc committed) |
| 2 — Docker Postgres + seed | ✅ |
| 3 — Tools 1–8 | ✅ (8 tools, adversarial tests, mocked DB) |
| 4 — Agent loop + structured diagnosis | ✅ (Anthropic + Fake LLMs, schema enforcement) |
| 5 — Security hardening | ✅ (rate limit, sanitizer, allowlist duplication) |
| 6 — Observability | ✅ (Prometheus + Grafana + dashboard) |
| 7 — Testing sweep + eval harness | ✅ (43 tests, chaos + scorer + runner) |
| 8 — (unused number) | — |
| 9 — Polish | 🔶 partial (README + this doc; no example transcripts / screenshots yet) |

**Bonus over the design doc**: web UI (not in the original scope).

**V2 (deferred, explicitly)**:
- `pgvector` incident memory + `find_similar_incidents`
- Oracle / MongoDB multi-engine
- Write / remediation tools with approval
- Cloud deployment
- Multi-database fleet view

---

## 12. If Claude is picking this up cold — start here

1. Read §1 (what this is) and §4 (invariants).
2. Skim `AI_DBA_Phase_0_Architecture.md` §1–§5 for the "why".
3. Open `mcp_server/tools/base.py` — that's the single most important
   file in the repo. If you understand it, you understand the trust
   boundary.
4. Open `agent/loop.py` — that's the single most important file on
   the agent side.
5. Run `METRICS_ENABLED=0 pytest` — 43 tests should pass. If they
   don't, something in the environment is broken; fix that before
   editing code.
6. To make changes: figure out which of the four systems (§3) owns
   the concern, use the checklist in §9 if it's a new tool, and
   never violate §4.
