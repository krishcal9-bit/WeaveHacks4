# Atlas — Strategic Planning Digital Twin & Finance Playbooks

The long-horizon **finance digital twin** of the company. Where the debate graph
(`src/agent.py`) resolves a single decision, this layer lets the council compare
*months of operating futures*: scenario libraries, finance playbooks, decision
portfolios, Monte Carlo stress runs, milestone tracking, and CFO-ready strategic
narratives — all projected forward from the live Redis system of record.

It is deliberately **distinct from the Redis core scenario-storage worker**: this
is the *planning algorithms, playbook composition, and agent-facing reasoning*,
persisted through the existing `redis_layer` primitives (no new Redis schema worker).

## Modules

| File | Responsibility |
| --- | --- |
| `src/planning.py` | Typed models + the deterministic projection engine, plan assembly, milestones, policy/compliance blockers, Redis persistence, and the board narrative (the one model call). |
| `src/playbooks.py` | The 7-playbook library + `compare_playbooks` → `DecisionPortfolio` (deterministic scoring + recommended portfolio). |
| `src/stress_tests.py` | Monte Carlo-style stress runs (seeded → reproducible) + one-variable sensitivity sweeps + the ranked sensitivity suite. |
| `src/tools.py` | LangChain tools the council can call (`build_strategic_plan`, `compare_finance_playbooks`, `run_plan_stress_test`, `run_plan_sensitivity`, `list_finance_playbooks`). |
| `src/agent.py` | Tool wiring only: the `strategic_plan` state field + a CFO-synthesis hook that builds the deterministic plan and grounds the CFO narrative in it. |
| `src/api.py` | REST endpoints for plans, stress, sensitivity, playbook comparison, and board narratives. |
| `frontend/src/lib/{types,api}.ts` | TypeScript types + client methods mirroring the above. |
| `tests/test_planning.py` | 11 deterministic scenario-math smoke checks. |

## Typed models (`src/planning.py`)

`StrategicPlan`, `ScenarioAssumption`, `StressTest`, `DecisionPortfolio`,
`Milestone`, `CapitalPlan`, `PlaybookStep`, `SensitivityResult`, `BoardNarrative`
(plus `MonthProjection`). All are Pydantic and mirrored in `frontend/src/lib/types.ts`.

## Deterministic vs. model-generated (the important part)

> **Rule:** every *number* is computed; the model only writes *prose*, and only
> after the figures are fixed. This keeps the strict-live contract honest — no
> fabricated metrics, sponsor health, or external data.

**Deterministic (pure Python, no LLM, reproducible):**
- The month-by-month projection: **cash, gross/net burn, runway, MRR, ARR,
  churn, pipeline conversion, hiring ramps, vendor savings, financing timing.**
- Accounting identities (validated by smoke checks):
  - `revenue = mrr`
  - `mrr_t = mrr_{t-1}·(1 − churn) + new_business_t`
  - `new_business = base_new_mrr·(conversion / base_conversion)·(1+ramp)^(t-1) + unlocks + hire_revenue`
  - `cogs = revenue·(1 − gross_margin)` · `gross_burn = cogs + opex` · `net_burn = gross_burn − revenue`
  - `runway = cash_end / net_burn` (None when cash-flow positive)
  - `cash_end = cash_begin − net_burn − one_time + financing`
  - `base_new_mrr` is anchored to the **seeded** growth+churn, so the flat case
    (conversion 0, churn 0) reproduces the stored system of record exactly.
- **Milestones** + statuses, **policy/compliance blockers** (runway guardrail,
  $1.5M cash buffer, $150K board-notification / $50K CFO-approval thresholds,
  headcount-discipline) — all checked against the seeded `board_constraints` /
  `atlas:policy:*`.
- **Monte Carlo stress tests** (triangular sampling, fixed RNG seed → identical
  results for a seed) and the **probabilities** of breaching guardrails.
- **Sensitivity** sweeps, near-base elasticity, and most-sensitive ranking.
- **Decision-portfolio** scoring (weighted, min-max-normalized) + the recommended
  primary / no-regret / stabilizer composition.
- Provenance + `calc_metadata` (formulas, thresholds, assumptions, engine version).

**Model-generated (OpenAI, *after* the math):**
- `BoardNarrative` — headline, CFO prose, risk/ask phrasing, recommended decision.
  It receives only the already-computed figures (carried in
  `deterministic_basis`) and is instructed to cite only those.
  Endpoint: `GET /api/plans/{id}/narrative` (the only model-backed planning call;
  enforces strict-live readiness).
- In the council, the **CFO recommendation rationale** (existing behavior) — now
  grounded in the deterministic plan summary that the synthesis node feeds it.

## The 7 playbooks

