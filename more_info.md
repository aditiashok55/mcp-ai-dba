# more_info.md — end-to-end validation walkthrough

> A plain-English log of **what we tested, why we tested it, and where the
> code lives** for anyone landing on this repo for the first time.
>
> If you want the design rationale, read `AI_DBA_Phase_0_Architecture.md`.
> If you want the "how do I run it" reference, read `CLAUDE.md`.
> **This file is the "did it actually work?" story.**

---

## 0. The mental model in 30 seconds

```
┌──────────────┐   MCP-style tool calls   ┌──────────────────────┐
│  LLM         │ ───────────────────────▶ │  8 read-only tools   │
│ (any of 4)   │ ◀─────────────────────── │  (agent/mcp_client)  │
└──────────────┘     JSON + correlation   └────────┬─────────────┘
                     IDs                           │
                                                   ▼
                                        ┌────────────────────┐
                                        │ Postgres (Docker)  │
                                        │ role: ai_dba_ro    │
                                        └────────────────────┘
```

Every layer we validated below answers **one** question about that picture.

---

## 1. Test plan — 5 layers, one question each

| Layer | Question it answers | Where to look |
|---|---|---|
| **1a** | Can the read-only role read? | [`db/init/03_readonly_user.sql`](db/init/03_readonly_user.sql) |
| **1b** | Can the read-only role write? *(should fail)* | same file |
| **1c** | Is `pg_stat_statements` actually loaded? | [`docker-compose.yml`](docker-compose.yml) `command:` |
| **1d** | Do all 8 tools succeed against a real DB? | [`mcp_server/tools/`](mcp_server/tools/) |
| **2**  | Does the agent loop diagnose real chaos? (offline scorer) | [`eval/runner.py`](eval/runner.py) |
| **3**  | Does the loop work with a **live LLM**? | [`agent/loop.py`](agent/loop.py) |
| **4**  | Does the web UI expose the same guarantees? | [`web/backend.py`](web/backend.py) |
| **5**  | Are metrics/dashboards showing real traffic? | [`observability/`](observability/) |

---

## 2. Layer 1 — Trust boundary at the DB, not the app

### 2a. Read-only role can read

**Command:**
```bash
docker exec -i ai-dba-postgres \
  psql -U ai_dba_readonly -d ai_dba -c "SELECT 1;"
```
**Result:** `1` (one row).
**Why we checked:** the whole safety story rests on the DB refusing writes.
If read didn't work either, the app just looks broken. This proves the
role exists and connects.
**Code:** [`db/init/03_readonly_user.sql`](db/init/03_readonly_user.sql)

### 2b. Read-only role blocked on write

**Command:**
```bash
docker exec -i ai-dba-postgres \
  psql -U ai_dba_readonly -d ai_dba \
  -c "CREATE TABLE test_should_fail (id int);"
```
**Result:** `ERROR: permission denied for schema public` ✅
**Why we checked:** this is the difference between a demo and a safe
system. Even if a prompt-injected LLM asks `DROP TABLE`, **Postgres
refuses at the wire level** — no app code needed. This is Rule #1 of the
architecture doc (§ Trust boundaries).

### 2c. `pg_stat_statements` loaded

**Command:**
```bash
docker exec -i ai-dba-postgres \
  psql -U admin -d ai_dba -c "SELECT count(*) FROM pg_stat_statements;"
```
**Result:** `45` (or more).
**Why we checked:** `get_slow_queries` reads this view. If the extension
isn't in `shared_preload_libraries` at container startup, the tool
silently returns empty and the agent will misdiagnose.
**Code:** [`docker-compose.yml`](docker-compose.yml) — see the
`postgres.command` args that pass `-c shared_preload_libraries=pg_stat_statements`.

### 2d. All 8 tools green against real DB

**Command (one-liner runs every tool):**
```bash
METRICS_ENABLED=0 .venv/bin/python -c "
from mcp_server.tools.connection import check_database_connection
from mcp_server.tools.health import get_database_health
from mcp_server.tools.active_connections import get_active_connections
from mcp_server.tools.slow_queries import get_slow_queries
from mcp_server.tools.locks import get_lock_information
from mcp_server.tools.table_statistics import get_table_statistics
from mcp_server.tools.index_usage import get_index_usage
for fn, kw in [(check_database_connection,{}), (get_database_health,{}),
               (get_active_connections,{'limit':5}), (get_slow_queries,{'min_duration_ms':0,'limit':5}),
               (get_lock_information,{'limit':5}), (get_table_statistics,{'limit':5}),
               (get_index_usage,{'limit':5})]:
    out = fn(**kw); print(fn.__name__, out['status'])
"
```
**Result:** all 7 print `status=ok` (there's also `explain_query`, which
needs a SQL string argument — covered by unit tests).
**Why we checked:** unit tests use fakes; this is the first proof that
the tools work end-to-end with a real socket, real role, real
transaction guards.

