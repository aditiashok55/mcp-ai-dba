 `oracle-mcp-server` repo is a really useful baseline. It's a legitimate MCP server (FastMCP, `query_tool` + `schema_tool`, Oracle, table whitelist, read-only enforcement via `SELECT`-prefix check), but it's also honest about its own gaps in the roadmap section: no query-level table validation (whitelist only applies to schema introspection, not arbitrary SELECTs), no row limits, no connection pooling, no audit log, no observability, and the "read-only" check is a string-prefix check rather than real SQL parsing (which means `SELECT ... ; DROP TABLE ...` or a CTE hiding a write is not actually blocked). It's also single-tool-shallow — one query tool and one schema tool, not the operational-diagnosis tool suite you're describing now.

That's a good thing to know going in: this new project isn't starting from zero conceptually, but it *is* starting from zero architecturally if you want the "AI-DBA that investigates and diagnoses" story instead of "AI writes SQL for you" story. Those are genuinely different systems. I'll treat the old repo as V0 — worth referencing in your README as "here's what I built first and why I rebuilt it" (that's itself a great interview answer to "why not just expose an API" — you'll have a concrete before/after).

Here's Phase 0, per your spec.

## 1. Proposed Final Architecture

The key architectural decision is: **the LLM never talks to the database directly, and never generates arbitrary SQL that gets executed as-is.** The LLM only calls named tools with typed parameters. Every tool internally runs a *fixed, parameterized* SQL statement (or a small safe family of statements) — not a string the LLM composed.

This is the single biggest upgrade over the old repo, where `query_tool` effectively let Claude write and run any SELECT. That's fine for "chat with your database," but it's disqualifying for "AI-DBA" — an ops agent should have a finite, auditable set of actions, not the entire SQL language as its action space. This constraint is also what makes the security model tractable: you can't fully secure "run arbitrary SQL the LLM wrote," but you can fully secure "call `get_slow_queries(threshold_ms, limit)`."

## 2. Architecture Diagram (text)

```
┌────────────────────────────────────────────────────────────┐
│  User (operator asking "why is DB1 slow?")                  │
└───────────────────────────┬──────────────────────────────────┘
                             ▼
┌────────────────────────────────────────────────────────────┐
│  Agent Loop (Python)                                          │
│  - LLM (API-based, e.g. Claude/GPT via API)                  │
│  - Orchestrates: plan → call tools → collect evidence →       │
│    reason → answer                                            │
│  - Never sees raw DB credentials, never writes raw SQL        │
└───────────────────────────┬──────────────────────────────────┘
                             │  MCP protocol (stdio or SSE)
                             ▼
┌────────────────────────────────────────────────────────────┐
│  MCP Client                                                   │
│  - Discovers tools from MCP server                            │
│  - Passes LLM's tool-call requests through                    │
│  - Enforces allowlist at the client boundary too (defense     │
│    in depth — don't trust the server alone)                   │
└───────────────────────────┬──────────────────────────────────┘
                             ▼
┌────────────────────────────────────────────────────────────┐
│  MCP Server (this is where most engineering lives)            │
│                                                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐    │
│  │ Tool Registry │  │ Input        │  │ Audit Logger      │    │
│  │ (allowlist)   │  │ Validation   │  │ (every call in/out│    │
│  │               │  │ (pydantic)   │  │  regardless of    │    │
│  └──────────────┘  └──────────────┘  │  outcome)          │    │
│                                        └──────────────────┘    │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Fixed/Parameterized Query Templates per tool            │  │
│  │ (no string concatenation of user/LLM input into SQL)    │  │
│  └────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Execution Guard: statement timeout, row-limit cap,      │  │
│  │ read-only DB role, circuit breaker on repeated failures │  │
│  └────────────────────────────────────────────────────────┘  │
└───────────────────────────┬──────────────────────────────────┘
                             │  read-only DB credentials
                             ▼
┌────────────────────────────────────────────────────────────┐
│  PostgreSQL (Docker) — seeded with realistic data +           │
│  reproducible failure scenarios                               │
└────────────────────────────────────────────────────────────┘

        ┌─────────────────────────────────────────┐
        │ Observability (sits alongside, not inline)│
        │ Prometheus scrapes MCP server metrics      │
        │ Grafana dashboards: tool latency, call      │
        │ volume, failure rate, audit log             │
        └─────────────────────────────────────────┘

```

