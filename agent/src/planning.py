"""
Atlas — Strategic Planning Digital Twin (deterministic core).

This is the long-horizon "finance digital twin" of the company. Where the debate
graph (``src/agent.py``) resolves a single decision, this module lets the council
compare *months of operating futures*: it projects cash, burn, runway, ARR, churn,
pipeline conversion, hiring ramps, vendor savings, and financing timing forward,
month by month, from the live Redis system of record.

Hard rule (matches the strict-live contract): **every number here is computed, not
generated.** No LLM is involved in the projection, milestones, capital plan, or
policy/compliance checks. OpenAI is used only afterwards — in
``generate_board_narrative`` — to summarize and critique a plan whose figures are
already fixed. The narrative carries a ``deterministic_basis`` so the prose can be
audited against the math.

Layout:
  • Typed models ............ StrategicPlan, ScenarioAssumption, Milestone,
                             CapitalPlan, PlaybookStep, MonthProjection,
                             SensitivityResult, StressTest, DecisionPortfolio,
                             BoardNarrative (the last three are consumed by
                             playbooks.py / stress_tests.py too).
  • Projection engine ....... assumption_values, compile_steps, project
  • Plan assembly ........... build_plan, milestones, policy/compliance blockers
  • Persistence ............. save_plan / get_plan / list_plans (Redis JSON + stream)
  • Narrative (LLM) ......... generate_board_narrative
  • Agent entry ............. is_strategic_request, plan_from_decision
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src import redis_layer as R

COMPANY_KEY = f"{R.NS}:company:northwind"
PLAN_PREFIX = f"{R.NS}:plan:"
PLAN_INDEX = f"{R.NS}:plans:index"

ENGINE_VERSION = "planning-v1"

# Base operating assumptions that are *not* directly stored as a single field on
# the company record but are needed by the forward model. ``base_conversion`` is
# the seeded board threshold ("pipeline conversion above 32%" in the hiring plan
# and AUD-21's flagged technical-validation conversion); new business in the model
# scales linearly off this baseline.
BASE_CONVERSION = 0.32
DEFAULT_HORIZON = 12

# Board / policy thresholds (mirrors atlas:policy:* and board_constraints in seed).
RUNWAY_FLOOR_MONTHS = 9.0
CASH_BUFFER_FLOOR = 1_500_000
BOARD_NOTIFY_ANNUAL = 150_000
CFO_APPROVAL_ANNUAL = 50_000


# --------------------------------------------------------------------------- #
# Typed models
# --------------------------------------------------------------------------- #
class ScenarioAssumption(BaseModel):
    """One economic dial in a plan, with where its value came from."""

    key: str = Field(description="machine key, e.g. mrr_growth_mom")
    label: str = Field(description="human label for the boardroom")
    value: float
    unit: str = Field(description="ratio_mom | ratio | usd | usd_month | months | x")
    source: str = Field(description="system_of_record | derived | playbook | override")
    rationale: str = Field(default="", description="why this value, grounded in real data")


class PlaybookStep(BaseModel):
    """A single required action inside a plan. ``financial_effect`` is the typed,
    machine-readable lever the projection engine compiles; ``kind`` selects how."""

    order: int = 0
    action: str
    owner: str = Field(default="cfo", description="role responsible, e.g. treasury/procurement")
    kind: Literal[
        "hire", "vendor_savings", "spend", "revenue_unlock", "financing", "cut", "policy"
    ] = "spend"
    start_month_index: int = Field(default=1, ge=1, description="1-based plan month the effect begins")
    financial_effect: dict[str, float] = Field(
        default_factory=dict,
        description="numeric levers: monthly_cost_delta, one_time_cost, monthly_revenue_delta, "
        "financing_amount, roles, ramp_months, revenue_ramp_months",
    )
    dependency: str = Field(default="", description="what must be true for this step")
    reversible: bool = True
    detail: str = ""


class CapitalPlan(BaseModel):
    """Financing timing for a plan (when/how cash comes in)."""

    instrument: str = Field(default="none", description="none | bridge | equity | venture_debt")
    raise_amount: float = 0.0
    close_month: str | None = Field(default=None, description="YYYY-MM the financing closes")
    close_month_index: int | None = None
    dilution_pct: float | None = None
    runway_extension_months: float | None = None
    triggers: list[str] = Field(default_factory=list, description="conditions that arm the raise")
    notes: str = ""


class Milestone(BaseModel):
    """A tracked checkpoint with a deterministic status from the projection."""

    id: str
    month: str = Field(description="YYYY-MM")
    month_index: int
    label: str
    category: Literal["runway", "cash", "revenue", "compliance", "hiring", "financing", "efficiency"]
    metric: str = ""
    target: float | None = None
    projected: float | None = None
    comparator: Literal[">=", "<=", "==", "n/a"] = "n/a"
    status: Literal["met", "on_track", "at_risk", "missed", "scheduled"] = "scheduled"
    depends_on: list[str] = Field(default_factory=list)
    source: str = "plan"


class MonthProjection(BaseModel):
    """One projected operating month — the row-level output of the engine."""

    month: str
    month_index: int
    headcount: int
    mrr: float
    arr: float
    revenue: float
    new_mrr: float
    churned_mrr: float
    cogs: float
    opex: float
    gross_burn: float
    net_burn: float
    one_time_cost: float
    financing_inflow: float
    cash_begin: float
    cash_end: float
    runway_months: float | None
    gross_margin: float


class SensitivityResult(BaseModel):
    """One-variable sweep: how an output (default runway/min-cash) moves with a lever."""

    variable: str
    label: str
    unit: str
    base_value: float
    output_metric: str = "min_cash"
    base_output: float | None = None
    points: list[dict[str, float | None]] = Field(default_factory=list)
    elasticity: float | None = Field(default=None, description="d(output%) / d(input%) near base")
    swing: float | None = Field(default=None, description="max-min of output across the sweep")
    direction: str = Field(default="", description="increases | decreases | non-monotonic")
    note: str = ""


class StressTest(BaseModel):
    """A Monte Carlo-style stress run summary (distributions over many trials)."""

    id: str = ""
    name: str
    description: str = ""
    trials: int = 0
    horizon_months: int = DEFAULT_HORIZON
    seed: int = 0
    distributions: dict[str, dict[str, float]] = Field(default_factory=dict)
    metrics: dict[str, dict[str, float | None]] = Field(
        default_factory=dict, description="per-output percentile bands (p5/p50/p95 etc.)"
    )
    prob_runway_breach: float | None = None
    prob_cash_negative: float | None = None
    prob_below_cash_buffer: float | None = None
    expected_breach_month: str | None = None
    worst_case: dict[str, Any] = Field(default_factory=dict)
    base_case: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class StrategicPlan(BaseModel):
    """A months-long operating future: assumptions + actions + projection + checks."""

    id: str
    title: str
    horizon_months: int
    created_at: str
    start_month: str
    playbook_id: str | None = None
    playbook_label: str | None = None
    objective: str = ""
    company: str = "Acme Corp"

    assumptions: list[ScenarioAssumption] = Field(default_factory=list)
    steps: list[PlaybookStep] = Field(default_factory=list)
    capital_plan: CapitalPlan = Field(default_factory=CapitalPlan)
    projection: list[MonthProjection] = Field(default_factory=list)
    milestones: list[Milestone] = Field(default_factory=list)
    policy_blockers: list[dict[str, Any]] = Field(default_factory=list)

    summary: dict[str, Any] = Field(default_factory=dict)
    risks: list[str] = Field(default_factory=list)
    monitoring_triggers: list[str] = Field(default_factory=list)

    provenance: dict[str, Any] = Field(default_factory=dict)
    calc_metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionPortfolio(BaseModel):
    """Comparison of multiple playbooks for one decision → a recommended portfolio
    (a ranked/sequenced set), not a binary approve/reject."""

    id: str = ""
    decision: str
    horizon_months: int = DEFAULT_HORIZON
    created_at: str = ""
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    ranking: list[str] = Field(default_factory=list, description="playbook ids best→worst by score")
    recommended_portfolio: list[dict[str, Any]] = Field(
        default_factory=list, description="sequenced picks with weight + role"
    )
    rationale: str = Field(default="", description="deterministic scoring rationale")
    tradeoffs: list[str] = Field(default_factory=list)
    scoring_weights: dict[str, float] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class BoardNarrative(BaseModel):
    """CFO-ready strategic narrative. Prose is model-generated; every figure it
    cites lives in ``deterministic_basis`` so it can be audited."""

    plan_id: str
    headline: str = ""
    narrative: str = ""
    key_metrics: list[dict[str, Any]] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    asks: list[str] = Field(default_factory=list)
    recommended_decision: str = ""
    generated_by: str = ""
    deterministic_basis: dict[str, Any] = Field(default_factory=dict)
    generated_at: str = ""


# --------------------------------------------------------------------------- #
# Internal engine inputs (compiled from steps; never serialized to the client)
# --------------------------------------------------------------------------- #
@dataclass
class _Hire:
    label: str
    monthly_cost: float
    start_index: int
    roles: int = 0
    ramp_months: int = 2
    monthly_revenue: float = 0.0
    revenue_ramp_months: int = 3
    revenue_start_index: int | None = None


@dataclass
class _OpexAdjustment:
    label: str
    monthly_delta: float  # + adds opex, − is a saving
    start_index: int
    ramp_months: int = 1


@dataclass
class _MrrAdjustment:
    label: str
    monthly_mrr_delta: float  # recurring MRR added once fully ramped
    start_index: int
    ramp_months: int = 1


@dataclass
class _OneTime:
    label: str
    amount: float
    month_index: int


@dataclass
class _Financing:
    label: str
    amount: float
    close_index: int


@dataclass
class _ProjectionInputs:
    assumptions: dict[str, float]
    hires: list[_Hire] = field(default_factory=list)
    opex_adjustments: list[_OpexAdjustment] = field(default_factory=list)
    mrr_adjustments: list[_MrrAdjustment] = field(default_factory=list)
    one_time_costs: list[_OneTime] = field(default_factory=list)
    financing: list[_Financing] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Date + small helpers
# --------------------------------------------------------------------------- #
def add_months(ym: str, n: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    idx = y * 12 + (m - 1) + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def month_index_of(start_ym: str, target_ym: str) -> int:
    """1-based plan-month index of target relative to a plan whose month 1 = start_ym."""
    sy, sm = int(start_ym[:4]), int(start_ym[5:7])
    ty, tm = int(target_ym[:4]), int(target_ym[5:7])
    return (ty * 12 + (tm - 1)) - (sy * 12 + (sm - 1)) + 1


def _ramp_fraction(i: int, start: int, ramp: int) -> float:
    """Linear ramp: 0 before start, 1/ramp at start, ... 1.0 at start+ramp-1 onward."""
    if i < start:
        return 0.0
    if ramp <= 1:
        return 1.0
    return min(1.0, (i - start + 1) / ramp)


def _ramp_flow(i: int, start: int, ramp: int) -> float:
    """Per-month delivered fraction of a one-shot total spread over ``ramp`` months."""
    ramp = max(int(ramp), 1)
    return 1.0 / ramp if start <= i < start + ramp else 0.0


def _start_month(company: dict) -> str:
    updated = (company.get("updated") or "2026-06-01")[:7]
    return add_months(updated, 1)


def load_company() -> dict:
    """Read the live financial system of record from Redis."""
    return R.get_json(COMPANY_KEY) or {}


# --------------------------------------------------------------------------- #
# Current-state recompute (validates the cash/burn/runway formulas vs the seed)
# --------------------------------------------------------------------------- #
def recompute_current_metrics(company: dict) -> dict[str, float | None]:
    """Recompute today's gross burn / net burn / runway from primitives so the
    engine's formulas can be smoke-checked against the stored system of record."""
    mrr = float(company.get("mrr") or company.get("monthly_revenue") or 0.0)
    margin = float(company.get("gross_margin") or 0.0)
    opex = float(sum((company.get("opex_monthly") or {}).values()))
    cash = float(company.get("cash_on_hand") or 0.0)
    cogs = mrr * (1.0 - margin)
    gross_burn = cogs + opex
    net_burn = gross_burn - mrr
    runway = round(cash / net_burn, 1) if net_burn > 0 else None
    return {
        "cogs": round(cogs),
        "gross_burn": round(gross_burn),
        "net_burn": round(net_burn),
        "runway_months": runway,
    }


