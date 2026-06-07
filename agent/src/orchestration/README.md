# Atlas orchestration engine (`ATLAS_ORCHESTRATOR`, default OFF)

An **opt-in, deep agent-orchestration layer** that takes the finance committee beyond
the fixed linear graph in `src/agent.py`. Self-contained: owns the `atlas:orch:*` Redis
subtree, reuses `src.agent` contracts via lazy imports, and activates only behind the
`ATLAS_ORCHESTRATOR` flag — so with the flag off the live demo is byte-for-byte unchanged.

## What it does
A **Conductor** plans the debate *topology* per decision (which seats, how many rounds,
fan-out, red-team, convergence threshold) and seats **specialists** (tax/legal/hedging/mna)
on demand. The **debate engine** runs multi-round adaptive debate with convergence +
stance-migration detection, an adversarial **red-team** gate, conflict **negotiation**, and
**reliability-weighted voting** with minority reports, then the **CFO** synthesizes a ruling.
Runs are **durable in Redis** (branchable/replayable/time-travelable checkpoints), remembered
as **episodic precedent** (vector recall), and the **topology itself is the unit W&B Weave
evaluates** — a **promotion gate** blocks any worse orchestrator from shipping. Operators can
**steer a live debate** (inject/retire seats, force rounds, override threshold), and complex
decisions can be **decomposed into concurrent sub-committees** (bus-coordinated) and aggregated.

## Modules
| file | role |
|---|---|
| `namespace.py` | `atlas:orch:*` key map + ownership guard (pure) |
| `models.py` | strict structured-output schemas + tolerant persistence records |
| `store.py` | checkpointer, episodic-memory vector index, topology/run stores, event stream + consumer-group bus, pub/sub, analytics |
| `conductor.py` | `@weave.op` topology planner (OpenAI) |
| `registry.py` | base `ROSTER` + on-demand specialists |
| `debate.py` / `llm_io.py` | multi-round debate engine + shared structured-call/telemetry |
| `eval.py` | topology scorers + A/B + promotion gate |
| `graph.py` | flag-gated `finance_department` graph (intake→conduct→debate→persist) |
| `api.py` | read-mostly `/api/orchestration/*` + control + observability + hierarchical |
| `control.py` | operator HITL directives |
| `subdebate.py` | hierarchical/parallel sub-debates (bus-coordinated) |
| `seed.py` | idempotent baseline topologies + episodic precedents |
| `selftest.py` | offline pure-logic regression guard (no model/Redis cost) |
| `eval_run.py` | CLI to A/B topologies on the replay set |

## Commands
```bash
# offline regression guard (fast, no model/Redis cost)
uv run --directory agent python -m src.orchestration.selftest

# seed baseline topologies + episodic precedents
uv run --directory agent python -m src.orchestration.seed

# A/B topologies on the replay set (add --promote / --judge / --dry-run)
uv run --directory agent python -m src.orchestration.eval_run

# run the whole app with the engine on
ATLAS_ORCHESTRATOR=1 scripts/dev-live.sh
```

See `.cursor/rules/atlas-orchestration.mdc` for the enforceable invariants and `PROGRESS.md`
for the build log + live-verification evidence.