## 3. Core Components

- **MCP Server** — the tool provider. Owns the DB connection, the allowlist, validation, audit logging, and execution guards. This is the trust boundary.
- **Tool layer** — one module per tool (mirrors your old `tools/` folder), each with an explicit input schema, output schema, and a single fixed query template.
- **Agent/orchestrator** — the loop that takes a user question, calls the LLM with tool definitions, executes tool calls via the MCP client, feeds results back, and produces a final diagnosis. This is where "evidence vs. assumption" separation gets enforced structurally (e.g., require the agent to cite which tool output supports each claim).
- **MCP Client** — thin layer, but worth building explicitly (not hiding behind a framework) so you can explain exactly what crosses the trust boundary.
- **Audit/observability layer** — structured logs (every tool call: who, what, params, result size, duration, success/failure) + Prometheus metrics + Grafana.
- **Sample DB environment** — Docker Compose Postgres with a seeding script and a separate "chaos" script that reproducibly injects slow queries, lock contention, connection spikes, etc.

## 4. First 5–8 Tools

Ordered by build complexity, not necessarily usage order:

1. `check_database_connection()` — trivial, but good first step to prove the whole pipeline (LLM → client → server → DB → back) works end to end.
2. `get_database_health()` — connection count vs. max, uptime, basic vitals. Composite tool, good second step.
3. `get_active_connections()` — list of current connections with state (active/idle/idle in transaction) — this is what surfaces connection leaks.
4. `get_slow_queries()` — from `pg_stat_statements` or `pg_stat_activity` for currently-running long queries. This is your centerpiece diagnostic tool.
5. `get_lock_information()` — blocking/blocked query pairs from `pg_locks`/`pg_stat_activity`. Great story for "how would you debug contention at scale."
6. `explain_slow_query(query_id)` — restricted redesign of the free-text `explain_query`: looks up the query text from `pg_stat_statements` by ID so the LLM never supplies free text. Avoids the risks of arbitrary EXPLAIN input (data leakage via functions in the plan, accidental `EXPLAIN ANALYZE` execution). Much cleaner security story.
7. `get_table_statistics()` — table sizes, dead tuples, last vacuum/analyze — ties into "why is this slow" as a secondary cause.
8. `get_index_usage()` — from `pg_stat_user_indexes`; pairs with `get_table_statistics` for the missing-index scenario and is cheap to build.
9. `get_recent_errors()` — requires log access or an errors table you seed; lowest priority, can be stubbed/simulated if Postgres log parsing is too heavy for V1.
10. `find_similar_incidents(symptom_description)` — (stretch goal) semantic search over past incident diagnoses via `pgvector`. Each completed diagnosis is embedded and stored; the agent retrieves similar past cases as evidence. A genuine RAG-in-an-agent story, not a bolt-on vector feature.
// ...existing code...
- **Prompt injection**: since tools are fixed and parameterized, the main injection risk is the LLM being tricked into calling a tool with a malicious *parameter* (e.g., an injected instruction inside DB data telling the agent to call a tool with a destructive-looking argument). Mitigation: validate every parameter server-side regardless of what the LLM intended, and never let tool *output* be treated as instructions in the next planning step without sanitization.
- **Rate limiting per tool** on the MCP server — caps a runaway agent loop and adds another defense-in-depth layer.
- **Correlation IDs**: thread a request ID from user question → every tool call → audit log → Grafana. Trivial to add early, painful to retrofit — build it in from day one.
- **No write tools in V1**, full stop. If you add remediation later, it goes through an explicit human-approval step, logged separately, and probably deserves its own dedicated tool with a much narrower blast radius (e.g., `kill_connection(pid)` with confirmation, not `run_sql(anything)`).

