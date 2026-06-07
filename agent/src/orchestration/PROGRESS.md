# Orchestration engine — build progress (durable, compaction-proof)

This file is the source of truth for the `ATLAS_ORCHESTRATOR` build run. It is
read at the start of every continuation so work resumes from the next unchecked
milestone even after the conversation context is summarized.

## Goal (short)
Build a deep, **opt-in** agent-orchestration engine in a NEW `agent/src/orchestration/`
package + a fresh `atlas:orch:*` Redis namespace, exercising every sponsor (OpenAI,
W&B Weave, Redis Stack, CopilotKit/AG-UI, Cursor) far more deeply — without breaking
the strict live-only contract and without clobbering the sibling editing
`agent.py`/`openai_council.py`/`realtime.py`/`structured_models.py`.

## Isolation contract (do not violate)
- ALL substantive work lives in new files under `agent/src/orchestration/`.
- Owns the `atlas:orch:*` subtree ONLY; never writes other namespaces.
- Shared files (`agent.py`, `api.py`, `tools.py`, `frontend/.../types.ts`) get only the
  smallest **additive** edits, gated behind `ATLAS_ORCHESTRATOR` (default off).
  Each shared-file edit = fresh-read → single atomic write; on "modified since read",
  re-read and retry. **Never revert a sibling's change.**
- Reuse `ROSTER`/`Position`/`llm` from `src.agent` (lazy import); never remove them.
- With the flag OFF the demo graph is byte-for-byte unchanged → committing is always safe.

## Verification policy
- Every new module: `.venv/bin/python -m py_compile` + an import smoke test.
- Pure logic (convergence, voting, topology validation): offline unit checks.
- Live end-to-end (preflight + real debate, flag off→on): requires Redis **Stack**
  (RedisJSON+RediSearch) up. Docker daemon was DOWN at start; warming it up. Until the
  stack is live, live-verify steps are deferred and noted here (the flag-off isolation
  keeps the demo safe regardless).

## Environment snapshot (start of run)
- Branch: `saturday-night`. Sibling actively editing core files (20:41 mtimes).
- Env keys present: OPENAI_API_KEY, WANDB_API_KEY, REDIS_URL, LLM_MODEL, LLM_PROVIDER.
- `REDIS_URL=redis://localhost:6379` (local) — nothing listening; only plain
  `redis-server` installed locally (NOT Stack). Docker installed, daemon starting.
- `uv` + `agent/.venv` present.

## Milestones
- [x] M0 — scaffold package + `namespace.py` + PROGRESS.md
- [x] M1 — typed Pydantic models (`models.py`)
- [x] M2 — Redis store: checkpointer + episodic memory + streams/bus (`store.py`)
- [x] M3 — Conductor topology planner (`conductor.py`)
- [x] M4 — dynamic specialist registry (`registry.py`)
- [x] M5 — debate engine: rounds/convergence/red-team/voting (`debate.py`, `llm_io.py`)
- [x] M6 — Weave eval + promotion gate (`eval.py`)
- [x] M7 — REST surface mounted flag-gated on src/api.py (0 routes off / 9 on, verified)
- [x] M8 — flag-gated graph: agent.py EOF swap verified (off=linear 11-node / on=orchestrator 4-node) + types.ts mirrored
- [x] M9 — seed (`seed.py`, live-verified) + `.cursor/rules/atlas-orchestration.mdc` + CLAUDE.md docs
- [x] Infra+verify — Redis Stack up + seeded; flag-on graph E2E live OK; preflight green (flag off); committed (324a386)
- [x] M10 — operator HITL control: inject/retire seats, force rounds, override threshold (live-verified)
- [x] M11 — analytics endpoint (/api/orchestration/observability) + eval CLI (eval_run) + selftest (26/26)
- [x] M12 — hierarchical/parallel sub-debates (bus-coordinated): decompose → concurrent committees → aggregate (live-verified)
- [x] M13 — auto-route complex decisions to hierarchical mode inside the live graph (live-verified: acquisition → hierarchical, REJECT@91)
- [x] M14 — what-if debate branching (time-travel checkpointer): branch + counterfactual re-run + compare (live-verified: weighting Treasury 3x flipped CONDITIONAL→REJECT)

## Log
- **M0 done**: `__init__.py`, `namespace.py` (atlas:orch:* key map, pure/offline-safe), PROGRESS.md.
  Docker Desktop brought up; Redis Stack container live (RedisJSON+RediSearch confirmed).