# --------------------------------------------------------------------------- #
# Assumptions
# --------------------------------------------------------------------------- #
def base_assumption_values(company: dict) -> dict[str, float]:
    """The dial values that reproduce the seeded system of record."""
    return {
        "mrr_growth_mom": float(company.get("mrr_growth_mom") or 0.0),
        "logo_churn_mom": float(company.get("logo_churn_mom") or 0.0),
        "gross_margin": float(company.get("gross_margin") or 0.0),
        "pipeline_conversion": BASE_CONVERSION,
        "opex_growth_mom": 0.0,
        "new_business_ramp_mom": 0.0,
        # frozen baselines used to derive the new-business run rate
        "base_conversion": BASE_CONVERSION,
        "base_mrr_growth_mom": float(company.get("mrr_growth_mom") or 0.0),
        "base_logo_churn_mom": float(company.get("logo_churn_mom") or 0.0),
    }


def assumption_values(company: dict, overrides: dict[str, float] | None = None) -> dict[str, float]:
    values = base_assumption_values(company)
    for k, v in (overrides or {}).items():
        if v is not None:
            values[k] = float(v)
    return values


_ASSUMPTION_META = {
    "mrr_growth_mom": ("New-business MoM growth", "ratio_mom"),
    "logo_churn_mom": ("Logo churn (monthly)", "ratio_mom"),
    "gross_margin": ("Gross margin", "ratio"),
    "pipeline_conversion": ("Pipeline conversion", "ratio"),
    "opex_growth_mom": ("Opex creep (monthly)", "ratio_mom"),
    "new_business_ramp_mom": ("New-business ramp (monthly)", "ratio_mom"),
}


