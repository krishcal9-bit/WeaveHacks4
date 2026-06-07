"""
Command-line entrypoint for finance-operations connectors.

    uv run --directory agent python -m src.integrations.cli status
    uv run --directory agent python -m src.integrations.cli import --demo
    uv run --directory agent python -m src.integrations.cli import --connector invoices --file /path/to/ap.csv
    uv run --directory agent python -m src.integrations.cli reconcile
    uv run --directory agent python -m src.integrations.cli inspect invoices
    uv run --directory agent python -m src.integrations.cli run --demo   # import + reconcile

Output is redacted (no secrets, file paths scrubbed of credentials) and honest:
unconfigured connectors are shown as such rather than backfilled with data.
"""

from __future__ import annotations

import argparse
import json
import sys

from src.env import load_env, safe_url
from src import redis_layer as R
from src.integrations import connectors as C
from src.integrations import service


def _require_redis() -> None:
    if not R.ping():
        print(f"[connectors] Redis is not reachable at {safe_url(R.REDIS_URL)}", file=sys.stderr)
        raise SystemExit(2)


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_status(_: argparse.Namespace) -> int:
    _require_redis()
    _print({
        "connectors": service.connector_statuses(),
        "confidence": service.import_confidence().model_dump(mode="json"),
    })
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    _require_redis()
    connector_ids = [args.connector] if args.connector else None
    results = service.run_import(
        connector_ids,
        demo=args.demo,
        explicit_path=args.file,
    )
    summary = [
        {
            "connector": r.provenance.connector_id,
            "status": r.provenance.status.value,
            "origin": r.provenance.origin.value,
            "accepted": r.provenance.accepted_count,
            "rejected": r.provenance.rejected_count,
            "duplicates": r.provenance.duplicate_count,
            "blockers": r.provenance.blockers,
        }
        for r in results
    ]
    _print({"imported": summary})
    return 0


def cmd_reconcile(_: argparse.Namespace) -> int:
    _require_redis()
    service.apply_company_derivation()
    report = service.run_reconciliation()
    _print({
        "run_id": report.run_id,
        "status": report.status,
        "counts_by_severity": report.counts_by_severity,
        "workflows": [w.model_dump(mode="json") for w in report.workflows],
        "discrepancies": [d.model_dump(mode="json") for d in report.discrepancies],
        "blockers": report.blockers,
        "confidence": report.confidence.model_dump(mode="json"),
    })
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    _require_redis()
    if args.connector not in C.CONNECTORS:
        print(f"unknown connector: {args.connector}; valid: {', '.join(C.CONNECTORS)}", file=sys.stderr)
        return 2
    source = service.get_source(args.connector, sample=args.sample)
    if not source:
        _print({"connector": args.connector, "status": "no_source_persisted"})
        return 0
    _print(source)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    rc = cmd_import(args)
    if rc != 0:
        return rc
    return cmd_reconcile(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atlas-connectors", description="Atlas finance-operations connectors")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show connector configuration + import confidence").set_defaults(func=cmd_status)

    p_import = sub.add_parser("import", help="Import configured connectors (or --demo fixtures)")
    p_import.add_argument("--connector", help=f"one of: {', '.join(C.CONNECTORS)}")
    p_import.add_argument("--file", help="explicit source file (requires --connector)")
    p_import.add_argument("--demo", action="store_true", help="load bundled Acme demo fixtures")
    p_import.set_defaults(func=cmd_import)

    sub.add_parser("reconcile", help="Run reconciliation over imported data").set_defaults(func=cmd_reconcile)

    p_inspect = sub.add_parser("inspect", help="Show provenance + a sample for one connector")
    p_inspect.add_argument("connector")
    p_inspect.add_argument("--sample", type=int, default=10)
    p_inspect.set_defaults(func=cmd_inspect)

    p_run = sub.add_parser("run", help="Import then reconcile")
    p_run.add_argument("--connector")
    p_run.add_argument("--file")
    p_run.add_argument("--demo", action="store_true")
    p_run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
