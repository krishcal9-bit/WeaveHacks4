"""
Atlas scenario engine — fork the live company state into named what-if branches,
apply finance changes, and compute board-grade metrics deterministically from the
real Redis system of record (never hallucinated).

A scenario forks ``atlas:company:northwind`` into ``atlas:scenario:<id>`` and
applies an ordered list of :class:`~src.redis_models.ScenarioChange` mutations:

    hire · vendor_renegotiation · revenue_slip · financing · churn_shock ·
    compliance_blocker · capex · opex_change

For each branch it computes runway, burn multiple, gross margin, CAC payback, a
multi-period cash projection, and any board-constraint violations (against the
machine-readable ``board_policy`` embedded in the company doc). Branches are
persisted as JSON, indexed for structured comparison (``atlas:idx:scenarios``),
appended to the ``atlas:stream:scenarios`` mutation log, and published to the
``atlas:dashboard`` channel — so they are searchable, comparable, and replayable.

All numbers come from Redis records + the change inputs; this module fabricates
no model output.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from src import redis_layer as R
from src import redis_models as M
from src import redis_store as S

_EPS = 1e-9


# --------------------------------------------------------------------------- #
# Loading the base company + board policy from the live system of record
# --------------------------------------------------------------------------- #
def _company() -> dict:
    co = R.get_json(M.COMPANY_KEY)
    if not isinstance(co, dict):
        raise RuntimeError(
            "No company system-of-record found at "
            f"{M.COMPANY_KEY}. Run the seed (python -m src.data.seed) first."
        )
    return co


def _board_policy(company: dict) -> M.BoardPolicy:
    raw = company.get("board_policy")
    if isinstance(raw, dict):
        try:
            return M.BoardPolicy.model_validate(raw)
        except Exception:
            pass
    return M.BoardPolicy()


def _opex_total(company: dict) -> float:
    opex = company.get("opex_monthly") or {}
    if isinstance(opex, dict):
        return float(sum(float(v or 0) for v in opex.values()))
    return float(opex or 0)


def _total_customers(company: dict) -> int:
    cohorts = company.get("customer_cohorts") or []
    return int(sum(int(c.get("customers", 0) or 0) for c in cohorts)) or 1


def _net_new_arr_annual(company: dict) -> float:
    """Trailing-12-month net new ARR from seeded ARR movements, else derived
    from MRR growth. Used as the burn-multiple denominator."""
    movements = S.scan_collection(M.ARR_PREFIX)
    if movements:
        recent = sorted(movements, key=lambda m: m.get("month", ""))[-12:]
        total = sum(float(m.get("net_new_arr", 0) or 0) for m in recent)
        if total > _EPS:
            return float(total)
    mrr = float(company.get("mrr", 0) or 0)
    growth = float(company.get("mrr_growth_mom", 0) or 0)
    return mrr * growth * 12.0


# --------------------------------------------------------------------------- #
# Metric math (pure functions over a financial "state" dict)
# --------------------------------------------------------------------------- #
def _state_from_company(company: dict) -> dict:
    cogs = float(company.get("cogs_monthly", 0) or 0)
    opex = _opex_total(company)
    revenue = float(company.get("monthly_revenue", 0) or 0)
    return {
        "cash": float(company.get("cash_on_hand", 0) or 0),
        "monthly_revenue": revenue,
        "cogs_monthly": cogs,
        "opex_total": opex,
        "mrr": float(company.get("mrr", 0) or 0),
        "arr": float(company.get("arr", 0) or 0),
        "headcount": int(company.get("headcount", 0) or 0),
        "net_new_arr_annual": _net_new_arr_annual(company),
        "cac": float(company.get("cac", 0) or 0),
        "total_customers": _total_customers(company),
    }


def _metrics_from_state(state: dict) -> M.ScenarioMetrics:
    revenue = state["monthly_revenue"]
    cogs = state["cogs_monthly"]
    opex = state["opex_total"]
    gross_burn = cogs + opex
    net_burn = gross_burn - revenue
    runway = round(state["cash"] / net_burn, 1) if net_burn > _EPS else None
    gross_margin = round((revenue - cogs) / revenue, 4) if revenue > _EPS else 0.0
    net_new = state["net_new_arr_annual"]
    burn_multiple = round((net_burn * 12.0) / net_new, 2) if net_burn > _EPS and net_new > _EPS else None
    arpa_monthly = state["mrr"] / max(state["total_customers"], 1)
    margin_per_acct = arpa_monthly * gross_margin
    cac_payback = round(state["cac"] / margin_per_acct, 1) if margin_per_acct > _EPS and state["cac"] > _EPS else None
    return M.ScenarioMetrics(
        cash_on_hand=round(state["cash"], 2),
        monthly_revenue=round(revenue, 2),
        cogs_monthly=round(cogs, 2),
        opex_monthly_total=round(opex, 2),
        monthly_gross_burn=round(gross_burn, 2),
        monthly_net_burn=round(net_burn, 2),
        runway_months=runway,
        gross_margin=gross_margin,
        burn_multiple=burn_multiple,
        cac_payback_months=cac_payback,
        mrr=round(state["mrr"], 2),
        arr=round(state["arr"], 2),
        net_new_arr_annual=round(net_new, 2),
        headcount=state["headcount"],
    )


# --------------------------------------------------------------------------- #
# Applying changes to a working state (returns mutations + applied labels)
# --------------------------------------------------------------------------- #
_DEFAULT_ROLE_COST = 16_000.0  # fully-loaded monthly cost per role if unspecified


def _vendor_annual_cost(vendor_id: str) -> float | None:
    doc = R.get_json(M.vendor_key(vendor_id))
    if isinstance(doc, dict):
        annual = doc.get("annual_cost")
        if annual is not None:
            return float(annual)
        monthly = doc.get("monthly_cost")
        if monthly is not None:
            return float(monthly) * 12.0
    return None


def _apply_change(state: dict, change: M.ScenarioChange) -> dict:
    """Mutate ``state`` in place; return a record describing what was applied
    (including any single-commitment annual amount for approval-threshold checks)."""
    applied: dict[str, Any] = {"type": change.type, "label": change.label or change.type}
    commitment_annual = 0.0

    if change.type == "hire":
        roles = int(change.roles or 1)
        monthly = float(change.monthly_cost) if change.monthly_cost is not None else roles * _DEFAULT_ROLE_COST
        state["opex_total"] += monthly
        state["headcount"] += roles
        commitment_annual = monthly * 12.0
        applied.update(team=change.team, roles=roles, monthly_cost=monthly)

    elif change.type == "opex_change":
        monthly = float(change.monthly_cost or change.amount or 0)
        state["opex_total"] += monthly
        commitment_annual = abs(monthly) * 12.0
        applied.update(monthly_delta=monthly)

    elif change.type == "vendor_renegotiation":
        old_annual = _vendor_annual_cost(change.vendor_id or "") or 0.0
        if change.new_annual_cost is not None:
            new_annual = float(change.new_annual_cost)
        elif change.pct is not None:
            new_annual = old_annual * (1.0 + float(change.pct))
        else:
            new_annual = old_annual
        delta_monthly = (new_annual - old_annual) / 12.0
        state["opex_total"] += delta_monthly
        commitment_annual = new_annual
        applied.update(vendor_id=change.vendor_id, old_annual=old_annual, new_annual=new_annual, monthly_delta=round(delta_monthly, 2))

    elif change.type == "revenue_slip":
        if change.amount is not None:
            lost = float(change.amount)
        else:
            lost = state["monthly_revenue"] * float(change.pct or 0)
        state["monthly_revenue"] = max(0.0, state["monthly_revenue"] - lost)
        state["mrr"] = max(0.0, state["mrr"] - lost)
        state["arr"] = max(0.0, state["arr"] - lost * 12.0)
        state["net_new_arr_annual"] -= lost * 12.0
        applied.update(monthly_revenue_lost=round(lost, 2))

    elif change.type == "churn_shock":
        base = state["mrr"]
        if change.segment:
            seg_mrr = _segment_mrr(change.segment)
            base = seg_mrr if seg_mrr > 0 else base
        if change.amount is not None:
            lost = float(change.amount)
        else:
            lost = base * float(change.pct or 0)
        state["monthly_revenue"] = max(0.0, state["monthly_revenue"] - lost)
        state["mrr"] = max(0.0, state["mrr"] - lost)
        state["arr"] = max(0.0, state["arr"] - lost * 12.0)
        state["net_new_arr_annual"] -= lost * 12.0
        applied.update(segment=change.segment, monthly_mrr_lost=round(lost, 2))

    elif change.type == "compliance_blocker":
        blocked = float(change.blocked_arr or 0)
        state["net_new_arr_annual"] = max(0.0, state["net_new_arr_annual"] - blocked)
        applied.update(control=change.control, blocked_arr=blocked)

    elif change.type == "financing":
        amount = float(change.amount or 0)
        state["cash"] += amount
        applied.update(financing_type=change.financing_type, amount=amount)

    elif change.type == "capex":
        one_time = float(change.one_time or change.amount or 0)
        state["cash"] = state["cash"] - one_time
        commitment_annual = one_time
        applied.update(one_time=one_time)

    else:  # pragma: no cover - guarded by the Literal type
        raise ValueError(f"unknown scenario change type: {change.type}")

    # A change may also bring incremental recurring revenue (e.g. a hire that
    # closes pipeline) — applied uniformly so the math stays explicit.
    if change.added_monthly_revenue:
        add = float(change.added_monthly_revenue)
        state["monthly_revenue"] += add
        state["mrr"] += add
        state["arr"] += add * 12.0
        state["net_new_arr_annual"] += add * 12.0
        applied["added_monthly_revenue"] = add

    applied["commitment_annual"] = round(commitment_annual, 2)
    return applied


def _segment_mrr(segment: str) -> float:
    company = _company()
    for cohort in company.get("customer_cohorts") or []:
        if str(cohort.get("segment", "")).lower() == segment.lower():
            return float(cohort.get("mrr", 0) or 0)
    return 0.0


# --------------------------------------------------------------------------- #
# Board-constraint violations (deterministic)
# --------------------------------------------------------------------------- #
def _violations(
    projected: M.ScenarioMetrics,
    baseline: M.ScenarioMetrics,
    policy: M.BoardPolicy,
    applied: list[dict],
) -> list[M.ConstraintViolation]:
    out: list[M.ConstraintViolation] = []

    if projected.runway_months is not None and projected.runway_months < policy.min_runway_months:
        sev = "high" if projected.runway_months < policy.min_runway_months - 3 else "medium"
        out.append(M.ConstraintViolation(
            code="min_runway_months",
            label="Minimum runway",
            threshold=policy.min_runway_months,
            actual=projected.runway_months,
            severity=sev,
            detail=f"Projected runway {projected.runway_months}mo is below the {policy.min_runway_months}mo board floor.",
        ))

    if projected.cash_on_hand < policy.min_cash_buffer:
        out.append(M.ConstraintViolation(
            code="min_cash_buffer",
            label="Minimum cash buffer",
            threshold=policy.min_cash_buffer,
            actual=projected.cash_on_hand,
            severity="high",
            detail=f"Cash ${projected.cash_on_hand:,.0f} falls below the ${policy.min_cash_buffer:,.0f} operating buffer.",
        ))

    if projected.burn_multiple is not None and projected.burn_multiple > policy.max_burn_multiple:
        out.append(M.ConstraintViolation(
            code="max_burn_multiple",
            label="Burn multiple ceiling",
            threshold=policy.max_burn_multiple,
            actual=projected.burn_multiple,
            severity="medium",
            detail=f"Burn multiple {projected.burn_multiple}x exceeds the {policy.max_burn_multiple}x efficiency ceiling.",
        ))

    if projected.gross_margin < policy.min_gross_margin:
        out.append(M.ConstraintViolation(
            code="min_gross_margin",
            label="Gross-margin floor",
            threshold=policy.min_gross_margin,
            actual=projected.gross_margin,
            severity="medium",
            detail=f"Gross margin {projected.gross_margin:.0%} is below the {policy.min_gross_margin:.0%} floor.",
        ))

    # Quarterly net-burn growth (monthly proxy) vs. discipline threshold.
    if baseline.monthly_net_burn > _EPS:
        growth = (projected.monthly_net_burn - baseline.monthly_net_burn) / baseline.monthly_net_burn
        if growth > policy.max_quarterly_netburn_growth:
            out.append(M.ConstraintViolation(
                code="max_netburn_growth",
                label="Net-burn growth discipline",
                threshold=policy.max_quarterly_netburn_growth,
                actual=round(growth, 4),
                severity="medium",
                detail=f"Net burn rises {growth:.0%}, above the {policy.max_quarterly_netburn_growth:.0%} discipline limit.",
            ))

    # Single-commitment approval thresholds (board notification / CFO approval).
    max_commitment = max([float(a.get("commitment_annual", 0) or 0) for a in applied], default=0.0)
    if max_commitment > policy.board_notify_annual:
        out.append(M.ConstraintViolation(
            code="board_notify",
            label="Board notification threshold",
            threshold=policy.board_notify_annual,
            actual=round(max_commitment, 2),
            severity="medium",
            detail=f"A ${max_commitment:,.0f}/yr commitment exceeds the ${policy.board_notify_annual:,.0f} board-notification threshold.",
        ))
    elif max_commitment > policy.cfo_approval_annual:
        out.append(M.ConstraintViolation(
            code="cfo_approval",
            label="CFO approval threshold",
            threshold=policy.cfo_approval_annual,
            actual=round(max_commitment, 2),
            severity="low",
            detail=f"A ${max_commitment:,.0f}/yr commitment requires CFO approval (over ${policy.cfo_approval_annual:,.0f}).",
        ))

    return out


# --------------------------------------------------------------------------- #
# Multi-period cash projection
# --------------------------------------------------------------------------- #
def _projection(projected: M.ScenarioMetrics, months: int = 18) -> list[M.ScenarioProjectionPoint]:
    start = datetime.now(timezone.utc)
    cash = projected.cash_on_hand
    net_burn = projected.monthly_net_burn
    arr = projected.arr
    monthly_net_new = projected.net_new_arr_annual / 12.0
    points: list[M.ScenarioProjectionPoint] = []
    for i in range(1, months + 1):
        month = (start.year + (start.month - 1 + i) // 12, (start.month - 1 + i) % 12 + 1)
        cash = cash - net_burn
        arr = max(0.0, arr + monthly_net_new)
        points.append(M.ScenarioProjectionPoint(
            month=f"{month[0]:04d}-{month[1]:02d}",
            cash=round(cash, 2),
            net_burn=round(net_burn, 2),
            arr=round(arr, 2),
        ))
        if cash <= 0:
            break
    return points


# --------------------------------------------------------------------------- #
# Change coercion (tool/REST inputs → ScenarioChange)
# --------------------------------------------------------------------------- #
def coerce_changes(changes: Iterable[Any]) -> list[M.ScenarioChange]:
    out: list[M.ScenarioChange] = []
    for ch in changes:
        if isinstance(ch, M.ScenarioChange):
            out.append(ch)
        elif isinstance(ch, dict):
            out.append(M.ScenarioChange.model_validate(ch))
        else:
            raise ValueError(f"invalid change (need dict or ScenarioChange): {ch!r}")
    return out


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or "scenario").lower()).strip("-") or "scenario"
    return f"{base[:40]}-{uuid.uuid4().hex[:6]}"


def _deltas(baseline: M.ScenarioMetrics, projected: M.ScenarioMetrics) -> dict[str, float | None]:
    def diff(key: str) -> float | None:
        a, b = getattr(baseline, key), getattr(projected, key)
        if a is None or b is None:
            return None
        return round(b - a, 4)

    return {
        "runway_months": diff("runway_months"),
        "monthly_net_burn": diff("monthly_net_burn"),
        "gross_margin": diff("gross_margin"),
        "burn_multiple": diff("burn_multiple"),
        "cac_payback_months": diff("cac_payback_months"),
        "cash_on_hand": diff("cash_on_hand"),
        "arr": diff("arr"),
    }


def _summarize(name: str, baseline: M.ScenarioMetrics, projected: M.ScenarioMetrics, violations: list[M.ConstraintViolation]) -> str:
    def runway(v: float | None) -> str:
        return "cash-flow+" if v is None else f"{v}mo"

    parts = [
        f"Runway {runway(baseline.runway_months)} → {runway(projected.runway_months)}",
        f"net burn ${projected.monthly_net_burn:,.0f}/mo",
    ]
    if projected.burn_multiple is not None:
        parts.append(f"burn multiple {projected.burn_multiple}x")
    if violations:
        parts.append(f"{len(violations)} board-constraint violation(s)")
    else:
        parts.append("no board-constraint violations")
    return f"{name}: " + ", ".join(parts) + "."


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def create_scenario(
    name: str,
    changes: Iterable[Any] | None = None,
    *,
    description: str = "",
    tags: Iterable[str] | None = None,
    scenario_id: str | None = None,
    persist: bool = True,
) -> M.Scenario:
    """Fork the live company state, apply ``changes``, compute metrics +
    violations, and (by default) persist the branch to Redis.

    Pass ``scenario_id`` for a stable, idempotent branch (canonical demo
    scenarios overwrite on reseed); otherwise a unique slug id is generated."""
    company = _company()
    policy = _board_policy(company)
    change_objs = coerce_changes(changes or [])

    baseline_state = _state_from_company(company)
    baseline = _metrics_from_state(baseline_state)

    working = dict(baseline_state)
    applied: list[dict] = [_apply_change(working, ch) for ch in change_objs]
    projected = _metrics_from_state(working)

    violations = _violations(projected, baseline, policy, applied)
    scenario = M.Scenario(
        id=scenario_id or _slug(name),
        name=name,
        base=company.get("id", M.COMPANY_ID),
        status="evaluated",
        description=description,
        summary=_summarize(name, baseline, projected, violations),
        tags=list(tags or []),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        changes=change_objs,
        baseline=baseline,
        projected=projected,
        deltas=_deltas(baseline, projected),
        violations=violations,
        violation_count=len(violations),
        projection=_projection(projected),
    )

    if persist:
        _persist(scenario, applied)
    return scenario


def _persist(scenario: M.Scenario, applied: list[dict]) -> None:
    S.set_doc(M.scenario_key(scenario.id), scenario)
    try:
        R.append_event(M.STREAM_SCENARIOS, {
            "event": "scenario_evaluated",
            "scenario_id": scenario.id,
            "name": scenario.name,
            "summary": scenario.summary,
            "changes": applied,
            "runway_months": scenario.projected.runway_months,
            "burn_multiple": scenario.projected.burn_multiple,
            "violation_count": scenario.violation_count,
            "source": "scenario_engine",
        })
        R.publish(M.DASHBOARD_CHANNEL, {
            "event": "scenario",
            "scenario_id": scenario.id,
            "name": scenario.name,
            "violation_count": scenario.violation_count,
        })
        S.cache_invalidate("scenarios:*")
    except Exception as exc:  # persistence must not crash an evaluation
        print(f"[scenario_engine] persistence warning: {exc}")


def get_scenario(scenario_id: str) -> dict | None:
    return S.get_doc(M.scenario_key(scenario_id))


def list_scenarios(limit: int = 50, sort_by: str = "created_at", ascending: bool = False) -> list[dict]:
    try:
        return S.search_index(M.SCENARIO_INDEX, "*", sort_by=sort_by, ascending=ascending, limit=limit)
    except Exception:
        return S.scan_collection(M.SCENARIO_PREFIX, limit=limit)


def search_scenarios(query: str = "*", *, filters: dict[str, Any] | None = None, limit: int = 25) -> list[dict]:
    return S.search_index(M.SCENARIO_INDEX, query, filters=filters, limit=limit)


def delete_scenario(scenario_id: str) -> int:
    return S.delete(M.scenario_key(scenario_id))


_COMPARE_METRICS = (
    "runway_months",
    "monthly_net_burn",
    "gross_margin",
    "burn_multiple",
    "cac_payback_months",
    "cash_on_hand",
    "arr",
    "net_new_arr_annual",
)


def compare_scenarios(scenario_ids: list[str]) -> dict[str, Any]:
    """Side-by-side comparison of scenarios (plus the live baseline) for the
    metrics the board cares about — a reranking/decision-friendly table."""
    company = _company()
    baseline = _metrics_from_state(_state_from_company(company))
    columns: list[dict[str, Any]] = [{
        "id": "baseline",
        "name": f"{company.get('name', 'Company')} (live)",
        "metrics": baseline.model_dump(),
        "violation_count": 0,
    }]
    for sid in scenario_ids:
        doc = get_scenario(sid)
        if not doc:
            continue
        columns.append({
            "id": sid,
            "name": doc.get("name", sid),
            "metrics": doc.get("projected", {}),
            "violation_count": doc.get("violation_count", 0),
            "summary": doc.get("summary", ""),
        })
    rows = [
        {"metric": metric, "values": {col["id"]: col["metrics"].get(metric) for col in columns}}
        for metric in _COMPARE_METRICS
    ]
    return {"columns": columns, "rows": rows, "metrics": list(_COMPARE_METRICS)}