def describe_assumptions(
    company: dict, values: dict[str, float], overrides: dict[str, float] | None = None
) -> list[ScenarioAssumption]:
    overrides = overrides or {}
    base = base_assumption_values(company)
    out: list[ScenarioAssumption] = []
    for key, (label, unit) in _ASSUMPTION_META.items():
        value = values.get(key, base.get(key, 0.0))
        if key in overrides and overrides[key] is not None:
            source = "override"
            rationale = f"Scenario override (system-of-record baseline {base.get(key)})."
        elif value != base.get(key):
            source = "playbook"
            rationale = f"Adjusted by playbook from baseline {base.get(key)}."
        else:
            source = "system_of_record"
            rationale = "From the live Redis company record."
        out.append(
            ScenarioAssumption(key=key, label=label, value=round(value, 5), unit=unit, source=source, rationale=rationale)
        )
    return out


# --------------------------------------------------------------------------- #
# Compile human steps → engine inputs
# --------------------------------------------------------------------------- #
def default_hire_steps(company: dict, start_month: str) -> list[PlaybookStep]:
    """Turn the seeded hiring_plan into typed steps so the base plan and playbooks
    flow through one code path."""
    steps: list[PlaybookStep] = []
    for i, hire in enumerate(company.get("hiring_plan") or []):
        idx = max(1, month_index_of(start_month, (hire.get("start_month") or start_month)[:7]))
        steps.append(
            PlaybookStep(
                order=i + 1,
                action=f"Hire {hire.get('roles')} on {hire.get('team')}",
                owner="fpna",
                kind="hire",
                start_month_index=idx,
                financial_effect={
                    "monthly_cost_delta": float(hire.get("monthly_cost") or 0.0),
                    "roles": float(hire.get("roles") or 0.0),
                    "ramp_months": 2.0,
                },
                dependency=hire.get("dependency", ""),
                reversible=True,
                detail=f"Planned {hire.get('team')} hires starting {hire.get('start_month')}.",
            )
        )
    return steps


def compile_steps(steps: list[PlaybookStep], assumptions: dict[str, float]) -> _ProjectionInputs:
    """Translate display steps into the numeric levers the projection consumes."""
    inputs = _ProjectionInputs(assumptions=assumptions)
    for s in steps:
        fx = s.financial_effect or {}
        idx = max(1, int(s.start_month_index))
        if s.kind == "hire":
            inputs.hires.append(
                _Hire(
                    label=s.action,
                    monthly_cost=float(fx.get("monthly_cost_delta", 0.0)),
                    start_index=idx,
                    roles=int(fx.get("roles", 0)),
                    ramp_months=int(fx.get("ramp_months", 2)),
                    monthly_revenue=float(fx.get("monthly_revenue_delta", 0.0)),
                    revenue_ramp_months=int(fx.get("revenue_ramp_months", 3)),
                )
            )
        elif s.kind in ("vendor_savings", "cut"):
            # savings reduce opex → negative delta
            delta = -abs(float(fx.get("monthly_cost_delta", 0.0)))
            inputs.opex_adjustments.append(
                _OpexAdjustment(label=s.action, monthly_delta=delta, start_index=idx, ramp_months=int(fx.get("ramp_months", 1)))
            )
        elif s.kind == "spend":
            if fx.get("monthly_cost_delta"):
                inputs.opex_adjustments.append(
                    _OpexAdjustment(
                        label=s.action,
                        monthly_delta=float(fx.get("monthly_cost_delta", 0.0)),
                        start_index=idx,
                        ramp_months=int(fx.get("ramp_months", 1)),
                    )
                )
            if fx.get("one_time_cost"):
                inputs.one_time_costs.append(_OneTime(label=s.action, amount=float(fx["one_time_cost"]), month_index=idx))
            if fx.get("monthly_revenue_delta"):
                inputs.mrr_adjustments.append(
                    _MrrAdjustment(
                        label=s.action,
                        monthly_mrr_delta=float(fx["monthly_revenue_delta"]),
                        start_index=int(fx.get("revenue_start_index", idx)),
                        ramp_months=int(fx.get("revenue_ramp_months", 3)),
                    )
                )
        elif s.kind == "revenue_unlock":
            if fx.get("one_time_cost"):
                inputs.one_time_costs.append(_OneTime(label=s.action, amount=float(fx["one_time_cost"]), month_index=idx))
            if fx.get("monthly_cost_delta"):
                inputs.opex_adjustments.append(
                    _OpexAdjustment(label=s.action, monthly_delta=float(fx["monthly_cost_delta"]), start_index=idx)
                )
            if fx.get("monthly_revenue_delta"):
                inputs.mrr_adjustments.append(
                    _MrrAdjustment(
                        label=s.action,
                        monthly_mrr_delta=float(fx["monthly_revenue_delta"]),
                        start_index=int(fx.get("revenue_start_index", idx)),
                        ramp_months=int(fx.get("revenue_ramp_months", 3)),
                    )
                )
        elif s.kind == "financing":
            inputs.financing.append(
                _Financing(label=s.action, amount=float(fx.get("financing_amount", 0.0)), close_index=idx)
            )
    return inputs


