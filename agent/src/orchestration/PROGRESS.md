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
- [~] M5 — debate engine: rounds/convergence/red-team/voting (`debate.py`)   (in progress)
- [ ] M6 — Weave eval + promotion gate (`eval.py`)
- [ ] M7 — REST surface mounted on `api.py` (`api.py`)
- [ ] M8 — flag-gated graph integration + AG-UI streaming (`graph.py` + agent.py EOF)
- [ ] M9 — seed data + `.cursor/rules` + CLAUDE.md docs
- [ ] Infra+verify — Redis Stack up, preflight, real debate (flag off+on), commit

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
- **M5 start**: debate engine.