**Bug we found here → fixed:**
Postgres does **not** accept bind parameters in `SET`. The old code
`cur.execute("SET statement_timeout = %s", (ms,))` blew up with
`syntax error at or near "$1"`.
**Fix:** coerce to `int` and interpolate directly (safe because
`STATEMENT_TIMEOUT_MS` comes from our own config, not user input).
**Code:** [`mcp_server/db.py`](mcp_server/db.py) — see `connect()`.

---

## 3. Layer 2 — Chaos + rule-based agent (offline, deterministic)

**Command:**
```bash
AI_DBA_INTEGRATION=1 METRICS_ENABLED=0 \
  .venv/bin/python -m eval.runner --all --runs 1
```
**Result:**
```
slow_query:          mean=1.00 pass_rate=1/1
lock_contention:     mean=1.00 pass_rate=1/1
idle_in_transaction: mean=0.67 pass_rate=1/1
overall mean score: 0.89
```

**Why we checked:** this is the "measurable accuracy" story for the
blog. We inject known-bad conditions and see if the agent's diagnosis
mentions the right keywords.

**How it works:**
- **Chaos injectors** open admin-role connections that hold slow queries,
  row locks, or idle transactions. Agent still reads via the read-only
  role — trust boundary unchanged.
  **Code:** [`eval/chaos.py`](eval/chaos.py)
- **Rule-based LLM** replaces the real LLM with a hand-written playbook:
  call `get_database_health`, then `get_active_connections`, then
  `get_slow_queries`, then `get_lock_information`, then emit a
  `Diagnosis` — no cloud, no cost, deterministic.
  **Code:** [`eval/rule_llm.py`](eval/rule_llm.py)
- **Scorer** is a transparent bag-of-keywords over the diagnosis text.
  **Code:** [`eval/scorer.py`](eval/scorer.py)

**Bug we found here → fixed:**
`SlowQueryScenario` and `LockContentionScenario` subclassed
`_Background` (which requires `name` in `__init__`) but forgot to define
their own `__init__`. Runner crashed with
`_Background.__init__() missing 1 required positional argument: 'name'`.
**Fix:** added `__init__(self)` that calls `super().__init__(name=...)`.
**Code:** [`eval/chaos.py`](eval/chaos.py)

---

## 4. Layer 3 — Live LLM (Ollama)

**Setup:**
```bash
brew install ollama
brew services start ollama
ollama pull llama3.1              # ~4.9 GB
```

**Command:**
```bash
AI_DBA_LLM=ollama AI_DBA_INTEGRATION=1 METRICS_ENABLED=0 \
  .venv/bin/python -m eval.runner --all --runs 1 --live
```

**Result:**
```
slow_query:          mean=0.00
lock_contention:     mean=0.00
idle_in_transaction: mean=0.33
overall mean score: 0.11
```

**Why we checked:** proves the **loop protocol** is truly LLM-agnostic
(same code path drove Ollama's HTTP `/api/chat` and Anthropic's SDK).