# --------------------------------------------------------------------------- #
# The projection engine (pure, deterministic)
# --------------------------------------------------------------------------- #
def project(company: dict, inputs: _ProjectionInputs, horizon: int) -> tuple[list[MonthProjection], dict[str, Any]]:
    a = inputs.assumptions
    start_month = _start_month(company)

    mrr0 = float(company.get("mrr") or company.get("monthly_revenue") or 0.0)
    cash = float(company.get("cash_on_hand") or 0.0)
    base_opex = float(sum((company.get("opex_monthly") or {}).values()))
    headcount0 = int(company.get("headcount") or 0)

    churn = a["logo_churn_mom"]
    margin = a["gross_margin"]
    conversion = a["pipeline_conversion"]
    opex_growth = a.get("opex_growth_mom", 0.0)
    nb_ramp = a.get("new_business_ramp_mom", 0.0)

    # New-business run rate is anchored to the *seeded* growth+churn so that
    # changing churn/conversion deviates from a fixed baseline (and the flat case
    # — conversion 0, churn 0 — reproduces the system of record exactly).
    base_new_mrr = mrr0 * (a["base_mrr_growth_mom"] + a["base_logo_churn_mom"])
    conv_factor = (conversion / a["base_conversion"]) if a["base_conversion"] else 0.0

    rows: list[MonthProjection] = []
    mrr = mrr0
    cash_end = cash
    for i in range(1, horizon + 1):
        cash_begin = cash_end
        month = add_months(start_month, i - 1)

        # --- revenue / MRR --------------------------------------------------
        organic_new = base_new_mrr * conv_factor * ((1.0 + nb_ramp) ** (i - 1))
        unlock_flow = sum(
            adj.monthly_mrr_delta * _ramp_flow(i, adj.start_index, adj.ramp_months) for adj in inputs.mrr_adjustments
        )
        hire_rev_flow = sum(
            h.monthly_revenue * _ramp_flow(i, (h.revenue_start_index or h.start_index), h.revenue_ramp_months)
            for h in inputs.hires
            if h.monthly_revenue
        )
        new_mrr = organic_new + unlock_flow + hire_rev_flow
        churned_mrr = mrr * churn
        mrr = max(0.0, mrr - churned_mrr + new_mrr)
        revenue = mrr

        # --- costs ----------------------------------------------------------
        cogs = revenue * (1.0 - margin)
        opex = base_opex * ((1.0 + opex_growth) ** (i - 1))
        opex += sum(h.monthly_cost * _ramp_fraction(i, h.start_index, h.ramp_months) for h in inputs.hires)
        opex += sum(adj.monthly_delta * _ramp_fraction(i, adj.start_index, adj.ramp_months) for adj in inputs.opex_adjustments)
        opex = max(0.0, opex)

        gross_burn = cogs + opex
        net_burn = gross_burn - revenue

        one_time = sum(o.amount for o in inputs.one_time_costs if o.month_index == i)
        financing = sum(f.amount for f in inputs.financing if f.close_index == i)
        cash_end = cash_begin - net_burn - one_time + financing

        headcount = headcount0 + sum(h.roles for h in inputs.hires if i >= h.start_index)
        runway = round(cash_end / net_burn, 1) if net_burn > 0 else None

        rows.append(
            MonthProjection(
                month=month,
                month_index=i,
                headcount=headcount,
                mrr=round(mrr),
                arr=round(mrr * 12),
                revenue=round(revenue),
                new_mrr=round(new_mrr),
                churned_mrr=round(churned_mrr),
                cogs=round(cogs),
                opex=round(opex),
                gross_burn=round(gross_burn),
                net_burn=round(net_burn),
                one_time_cost=round(one_time),
                financing_inflow=round(financing),
                cash_begin=round(cash_begin),
                cash_end=round(cash_end),
                runway_months=runway,
                gross_margin=round(margin, 4),
            )
        )

    summary = _summarize(company, rows)
    return rows, summary


