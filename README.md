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

## W&B Weave evaluation, replay & promotion OS

W&B Weave is Atlas's **learning layer**, not just a trace viewer. Every completed council
run is captured as a durable, queryable **eval packet**; past decisions become **replay
sets**; and candidate prompt versions are **replayed against incumbents** behind enforced
**promotion gates** so unproven changes can't ship.

- **Eval packets** (`agent/src/weave_eval.py`) — the reliability node scores each run on six
  rubric dimensions (context retrieval, policy grounding, debate quality, CFO synthesis,
  reliability scoring, persistence) as **nested `@weave.op` child spans** under
  `reliability_auditor`, then persists an `EvalPacket` to Redis (`atlas:evaluation:*` +
  `atlas:stream:eval_packets`) and publishes it to Weave.
- **Replay sets** (`agent/src/replay_sets.py`) — built from the decision log (historical +
  live) and published as a live **`weave.Dataset`** (versioned, with a Weave object URL).
- **Promotion gates** (`agent/src/promotion_gates.py`) — a candidate is replayed against a
  set with `weave.Model` + `weave.Scorer` (+ `weave.Evaluation`), scored on
  **reliability / policy compliance / evidence grounding / calibration**, and a `GateDecision`
  is recorded to `atlas:stream:promotions`, published to Weave, and explained in board language.

**Enforced gates** (a change is **blocked** unless all hard gates pass; **approved** only with a
demonstrable gain, else **held for review**):

| Gate | Kind | Rule |
| --- | --- | --- |
| `reliability_no_regression` | hard | Candidate reliability must not fall below incumbent (Δ ≥ 0) |
| `policy_compliance_no_regression` | hard | Policy compliance must not regress (Δ ≥ 0) |
| `evidence_grounding_no_regression` | hard | Evidence grounding must not regress (Δ ≥ 0) |
| `calibration_no_regression` | hard | Decision calibration must not regress (Δ ≥ 0) |
| `coverage` | hard | Replay set must contain ≥ 2 cases |
| `trace_quality` | hard | No malformed/empty candidate predictions |
| `reliability_improvement` | soft | Auto-approval requires reliability Δ ≥ 3; otherwise held for human review |

**REST surface** (read-only dashboards + explicit promotion actions):
`GET /api/evals`, `GET|POST /api/evals/replay-sets`, `GET|POST /api/evals/promotions`,
`GET /api/observability/evals`.

**Smoke check** (creates + lists eval metadata; never prints `WANDB_API_KEY`):

```bash
scripts/eval-smoke.sh                 # create + list eval metadata (redacted)
scripts/eval-smoke.sh promotions      # candidates + recorded gate decisions
scripts/eval-smoke.sh gate --candidate cand-treasury-treasury-v4-liquidity-stress --live
```

**For judges — inspect the Weave link:** open the project Weave URL (printed at agent startup
and surfaced in `/api/health` → `weave.url` and every streamed `learning_report.weave_url`). In
the W&B UI you will find: per-node trace spans with nested eval scorers, published
`atlas-eval-packet-*` objects, the `atlas-replay-*` Dataset, and `atlas-gate-*` GateDecision
objects with incumbent-vs-candidate score deltas. The seven gates above are enforced in
`agent/src/promotion_gates.py`; all four seeded candidates start **blocked** until a live replay
proves an improvement.

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

## Finance operations connectors (data ingestion & reconciliation)

Atlas can ingest **real finance-operations exports** and reconcile them against the
seeded company system of record, surfacing explainable discrepancies. Connectors are
**file-based and optional** — they are not required for the core debate demo, and
nothing is fabricated: an unconfigured connector reports `not_configured` with a
blocker rather than inventing data.

### Connector contracts

Each connector reads a CSV **or** JSON export (auto-detected by extension; JSON may be
a bare array, `{"records": [...]}`, or `{"<source_type>": [...]}`). Set the env var to
an absolute path, or load the bundled Acme demo fixtures with `--demo`.

