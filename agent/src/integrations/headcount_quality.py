"""Parser-derived quality signals for HRIS/headcount planning exports."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any


AS_OF_DATE = date(2026, 6, 15)
NEXT_90_DAYS = 90


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    text = str(value).strip()
    if len(text) == 7 and text[4] == "-":
        text = f"{text}-01"
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _start_date(row: dict[str, Any]) -> date | None:
    return (
        _parse_date(row.get("actual_start_date"))
        or _parse_date(row.get("current_start_date"))
        or _parse_date(row.get("start_date"))
        or _parse_date(row.get("start_month"))
    )


def _loaded_cost(row: dict[str, Any]) -> float:
    return _float(row.get("fully_loaded_monthly_cost") or row.get("monthly_cost"))


def _is_contractor(row: dict[str, Any]) -> bool:
    return _norm(row.get("employment_type")) in {"contractor", "consultant", "temp", "temporary"}


def _is_backfill(row: dict[str, Any]) -> bool:
    role_type = _norm(row.get("role_type"))
    return role_type == "backfill" or bool(row.get("backfill_for"))


def summarize_headcount_quality(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return compact, deterministic quality signals for headcount provenance."""
    if not records:
        return {}

    status_counts: Counter[str] = Counter()
    employment_counts: Counter[str] = Counter()
    role_type_counts: Counter[str] = Counter()
    approval_counts: Counter[str] = Counter()
    start_months: Counter[str] = Counter()
    loaded_cost_by_team: dict[str, float] = defaultdict(float)

    total_headcount = 0
    total_base_monthly_cost = 0.0
    total_loaded_monthly_cost = 0.0
    next_90_day_loaded_cost = 0.0
    recruiting_slip_count = 0
    recruiting_slip_days_total = 0
    contractor_count = 0
    backfill_count = 0
    partial_approval_count = 0
    unapproved_count = 0
    department_mapping_drift_count = 0
    approval_risk_loaded_cost = 0.0

    for row in records:
        count = _int(row.get("headcount"))
        total_headcount += count
        base_cost = _float(row.get("monthly_cost"))
        loaded_cost = _loaded_cost(row)
        total_base_monthly_cost += base_cost
        total_loaded_monthly_cost += loaded_cost

        status = _norm(row.get("status")) or "unknown"
        employment_type = _norm(row.get("employment_type")) or "fte"
        role_type = _norm(row.get("role_type")) or "net new"
        approval = _norm(row.get("approval_status")) or "unknown"
        status_counts[status] += count or 1
        employment_counts[employment_type] += count or 1
        role_type_counts[role_type] += count or 1
        approval_counts[approval] += count or 1

        team = str(row.get("team") or "").strip()
        mapped_team = str(row.get("mapped_team") or row.get("canonical_team") or "").strip()
        team_key = mapped_team or team
        loaded_cost_by_team[team_key] += loaded_cost
        if mapped_team and _norm(mapped_team) != _norm(team):
            department_mapping_drift_count += 1

        start = _start_date(row)
        if start:
            start_months[f"{start.year:04d}-{start.month:02d}"] += count or 1
            if 0 <= (start - AS_OF_DATE).days <= NEXT_90_DAYS:
                next_90_day_loaded_cost += loaded_cost

        slip_days = _int(row.get("recruiting_slippage_days"))
        if slip_days > 0:
            recruiting_slip_count += 1
            recruiting_slip_days_total += slip_days

        if _is_contractor(row):
            contractor_count += count or 1
        if _is_backfill(row):
            backfill_count += count or 1
        approved_count = _int(row.get("approved_headcount"))
        if approval in {"partial", "partially approved", "partially approved roles"} or (approved_count and approved_count < count):
            partial_approval_count += 1
            approval_risk_loaded_cost += loaded_cost * max(0, count - approved_count) / max(1, count)
        elif approval in {"pending", "unapproved", "not approved", "missing", "unknown"}:
            unapproved_count += 1
            approval_risk_loaded_cost += loaded_cost

    issue_count = (
        recruiting_slip_count
        + contractor_count
        + backfill_count
        + partial_approval_count
        + unapproved_count
        + department_mapping_drift_count
    )

    return {
        "records": len(records),
        "total_headcount": total_headcount,
        "issue_count": issue_count,
        "total_base_monthly_cost": round(total_base_monthly_cost, 2),
        "total_loaded_monthly_cost": round(total_loaded_monthly_cost, 2),
        "next_90_day_loaded_cost": round(next_90_day_loaded_cost, 2),
        "recruiting_slip_count": recruiting_slip_count,
        "recruiting_slip_days_total": recruiting_slip_days_total,
        "contractor_count": contractor_count,
        "backfill_count": backfill_count,
        "partial_approval_count": partial_approval_count,
        "unapproved_count": unapproved_count,
        "department_mapping_drift_count": department_mapping_drift_count,
        "approval_risk_loaded_cost": round(approval_risk_loaded_cost, 2),
        "status_counts": dict(sorted(status_counts.items())),
        "employment_type_counts": dict(sorted(employment_counts.items())),
        "role_type_counts": dict(sorted(role_type_counts.items())),
        "approval_status_counts": dict(sorted(approval_counts.items())),
        "start_month_counts": dict(sorted(start_months.items())),
        "loaded_cost_by_team": dict(sorted((team, round(cost, 2)) for team, cost in loaded_cost_by_team.items())),
        "recommended_actions": [
            "Reconcile start-date slippage before accepting hiring-plan cash timing.",
            "Separate contractors and backfills from net-new capacity in FP&A scenarios.",
            "Block or condition partially approved and unapproved roles until approval provenance is attached.",
        ],
        "fields_persisted": [
            "mapped_team",
            "fully_loaded_monthly_cost",
            "planned_start_date",
            "current_start_date",
            "actual_start_date",
            "recruiting_slippage_days",
            "employment_type",
            "role_type",
            "backfill_for",
            "approval_status",
            "approved_headcount",
            "approval_id",
        ],
    }
