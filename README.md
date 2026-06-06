# Atlas — Autonomous Finance Operations

Atlas is an AI **finance department** for startups. Pose any financial decision — a vendor
renewal, a hire, a capital commitment — and a committee of role-based agents (Treasury, FP&A,
Risk & Audit, Procurement) analyzes it against your real numbers, **debates it like an
investment committee**, and the CFO issues a board-ready, quantified recommendation.

Built for **WeaveHacks 4 — Multi-Agent Orchestration**.

🔗 **W&B Weave traces (judges):** https://wandb.ai/krishcal9-uc-irvine-anteaters/atlas-finance-os/weave

---

## What it does

- **Open-ended decision input → live multi-agent debate → quantified resolution.** The committee
  argues with real figures and the CFO rules with a confidence score and exact runway/burn impact.
- Ships with a seeded demo company, **Northwind Robotics** (Series A SaaS), so the agents reason
  over a real balance sheet, vendor contracts, finance policies, and past board decisions.
- Four surfaces: **Executive Dashboard**, **Decision Room** (the debate), **Department** (org chart),
  **Activity** (decision log).

## How the sponsor tech is used

| Tool | Role in Atlas |
| --- | --- |
| **Redis** (load-bearing) | RedisJSON system-of-record (financials, vendors); RediSearch structured queries; **vector RAG** over finance policies & past decisions; **Streams** as the decision log; **Pub/Sub** for live updates. |
| **W&B Weave** | Every agent turn and model call is traced — `weave.init()` + a `@weave.op` span per committee member (`intake`, `analyst_*`, `debate_round`, `cfo_synthesis`, `persist`). |
| **OpenAI (GPT-5.5)** | Powers the agents via LangChain `init_chat_model` (provider-configurable). |
| **CopilotKit** | AG-UI shared-state streaming drives the live boardroom (`useCoAgent`); the Next.js runtime proxies to the LangGraph agent. |

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
      │                 → cross-examination → CFO synthesis → persist
      ├── tools: get_company_financials, compute_runway, list_vendors, search_finance_policies
      └── Redis: JSON records · vendor search · vector RAG · decision stream · pub/sub
```

## Run it

**Prereqs:** Node 18+, [`uv`](https://docs.astral.sh/uv/), Redis Stack
(`brew install redis-stack-server` — bundles RediSearch/RedisJSON/vector).

```bash
# 1. Start Redis (with modules)
redis-stack-server

# 2. Configure keys
cp agent/.env.example agent/.env      # set OPENAI_API_KEY and WANDB_API_KEY

# 3. Seed the demo company into Redis
uv run --directory agent python -m src.data.seed

# 4. Run the agent + UI together
cd frontend && npm install && npm run dev
#   → UI  http://localhost:3000
#   → agent http://localhost:8123
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
  src/data/seed.py    Northwind Robotics dataset + loader
  src/tools.py        finance tools (all grounded in Redis)
  src/api.py          /api/company · /api/vendors · /api/decisions · /api/roster
```
