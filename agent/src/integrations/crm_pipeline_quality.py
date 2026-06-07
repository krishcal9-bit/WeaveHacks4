"""Parser-derived quality signals for CRM opportunity exports."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any


AS_OF_DATE = date(2026, 6, 15)
STALE_ACTIVITY_DAYS = 45
STAGE_AGING_DAYS = 60


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _slipped(row: dict[str, Any]) -> bool:
    current = _parse_date(row.get("close_date"))
    prior = _parse_date(row.get("prior_close_date") or row.get("original_close_date"))
    return bool(current and prior and current > prior)


def _days_in_stage(row: dict[str, Any]) -> int:
    explicit = row.get("days_in_stage")
    if explicit not in (None, ""):
        try:
            return int(float(explicit))
        except (TypeError, ValueError):
            return 0
    entered = _parse_date(row.get("stage_entered_date"))
    return max(0, (AS_OF_DATE - entered).days) if entered else 0


def _is_stale(row: dict[str, Any]) -> bool:
    last_activity = _parse_date(row.get("last_activity_date"))
    if last_activity:
        return (AS_OF_DATE - last_activity).days > STALE_ACTIVITY_DAYS
    return _days_in_stage(row) > STAGE_AGING_DAYS


def _weighted(row: dict[str, Any]) -> float:
    explicit = row.get("weighted_arr")
    if explicit not in (None, ""):
        return _float(explicit)
    return _float(row.get("arr")) * _float(row.get("probability"))


def summarize_pipeline_quality(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return compact, deterministic quality signals for CRM provenance."""
    if not records:
        return {}

    account_names: dict[str, set[str]] = defaultdict(set)
    stage_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    close_months: Counter[str] = Counter()
    total_arr = 0.0
    weighted_arr = 0.0
    slipped = 0
    aged = 0
    stale = 0
    owner_changes = 0
    probability_overrides = 0
    weighted_mismatches = 0
    missing_probability = 0
    renewal_arr_at_risk = 0.0
    expansion_arr = 0.0
    new_business_arr = 0.0

    for row in records:
        stage = str(row.get("stage") or "unknown").strip()
        stage_counts[stage] += 1
        opp_type = _norm(row.get("opportunity_type")) or ("renewal" if row.get("is_renewal") else "expansion" if row.get("is_expansion") else "unknown")
        type_counts[opp_type] += 1
        account_key = str(row.get("account_id") or row.get("parent_account") or row.get("account") or "").strip()
        account_name = str(row.get("account") or "").strip()
        if account_key and account_name:
            account_names[account_key].add(account_name)
        arr = _float(row.get("arr"))
        total_arr += arr
        weighted = _weighted(row)
        weighted_arr += weighted
        probability = row.get("probability")
        if probability in (None, ""):
            missing_probability += 1
        expected = arr * _float(probability)
        if probability not in (None, "") and row.get("weighted_arr") not in (None, "") and abs(weighted - expected) > max(5_000.0, arr * 0.05):
            weighted_mismatches += 1
        if _slipped(row):
            slipped += 1
        if _days_in_stage(row) > STAGE_AGING_DAYS:
            aged += 1
        if _is_stale(row):
            stale += 1
        if row.get("previous_owner") and row.get("owner") and row.get("previous_owner") != row.get("owner"):
            owner_changes += 1
        if row.get("probability_override") not in (None, "") or row.get("probability_override_reason"):
            probability_overrides += 1
        close_date = _parse_date(row.get("close_date"))
        if close_date:
            close_months[f"{close_date.year:04d}-{close_date.month:02d}"] += 1
        if opp_type == "renewal":
            renewal_arr_at_risk += _float(row.get("renewal_arr_at_risk") or arr)
        elif opp_type == "expansion":
            expansion_arr += arr
        elif opp_type == "new_business":
            new_business_arr += arr

    duplicate_account_count = sum(1 for names in account_names.values() if len(names) > 1)
    quality_issue_count = (
        slipped
        + aged
        + stale
        + owner_changes
        + probability_overrides
        + weighted_mismatches
        + missing_probability
        + duplicate_account_count
    )

    return {
        "records": len(records),
        "quality_issue_count": quality_issue_count,
        "total_unweighted_arr": round(total_arr, 2),
        "total_weighted_arr": round(weighted_arr, 2),
        "weighted_to_unweighted_ratio": round(weighted_arr / total_arr, 4) if total_arr else None,
        "slipped_close_date_count": slipped,
        "stage_aging_count": aged,
        "stale_opportunity_count": stale,
        "owner_change_count": owner_changes,
        "probability_override_count": probability_overrides,
        "weighted_arr_mismatch_count": weighted_mismatches,
        "missing_probability_count": missing_probability,
        "duplicate_account_count": duplicate_account_count,
        "renewal_arr_at_risk": round(renewal_arr_at_risk, 2),
        "expansion_arr": round(expansion_arr, 2),
        "new_business_arr": round(new_business_arr, 2),
        "stage_counts": dict(sorted(stage_counts.items())),
        "opportunity_type_counts": dict(sorted(type_counts.items())),
        "close_month_counts": dict(sorted(close_months.items())),
        "recommended_actions": [
            "Re-age stale stages before accepting weighted ARR as forecastable.",
            "Separate renewal protection from expansion and new-business growth assumptions.",
            "Reconcile probability overrides and weighted ARR mismatches to stage history.",
        ],
        "fields_persisted": [
            "opportunity_type",
            "prior_close_date",
            "stage_entered_date",
            "days_in_stage",
            "previous_owner",
            "probability_override",
            "last_activity_date",
            "account_id",
            "renewal_arr_at_risk",
        ],
    }
