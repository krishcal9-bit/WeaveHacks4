"""
Atlas — W&B Weave evaluation CLI / smoke check.

Create and list eval metadata (eval packets, replay sets, promotion candidates,
gate decisions) and optionally run a **live** replay — all without ever printing
``WANDB_API_KEY`` or any secret. Every line of output is routed through
:func:`src.env.redact_secrets`, and the smoke command asserts the W&B key never
appears in its own output.

Run from the repo root with the root ``.env`` sourced (``scripts/eval-smoke.sh``
does this for you):

    uv run --directory agent python -m src.eval_cli smoke
    uv run --directory agent python -m src.eval_cli packets [--limit N]
    uv run --directory agent python -m src.eval_cli replay-sets [--create] [--name NAME]
    uv run --directory agent python -m src.eval_cli promotions
    uv run --directory agent python -m src.eval_cli gate --candidate ID [--live] [--replay-set SLUG]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from src.env import load_env, redact_secrets

load_env()


def safe_print(label: str, payload: object = None) -> None:
    """Print a labeled, fully-redacted line (never leaks secrets)."""
    if payload is None:
        print(redact_secrets(label))
        return
    text = json.dumps(payload, default=str, indent=2, ensure_ascii=False)
    print(f"{label}:\n{redact_secrets(text)}")


def _init_weave() -> bool:
    from src.data.seed import _ensure_weave

    initialized = _ensure_weave()
    safe_print(
        "weave",
        {"initialized": initialized, "project": os.getenv("WANDB_PROJECT"), "entity": os.getenv("WANDB_ENTITY")},
    )
    return initialized


def cmd_packets(args: argparse.Namespace) -> int:
    from src import weave_eval as WE

    safe_print("eval_summary", WE.eval_summary())
    packets = WE.list_eval_packets(args.limit)
    safe_print(
        "eval_packets",
        [
            {
                "id": p.get("id"),
                "created_at": p.get("created_at"),
                "decision": p.get("decision_label"),
                "overall_score": p.get("overall_score"),
                "council_average": p.get("council_average"),
                "high_issues": sum(1 for i in (p.get("trace_quality_issues") or []) if (i or {}).get("severity") == "high"),
            }
            for p in packets
        ],
    )
    return 0


def cmd_replay_sets(args: argparse.Namespace) -> int:
    from src import replay_sets as RS

    if args.create:
        published = _init_weave()
        record = RS.create_replay_set(args.name, publish=published)
        safe_print(
            "created_replay_set",
            {k: record.get(k) for k in ("name", "slug", "case_count", "history_cases", "live_cases", "weave")},
        )
    safe_print("replay_summary", RS.replay_summary())
    safe_print("replay_sets", RS.list_replay_sets())
    return 0


def cmd_promotions(args: argparse.Namespace) -> int:
    from src import promotion_gates as PG

    safe_print("promotion_status", PG.promotion_status_summary().get("counts"))
    safe_print("enforced_gates", PG.summarize_gates())
    safe_print(
        "candidates",
        [{"id": c.get("id"), "version": c.get("version_label"), "status": c.get("status")} for c in PG.list_candidates()],
    )
    safe_print(
        "recent_gate_decisions",
        [
            {"candidate": p.get("candidate_label"), "status": p.get("status"), "decided_by": p.get("decided_by")}
            for p in PG.list_promotions(args.limit)
        ],
    )
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    from src import promotion_gates as PG

    candidate = PG.get_candidate(args.candidate)
    if not candidate:
        safe_print("error", {"unknown_candidate": args.candidate, "known": [c["id"] for c in PG.list_candidates()]})
        return 2
    if args.live:
        _init_weave()
        safe_print(f"Running LIVE replay for {args.candidate} (incumbent vs candidate)…")
        result = asyncio.run(
            PG.run_promotion_replay(args.candidate, replay_set=args.replay_set, max_cases=args.max_cases, publish=True)
        )
        decision = result.get("decision") or {}
        safe_print(
            "gate_decision",
            {
                "status": decision.get("status"),
                "incumbent_scores": decision.get("incumbent_scores"),
                "candidate_scores": decision.get("candidate_scores"),
                "score_deltas": decision.get("score_deltas"),
                "board_explanation": decision.get("board_explanation"),
                "weave": result.get("weave"),
            },
        )
    else:
        result = PG.block_unproven_candidate(args.candidate, replay_set=args.replay_set, publish=False)
        decision = result.get("decision") or {}
        safe_print(
            "gate_decision",
            {"status": decision.get("status"), "board_explanation": decision.get("board_explanation")},
        )
        safe_print("hint", "Run with --live to replay this candidate against the set and produce real score deltas.")
    return 0


def cmd_role_distinction(args: argparse.Namespace) -> int:
    from src import role_distinction_eval as RD

    publish = bool(args.publish)
    if publish:
        publish = _init_weave()
    report = RD.run_role_distinction_eval(source="representative")
    persisted = RD.persist_role_distinction_report(
        report,
        artifact_path=None if args.no_artifact else args.artifact,
        redis=not args.no_redis,
        publish=publish,
    )
    payload = report.model_dump(mode="json")
    safe_print(
        "role_distinction_eval",
        {
            "id": payload.get("id"),
            "overall_score": payload.get("overall_score"),
            "passed": payload.get("passed"),
            "case_count": payload.get("case_count"),
            "role_average_scores": payload.get("role_average_scores"),
            "artifact_path": persisted.get("artifact_path"),
            "event_id": persisted.get("event_id"),
            "redis_error": persisted.get("redis_error"),
            "weave": persisted.get("weave"),
            "collapse_flags": [
                {"case": case.get("id"), "flags": case.get("collapse_flags")}
                for case in payload.get("cases", [])
                if case.get("collapse_flags")
            ],
        },
    )
    if not report.passed:
        safe_print("ROLE_DISTINCTION_FAILURE", "One or more roles collapsed below the distinction threshold.")
        return 1
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    """End-to-end metadata smoke check: create + list eval metadata, redacted."""
    from src import promotion_gates as PG
    from src import redis_layer as R
    from src import replay_sets as RS
    from src import weave_eval as WE

    print("== Atlas W&B Weave eval smoke check ==")
    try:
        safe_print("redis_ping", {"ok": R.ping(), "url": redact_secrets(os.getenv("REDIS_URL", ""))})
    except Exception as exc:
        safe_print("redis_error", {"error": redact_secrets(exc)})
        return 1

    published = _init_weave()

    # 1) create eval metadata (idempotent)
    candidates = PG.upsert_candidates_from_prompt_versions()
    safe_print("candidates_registered", [c["id"] for c in candidates])
    replay = RS.ensure_default_replay_set(publish=published)
    safe_print(
        "replay_set",
        {k: replay.get(k) for k in ("slug", "case_count", "history_cases", "live_cases", "weave")},
    )
    blocked = 0
    for candidate in candidates:
        if candidate.get("status") == "proposed" and not candidate.get("last_gate_id"):
            PG.block_unproven_candidate(candidate["id"], publish=False)
            blocked += 1
    safe_print("unproven_candidates_blocked", blocked)

    # 2) list it all back
    safe_print("eval_summary", WE.eval_summary())
    safe_print("replay_summary", RS.replay_summary())
    safe_print("promotion_status", PG.promotion_status_summary().get("counts"))
    safe_print("enforced_gates", [g["name"] for g in PG.summarize_gates()])

    # 3) prove no secret leaked through the smoke output
    wandb_key = os.getenv("WANDB_API_KEY") or ""
    captured = json.dumps(
        {
            "summary": WE.eval_summary(),
            "replay": RS.replay_summary(),
            "promotions": PG.promotion_status_summary(),
        },
        default=str,
    )
    if wandb_key and wandb_key in captured:
        safe_print("SECURITY_FAILURE", "WANDB_API_KEY appeared in eval metadata output")
        return 1
    print("\nSMOKE OK — eval metadata created + listed; WANDB_API_KEY not present in any payload.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval_cli", description="Atlas W&B Weave eval/replay/promotion CLI")
    sub = parser.add_subparsers(dest="command")

    p_smoke = sub.add_parser("smoke", help="create + list eval metadata (redacted, no secret exposure)")
    p_smoke.set_defaults(func=cmd_smoke)

    p_packets = sub.add_parser("packets", help="list recent eval packets")
    p_packets.add_argument("--limit", type=int, default=25)
    p_packets.set_defaults(func=cmd_packets)

    p_replay = sub.add_parser("replay-sets", help="list (or --create) replay sets")
    p_replay.add_argument("--create", action="store_true")
    p_replay.add_argument("--name", default="Board Decision Replay Set")
    p_replay.set_defaults(func=cmd_replay_sets)

    p_promo = sub.add_parser("promotions", help="list promotion candidates + gate decisions")
    p_promo.add_argument("--limit", type=int, default=25)
    p_promo.set_defaults(func=cmd_promotions)

    p_gate = sub.add_parser("gate", help="block (default) or --live replay a candidate through the gates")
    p_gate.add_argument("--candidate", required=True)
    p_gate.add_argument("--replay-set", dest="replay_set", default=None)
    p_gate.add_argument("--live", action="store_true")
    p_gate.add_argument("--max-cases", dest="max_cases", type=int, default=3)
    p_gate.set_defaults(func=cmd_gate)

    p_role = sub.add_parser("role-distinction", help="run the deterministic role-persona distinction harness")
    p_role.add_argument("--artifact", default=str(__import__("src.role_distinction_eval", fromlist=["DEFAULT_ARTIFACT"]).DEFAULT_ARTIFACT))
    p_role.add_argument("--no-artifact", action="store_true", help="do not write a JSON artifact")
    p_role.add_argument("--no-redis", action="store_true", help="skip Redis persistence")
    p_role.add_argument("--publish", action="store_true", help="publish the report object to W&B Weave")
    p_role.set_defaults(func=cmd_role_distinction)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except Exception as exc:  # never leak a secret in a traceback message
        safe_print("error", {"command": args.command, "error": redact_secrets(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