| Connector (`source_type`) | Env var | Required fields (typed) |
| --- | --- | --- |
| `ledger` | `ATLAS_LEDGER_FILE` | `txn_id, date, account, amount` (+ currency, category, vendor_id/name) |
| `invoices` | `ATLAS_INVOICES_FILE` | `invoice_id, vendor_name, amount` (+ vendor_id, issue/due_date, period, status) |
| `vendor_export` | `ATLAS_VENDOR_EXPORT_FILE` | `vendor_id, name, annual_cost` (+ renewal_date, board_approved, …) |
| `crm_opportunities` | `ATLAS_CRM_FILE` | `opportunity_id, name, stage, arr` (+ probability, weighted_arr, close_date) |
| `headcount_plan` | `ATLAS_HEADCOUNT_FILE` | `team, headcount, monthly_cost` (+ role, start_month, status) |
| `security_evidence` | `ATLAS_SECURITY_FILE` | `control_id, title, status` (+ blocks_revenue, blocked_arr, framework) |
| `board_policy` | `ATLAS_BOARD_POLICY_FILE` | `policy_id, title, text` (+ machine-checkable `rule`/`threshold`) |

Money fields accept `"$28,000"` / `"(1,200)"`; malformed rows are **rejected with
field-level validation errors**, not silently coerced. Imports are **idempotent**
(re-importing an unchanged file is `skipped_unchanged` by SHA-256 checksum) and never
destroy a prior good import.

### Reconciliation workflows

`invoices → vendors` (unmatched/shadow spend) · `contract terms → spend` (annualised
over/underspend) · `CRM pipeline → forecast` (weighted ARR vs the forecast assumption) ·
`headcount → hiring plan` (count/cost drift, unplanned teams) · `policy & board
constraints` (vendor-commitment notification, renewal-review window) · `security →
revenue priority` (controls blocking signed/late-stage revenue). A workflow whose
source is missing reports `insufficient_data` with a blocker — it never assumes a pass.

### Redis namespaces (all under `atlas:`)

```
atlas:source:<connector_id>        JSON  provenance: origin, checksum, schema_version,
                                         source timestamp, counts, reconciliation status
atlas:dataset:<connector_id>       JSON  the validated record payload
atlas:reconciliation:latest        JSON  the most recent reconciliation report
atlas:stream:reconciliation        Stream  append-only reconciliation run log (audit)
```

### Example commands

```bash
# Load the bundled Acme demo operating data (honest provenance: origin=acme-demo-fixture),
# then reconcile. Requires only REDIS_URL (not the LLM/Weave stack).
scripts/import-operations.sh --demo

# Point a connector at a real export and import just that one, then reconcile.
ATLAS_INVOICES_FILE=/abs/path/ap.csv scripts/import-operations.sh --connector invoices

# Direct CLI (run from repo root with the root .env exported):
uv run --directory agent python -m src.integrations.cli status
uv run --directory agent python -m src.integrations.cli import --demo
uv run --directory agent python -m src.integrations.cli reconcile
uv run --directory agent python -m src.integrations.cli inspect invoices
```

REST (read-only, served by `agent/src/api.py`): `GET /api/connectors`, `GET /api/sources`,
`GET /api/sources/{id}`, `GET /api/reconciliation`, `POST /api/reconciliation/run`,
`GET /api/reconciliation/discrepancies[?severity=&kind=]`,
`GET /api/reconciliation/discrepancies/{id}`. The council can also query reconciled facts
through the LangChain tools `list_operations_sources`, `get_reconciliation_summary`,
`list_open_discrepancies`, and `get_operations_data_confidence`.

### Limitations

- **File connectors only.** API-backed connectors (Stripe/NetSuite/Salesforce, …) are a
  planned extension; the `live-api` origin is reserved and never claimed until a real
  integration is wired and verified.
- Invoice→vendor matching is by `vendor_id` or normalised exact name — it does **not**
  fuzzy-guess; unmatched invoices are reported as discrepancies for a human to resolve.
- `board_policy` rules drive the policy thresholds when imported; otherwise the workflow
  falls back to defaults mirroring the seeded board constraints.
- Reconciliation runs on demand (CLI / `POST /api/reconciliation/run`); it is not yet
  triggered automatically after every import.
- The bundled fixtures are **demo data**, opt-in only, and never auto-loaded — see
  `agent/src/data/fixtures/README.md`.

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
  src/data/seed.py    Acme Corp operating dataset + loader (+ eval/replay/gate seeding)
  src/tools.py        finance tools (all grounded in Redis)
  src/api.py          /api/company · /api/vendors · /api/decisions · /api/roster · /api/evals*
  src/weave_eval.py   eval packets + rubric child-span scorers (W&B Weave learning layer)
  src/replay_sets.py  replay sets from decisions, published as live weave.Dataset
  src/promotion_gates.py  candidate-vs-incumbent replay, enforced gates, GateDecision
  src/eval_cli.py     eval/replay/promotion CLI + redacted smoke check
```
