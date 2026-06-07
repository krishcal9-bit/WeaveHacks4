"""
Pure (no-Redis) coverage for the upload-driven company derivation.

Parses a real demo upload pack through the production connectors and asserts the
derived company record is sane *and internally consistent* — i.e. the stored
primitives reproduce burn/runway through ``planning.recompute_current_metrics``,
the same contract the seeded baseline had to satisfy.

    uv run --directory agent pytest tests/test_company_derivation.py
    uv run --directory agent python -m tests.test_company_derivation   # standalone
"""

from __future__ import annotations

from pathlib import Path

from src import planning as PL
from src.integrations import connectors as C
from src.integrations.derive_company import COMPANY_ID, build_company_record
from src.integrations.models import (
    BoardPolicyDoc,
    CrmOpportunity,
    HeadcountPlanRow,
    LedgerEntry,
    SecurityEvidence,
)

PACK = Path(__file__).resolve().parents[2] / "demo_uploads" / "verdant-medtech"

_FILE_GLOBS = {
    "ledger": "*GL_Detail*.csv",
    "headcount_plan": "*headcount_plan*.csv",
    "crm_opportunities": "*opportunity_pipeline*.csv",
    "security_evidence": "*security_control_evidence*.json",
    "board_policy": "*board_policy_register*.json",
}


def _parse(connector_id: str):
    spec = C.CONNECTORS[connector_id]
    path = next(PACK.glob(_FILE_GLOBS[connector_id]))
    raw = path.read_bytes()
    fmt = C.detect_format(path)
    records, _issues, _dups, _meta = C.parse_records_with_metadata(spec, raw, fmt)
    return records


def _build() -> dict:
    record = build_company_record(
        ledger=_parse("ledger"),
        headcount=_parse("headcount_plan"),
        opportunities=_parse("crm_opportunities"),
        security=_parse("security_evidence"),
        board_policies=_parse("board_policy"),
    )
    assert record is not None, "derivation returned None for a real upload pack"
    return record


def test_derives_core_financials_from_uploads() -> None:
    rec = _build()
    assert rec["id"] == COMPANY_ID
    assert "Verdant" in rec["name"], rec["name"]
    assert rec["mrr"] > 0 and rec["monthly_revenue"] == rec["mrr"]
    assert rec["arr"] == rec["mrr"] * 12
    assert rec["monthly_gross_burn"] > rec["mrr"]  # the company is burning
    assert rec["monthly_net_burn"] > 0
    assert rec["headcount"] > 0
    assert 0.0 < rec["gross_margin"] < 1.0


def test_cash_anchored_to_board_runway_floor() -> None:
    rec = _build()
    floor = rec["derived"]["runway_floor_months"]
    assert floor == 9.0, "verdant board policy sets a 9-month runway floor"
    # Cash is anchored to the floor, so current runway equals the policy floor.
    assert rec["runway_months"] == floor
    # (cash uses the unrounded burn; stored burn is rounded — allow a few dollars)
    assert abs(rec["cash_on_hand"] - round(floor * rec["monthly_net_burn"])) <= max(10, floor)
    # Trajectory is rebuilt from real monthly net flow, newest = current cash.
    history = rec["cash_history"]
    assert len(history) == rec["derived"]["window"]["months"] == 6
    assert history[-1]["cash"] == rec["cash_on_hand"]


def test_internally_consistent_with_planning_recompute() -> None:
    rec = _build()
    cur = PL.recompute_current_metrics(rec)
    stored_net = float(rec["monthly_net_burn"])
    assert abs(cur["net_burn"] - stored_net) / stored_net < 0.01, (cur, stored_net)
    assert abs(cur["runway_months"] - float(rec["runway_months"])) <= 0.3, cur


def test_pipeline_constraints_and_hiring_present() -> None:
    rec = _build()
    assert rec["pipeline_by_stage"], "expected pipeline derived from CRM opportunities"
    assert rec["board_constraints"], "expected board constraints derived from board policy"
    assert all("stage" in row and "arr" in row for row in rec["pipeline_by_stage"])


def test_expense_only_ledger_yields_no_record() -> None:
    """An expense-only ledger (no recurring revenue) must not fabricate a company."""
    rows = [
        LedgerEntry(txn_id="T1", date="2026-06-01", account="6550 Software", amount=-21500, category="software"),
        LedgerEntry(txn_id="T2", date="2026-06-02", account="6510 Infrastructure", amount=-4200, category="infrastructure"),
    ]
    assert build_company_record(ledger=rows) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all derivation checks passed")