**Why the score is low:** llama3.1 8B is a small local model and it
misdiagnosed the scenarios ("high active connections" instead of "slow
query"). That's *the point of the eval harness* — it gives you a
number that says "this model isn't good enough at this task". Great
blog material: **0.89 (rules) vs 0.11 (llama3.1) vs whatever Claude
scores** — measurable, defensible, reproducible.

**Code:**
- Loop: [`agent/loop.py`](agent/loop.py) → `run()`
- LLM adapters (all 4): [`agent/llm.py`](agent/llm.py) →
  `AnthropicLLM`, `OpenAILLM`, `OllamaLLM`, `FakeLLM`
- Factory that picks one: [`agent/llm.py`](agent/llm.py) → `build_llm()`

**Bonus discovery — Anthropic key was rejected (`401`).**
The **network** now works (we could reach `api.anthropic.com` and got a
proper 401 response, no TLS reset). The key in `$ANTHROPIC_API_KEY`
is stale/invalid. Rotate at
<https://console.anthropic.com/settings/keys> and re-export.
Also: **that key has been pasted into terminal logs twice, please
rotate it regardless.**

---

## 5. Layer 4 — Web UI

**Start it:**
```bash
METRICS_ENABLED=1 .venv/bin/python -m web.backend
# open http://127.0.0.1:8000
```

**API smoke test:**
```bash
curl -s http://127.0.0.1:8000/api/providers   # which LLMs are usable right now
curl -s -X POST http://127.0.0.1:8000/api/diagnose \
  -H "Content-Type: application/json" \
  -d '{"question":"Investigate.","llm":"rule","max_steps":6}'
```
**Result:** JSON `Diagnosis` with **real correlation IDs** as evidence
(same IDs printed by the audit log — you can grep them).

**Why we checked:** the web UI is a thin wrapper over `agent.loop.run`;
if the CLI works and the API returns the same shape, the UI does too.

**Code:**
- FastAPI app: [`web/backend.py`](web/backend.py) → `create_app()`
- Request schema (this is why `provider` was rejected — the field is
  `llm`): [`web/backend.py`](web/backend.py) → `DiagnoseRequest`
- Single-page frontend: [`web/static/`](web/static/) —
  vanilla JS, unwraps `<untrusted>` markers before rendering

**Bug we found here → fixed:**
Web backend never called `ensure_started()` on the Prometheus HTTP
server (only the stdio MCP entrypoint did). Metrics port 9108 stayed
closed even with `METRICS_ENABLED=1`.
**Fix:** call `mcp_server.metrics.ensure_started()` inside
`create_app()`.
**Code:** [`web/backend.py`](web/backend.py) → top of `create_app()`.

---

## 6. Layer 5 — Prometheus + Grafana

**Verify Prometheus scraped the app:**
```bash
curl -s 'http://127.0.0.1:9090/api/v1/query?query=sum(aidba_tool_calls_total)'
# → {"result":[{"value":[...,"80"]}]}
```

**Open the dashboard:**
<http://localhost:3000/d/ai-dba/ai-dba-tools>
Login `admin` / `admin` (skip the password-change prompt).

The provisioned dashboard has 4 panels:
1. Tool call rate per tool per status
2. p95 latency per tool
3. In-flight calls (gauge)
4. Failure ratio (non-ok / total)

**Why we checked:** in production this is your "is the agent
actually behaving?" board — if failure ratio spikes or latency
p95 climbs, something's wrong.

**Code:**
- Metrics defs (naming, help text): [`mcp_server/metrics.py`](mcp_server/metrics.py)
- Scrape config: [`observability/prometheus/prometheus.yml`](observability/prometheus/prometheus.yml)
- Datasource: [`observability/grafana/provisioning/datasources/prometheus.yml`](observability/grafana/provisioning/datasources/prometheus.yml)
- Dashboard JSON: [`observability/grafana/dashboards/ai_dba.json`](observability/grafana/dashboards/ai_dba.json)
  (has `"uid": "ai-dba"` so the URL is stable across rebuilds)

**Two bugs we found here → fixed:**

**(i)** `docker-compose.yml` had
`extra_hosts: ["host.docker.internal:host-gateway"]` on the Prometheus
service. On **macOS**, Docker Desktop already provides
`host.docker.internal` and points it at the mac host; the `host-gateway`
override *replaced* that with `172.17.0.1` (the Linux bridge), which is
unreachable from macOS. Result: scrape target `down`.
**Fix:** commented out `extra_hosts` (kept a note for Linux users).

**(ii)** Grafana provisioning is one-shot — once a dashboard is in its
SQLite, changes to the JSON are ignored unless the volume is wiped or
the JSON has a stable `uid`. Added `"uid": "ai-dba"` and reset the
grafana volume once so the URL is now stable:
`http://localhost:3000/d/ai-dba/ai-dba-tools`.

---

## 7. What we did NOT do (blog-honest scope)

- **Anthropic live run** — key was rejected. Once you have a fresh key,
  re-export and run:
  ```bash
  AI_DBA_LLM=anthropic AI_DBA_INTEGRATION=1 \
    .venv/bin/python -m eval.runner --all --runs 1 --live
  ```
- **OpenAI live run** — no key exported.
- **Multi-run statistical significance** — we ran `--runs 1`. For the
  blog we'd want `--runs 5` per scenario to show variance.

---

## 8. Reproducibility cheat-sheet

Everything above, in order, in one script:

```bash
# 0. Prereqs
brew services stop postgresql@14      # if you have host Postgres on 5432
docker compose up -d                  # postgres, prometheus, grafana

# 1. DB trust boundary
docker exec ai-dba-postgres psql -U ai_dba_readonly -d ai_dba -c "SELECT 1;"
docker exec ai-dba-postgres psql -U ai_dba_readonly -d ai_dba -c "CREATE TABLE x(id int);" || echo "denied ✓"
docker exec ai-dba-postgres psql -U admin -d ai_dba -c "SELECT count(*) FROM pg_stat_statements;"

# 2. Tests + eval
METRICS_ENABLED=0 .venv/bin/python -m pytest -q
AI_DBA_INTEGRATION=1 METRICS_ENABLED=0 .venv/bin/python -m eval.runner --all --runs 1

# 3. Live LLM (Ollama)
brew install ollama && brew services start ollama && ollama pull llama3.1
AI_DBA_LLM=ollama AI_DBA_INTEGRATION=1 METRICS_ENABLED=0 \
  .venv/bin/python -m eval.runner --all --runs 1 --live

# 4. Web UI (leave running)
METRICS_ENABLED=1 .venv/bin/python -m web.backend &
open http://127.0.0.1:8000

# 5. Grafana
open http://localhost:3000/d/ai-dba/ai-dba-tools    # admin / admin
```

---

## 9. Files touched during validation

| File | Why |
|---|---|
| [`mcp_server/db.py`](mcp_server/db.py) | Fixed `SET $1` bind-param bug |
| [`eval/chaos.py`](eval/chaos.py) | Added missing `__init__` in two scenarios |
| [`web/backend.py`](web/backend.py) | Wired `ensure_started()` for metrics |
| [`docker-compose.yml`](docker-compose.yml) | Removed macOS-hostile `extra_hosts` |
| [`observability/grafana/dashboards/ai_dba.json`](observability/grafana/dashboards/ai_dba.json) | Pinned `uid` for stable URL |

**Test suite: 56/56 passing after every change.**
