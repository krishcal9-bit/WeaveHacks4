# Atlas — Autonomous Finance Operations

Atlas is an AI **finance council** for real operating decisions. Pose any financial decision —
a vendor renewal, a hire, a capital commitment, a security blocker — and a committee of
role-based agents (Treasury, FP&A, Risk & Audit, Procurement, Reliability Auditor) analyzes it
against Acme Corp's operating data, **debates it like an investment committee**, and the CFO
issues a board-ready, quantified recommendation.

Atlas is framed as Acme Corp's live finance governance system, not a prototype. W&B Weave is
the self-improvement layer: every run produces trace evidence, reliability scores, replay plans,
and prompt-promotion gates.

🔗 **W&B Weave traces:** https://wandb.ai/krishcal9-uc-irvine-anteaters/atlas-finance-os/weave

---

## What it does

- **Open-ended decision input → live multi-agent debate → quantified resolution.** The committee
  argues with real figures and the CFO rules with a confidence score and exact runway/burn impact.
- Ships with seeded Acme Corp operating data: 18-month cash forecast, customer cohorts, pipeline
  risk, hiring plans, vendor obligations, audit findings, incidents, board constraints, and prior
  decision outcomes.
- A Reliability Auditor scores each agent on evidence grounding, forecast calibration, policy
  compliance, debate value, outcome accuracy, confidence calibration, and trace quality.
- Four surfaces: **Executive Dashboard**, **Decision Room** (the debate), **Department** (org chart),
  **Activity** (decision log).

## How the sponsor tech is used

| Tool | Role in Atlas |
| --- | --- |
| **OpenAI** | Powers the agents and policy embeddings from live environment credentials. `LLM_PROVIDER`, `LLM_MODEL`, and `EMBED_MODEL` are configurable, but the demo must not fall back to canned model output. |
| **W&B Weave** | Every agent turn and model call is traced with `weave.init()` plus `@weave.op` spans per committee member (`intake`, `analyst_*`, `debate_round`, `cfo_synthesis`, `reliability_auditor`, `persist`). Reliability scorecards become replay/eval packets for prompt promotion gates. |
| **Redis** (load-bearing) | RedisJSON system-of-record (financials, vendors); RediSearch structured queries; vector RAG over finance policies & past decisions; Streams as the decision log; Pub/Sub for live updates. |
| **CopilotKit** | AG-UI shared-state streaming drives the live boardroom (`useCoAgent`); the Next.js runtime proxies to the FastAPI LangGraph agent. |
| **Cursor** | Project workflow rules in `.cursor/rules/` preserve the strict live-only setup, sponsor checklist, root `.env` contract, and no-secret handling. |

## Live-only contract

Atlas is a strict live system. Do not run or present it with mocked LLM output, fake
Weave traces, a non-Stack Redis server, browser-only data, or hard-coded sponsor responses.
The required live environment keys are loaded from the workspace root `.env`:

- `OPENAI_API_KEY`
- `WANDB_API_KEY`
- `REDIS_URL`

Optional live configuration includes `WANDB_PROJECT`, `WANDB_ENTITY`, `LLM_PROVIDER`,
`LLM_MODEL`, `EMBED_MODEL`, `PORT`, `AGENT_URL`, and `NEXT_PUBLIC_AGENT_URL`. Never commit or
print secret values. `agent/.env.example` lists the expected names; the repeatable scripts use
the root `.env` so the backend and frontend share one local configuration source.

## Architecture

```
Browser (Next.js 16 + CopilotKit)
      │  useCoAgent / sendMessage
      ▼
/api/copilotkit  (CopilotRuntime → LangGraphHttpAgent)
      ▼
FastAPI + LangGraph agent  (:8123, AG-UI)        ← Weave traces every node
      │
      ├── debate graph: intake → treasury → fpna → risk → procurement
      │                 → cross-examination → CFO synthesis → reliability audit → persist
      ├── tools: get_company_financials, compute_runway, list_vendors, search_finance_policies
      └── Redis: JSON records · vendor search · vector RAG · decision stream · pub/sub
```

## Run it

**Prereqs:** Node 18+, [`uv`](https://docs.astral.sh/uv/), Docker Desktop, and a root `.env`
with live sponsor credentials. Redis must be Redis Stack; the repeatable setup uses Docker image
`redis/redis-stack-server:latest`.

```bash
# 1. Configure live keys once. Do not commit the resulting .env.
cp agent/.env.example .env
# Fill OPENAI_API_KEY, WANDB_API_KEY, REDIS_URL, and optional model/project settings.

# 2. Run repeatable live setup.
scripts/live-setup.sh

# 3. Start the FastAPI agent and Next.js app.
scripts/dev-live.sh
#   UI      http://localhost:3000
#   agent   http://localhost:8123
```

`scripts/live-setup.sh` runs the required setup steps in order:

```bash
scripts/start-redis-stack.sh              # Docker: redis/redis-stack-server:latest
npm ci --prefix frontend                  # exact Next/CopilotKit dependency install
uv sync --directory agent                 # exact FastAPI/LangGraph/Weave dependency sync
scripts/live-preflight.sh                 # env/tool/sponsor DNS/Redis Stack checks
scripts/seed-live.sh                      # live OpenAI embeddings + Redis Stack seed
scripts/live-preflight.sh                 # final readiness check after seed
```

If Docker Desktop is closed or the daemon is unavailable, the Redis script stops with:

```text
Docker daemon is not running. Start Docker Desktop, then rerun scripts/start-redis-stack.sh.
```

Manual live commands are also supported as long as root `.env` is exported first:

```bash
set -a; source .env; set +a
scripts/start-redis-stack.sh
npm ci --prefix frontend
uv sync --directory agent
scripts/live-preflight.sh
scripts/seed-live.sh
uv run --directory agent python main.py
npm --prefix frontend run dev:ui
```

## Demo prompts

- *"Should we renew the $180k/yr Datadog contract as-is, or renegotiate it down?"*
- *"Should we hire 5 engineers next quarter (~$95k/mo) or extend runway?"*
- *"A vendor wants $250k upfront for a year of an analytics platform — approve it?"*

## Repo layout

```
frontend/   Next.js 16, CopilotKit, Tailwind v4, Recharts
  src/app/            dashboard (/), decisions, department, activity + /api/copilotkit
  src/components/     app shell, runway chart, ui primitives
  src/lib/            types, formatters, roster, data client
agent/      FastAPI + LangGraph + Weave + Redis (Python 3.12, uv)
  main.py             server: weave.init + AG-UI mount + dashboard data API
  src/agent.py        the multi-agent debate graph
  src/redis_layer.py  Redis: JSON, search, vector RAG, streams, pub/sub, cache
  src/data/seed.py    Acme Corp operating dataset + loader
  src/tools.py        finance tools (all grounded in Redis)
  src/api.py          /api/company · /api/vendors · /api/decisions · /api/roster
```
