# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Atlas is an AI **finance department** for WeaveHacks 4 (Multi-Agent Orchestration). A committee of
role-based agents (Treasury, FP&A, Risk & Audit, Procurement) debates a financial decision against a
seeded demo company (Northwind Robotics) and the CFO issues a quantified, board-ready recommendation.
Two processes: a **Python agent** (`agent/`, FastAPI + LangGraph + Weave + Redis, port 8123) and a
**Next.js frontend** (`frontend/`, port 3000).

## Strict live-only contract (read first)

This is a **live sponsor demo**, enforced in code, not just convention. Do **not** add mock model
calls, fake Weave traces, fake Redis data, browser-only fallbacks, or hard-coded sponsor responses —
the same rule lives in `.cursor/rules/atlas-live-only.mdc` and is checked at runtime.

- `agent/src/health.py::require_live_ready()` is called inside the `intake` graph node and **hard-fails
  the whole debate** unless every sponsor is green (OpenAI/LLM key, Weave initialized, Redis Stack with
  RedisJSON+RediSearch, CopilotKit mounted, `.cursor/rules` present).
- The Decision Room UI polls `/api/health` every 15s and **locks decision submission** until ready.
- Secrets come only from the **repo-root `.env`** (authoritative). Required keys: `OPENAI_API_KEY`,
  `WANDB_API_KEY`, `REDIS_URL`. Never print or commit secret values — `redact_secrets()` / `safe_url()`
  in `agent/src/env.py` scrub them from all health responses and error strings; keep it that way.
- Redis **must be Redis Stack** (`redis/redis-stack-server:latest`); plain Redis lacks the JSON/Search
  modules the demo depends on.

## Commands

All commands run from the repo root. Setup/run scripts source the root `.env` themselves.

```bash
# Full repeatable setup (Redis Stack container + installs + seed + preflight, in order)
scripts/live-setup.sh

# Run both processes (sources .env, validates keys, starts FastAPI :8123 + Next.js :3000)
scripts/dev-live.sh

# Alternative run-both via concurrently (does NOT pre-source .env into the shell)
npm --prefix frontend run dev

# Run a single process during iteration
uv run --directory agent python main.py      # agent only (FastAPI + graph)
npm --prefix frontend run dev:ui             # UI only (next dev)

# Install / sync dependencies
uv sync --directory agent                    # exact Python deps (uv, Python 3.12)
npm ci --prefix frontend                      # exact Node deps (Node 18+)

# Reseed Redis with the Northwind dataset (idempotent; needs live OpenAI for embeddings)
uv run --directory agent python -m src.data.seed

# Lint / build the frontend
npm --prefix frontend run lint                # eslint
npm --prefix frontend run build               # next build

# Verify live readiness (env keys, tools, sponsor DNS, Redis Stack modules)
scripts/live-preflight.sh
scripts/start-redis-stack.sh                  # start/create the Redis Stack Docker container
```

There is **no automated test suite** (no pytest/jest config, no test files). Verify changes by running
the app and exercising the Decision Room.

## Architecture

### Two front-to-back paths (do not conflate them)

1. **Debate (stateful, streaming).** Browser `useCoAgent`/`useCopilotChat` →
   Next.js `/api/copilotkit` route (`LangGraphHttpAgent`, server-side) → FastAPI agent mounted at `/`
   over the **AG-UI protocol**. This is how a decision is submitted and how live debate state streams
   back to the UI.
2. **Dashboard data + health (read-only).** Browser fetches **directly** (cross-origin, CORS `*`) from
   `NEXT_PUBLIC_AGENT_URL` → FastAPI REST router `agent/src/api.py` (`/api/company`, `/vendors`,
   `/decisions`, `/roster`, `/health`, `/observability`). This bypasses CopilotKit entirely; see
   `frontend/src/lib/api.ts`.

`AGENT_URL` is the server-side proxy target (used in `route.ts`); `NEXT_PUBLIC_AGENT_URL` is the
browser-side base URL (used in `lib/api.ts` and the Decision Room health poll). Both default to
`http://localhost:8123`.

### The debate graph — `agent/src/agent.py`

A linear LangGraph `StateGraph`:

```
intake → treasury → fpna → risk → procurement → debate → synthesis → persist → END
```