def _summarize(company: dict, rows: list[MonthProjection]) -> dict[str, Any]:
    if not rows:
        return {}
    starting_cash = float(company.get("cash_on_hand") or 0.0)
    starting_runway = company.get("runway_months")
    cash_ends = [r.cash_end for r in rows]
    min_cash = min(cash_ends)
    min_row = next(r for r in rows if r.cash_end == min_cash)
    positive_months = [r for r in rows if r.runway_months is not None]
    lowest_runway = min((r.runway_months for r in positive_months), default=None)
    lowest_runway_row = (
        next((r for r in positive_months if r.runway_months == lowest_runway), None) if lowest_runway is not None else None
    )
    cash_positive = next((r for r in rows if r.net_burn <= 0), None)
    floor_breaches = [r for r in rows if r.runway_months is not None and r.runway_months < RUNWAY_FLOOR_MONTHS]
    buffer_breaches = [r for r in rows if r.cash_end < CASH_BUFFER_FLOOR]
    insolvent = [r for r in rows if r.cash_end < 0]
    last = rows[-1]
    return {
        "horizon_months": len(rows),
        "start_month": rows[0].month,
        "end_month": last.month,
        "starting_cash": round(starting_cash),
        "ending_cash": last.cash_end,
        "min_cash": min_cash,
        "min_cash_month": min_row.month,
        "starting_runway_months": starting_runway,
        "runway_at_horizon": last.runway_months,
        "lowest_runway_months": lowest_runway,
        "lowest_runway_month": lowest_runway_row.month if lowest_runway_row else None,
        "starting_mrr": round(float(company.get("mrr") or 0.0)),
        "ending_mrr": last.mrr,
        "ending_arr": last.arr,
        "arr_growth_pct": round((last.arr / (float(company.get("arr") or 1.0)) - 1.0) * 100, 1),
        "total_net_burn": round(sum(r.net_burn for r in rows)),
        "total_financing": round(sum(r.financing_inflow for r in rows)),
        "total_one_time": round(sum(r.one_time_cost for r in rows)),
        "cash_flow_positive_month": cash_positive.month if cash_positive else None,
        "months_below_runway_floor": len(floor_breaches),
        "months_below_cash_buffer": len(buffer_breaches),
        "breaches_runway_floor": bool(floor_breaches),
        "breaches_cash_buffer": bool(buffer_breaches),
        "goes_insolvent": bool(insolvent),
        "insolvent_month": insolvent[0].month if insolvent else None,
        "ending_headcount": last.headcount,
    }