| id | what it does | key levers (grounded in seed data) |
| --- | --- | --- |
| `extend_runway` | Extend runway without freezing growth | Datadog right-size, Salesforce seats, G&A trim, delay Eng cohort, keep CS |
| `unblock_enterprise` | Unblock enterprise revenue via security spend | SOC 2 audit ($120K + $12K/mo) → release the $994K-weighted procurement pipeline |
| `renegotiate_vendors` | Renegotiate the vendor stack | Datadog, Salesforce, Gong/Figma/GitHub, AWS committed-use |
| `hire_against_revenue` | Hire against signed revenue | CS + Sales tied to the $774K-weighted Contracting pipeline, conversion → 0.36 |
| `financing_bridge` | Prepare a financing bridge | $3M convertible bridge off the $34M post-money, ~8.1% dilution |
| `growth_to_efficiency` | Shift from growth to efficiency | cut S&M $60K/mo, freeze Sales, margin 78%→80%, growth 9%→5% |
| `recover_pipeline` | Recover from pipeline slippage | re-baseline AUD-21 conversion, CS hire to cut churn, technical-validation fast-lane |

Each playbook produces: **assumptions** (`ScenarioAssumption[]`), **required
actions** (`PlaybookStep[]`), **expected financial impact** (the projection +
summary), **risks**, **policy conflicts** (`policy_blockers`), **milestones**, and
**monitoring triggers**.

## Example council prompts (each builds + persists a real 12-month plan)

These contain a strategic trigger (e.g. "12-month", "operating plan", "quarters",
"financial plan") so the CFO synthesis node attaches a deterministic
`strategic_plan`; the keyword also selects the playbook (else the base operating
plan):

- *"Give me a 12-month strategic plan to extend our runway without freezing growth. Show the milestones, capital plan, and policy blockers I should watch."* → `extend_runway`
- *"Build a 12-month operating plan to unblock enterprise revenue through SOC 2 security spend."* → `unblock_enterprise`
- *"Draft a strategic plan over the next 4 quarters to renegotiate our vendor stack."* → `renegotiate_vendors`
- *"Show me a 12-month financial plan to hire against signed revenue."* → `hire_against_revenue`
- *"Prepare a 12-month strategic plan with a financing bridge to keep investing."* → `financing_bridge`
- *"Give me an 18-month strategic plan to shift from growth to efficiency."* → `growth_to_efficiency` (horizon 18)
- *"Map out a strategic plan to recover from pipeline slippage over the next year."* → `recover_pipeline`

## REST API (base: `NEXT_PUBLIC_AGENT_URL`, default `http://localhost:8123`)

```bash
GET  /api/playbooks                      # playbook catalog
GET  /api/plans?limit=25                 # recent persisted plans (cards)
POST /api/plans                          # {horizon_months, playbook?, decision?, title?, assumptions_overrides?}
GET  /api/plans/{plan_id}                # full plan (assumptions, projection, milestones, blockers, provenance)
POST /api/plans/{plan_id}/stress         # {trials?, seed?, horizon_months?} → Monte Carlo bands + breach probs
GET  /api/sensitivity?variable=churn     # one sweep; omit ?variable= for the ranked suite
POST /api/playbooks/compare              # {decision, playbooks?, horizon_months?} → DecisionPortfolio
GET  /api/plans/{plan_id}/narrative      # CFO board narrative (OpenAI; strict-live gated; cached)
```

`?variable=` options: `churn | conversion | gross_margin | hiring_start | vendor_savings | financing_close_month`.

## Persistence (Redis, via existing `redis_layer` primitives)

- `atlas:plan:{id}` — full `StrategicPlan` JSON (RedisJSON) with provenance + calc metadata.
- `atlas:plans:index` — sorted set (recency) for listing.
- `atlas:plan:{id}:narrative` — cached board narrative.
- `atlas:stress:{id}` — stress-run JSON.
- Streams: `atlas:stream:plans`, `atlas:stream:stress`, `atlas:stream:portfolios`.
- Pub/Sub: `atlas:dashboard` published on plan creation.

## Verify

```bash
# Deterministic scenario-math smoke checks (11) — no LLM, needs seeded Redis
uv run --directory agent python -m tests.test_planning

# Live preflight (env, sponsor DNS, OpenAI models, Redis Stack)
scripts/live-preflight.sh

# Frontend (only lib/types.ts + lib/api.ts touched)
npm --prefix frontend run lint
npm --prefix frontend run build

# One manual council prompt that produces a 12-month plan (full live debate)
#   → open the Decision Room and submit one of the example prompts above.
```

If the company record is missing the rich operating fields (hiring plan,
pipeline, board constraints, …) the engine still runs but the plans are thin —
re-seed the canonical dataset first:
`uv run --directory agent python -m src.data.seed`.
