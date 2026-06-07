"""
CLI: A/B orchestration topologies on the replay set and print a leaderboard.

    uv run --directory agent python -m src.orchestration.eval_run            # run + leaderboard
    uv run --directory agent python -m src.orchestration.eval_run --judge    # + LLM judge
    uv run --directory agent python -m src.orchestration.eval_run --promote  # gate-promote the winner
    uv run --directory agent python -m src.orchestration.eval_run --dry-run  # list candidates only (no model calls)

Runs REAL debates (model cost) unless --dry-run, so it is an on-demand tool, not part
of the hot demo path. The topology is the evaluatable unit (see eval.py).
"""

import argparse
import asyncio

from src.env import load_env
from src.orchestration import eval as EVAL
from src.orchestration import store as STORE


def _candidates(max_n: int):
    topos = [t for t in STORE.list_topologies() if t.id.startswith("seed-")]
    if not topos:
        from src.orchestration import seed as SEED

        SEED.seed_orchestration()
        topos = [t for t in STORE.list_topologies() if t.id.startswith("seed-")]
    topos.sort(key=lambda t: t.id)
    return topos[:max_n]


async def _main(use_judge: bool, promote: bool, max_n: int, dry_run: bool) -> int:
    load_env()
    dataset = EVAL.default_replay_set()
    candidates = _candidates(max_n)
    print(f"candidates ({len(candidates)}): " + ", ".join(f"{t.name}[{t.id}]" for t in candidates))
    print(f"replay set ({len(dataset)}): " + ", ".join(c.decision_type for c in dataset))
    if dry_run:
        print("dry-run: no debates executed.")
        return 0
    if not candidates:
        print("no candidate topologies to evaluate.")
        return 1

    import weave, os

    if os.getenv("WANDB_API_KEY") and os.getenv("WANDB_PROJECT"):
        weave.init(os.getenv("WANDB_PROJECT"))

    scores = await EVAL.evaluate_topologies(candidates, dataset, use_judge=use_judge)
    print("\n=== TOPOLOGY LEADERBOARD ===")
    for rank, s in enumerate(scores, 1):
        print(f"  {rank}. {s.name:<28} overall={s.overall:.4f}  grounding={s.grounding}  "
              f"decision={s.decision_quality}  conv={s.convergence_speed}  cost={s.cost_score}  (n={s.samples})")

    if promote and len(scores) >= 2:
        incumbent = next(t for t in candidates if t.id == scores[-1].topology_id)
        challenger = next(t for t in candidates if t.id == scores[0].topology_id)
        result = await EVAL.promote_if_better(incumbent, challenger, dataset, use_judge=use_judge,
                                              dataset_label="eval_run-cli")
        print(f"\npromotion gate: {result.gate_rationale}")
        print(f"winner: {result.winner} | promoted: {result.promoted} | eval_id: {result.eval_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="A/B orchestration topologies on the replay set.")
    parser.add_argument("--judge", action="store_true", help="add an LLM judge to decision-quality scoring")
    parser.add_argument("--promote", action="store_true", help="gate-promote the winner over the lowest scorer")
    parser.add_argument("--max", type=int, default=2, help="max candidate topologies to evaluate (default 2)")
    parser.add_argument("--dry-run", action="store_true", help="list candidates + dataset only; no model calls")
    args = parser.parse_args()
    return asyncio.run(_main(args.judge, args.promote, args.max, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
