"""
Derive a company financial system-of-record from uploaded operations data.

When Atlas runs upload-driven (no seeded Northwind baseline), the council's
financials have to come from the operator's own files instead of a fixture. This
module reconstructs an internally-consistent company record from the imported
connector datasets:

  • ledger (GL detail) ........ revenue, expenses, burn, gross margin, cash trail
  • headcount plan ............ current (filled) headcount + forward hiring plan
  • crm opportunities ......... pipeline by stage
  • security evidence ......... open control gaps that block revenue
  • board policy .............. board constraints + the runway-floor anchor

Two contracts are honored deliberately:

1. **Internal consistency.** The stored primitives (``mrr``, ``gross_margin``,
   ``opex_monthly``, ``cash_on_hand``) reproduce ``monthly_net_burn`` and
   ``runway_months`` when fed back through ``planning.recompute_current_metrics``
   — the same smoke check the seed satisfied.

2. **No silent fabrication.** A general ledger holds signed *transactions*, never
   a bank balance, so ``cash_on_hand`` cannot be read off the data. It is instead
   *anchored* to the board's runway-floor policy and rolled across the real
   ledger net-flow, with every assumption recorded under the ``derived`` key so
   the figure is auditable rather than invented.

The public surface is :func:`derive_company_record` (loads typed datasets from
Redis) and the pure :func:`build_company_record` (typed lists in, dict out) which
is unit-testable without Redis.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from src.integrations import store
from src.integrations.models import (
    BoardPolicyDoc,
    CrmOpportunity,
    HeadcountPlanRow,
    Invoice,
    LedgerEntry,
    SecurityEvidence,
    SourceType,
)

# The canonical company slug. Kept stable so reliability / self-improvement
# overlays (seeded under this id) keep matching the derived record.
COMPANY_ID = "northwind"

# Default runway floor (months) used only when no board policy supplies one.
# Mirrors src.planning.RUNWAY_FLOOR_MONTHS so the two layers agree.
DEFAULT_RUNWAY_FLOOR_MONTHS = 9.0

# Ledger account / category tokens that mark a line as cost-of-goods-sold for a
# software business (hosting + infrastructure). Everything else negative is opex.
_COGS_TOKENS = ("infrastructure", "hosting", "cloud", "compute", "cogs", "cost of goods", "bandwidth", "data center")

# Recurring-revenue categories — one-time inflows (interest, tax credits, fx,
# accruals) are excluded from MRR so the run-rate is not flattered.
_RECURRING_REVENUE_TOKENS = ("revenue", "subscription", "saas", "recurring")

_SM_TOKENS = ("sales", "marketing", "event", "travel", "advertis", "brand", "gtm", "meals", "demand")


def _round(value: float | None) -> Optional[float]:
    return round(value) if value is not None else None


def _month_key(entry: LedgerEntry) -> Optional[str]:
    if entry.date is None:
        return None
    return entry.date.strftime("%Y-%m")


def _is_cogs(entry: LedgerEntry) -> bool:
    haystack = f"{entry.account or ''} {entry.category or ''}".lower()
    return any(token in haystack for token in _COGS_TOKENS)


def _is_recurring_revenue(entry: LedgerEntry) -> bool:
    haystack = f"{entry.account or ''} {entry.category or ''}".lower()
    return any(token in haystack for token in _RECURRING_REVENUE_TOKENS)


def _account_label(entry: LedgerEntry) -> str:
    """Human-readable bucket key for an expense account (numeric code stripped)."""
    raw = (entry.account or entry.category or "operating").strip()
    parts = raw.split(None, 1)
    if len(parts) == 2 and parts[0].isdigit():
        raw = parts[1]
    return raw.lower().replace(" ", "_").replace("-", "_") or "operating"


def _extract_company_name(ledger: list[LedgerEntry]) -> Optional[str]:
    """Best-effort company name from the ledger's payroll / revenue descriptions.

    Demo exports phrase these as ``"<Company> payroll batch N"`` and
    ``"<Company> customer receipt - ..."`` so the longest common prefix of those
    descriptions recovers the company name without parsing file metadata.
    """
    for selector in (
        lambda e: (e.category or "").lower() == "payroll" or "payroll" in (e.account or "").lower(),
        lambda e: _is_recurring_revenue(e) and e.amount > 0,
    ):
        descriptions = [e.description.strip() for e in ledger if selector(e) and e.description]
        prefix = _common_prefix(descriptions)
        name = _trim_name(prefix)
        if name:
            return name
    return None


def _common_prefix(values: list[str]) -> str:
    if not values:
        return ""
    prefix = values[0]
    for value in values[1:]:
        while not value.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix


def _trim_name(prefix: str) -> Optional[str]:
    cut = prefix.lower()
    for marker in (" payroll", " customer receipt", " customer", " receipt", " payment"):
        idx = cut.find(marker)
        if idx > 0:
            return prefix[:idx].strip(" -–:") or None
    cleaned = prefix.strip(" -–:")
    # Avoid returning a single dangling token that is clearly not a company name.
    return cleaned if len(cleaned) >= 3 and " " in cleaned else None


def _runway_floor(board_policies: list[BoardPolicyDoc]) -> tuple[float, str]:
    for policy in board_policies:
        if (policy.rule or "").strip() == "runway_floor_months" and policy.threshold:
            return float(policy.threshold), f"board policy {policy.policy_id} ({policy.title})"
    return DEFAULT_RUNWAY_FLOOR_MONTHS, "default policy (no runway-floor policy uploaded)"


def _infer_stage(arr: float) -> str:
    if arr <= 0:
        return "Pre-revenue"
    if arr < 1_000_000:
        return "Seed"
    if arr < 10_000_000:
        return "Series A"
    if arr < 50_000_000:
        return "Series B"
    return "Growth"


def _pipeline_by_stage(opportunities: list[CrmOpportunity]) -> list[dict]:
    buckets: dict[str, dict] = defaultdict(lambda: {"opportunities": 0, "arr": 0.0, "weighted_arr": 0.0})
    for opp in opportunities:
        bucket = buckets[opp.stage or "Unspecified"]
        bucket["opportunities"] += 1
        bucket["arr"] += float(opp.arr or 0.0)
        bucket["weighted_arr"] += float(opp.weighted())
    return [
        {
            "stage": stage,
            "opportunities": data["opportunities"],
            "arr": _round(data["arr"]),
            "weighted_arr": _round(data["weighted_arr"]),
        }
        for stage, data in sorted(buckets.items(), key=lambda kv: kv[1]["arr"], reverse=True)
    ]


def _hiring_plan(headcount: list[HeadcountPlanRow]) -> list[dict]:
    buckets: dict[str, dict] = defaultdict(lambda: {"roles": 0, "monthly_cost": 0.0, "start_month": None, "dependency": None})
    for row in headcount:
        if (row.status or "").lower() == "filled":
            continue  # already on payroll — part of current headcount, not the plan
        bucket = buckets[row.team or "Unspecified"]
        bucket["roles"] += int(row.headcount or 0) or 1
        bucket["monthly_cost"] += float(row.loaded_monthly_cost())
        start = row.start_month or (row.planned_start_date.isoformat() if row.planned_start_date else None)
        if start and (bucket["start_month"] is None or start < bucket["start_month"]):
            bucket["start_month"] = start
        if not bucket["dependency"] and row.funding_basis:
            bucket["dependency"] = row.funding_basis
    plan = [
        {
            "team": team,
            "roles": data["roles"],
            "monthly_cost": _round(data["monthly_cost"]),
            "start_month": data["start_month"],
            "dependency": data["dependency"],
        }
        for team, data in buckets.items()
    ]
    plan.sort(key=lambda item: (item["start_month"] or "9999-99"))
    return plan[:8]


def _security_incidents(security: list[SecurityEvidence]) -> list[dict]:
    out: list[dict] = []
    for control in security:
        status = (control.status or "").lower()
        open_gap = status in ("gap", "in_progress", "not_started") or control.blocks_revenue
        if not open_gap:
            continue
        out.append(
            {
                "date": control.evidence_date.isoformat() if control.evidence_date else None,
                "severity": "high" if control.blocks_revenue else "medium",
                "summary": control.summary or control.title,
                "control_id": control.control_id,
                "cash_risk": _round(control.blocked_arr) if control.blocked_arr else None,
                "status": status or "open",
            }
        )
    out.sort(key=lambda item: (item["cash_risk"] or 0), reverse=True)
    return out[:8]


def _board_constraints(board_policies: list[BoardPolicyDoc]) -> list[str]:
    constraints: list[str] = []
    for policy in board_policies:
        text = (policy.text or "").strip()
        if not text:
            continue
        title = (policy.title or "").strip()
        constraints.append(f"{title}: {text}" if title and not text.startswith(title) else text)
    return constraints[:8]


def build_company_record(
    *,
    ledger: list[LedgerEntry],
    headcount: Optional[list[HeadcountPlanRow]] = None,
    opportunities: Optional[list[CrmOpportunity]] = None,
    security: Optional[list[SecurityEvidence]] = None,
    board_policies: Optional[list[BoardPolicyDoc]] = None,
    invoices: Optional[list[Invoice]] = None,  # accepted for symmetry; not yet used
    name: Optional[str] = None,
) -> Optional[dict]:
    """Reconstruct a company financials record from typed upload datasets.

    Returns ``None`` when the ledger lacks the recurring revenue + expense signal
    needed to model a company (e.g. an expense-only test fixture), so the caller
    never overwrites an existing record with a meaningless one.
    """
    headcount = headcount or []
    opportunities = opportunities or []
    security = security or []
    board_policies = board_policies or []

    # --- Monthly revenue / expense / COGS roll-up from the ledger ------------- #
    months: dict[str, dict[str, float]] = defaultdict(lambda: {"revenue": 0.0, "expense": 0.0, "cogs": 0.0})
    opex_by_account: dict[str, float] = defaultdict(float)
    total_revenue = 0.0
    total_expense = 0.0
    total_cogs = 0.0

    for entry in ledger:
        month = _month_key(entry)
        if month is None:
            continue
        amount = float(entry.amount or 0.0)
        if amount > 0:
            if _is_recurring_revenue(entry):
                months[month]["revenue"] += amount
                total_revenue += amount
            # one-time inflows are intentionally ignored for run-rate metrics
        elif amount < 0:
            spend = -amount
            months[month]["expense"] += spend
            total_expense += spend
            if _is_cogs(entry):
                months[month]["cogs"] += spend
                total_cogs += spend
            else:
                opex_by_account[_account_label(entry)] += spend

    ordered_months = sorted(months)
    num_months = len(ordered_months)
    if num_months == 0 or total_revenue <= 0 or total_expense <= 0:
        return None

    monthly_revenue = total_revenue / num_months
    monthly_gross_burn = total_expense / num_months
    cogs_monthly = total_cogs / num_months
    monthly_net_burn = monthly_gross_burn - monthly_revenue

    # gross_margin chosen so recompute reproduces cogs/burn exactly (no clamp).
    gross_margin = round(1.0 - (cogs_monthly / monthly_revenue), 4) if monthly_revenue else 0.0

    opex_monthly = {label: _round(total / num_months) for label, total in opex_by_account.items()}

    # MoM recurring-revenue growth (geometric mean across the window).
    first_rev = months[ordered_months[0]]["revenue"]
    last_rev = months[ordered_months[-1]]["revenue"]
    if num_months >= 2 and first_rev > 0 and last_rev > 0:
        mrr_growth = (last_rev / first_rev) ** (1.0 / (num_months - 1)) - 1.0
        mrr_growth_mom = round(max(-0.5, min(0.5, mrr_growth)), 4)
    else:
        mrr_growth_mom = 0.0

    mrr = round(monthly_revenue)
    arr = round(mrr * 12)

    # --- Headcount (current = filled) ---------------------------------------- #
    filled = sum(int(r.headcount or 0) for r in headcount if (r.status or "").lower() == "filled")
    if filled == 0 and headcount:
        filled = sum(int(r.headcount or 0) for r in headcount)  # fall back to plan total
    headcount_total = filled

    # --- Cash anchor (transparent; GL has no balance) ------------------------ #
    runway_floor_months, runway_floor_source = _runway_floor(board_policies)
    if monthly_net_burn > 0:
        cash_on_hand = round(runway_floor_months * monthly_net_burn)
        runway_months = round(cash_on_hand / monthly_net_burn, 1)
        cash_anchor = (
            f"No cash balance exists in a general ledger, so current cash is anchored to the board "
            f"runway-floor policy ({runway_floor_months:g} months of net burn) and rolled across the "
            f"real ledger net-flow. Source: {runway_floor_source}."
        )
    else:
        # Cash-flow positive over the window — runway is not burn-constrained.
        cash_on_hand = round(runway_floor_months * monthly_gross_burn)
        runway_months = None
        cash_anchor = (
            f"Net cash flow was non-negative across the ledger window (cash-flow positive); cash is "
            f"anchored to {runway_floor_months:g} months of gross burn as an operating buffer. "
            f"Source: {runway_floor_source}."
        )

    # --- Cash trajectory reconstructed from real monthly net flow ------------ #
    cash_history: list[dict] = []
    running = float(cash_on_hand)
    per_month_flow = {m: months[m]["revenue"] - months[m]["expense"] for m in ordered_months}
    for month in reversed(ordered_months):
        cash_history.append({"month": month, "cash": round(running), "net_burn": round(-per_month_flow[month])})
        running -= per_month_flow[month]  # step back one month: undo this month's flow
    cash_history.reverse()

    newest_month = ordered_months[-1]
    record: dict = {
        "id": COMPANY_ID,
        "name": name or _extract_company_name(ledger) or "Your Company",
        "stage": _infer_stage(arr),
        "updated": f"{newest_month}-15",
        "headcount": headcount_total,
        "cash_on_hand": cash_on_hand,
        "monthly_revenue": mrr,
        "cogs_monthly": round(cogs_monthly),
        "opex_monthly": opex_monthly,
        "monthly_gross_burn": round(monthly_gross_burn),
        "monthly_net_burn": round(monthly_net_burn),
        "runway_months": runway_months,
        "mrr": mrr,
        "arr": arr,
        "mrr_growth_mom": mrr_growth_mom,
        "gross_margin": gross_margin,
        "cash_history": cash_history,
        "pipeline_by_stage": _pipeline_by_stage(opportunities),
        "hiring_plan": _hiring_plan(headcount),
        "security_incidents": _security_incidents(security),
        "board_constraints": _board_constraints(board_policies),
        "derived": {
            "basis": "uploaded operations data (ledger, headcount, crm, security, board policy)",
            "window": {"start": ordered_months[0], "end": newest_month, "months": num_months},
            "cash_anchor": cash_anchor,
            "runway_floor_months": runway_floor_months,
            "runway_floor_source": runway_floor_source,
            "gross_margin_basis": "COGS = hosting/infrastructure ledger accounts; opex = remaining spend",
            "source_counts": {
                "ledger": len(ledger),
                "headcount_plan": len(headcount),
                "crm_opportunities": len(opportunities),
                "security_evidence": len(security),
                "board_policy": len(board_policies),
            },
            "note": (
                "Figures are derived from the operator's uploaded files; cash/runway use a documented "
                "board-policy anchor because no bank balance is present in the source data."
            ),
        },
    }
    return record


# --------------------------------------------------------------------------- #
# Redis-backed loader
# --------------------------------------------------------------------------- #
def _load_typed(source_type: SourceType, model):
    out = []
    for row in store.load_dataset(source_type.value):
        try:
            out.append(model.model_validate(row))
        except Exception:
            continue  # rows were validated at import; skip any stragglers
    return out


def derive_company_record() -> Optional[dict]:
    """Build the company record from the currently-persisted upload datasets."""
    ledger = _load_typed(SourceType.LEDGER, LedgerEntry)
    if not ledger:
        return None
    return build_company_record(
        ledger=ledger,
        headcount=_load_typed(SourceType.HEADCOUNT_PLAN, HeadcountPlanRow),
        opportunities=_load_typed(SourceType.CRM_OPPORTUNITIES, CrmOpportunity),
        security=_load_typed(SourceType.SECURITY_EVIDENCE, SecurityEvidence),
        board_policies=_load_typed(SourceType.BOARD_POLICY, BoardPolicyDoc),
        invoices=_load_typed(SourceType.INVOICES, Invoice),
    )