- **M1 done**: `models.py` — strict structured-output schemas (ConductorPlan/RedTeamReport/VoteBallot,
  additionalProperties=false) + tolerant records (Topology/DebateRound/VoteTally/EpisodicMemoryRecord/
  OrchestrationTrace/eval). Verified: round-trips + strict-schema check.
- **M2 done**: `store.py` — checkpointer (save/time-travel/branch), episodic vector memory (HNSW/COSINE/1536,
  hybrid filter+KNN), topology/run JSON stores + RediSearch indices, event stream + consumer-group bus,
  pub/sub fan-out, migrations, overview. Verified LIVE against Redis Stack (recall score 0.64; filter excludes
  non-matching decision_type). Gotcha fixed: RediSearch `Document` reserves field name `payload` → renamed to
  `record_json`.
- **M3 done**: `conductor.py` — structured OpenAI call (reuses src.agent.llm, lazy) → ConductorPlan →
  compiled Topology (conductor→seats→[red_team(+loop)]→vote→synthesis). @weave.op `orch_conductor`.
  Verified LIVE: cross-border M&A → 5 rounds, red-team, loop-back, threshold 0.78, seated all 4 specialists
  (mna/legal/tax/hedging) + base committee; rationale grounded in real figures; real Weave span logged.
  Known weave-side warning (LegacyAPIResponse.model_dump in weave's OpenAI responses/v1 capture) — pre-exists
  in main app's llm(); does not affect correctness.
- **M4 done**: `registry.py` — base committee reused from src.agent.ROSTER (lazy + offline mirror) +
  on-demand specialists (tax/legal/hedging/mna) with mandates & system prompts. seat_persona/resolve_seats
  (dedup, drop-unknown)/suggest_specialists. Verified offline + live ROSTER reuse.
- **M5 done**: `llm_io.py` (shared structured-call + telemetry + cost estimate) + `debate.py` — multi-round
  adaptive debate (seats see prior round, migrate stance), deterministic convergence + stance-migration,
  red-team gate with bounded loop-back, conflict negotiation, reliability-weighted voting + minority reports,
  CFO synthesis (reuses src.agent.Recommendation). @weave.op `orch_debate`. Verified offline (convergence/
  weighted-tally/migration) + LIVE (Datadog case: converged round 1, weighted CONDITIONAL, CFO ruled
  CONDITIONAL@90, real Weave span, $0.15).
- **M6 done**: `eval.py` — topology is the evaluatable unit; scorers (grounding/decisiveness/convergence/
  red-team/cost/latency), `evaluate_topology`/`evaluate_topologies`, pure `gate_decision`, `promote_if_better`
  (persists eval + promotion). @weave.op spans. Verified offline (good 0.935 vs bad 0.295; gate blocks on
  grounding regression) + LIVE A/B (challenger 0.8955 vs incumbent 0.8771 → BLOCKED, gain 0.0184 < 0.02).
- **M7 module done**: `api.py` — read-mostly /orchestration/* router (map/topologies/runs/memory/evals/
  checkpoints + POST /plan). Compiles. Wiring into src/api.py (flag-gated include_router) pending.
- **M8 module done**: `graph.py` — flag-gated orchestration graph (intake→conduct→debate→persist) over a
  lazily-defined DebateState subclass (adds `orchestration` key); streams onto existing DebateState keys +
  checkpoints/trace/memory/bus to Redis. Builds (CompiledStateGraph, 4 nodes).
- **Wiring done (additive, flag-gated, fresh-read; sibling files unchanged since 20:41)**:
  - `src/api.py` flag-gated `include_router(orchestration_router)` — verified 0 routes off / 9 on.
  - `src/agent.py` EOF flag-gated graph swap (try/except fallback) — verified off=linear (11 nodes) /
    on=orchestrator (4 nodes), with activation log line.
  - `src/data/seed.py` flag-gated `seed_orchestration()` + summary key.
  - `frontend/src/lib/types.ts` optional `orchestration?: OrchestrationView` + interfaces.
  - `CLAUDE.md` orchestration subsystem section.
- **M9 done**: `seed.py` (4 baseline topologies + 3 precedents; idempotent; live recall verified) +
  `.cursor/rules/atlas-orchestration.mdc`.
- **FLAG-ON GRAPH E2E (live) OK**: Datadog renewal → Conductor seated 5 seats (incl. legal specialist),
  3-round red-team topology → 3 rounds → CONDITIONAL (CFO @84); trace+memory+bus persisted
  (run-85eb…, 3 checkpoints); episodic recall after run OK. All sponsors exercised through one flag.
- **DEMO GREEN (flag off)**: `scripts/live-preflight.sh` PASSED — env keys, OpenAI models, Redis Stack
  (JSON/Search/TS), financial-OS indices, scenario branch — unaffected by the additive flag-off wiring.
- **M10 done**: `control.py` — operator HITL directives in `atlas:orch:control:<thread>` (inject/retire seats,
  force rounds [one-shot], override threshold), read cooperatively between rounds in `run_debate(control_thread=…)`,
  wired through `graph._debate_node` + POST/GET `/api/orchestration/control/{thread}`. Verified offline
  (apply_seats, one-shot force) + LIVE (operator injected `risk` + forced +1 round → 2 rounds, risk seated).
  Also: the debate node now returns final positions/transcript for a rich end-state snapshot.
- **Regression guard**: `selftest.py` — 23 offline pure-logic checks (models, topology compile, convergence,
  weighted tally, eval scoring + gate, seat control); zero model/Redis cost. `python -m src.orchestration.selftest` → 23/23.
- **M11 done**: `store.orch_analytics()` + GET `/api/orchestration/observability` (cost/latency/tokens/convergence-rate/
  decision+stop mix/red-team rate/by-topology over real persisted runs — Redis reads only) + `eval_run.py` CLI
  (A/B seeded topologies → leaderboard; `--promote/--judge/--dry-run`). Verified: analytics live, 12 orch routes (flag on),
  `--dry-run` lists candidates. Frontend `eslint` exit 0 (types.ts edit valid).
- **M12 done**: `subdebate.py` — hierarchical/parallel sub-debates: decompose → concurrent sub-committees
  (bus-coordinated) → aggregate. New `Decomposition`/`HierarchicalTrace` models, `atlas:orch:hrun:*`,
  GET/POST `/api/orchestration/hierarchical`, `@weave.op` orch_hierarchical/decompose/aggregate. Selftest 26/26.
  LIVE: "Open a Berlin sales office?" → 4 sub-decisions (affordability/runway, ROI/CAC, operating-model,
  tax+compliance) → 4 concurrent committees [REJECT,REJECT,CONDITIONAL,CONDITIONAL] → CFO aggregate REJECT@90,
  grounded in $4.2M cash / $310k burn; 4 sub-traces + 1 hrun persisted; $0.28. Package `README.md` added.
- **M13 done**: `graph.py` deterministic complexity router auto-sends complex decisions to hierarchical mode
  inside the live graph (cost-bounded lean sub-committees); persist now writes episodic memory + decision event
  for BOTH single and hierarchical paths. LIVE: "$6M acquisition to expand into Europe" → mode=hierarchical,
  4 sub-committees [REJECT,REJECT,CONDITIONAL,CONDITIONAL] → REJECT@91, hrun persisted, $0.26. CLAUDE.md synced.
- **M14 done**: `whatif.py` — what-if branching via the time-travel checkpointer: branch a persisted run's
  thread, counterfactually re-run with altered reliability weights, compare. POST /api/orchestration/whatif.
  LIVE: weighting Treasury 3x flipped the Datadog renewal CONDITIONAL→REJECT (branch thread + lineage persisted).
  Now ALL orchestration infra is exercised end-to-end (incl. branch / time-travel).

## Status: every explicit goal bullet delivered + live-verified; demo green with the flag off.
All 11 modules (namespace, models, store, conductor, registry, debate, llm_io, eval, graph, api, control, seed)
+ flag-gated wiring committed (40dcea1, cfa07ef, cbd3020, 324a386, + M10). OpenAI (Conductor/dynamic roster/
multi-round/red-team/negotiation/weighted-vote), W&B Weave (topology-as-eval-unit + promotion gate + span trees),
Redis Stack (checkpointer/episodic vector memory/streams+consumer-group bus/pubsub), CopilotKit/AG-UI (streamed
orchestration state + operator HITL), Cursor (rules) — all exercised deeper, behind ATLAS_ORCHESTRATOR (default off).
