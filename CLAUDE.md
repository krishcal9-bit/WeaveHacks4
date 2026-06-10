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
cd frontend && npx next dev                  # UI only (NB: `npm run dev`/`dev:ui` start BOTH via concurrently)

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
   `/decisions`, `/roster`, `/health`, `/observability`, and many more). This bypasses CopilotKit
   entirely; see `frontend/src/lib/api.ts`. Additionally, `GET /api/live` (`agent/src/live_feed.py`)
   is an **SSE bridge** over the Redis pub/sub channel `atlas:dashboard`: the Executive Overview
   subscribes via `frontend/src/lib/use-live-feed.ts` and refetches the moment a council ruling
   persists, instead of polling aggressively.

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
- **Never remove `turbopack.root` from `frontend/next.config.ts`.** Without it Next infers the git
  repo root and dev mode watches the entire repo: backend/tooling writes during a live council run
  fire a Fast Refresh rebuild storm that pins the browser main thread and freezes the Decision Room.
  This — not CSS/motion animations — was the original cause of the "live run lags/crashes" problem.
- **Dark-first theme default lives in two places**: the pre-paint script in
  `frontend/src/app/layout.tsx` and `resolveTheme()` in `frontend/src/lib/theme.ts`. They must agree
  (dark unless the operator explicitly stored "light") or the toggle icon desyncs from the page.

## Frontend pages & design system

App Router under `frontend/src/app/`, routes wrapped by `AppShell`: `/` (landing), `/overview`
(**Executive Overview** — live KPI hero with count-up serif numerals, cash/runway chart, recent
rulings; auto-refreshes via the `/api/live` SSE feed), `/dashboard` (Data Room: connector uploads +
reconciliation), `/decisions` (the live **AI Council Chamber** — the centerpiece; idle state is the
`DecisionComposer` hero with inline data-gating and a one-click *real* demo reseed), `/activity`
(decision log), `/department` (org chart), `/settings`.

Design system — **"The Ledger & The Press"** (`globals.css`): dark-first "after-hours ledger"
(green-black surfaces, cream ink, oxblood `--accent`, brass `--gilt` trim) with a salmon-newsprint
light theme; fonts Fraunces (display) / Newsreader (serif) / Schibsted Grotesk (sans) / IBM Plex
Mono. **Motion contract** (enforced by convention, do not regress): keyframes animate ONLY
`transform`/`opacity` — never box-shadow, background-position, filter, or layout properties;
continuous loops are reserved for small "live right now" signals (seat pings, waveform, phase spark);
entrances run once with `animation-fill-mode: both`. The Decision Room throttles streamed coagent
state to 250ms for display (`useThrottledValue`) and memoizes every panel — keep both.

## Orchestration engine (optional — `ATLAS_ORCHESTRATOR`, default OFF)

A deep, **opt-in** agent-orchestration layer in `agent/src/orchestration/` that takes the committee
beyond the fixed linear graph. With the flag off the demo is byte-for-byte unchanged; with it on,
`agent.py`'s EOF swaps `graph` for `orchestration.graph.build_orchestrator_graph()` (same `DebateState`,
so the AG-UI bridge + frontend are unchanged), and `api.py`/`data/seed.py` additively mount/seed it.

- **`namespace.py`** — owns the `atlas:orch:*` Redis subtree ONLY (pure/offline-safe key map + guard).
- **`models.py`** — strict structured-output schemas (`ConductorPlan`, `RedTeamReport`, `SeatPosition`,
  `VoteBallot`, `NegotiationOutcome`) + tolerant records (`Topology`, `DebateRound`, `VoteTally`,
  `EpisodicMemoryRecord`, `OrchestrationTrace`, eval types).
- **`store.py`** — on `redis_layer`'s public API: durable/branchable/time-travelable **checkpointer**,
  episodic-**memory** vector index (HNSW/COSINE/1536, hybrid filter+KNN), topology/run JSON stores +
  RediSearch indices, an event **stream** + consumer-group **bus**, pub/sub fan-out, migrations.
- **`conductor.py`** — `@weave.op orch_conductor`: a structured OpenAI call designs the debate **topology**
  per decision (seats incl. on-demand specialists, rounds, fan-out, red-team, loops, convergence
  threshold) → compiled `Topology` (nodes+edges); deterministic fallback on failure.
- **`registry.py`** — base committee reused from `agent.ROSTER` + on-demand specialists (tax/legal/
  hedging/mna).
- **`debate.py`** (+`llm_io.py`) — `@weave.op orch_debate`: multi-round adaptive debate (stance
  migration), deterministic convergence detection, adversarial **red-team** gate with bounded loop-back,
  conflict negotiation, **reliability-weighted voting** + minority reports, CFO synthesis (reuses
  `agent.Recommendation`).
- **`eval.py`** — the **topology is the evaluatable unit**: scorers (grounding/decisiveness/convergence/
  red-team/cost/latency), `evaluate_topology`, pure `gate_decision`, and `promote_if_better` — a
  **promotion gate** so a worse (or insufficiently-better) orchestrator never ships.
- **`graph.py`** — the flag-gated `finance_department` graph: `intake → conduct → debate → persist`,
  streaming onto existing `DebateState` keys + a new `orchestration` key (mirrored in
  `frontend/src/lib/types.ts`); persists checkpoints/trace/episodic-memory/bus to `atlas:orch:*`.
- **`api.py`** — read-mostly `/api/orchestration/*` router (map/topologies/runs/memory/evals/checkpoints
  + POST `/plan`), mounted additively + flag-gated onto `src.api.router`.
- **`seed.py`** — idempotent baseline topologies + episodic precedents:
  `uv run --directory agent python -m src.orchestration.seed` (also wired into `seed()` behind the flag).
- **`control.py`** — operator human-in-the-loop directives (inject/retire seats, force rounds, override
  convergence threshold) in `atlas:orch:control:<thread>`, read cooperatively between debate rounds.
- **`subdebate.py`** — hierarchical/parallel sub-debates: decompose a complex decision into concurrent,
  bus-coordinated sub-committees and aggregate. `graph.py` auto-routes complex decisions (acquisition/
  expansion/… or wide/deep topologies) here; also POST `/api/orchestration/hierarchical`.
- **`selftest.py`** / **`eval_run.py`** — offline regression guard (`python -m src.orchestration.selftest`,
  no model/Redis cost) and a topology A/B CLI; analytics over real runs at `/api/orchestration/observability`.

Same strict rules as the rest of the repo, plus `.cursor/rules/atlas-orchestration.mdc`: live-only (no
fakes), `atlas:orch:*`-only ownership, reuse `agent` contracts via lazy imports, every model step a
`@weave.op`. Run with the engine on: `ATLAS_ORCHESTRATOR=1 scripts/dev-live.sh`.