## 5.5 Structured Diagnosis Output

The agent must emit a structured diagnosis, not prose:

```json
{
  "finding": "Lock contention on orders table",
  "evidence": ["tool_call_3", "tool_call_5"],
  "confidence": "high",
  "recommended_action": "Investigate idle-in-transaction session pid 4132"
}
```

This makes "evidence vs. assumption" enforceable and testable — every finding must cite tool-call IDs — and the structured records feed the `find_similar_incidents` embedding store.

## 5.6 Eval Harness

Since chaos scenarios are reproducible, add a small eval suite: run each scenario N times and score whether the agent reached the correct root cause. Track diagnosis accuracy per scenario. "I measured diagnosis accuracy" is a far stronger interview line than "it usually works."
// ...existing code...
**V1 (this project's real target):**

- Postgres only
- Read-only tools 1–8 above (skip `get_recent_errors` if log parsing gets heavy)
- Structured diagnosis output schema (findings must cite evidence)
- Eval harness scoring diagnosis accuracy across chaos scenarios
- Correlation IDs threaded through agent → tools → audit log → metrics
- Fixed API-based LLM (see cost note below)
- Docker Compose local environment with chaos scripts
- Structured audit logging + basic Prometheus/Grafana
- pytest coverage: unit, tool-level, permission/security, and at least a few adversarial-input tests

**V2 (explicitly out of scope until V1 is solid):**

- `pgvector` incident memory + `find_similar_incidents` (promote to V1 only if time allows)
- Oracle support (multi-engine abstraction)
- MongoDB support — deliberately deferred; multi-engine diagnostics (`currentOp`/`serverStatus` vs. `pg_stat_*`) is real abstraction work, and the "vector DB" angle is better served by `pgvector` inside the existing Postgres
- Any write/remediation tools with approval workflow
- Cloud deployment
- Multi-database fleet view (the "1000 databases" scaling story stays theoretical/design-doc for now, not implemented)
// ...existing code...
- **Days 16–18**: Phase 7 testing sweep, especially adversarial/permission tests + eval harness runs across all chaos scenarios.


## 5. Security Model

Layered, not single-point:

- **DB layer**: dedicated Postgres role, `SELECT`-only grants, no DDL/DML privileges at all — enforced by Postgres itself, not just application code. This is the one your old repo was missing; a string-prefix check is not a security boundary, a DB role is.
- **Statement layer**: `statement_timeout` set at the role/session level, plus an app-level timeout as backup.
- **Tool layer**: fixed query templates, no string concatenation — parameters go through psycopg2/asyncpg parameterized queries, and free-text params (like `explain_query`) go through a strict allowlist grammar, not "starts with SELECT."
- **Result layer**: hard row-limit cap on every tool, response size cap, to prevent a runaway query result from blowing up context or exfiltrating a full table.
- **Orchestration layer**: MCP client-side allowlist as a second check (don't trust the server alone — defense in depth, and a good "how would you scale/secure this at 1000 databases" talking point: centralize the allowlist policy, don't let each server define its own).
- **Audit layer**: every tool call logged with full input/output metadata regardless of success — this is what "auditability" means in practice, and it's also your prompt-injection detection surface (weird tool-call patterns show up here).
- **Prompt injection**: since tools are fixed and parameterized, the main injection risk is the LLM being tricked into calling a tool with a malicious *parameter* (e.g., an injected instruction inside DB data telling the agent to call `explain_query` with a destructive-looking string). Mitigation: validate every parameter server-side regardless of what the LLM intended, and never let tool *output* be treated as instructions in the next planning step without sanitization.
- **No write tools in V1**, full stop. If you add remediation later, it goes through an explicit human-approval step, logged separately, and probably deserves its own dedicated tool with a much narrower blast radius (e.g., `kill_connection(pid)` with confirmation, not `run_sql(anything)`).

## 6. Database Failure Scenarios to Simulate

- Long-running query (artificial `pg_sleep` or a deliberately unindexed join on a large table)
- Lock contention (two sessions, one holding a row lock, one blocked)
- Connection pool exhaustion (spin up N idle-in-transaction connections)
- Missing index causing sequential scan on a large table
- Dead tuple bloat (delete/update churn without vacuum, to show up in `get_table_statistics`)
- Failed/erroring queries (bad type cast, constraint violation) for `get_recent_errors`
- A "healthy baseline" scenario too — you want the agent to correctly say "nothing's wrong" sometimes, otherwise the diagnosis story looks scripted

Each scenario should have its own reset script so you can demo it reliably in an interview.

## 7. V1 vs V2

**V1 (this project's real target):**

- Postgres only
- Read-only tools 1–7 above (skip `get_recent_errors` if log parsing gets heavy)
- Fixed API-based LLM (see cost note below)
- Docker Compose local environment with chaos scripts
- Structured audit logging + basic Prometheus/Grafana
- pytest coverage: unit, tool-level, permission/security, and at least a few adversarial-input tests

**V2 (explicitly out of scope until V1 is solid):**

- Oracle support (multi-engine abstraction)
- Any write/remediation tools with approval workflow
- Cloud deployment
- Multi-database fleet view (the "1000 databases" scaling story stays theoretical/design-doc for now, not implemented)

On LLM cost: you don't need an expensive API to build this. Claude and GPT both have cheap small models (Haiku-class/mini-class) that are plenty for tool-calling in a bounded action space like this — your token spend will be dominated by tool outputs, not model tier. A local model (e.g., via Ollama) is a valid option too if you want zero API cost, with the trade-off that tool-calling reliability is noticeably worse on smaller local models — worth mentioning as a trade-off you evaluated even if you don't build it.

## 8. Realistic 2–3 Week Roadmap

- **Days 1–2**: Phase 1 architecture doc + Phase 2 Docker Postgres environment with seed data and one chaos scenario working reproducibly.
- **Days 3–5**: Phase 3, first 2 tools (`check_database_connection`, `get_database_health`) end-to-end through MCP, with tests.
- **Days 6–9**: Remaining tools (active connections, slow queries, locks, explain\_query), each with validation + tests as you go.
- **Days 10–12**: Phase 4 agent loop — planning, tool selection, evidence collection, diagnosis generation.
- **Days 13–15**: Phase 5 security hardening pass + Phase 6 observability (Prometheus/Grafana).
- **Days 16–18**: Phase 7 testing sweep, especially adversarial/permission tests.
- **Days 19–21**: Phase 9 polish — README, architecture diagram, example transcripts, screenshots.

This is aggressive but doable if tool-building days stay tight — the risk is scope creep on tool count, not on any single phase being too hard.

## 9. Skills This Demonstrates

For Google/Datadog/Cloudflare-style infra roles specifically: **system design under real constraints** (trust boundaries, not just "it works"), **database internals** (`pg_stat_activity`, `pg_locks`, `pg_stat_statements` — not toy queries), **security engineering as a first-class concern** (defense in depth, least privilege, audit trails — not bolted on), **observability of your own system** (you're not just building an app, you're instrumenting it), **agent/tool-use architecture** (a currently red-hot but often shallow area — you'll have depth most candidates won't), and **honest scoping** (a documented V1/V2 split and a "here's what I tried first and why I changed approach" story from the old repo is a strong signal of engineering maturity, not just output).

Want me to start Phase 1 (the full architecture writeup — MCP client vs. server responsibilities, request/response flow, where exactly the trust boundary sits), or do you want to poke at any of the above first — especially the tool list or the V1/V2 cut, since those are the calls most likely to bite you later if wrong?