# --------------------------------------------------------------------------- #
# Milestones (deterministic status from the projection)
# --------------------------------------------------------------------------- #
def compute_milestones(
    company: dict, rows: list[MonthProjection], steps: list[PlaybookStep], summary: dict[str, Any]
) -> list[Milestone]:
    out: list[Milestone] = []
    start_month = rows[0].month if rows else _start_month(company)
    last_idx = len(rows)
    last_month = rows[-1].month if rows else start_month

    # 1) Board runway guardrail
    breach = next((r for r in rows if r.runway_months is not None and r.runway_months < RUNWAY_FLOOR_MONTHS), None)
    out.append(
        Milestone(
            id="runway-floor",
            month=(breach.month if breach else last_month),
            month_index=(breach.month_index if breach else last_idx),
            label=f"Hold runway ≥ {RUNWAY_FLOOR_MONTHS:.0f} months (board guardrail)",
            category="runway",
            metric="runway_months",
            target=RUNWAY_FLOOR_MONTHS,
            projected=summary.get("lowest_runway_months"),
            comparator=">=",
            status="missed" if breach else "on_track",
            source="board_constraint",
        )
    )

    # 2) Cash buffer
    buffer_breach = next((r for r in rows if r.cash_end < CASH_BUFFER_FLOOR), None)
    out.append(
        Milestone(
            id="cash-buffer",
            month=(buffer_breach.month if buffer_breach else summary.get("min_cash_month", last_month)),
            month_index=(buffer_breach.month_index if buffer_breach else last_idx),
            label=f"Keep operating cash ≥ ${CASH_BUFFER_FLOOR/1e6:.1f}M (cash policy)",
            category="cash",
            metric="cash_end",
            target=float(CASH_BUFFER_FLOOR),
            projected=summary.get("min_cash"),
            comparator=">=",
            status="missed" if buffer_breach else "on_track",
            source="policy:pol-cash",
        )
    )

    # 3) Cash-flow positive target (if it happens in-horizon)
    cfp = summary.get("cash_flow_positive_month")
    if cfp:
        cfp_row = next(r for r in rows if r.month == cfp)
        out.append(
            Milestone(
                id="cash-flow-positive",
                month=cfp,
                month_index=cfp_row.month_index,
                label="Reach cash-flow positive (net burn ≤ 0)",
                category="efficiency",
                metric="net_burn",
                target=0.0,
                projected=cfp_row.net_burn,
                comparator="<=",
                status="on_track",
                source="plan",
            )
        )

    # 4) Step-driven milestones (each material action becomes a checkpoint)
    for s in steps:
        idx = min(max(1, s.start_month_index), last_idx or 1)
        month = rows[idx - 1].month if rows else add_months(start_month, idx - 1)
        if s.kind == "financing":
            cat: Any = "financing"
            metric = "financing_amount"
            target = float((s.financial_effect or {}).get("financing_amount", 0.0))
        elif s.kind in ("vendor_savings", "cut"):
            cat, metric = "efficiency", "monthly_savings"
            target = abs(float((s.financial_effect or {}).get("monthly_cost_delta", 0.0)))
        elif s.kind in ("revenue_unlock",):
            cat, metric = "revenue", "monthly_revenue_delta"
            target = float((s.financial_effect or {}).get("monthly_revenue_delta", 0.0))
        elif s.kind == "hire":
            cat, metric = "hiring", "roles"
            target = float((s.financial_effect or {}).get("roles", 0.0))
        else:
            cat, metric = "efficiency", "monthly_cost_delta"
            target = float((s.financial_effect or {}).get("monthly_cost_delta", 0.0))
        out.append(
            Milestone(
                id=f"step-{s.order}-{s.kind}",
                month=month,
                month_index=idx,
                label=s.action,
                category=cat,
                metric=metric,
                target=round(target, 2) if target else None,
                comparator="n/a",
                status="scheduled",
                depends_on=[s.dependency] if s.dependency else [],
                source="plan",
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Policy / compliance blockers (deterministic, grounded in board_constraints)
# --------------------------------------------------------------------------- #
def compute_policy_blockers(
    company: dict,
    rows: list[MonthProjection],
    steps: list[PlaybookStep],
    summary: dict[str, Any],
    capital_plan: CapitalPlan,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    financing_idx = capital_plan.close_month_index if (capital_plan and capital_plan.raise_amount) else None

    # Runway guardrail — unless a financing closes on/before the breach month.
    breach = next((r for r in rows if r.runway_months is not None and r.runway_months < RUNWAY_FLOOR_MONTHS), None)
    if breach:
        covered = financing_idx is not None and financing_idx <= breach.month_index
        blockers.append(
            {
                "policy": "Runway guardrail (≥ 9 months)",
                "severity": "warning" if covered else "high",
                "source": "board_constraint + pol-runway",
                "month": breach.month,
                "detail": (
                    f"Runway falls to {breach.runway_months} months in {breach.month}. "
                    + ("A financing close is scheduled on or before this month, satisfying the carve-out."
                       if covered
                       else "No signed financing is scheduled before the breach — requires an explicit financing plan.")
                ),
            }
        )

    # Cash buffer
    if summary.get("breaches_cash_buffer"):
        blockers.append(
            {
                "policy": "Minimum cash buffer (≥ $1.5M)",
                "severity": "high",
                "source": "pol-cash",
                "month": summary.get("min_cash_month"),
                "detail": f"Projected cash dips to ${summary.get('min_cash'):,.0f} in {summary.get('min_cash_month')}, below the $1.5M floor.",
            }
        )

    # Insolvency
    if summary.get("goes_insolvent"):
        blockers.append(
            {
                "policy": "Solvency",
                "severity": "critical",
                "source": "derived",
                "month": summary.get("insolvent_month"),
                "detail": f"Cash goes negative in {summary.get('insolvent_month')} under this plan.",
            }
        )

    # Spend-size thresholds (annualized) on recurring spend + commitments
    for s in steps:
        fx = s.financial_effect or {}
        annual = abs(float(fx.get("monthly_cost_delta", 0.0))) * 12
        one_time = abs(float(fx.get("one_time_cost", 0.0)))
        commit = max(annual, one_time)
        if s.kind in ("spend", "revenue_unlock") and commit >= BOARD_NOTIFY_ANNUAL:
            blockers.append(
                {
                    "policy": "Board notification (> $150K annualized)",
                    "severity": "info",
                    "source": "pol-spend + board_constraint",
                    "month": rows[min(s.start_month_index, len(rows)) - 1].month if rows else None,
                    "detail": f"“{s.action}” commits ~${commit:,.0f}; requires board notification before signing.",
                }
            )
        elif s.kind in ("spend", "revenue_unlock") and commit >= CFO_APPROVAL_ANNUAL:
            blockers.append(
                {
                    "policy": "CFO approval (> $50K annualized)",
                    "severity": "info",
                    "source": "pol-spend",
                    "month": rows[min(s.start_month_index, len(rows)) - 1].month if rows else None,
                    "detail": f"“{s.action}” commits ~${commit:,.0f}; requires CFO approval.",
                }
            )

    # Headcount discipline — new hires must map to revenue / security / efficiency.
    for s in steps:
        if s.kind != "hire":
            continue
        fx = s.financial_effect or {}
        tied = bool(fx.get("monthly_revenue_delta")) or any(
            kw in (s.dependency + s.detail).lower() for kw in ("revenue", "soc 2", "security", "signed", "compliance", "runway-positive", "automation")
        )
        if not tied:
            blockers.append(
                {
                    "policy": "Headcount discipline",
                    "severity": "warning",
                    "source": "board_constraint + pol-hiring",
                    "month": rows[min(s.start_month_index, len(rows)) - 1].month if rows else None,
                    "detail": f"“{s.action}” is not explicitly tied to signed revenue, security compliance, or runway-positive automation.",
                }
            )
    return blockers


# --------------------------------------------------------------------------- #
# Plan assembly
# --------------------------------------------------------------------------- #
def _plan_id(title: str) -> str:
    h = hashlib.sha1(f"{title}-{time.time_ns()}".encode()).hexdigest()[:8]
    return f"plan-{time.strftime('%Y%m%d')}-{h}"


def build_plan(
    company: dict,
    *,
    title: str,
    horizon_months: int = DEFAULT_HORIZON,
    objective: str = "",
    assumptions_overrides: dict[str, float] | None = None,
    steps: list[PlaybookStep] | None = None,
    playbook_id: str | None = None,
    playbook_label: str | None = None,
    capital_plan: CapitalPlan | None = None,
    risks: list[str] | None = None,
    monitoring_triggers: list[str] | None = None,
) -> StrategicPlan:
    """Assemble a fully-computed StrategicPlan. Deterministic end to end."""
    start_month = _start_month(company)
    values = assumption_values(company, assumptions_overrides)
    if steps is None:
        steps = default_hire_steps(company, start_month)
    # normalize ordering
    for i, s in enumerate(steps):
        if not s.order:
            s.order = i + 1

    inputs = compile_steps(steps, values)
    rows, summary = project(company, inputs, horizon_months)

    # Capital plan: prefer an explicit one, else derive from financing steps.
    cap = capital_plan
    if cap is None:
        fin_steps = [s for s in steps if s.kind == "financing"]
        if fin_steps:
            s = fin_steps[0]
            amt = float((s.financial_effect or {}).get("financing_amount", 0.0))
            close_month = rows[min(s.start_month_index, len(rows)) - 1].month if rows else add_months(start_month, s.start_month_index - 1)
            cap = CapitalPlan(
                instrument="bridge",
                raise_amount=amt,
                close_month=close_month,
                close_month_index=s.start_month_index,
                notes=s.detail or s.action,
            )
        else:
            cap = CapitalPlan()
    if cap.close_month and cap.close_month_index is None:
        cap.close_month_index = month_index_of(start_month, cap.close_month)

    # runway extension attributable to financing (re-run without it)
    if cap.raise_amount and cap.runway_extension_months is None:
        no_fin_inputs = compile_steps([s for s in steps if s.kind != "financing"], values)
        _, no_fin_summary = project(company, no_fin_inputs, horizon_months)
        base_low = no_fin_summary.get("lowest_runway_months")
        with_low = summary.get("lowest_runway_months")
        if base_low is not None and with_low is not None:
            cap.runway_extension_months = round(with_low - base_low, 1)

    milestones = compute_milestones(company, rows, steps, summary)
    blockers = compute_policy_blockers(company, rows, steps, summary, cap)

    current = recompute_current_metrics(company)
    plan = StrategicPlan(
        id=_plan_id(title),
        title=title,
        horizon_months=horizon_months,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        start_month=start_month,
        playbook_id=playbook_id,
        playbook_label=playbook_label,
        objective=objective,
        company=company.get("name") or "Acme Corp",
        assumptions=describe_assumptions(company, values, assumptions_overrides),
        steps=steps,
        capital_plan=cap,
        projection=rows,
        milestones=milestones,
        policy_blockers=blockers,
        summary=summary,
        risks=risks or [],
        monitoring_triggers=monitoring_triggers or [],
        provenance={
            "data_source": COMPANY_KEY,
            "company": company.get("name"),
            "company_updated": company.get("updated"),
            "deterministic": True,
            "engine_version": ENGINE_VERSION,
            "redis_namespace": R.NS,
            "current_metrics_recomputed": current,
            "current_metrics_stored": {
                "monthly_net_burn": company.get("monthly_net_burn"),
                "monthly_gross_burn": company.get("monthly_gross_burn"),
                "runway_months": company.get("runway_months"),
            },
        },
        calc_metadata={
            "model": f"deterministic-projection/{ENGINE_VERSION}",
            "horizon_months": horizon_months,
            "assumption_values": values,
            "formulas": {
                "revenue": "revenue = mrr",
                "mrr": "mrr_t = mrr_{t-1}·(1 − churn) + new_business_t",
                "new_business": "base_new_mrr·(conversion/base_conversion)·(1+ramp)^(t-1) + unlocks + hire_revenue",
                "cogs": "cogs = revenue·(1 − gross_margin)",
                "gross_burn": "gross_burn = cogs + opex",
                "net_burn": "net_burn = gross_burn − revenue",
                "runway": "runway = cash_end / net_burn (None if net_burn ≤ 0)",
                "cash": "cash_end = cash_begin − net_burn − one_time + financing",
            },
            "thresholds": {
                "runway_floor_months": RUNWAY_FLOOR_MONTHS,
                "cash_buffer_floor": CASH_BUFFER_FLOOR,
                "board_notify_annual": BOARD_NOTIFY_ANNUAL,
                "cfo_approval_annual": CFO_APPROVAL_ANNUAL,
            },
        },
    )
    return plan


# --------------------------------------------------------------------------- #
# Compact view for the model / API summaries
# --------------------------------------------------------------------------- #
def plan_summary_card(plan: StrategicPlan) -> dict[str, Any]:
    """A small, model-friendly digest of an already-computed plan."""
    s = plan.summary
    return {
        "id": plan.id,
        "title": plan.title,
        "playbook": plan.playbook_label or plan.playbook_id,
        "horizon_months": plan.horizon_months,
        "ending_cash": s.get("ending_cash"),
        "min_cash": s.get("min_cash"),
        "min_cash_month": s.get("min_cash_month"),
        "lowest_runway_months": s.get("lowest_runway_months"),
        "runway_at_horizon": s.get("runway_at_horizon"),
        "ending_arr": s.get("ending_arr"),
        "arr_growth_pct": s.get("arr_growth_pct"),
        "cash_flow_positive_month": s.get("cash_flow_positive_month"),
        "breaches_runway_floor": s.get("breaches_runway_floor"),
        "breaches_cash_buffer": s.get("breaches_cash_buffer"),
        "policy_blockers": len(plan.policy_blockers),
        "capital_raise": plan.capital_plan.raise_amount or 0,
        "dilution_pct": plan.capital_plan.dilution_pct,
    }


def summarize_for_model(plan: StrategicPlan) -> dict[str, Any]:
    """Everything the narrative model needs — all figures already computed."""
    return {
        "title": plan.title,
        "objective": plan.objective,
        "playbook": plan.playbook_label or plan.playbook_id,
        "horizon_months": plan.horizon_months,
        "company": plan.company,
        "assumptions": [a.model_dump() for a in plan.assumptions],
        "summary": plan.summary,
        "capital_plan": plan.capital_plan.model_dump(),
        "milestones": [m.model_dump() for m in plan.milestones],
        "policy_blockers": plan.policy_blockers,
        "steps": [
            {"action": s.action, "owner": s.owner, "kind": s.kind, "month_index": s.start_month_index, "effect": s.financial_effect}
            for s in plan.steps
        ],
        "trajectory": [
            {"month": r.month, "cash_end": r.cash_end, "net_burn": r.net_burn, "runway_months": r.runway_months, "arr": r.arr}
            for r in plan.projection
        ],
        "risks": plan.risks,
        "monitoring_triggers": plan.monitoring_triggers,
    }


# --------------------------------------------------------------------------- #
# Persistence (Redis JSON + provenance stream) — reuses redis_layer primitives
# --------------------------------------------------------------------------- #
def save_plan(plan: StrategicPlan) -> str:
    payload = plan.model_dump()
    R.set_json(f"{PLAN_PREFIX}{plan.id}", payload)
    try:
        client = R.client()
        client.zadd(PLAN_INDEX, {plan.id: time.time()})
    except Exception:
        pass
    R.append_event(
        "plans",
        {
            "plan_id": plan.id,
            "title": plan.title,
            "playbook": plan.playbook_label or plan.playbook_id or "custom",
            "horizon_months": plan.horizon_months,
            "summary": plan_summary_card(plan),
            "provenance": plan.provenance,
            "source": "planning",
        },
    )
    try:
        R.publish("dashboard", {"event": "plan", "plan_id": plan.id, "title": plan.title})
    except Exception:
        pass
    return plan.id


def get_plan(plan_id: str) -> dict | None:
    return R.get_json(f"{PLAN_PREFIX}{plan_id}")


def list_plans(limit: int = 25) -> list[dict[str, Any]]:
    """Most-recent plans first, as compact cards."""
    ids: list[str] = []
    try:
        client = R.client()
        ids = list(client.zrevrange(PLAN_INDEX, 0, limit - 1))
    except Exception:
        ids = []
    if not ids:
        # Fall back to scanning plan keys (index missing or different redis-py).
        for key in R.keys(f"{PLAN_PREFIX}*"):
            ids.append(key.split(PLAN_PREFIX, 1)[-1])
    cards: list[dict[str, Any]] = []
    for pid in ids[:limit]:
        doc = get_plan(pid)
        if not doc:
            continue
        cards.append(
            {
                "id": doc.get("id"),
                "title": doc.get("title"),
                "playbook": doc.get("playbook_label") or doc.get("playbook_id"),
                "horizon_months": doc.get("horizon_months"),
                "created_at": doc.get("created_at"),
                "summary": doc.get("summary", {}),
                "policy_blockers": len(doc.get("policy_blockers") or []),
            }
        )
    cards.sort(key=lambda c: c.get("created_at") or "", reverse=True)
    return cards


# --------------------------------------------------------------------------- #
# Board narrative (the ONLY model-generated part — runs after the math is fixed)
# --------------------------------------------------------------------------- #
class _BoardNarrativeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str = Field(description="one-line board headline, <= 14 words")
    narrative: str = Field(description="4-7 sentence CFO strategic narrative grounded in the figures")
    key_metrics: list[str] = Field(description="3-5 'Label: value' bullets pulled from the figures")
    risks: list[str] = Field(description="risks the board should understand")
    asks: list[str] = Field(description="what the CFO asks the board to approve")
    recommended_decision: str = Field(description="ADOPT | ADOPT_WITH_CONDITIONS | REVISE | REJECT")


def _narrative_llm():
    """Build the same OpenAI reasoning model the council uses, lazily (keeps Weave
    auto-instrumentation and avoids importing src.agent → no circular import)."""
    provider = os.getenv("LLM_PROVIDER", "openai")
    model = os.getenv("LLM_MODEL", "gpt-5.5")
    if provider.lower() == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            temperature=0.3,
            reasoning_effort=os.getenv("LLM_REASONING_EFFORT", "xhigh"),
            verbosity=os.getenv("LLM_TEXT_VERBOSITY", "low"),
            output_version="responses/v1",
        ), f"{provider}:{model}"
    from langchain.chat_models import init_chat_model

    return init_chat_model(model, model_provider=provider, temperature=0.3), f"{provider}:{model}"


def generate_board_narrative(plan: StrategicPlan) -> BoardNarrative:
    """Summarize an already-computed plan into a CFO/board narrative via OpenAI.

    The model never sees the raw company record or invents numbers — it receives
    the fixed deterministic figures and is told to cite only those.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    basis = summarize_for_model(plan)
    model, model_id = _narrative_llm()
    structured = model.with_structured_output(_BoardNarrativeDraft)
    system = SystemMessage(
        content=(
            f"You are the Chief Financial Officer of {plan.company}, presenting a {plan.horizon_months}-month "
            "strategic operating plan to the board. The financial figures, runway, milestones, capital plan, and "
            "policy blockers below were computed deterministically by the planning engine — treat them as ground "
            "truth and cite only these numbers. Do not invent metrics, sponsor health, or external data. Be decisive "
            "and quantified, like a real board memo. If policy blockers exist, address them head-on in the asks."
        )
    )
    human = HumanMessage(content="DETERMINISTIC PLAN (authoritative figures):\n" + json.dumps(basis, default=str))
    draft: _BoardNarrativeDraft = structured.invoke([system, human])

    s = plan.summary
    key_metrics = [{"text": k} for k in (draft.key_metrics or [])] or [
        {"label": "Ending cash", "value": s.get("ending_cash")},
        {"label": "Lowest runway", "value": s.get("lowest_runway_months")},
        {"label": "Ending ARR", "value": s.get("ending_arr")},
    ]
    return BoardNarrative(
        plan_id=plan.id,
        headline=draft.headline,
        narrative=draft.narrative,
        key_metrics=key_metrics,
        risks=draft.risks or plan.risks,
        asks=draft.asks,
        recommended_decision=draft.recommended_decision,
        generated_by=model_id,
        deterministic_basis={
            "summary": plan.summary,
            "capital_plan": plan.capital_plan.model_dump(),
            "policy_blockers": plan.policy_blockers,
            "engine_version": ENGINE_VERSION,
        },
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


# --------------------------------------------------------------------------- #
# Agent entry points (used by tool wiring in src/agent.py)
# --------------------------------------------------------------------------- #
_STRATEGIC_KEYWORDS = (
    "strategic plan",
    "12-month",
    "12 month",
    "twelve month",
    "multi-month",
    "operating plan",
    "next year",
    "next 12",
    "quarters",
    "long-horizon",
    "long horizon",
    "scenario plan",
    "digital twin",
    "months of runway plan",
    "plan for the next",
    "financial plan",
    "roadmap",
)


def is_strategic_request(text: str) -> bool:
    t = (text or "").lower()
    return any(kw in t for kw in _STRATEGIC_KEYWORDS)


def detect_horizon(text: str, default: int = DEFAULT_HORIZON) -> int:
    """Pull an explicit '<n>-month'/'<n> months' horizon out of the prompt."""
    import re

    t = (text or "").lower()
    m = re.search(r"(\d{1,2})\s*[-\s]?month", t)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 36:
            return n
    if "quarter" in t:
        qm = re.search(r"(\d{1,2})\s*quarter", t)
        if qm:
            return min(36, max(3, int(qm.group(1)) * 3))
    return default


def plan_from_decision(decision: str, horizon: int | None = None, persist: bool = True) -> StrategicPlan:
    """Build (and optionally persist) a strategic plan implied by a council prompt.

    Picks the most relevant playbook by keyword; falls back to the base operating
    plan. Import of playbooks is deferred to avoid a circular import.
    """
    company = load_company()
    horizon = horizon or detect_horizon(decision)
    from src import playbooks as P

    playbook_id = P.match_playbook(decision)
    if playbook_id:
        plan = P.build_playbook_plan(company, playbook_id, horizon_months=horizon)
        plan.objective = plan.objective or f"Council strategic plan for: {decision[:160]}"
    else:
        plan = build_plan(
            company,
            title=f"{horizon}-month base operating plan",
            horizon_months=horizon,
            objective=f"Council strategic plan for: {decision[:160]}",
        )
    if persist:
        try:
            save_plan(plan)
        except Exception as exc:  # persistence must not fail the debate
            plan.provenance["persistence_warning"] = str(exc)
    return plan