- `DebateState` extends `CopilotKitState`, so its fields stream to the frontend's `useCoAgent`. The
  exact set that streams is `STREAM_STATE_KEYS`; `_emit_patch()` pushes incremental updates mid-node
  via `copilotkit_emit_state` (with version fallbacks).
- Every node is decorated `@weave.op(name=...)`, so the committee appears as named spans in Weave
  (`intake`, `analyst_treasury`, `debate_round`, `cfo_synthesis`, `persist_decision`).
- Agents return reliable JSON via `.with_structured_output()` against Pydantic models `Position`,
  `Rebuttals`, `Recommendation`.
- The CFO emits cost estimates; the `compute_runway` tool then computes the **actual** runway impact
  from the company's real cash record — runway numbers are computed, never hallucinated.
- The LLM is provider-swappable: `llm()` calls `init_chat_model(LLM_MODEL, model_provider=LLM_PROVIDER)`
  (`openai` default, `anthropic` also supported).

### Redis layer (load-bearing) — `agent/src/redis_layer.py`

Everything is namespaced under `atlas:`. Redis is the financial system of record **and** agent memory:

- **RedisJSON** — `atlas:company:northwind` (financials), `atlas:vendor:*` (contracts).
- **RediSearch** — `atlas:idx:vendors` (structured query over JSON docs).
- **Vector index** — `atlas:idx:policies` (HNSW / COSINE / 1536-dim over HASH docs) for semantic RAG
  over finance policies and past board decisions. Embeddings via OpenAI `text-embedding-3-small`,
  packed to FLOAT32 bytes (`to_bytes`).
- **Streams** — `atlas:stream:decisions` (append-only decision log; read by `/api/decisions`).
- **Pub/Sub** — `atlas:dashboard` channel, published when a decision concludes.

The four LangChain tools in `agent/src/tools.py` (`get_company_financials`, `compute_runway`,
`list_vendors`, `search_finance_policies`) are all grounded in this layer. The demo dataset is defined
in `agent/src/data/seed.py`.

### Health / preflight — `agent/src/health.py`

`sponsor_health()` aggregates env + LLM + Weave + Redis + CopilotKit + Cursor status into the payload
served at `/api/health` and `/api/observability`. Weave and CopilotKit readiness are tracked via
module-level status set at startup: `main.py` calls `set_weave_status()` after `weave.init()` and
`mark_copilotkit_mounted()` after mounting the AG-UI endpoint.

## Things that must stay in sync (cross-file gotchas)

- **Agent name `finance_department`** must be identical in `agent/main.py` (`LangGraphAGUIAgent` +
  `mark_copilotkit_mounted`), `frontend/src/app/layout.tsx` (`<CopilotKit agent=…>`),
  `frontend/src/app/api/copilotkit/route.ts` (agents map key), and the `useCoAgent({ name: … })` call
  in `frontend/src/app/decisions/page.tsx`. Changing it in one place breaks the bridge silently.
- **Weave must initialize before `src.agent` is imported.** `main.py` deliberately calls `_init_weave()`
  *before* `from src.agent import graph` so OpenAI auto-instrumentation is in place. Preserve this
  import ordering.
- **The committee roster is duplicated:** `ROSTER` in `agent/src/agent.py` and `ROSTER` in
  `frontend/src/lib/agents.ts` mirror each other — update both.
- **Streaming a new state field to the UI** requires three edits: add it to the `DebateState` class
  *and* to `STREAM_STATE_KEYS` in `agent/src/agent.py`, and to `DebateState` in
  `frontend/src/lib/types.ts`.
- **The frontend is Next.js 16, not the Next.js in your training data** (see `frontend/AGENTS.md`).
  APIs, conventions, and file structure may differ — consult `frontend/node_modules/next/dist/docs/`
  before writing Next-specific code.
- **Do not unpin the langgraph/langchain sub-packages** in `agent/pyproject.toml`; the pins match
  CopilotKit's known-good lock and unpinning causes import errors (see the comment there).

## Frontend pages

App Router under `frontend/src/app/`, four routes wrapped by `AppShell`: `/` (Executive Dashboard),
`/decisions` (the live AI Council / Decision Room — the centerpiece), `/department` (org chart),
`/activity` (decision log). Tailwind v4, Recharts for the runway chart, semantic color tokens
(`positive`/`risk`/`warning`/`info`) defined in `globals.css`.
