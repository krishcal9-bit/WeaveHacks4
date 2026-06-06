"""
Atlas — finance playbook library + decision-portfolio composition.

A playbook is a reusable, grounded strategy. Each one compiles into a real set of
typed ``PlaybookStep`` actions and assumption overrides, which the deterministic
engine in ``planning.py`` then projects forward — so every playbook automatically
carries its own *expected financial impact* (the projection + summary), *policy
conflicts* (the computed blockers), and *milestones*. The playbook itself supplies
the *assumptions*, *required actions*, *risks*, and *monitoring triggers*.

Everything here is grounded in the seeded/imported operating data (vendor
contracts, hiring plan, pipeline stages, security incidents, board constraints).
No LLM is used to build a playbook or its numbers — the optional CFO critique of a
portfolio is generated separately, after the math.

The seven playbooks (per the strategic-planning mandate):
  1. extend_runway ............ extend runway without freezing growth
  2. unblock_enterprise ....... unblock enterprise revenue through security spend
  3. renegotiate_vendors ...... renegotiate the vendor stack
  4. hire_against_revenue ..... hire against signed revenue
  5. financing_bridge ......... prepare a financing bridge
  6. growth_to_efficiency ..... shift from growth to efficiency
  7. recover_pipeline ......... recover from pipeline slippage
"""

from __future__ import annotations

import time
from typing import Any, Callable

from src import planning as PL
from src.planning import (
    CapitalPlan,
    DecisionPortfolio,
    PlaybookStep,
    StrategicPlan,
)

# Plan months (start = month after company.updated 2026-06-01 → month 1 = 2026-07):
#   m1 = 2026-07 (CS hires planned), m2 = 2026-08 (Datadog renewal, Eng hires),
#   m3 = 2026-09 (Sales hires, Salesforce renewal).


class PlaybookSpec:
    """A playbook definition: metadata + a builder that returns plan kwargs."""

    def __init__(
        self,
        id: str,
        label: str,
        summary: str,
        builder: Callable[[dict, dict[str, Any]], dict[str, Any]],
        keywords: tuple[str, ...] = (),
    ):
        self.id = id
        self.label = label
        self.summary = summary
        self.builder = builder
        self.keywords = keywords


# --------------------------------------------------------------------------- #
# Playbook builders — each returns kwargs for planning.build_plan
# --------------------------------------------------------------------------- #
def _pb_extend_runway(company: dict, params: dict[str, Any]) -> dict[str, Any]:
    """Trim vendor waste, slow the most discretionary hire, hold growth dials."""
    eng_delay = int(params.get("eng_delay_months", 2))
    steps = [
        PlaybookStep(
            order=1, action="Renegotiate Datadog back to committed tier at renewal", owner="procurement",
            kind="vendor_savings", start_month_index=2,
            financial_effect={"monthly_cost_delta": 5_500.0},
            dependency="Datadog renewal 2026-08-01; 45-day notice", reversible=True,
            detail="Datadog is ~40% over its committed tier ($15k/mo); right-size at the 2026-08 renewal.",
        ),
        PlaybookStep(
            order=2, action="Reclaim 9 underused Salesforce seats at renewal", owner="procurement",
            kind="vendor_savings", start_month_index=3,
            financial_effect={"monthly_cost_delta": 1_750.0},
            dependency="Salesforce renewal 2026-09-30", reversible=True,
            detail="32 seats, ~9 underused; drop seats at renewal.",
        ),
        PlaybookStep(
            order=3, action="Trim discretionary G&A spend", owner="treasury",
            kind="cut", start_month_index=1,
            financial_effect={"monthly_cost_delta": 18_000.0},
            dependency="Non-headcount discretionary G&A", reversible=True,
            detail="Defer non-essential G&A without touching growth or retention.",
        ),
        PlaybookStep(
            order=4, action="Keep Customer Success hires (retention-critical)", owner="fpna",
            kind="hire", start_month_index=1,
            financial_effect={"monthly_cost_delta": 42_000.0, "roles": 3.0, "ramp_months": 2.0},
            dependency="enterprise onboarding backlog", reversible=True,
            detail="Retains Enterprise 3PL NDR (1.24); not frozen.",
        ),
        PlaybookStep(
            order=5, action=f"Delay Engineering hires by {eng_delay} months", owner="fpna",
            kind="hire", start_month_index=2 + eng_delay,
            financial_effect={"monthly_cost_delta": 95_000.0, "roles": 5.0, "ramp_months": 2.0},
            dependency="SOC 2 roadmap and customer integrations", reversible=True,
            detail="Slip the largest cohort to extend runway while preserving the roadmap.",
        ),
    ]
    return {
        "title": "Extend runway without freezing growth",
        "objective": "Add months of runway by cutting vendor waste and slowing the largest hire while holding growth and retention.",
        "steps": steps,
        "risks": [
            "Delaying Engineering hires can slip the SOC 2 roadmap that gates enterprise revenue.",
            "Datadog right-sizing depends on a successful renewal redline before the 45-day notice window.",
            "Discretionary G&A cuts are partially reversible and may not fully stick.",
        ],
        "monitoring_triggers": [
            "Net new MRR < $30K for two consecutive months → revisit growth spend.",
            "Datadog redline not agreed 45 days before 2026-08-01 → escalate to CFO.",
            "Runway projection dips below 9 months in any month → trigger financing bridge.",
        ],
    }


def _pb_unblock_enterprise(company: dict, params: dict[str, Any]) -> dict[str, Any]:
    """Invest in SOC 2 to release security-blocked enterprise procurements."""
    unlock_mrr = float(params.get("unlock_mrr", 62_000.0))  # ~ Procurement-stage weighted ARR/12
    steps = [
        PlaybookStep(
            order=1, action="Fund SOC 2 Type II audit + evidence tooling", owner="risk",
            kind="spend", start_month_index=1,
            financial_effect={"one_time_cost": 120_000.0, "monthly_cost_delta": 12_000.0},
            dependency="Open control gap from 2026-04-09 incident (blocked 2 procurements)", reversible=False,
            detail="Closes the SOC 2 evidence gap flagged in security incidents and AUD-24.",
        ),
        PlaybookStep(
            order=2, action="Keep Engineering hires on the security/integration roadmap", owner="fpna",
            kind="hire", start_month_index=2,
            financial_effect={"monthly_cost_delta": 95_000.0, "roles": 5.0, "ramp_months": 2.0},
            dependency="SOC 2 roadmap and customer integrations", reversible=True,
            detail="Tied to compliance + signed integration work (board headcount rule satisfied).",
        ),
        PlaybookStep(
            order=3, action="Release security-blocked enterprise pipeline", owner="fpna",
            kind="revenue_unlock", start_month_index=3,
            financial_effect={
                "monthly_revenue_delta": unlock_mrr,
                "revenue_ramp_months": 4,
                "revenue_start_index": 3,
            },
            dependency="SOC 2 evidence complete; Procurement-stage deals ($994K weighted ARR)", reversible=False,
            detail="Two enterprise procurements were blocked by SOC 2 evidence; this releases them post-audit.",
        ),
    ]
    return {
        "title": "Unblock enterprise revenue through security spend",
        "objective": "Spend on SOC 2 to release the enterprise procurements blocked by the open control gap, prioritized by board policy.",
        "steps": steps,
        "risks": [
            "SOC 2 timeline can slip, delaying the revenue unlock.",
            "Released pipeline may convert below the modeled weighted value.",
            "Up-front + recurring security spend exceeds the $150K board-notification threshold.",
        ],
        "monitoring_triggers": [
            "SOC 2 Type I evidence complete by month 3 (gates the unlock).",
            "At least one blocked enterprise procurement re-enters contracting by month 4.",
            "Security control gap from 2026-04-09 marked remediated.",
        ],
    }


def _pb_renegotiate_vendors(company: dict, params: dict[str, Any]) -> dict[str, Any]:
    """Work the whole SaaS stack: Datadog, Salesforce seats, minor consolidations."""
    steps = [
        PlaybookStep(
            order=1, action="Right-size Datadog at renewal", owner="procurement",
            kind="vendor_savings", start_month_index=2,
            financial_effect={"monthly_cost_delta": 6_000.0},
            dependency="Renewal 2026-08-01; switching cost $70K caps leverage", reversible=True,
            detail="~40% over committed tier; redline usage-based overage.",
        ),
        PlaybookStep(
            order=2, action="Reclaim 9 underused Salesforce seats", owner="procurement",
            kind="vendor_savings", start_month_index=3,
            financial_effect={"monthly_cost_delta": 1_750.0},
            dependency="Renewal 2026-09-30", reversible=True,
            detail="9 of 32 seats underused.",
        ),
        PlaybookStep(
            order=3, action="Consolidate Gong + Figma + GitHub tiers", owner="procurement",
            kind="vendor_savings", start_month_index=2,
            financial_effect={"monthly_cost_delta": 1_400.0},
            dependency="Renewals 2026-10 to 2026-12", reversible=True,
            detail="Trim seats/tiers on smaller tools.",
        ),
        PlaybookStep(
            order=4, action="Re-tender AWS committed-use at expansion", owner="treasury",
            kind="vendor_savings", start_month_index=4,
            financial_effect={"monthly_cost_delta": 2_500.0},
            dependency="Committed-use discount already in place (limited headroom)", reversible=True,
            detail="AWS is ~22% of gross burn; pursue an incremental committed-use tier.",
        ),
    ]
    return {
        "title": "Renegotiate the vendor stack",
        "objective": "Convert vendor waste into recurring monthly savings across the SaaS portfolio without service loss.",
        "steps": steps,
        "risks": [
            "Datadog switching cost ($70K) and production-telemetry dependency limit negotiating leverage.",
            "Seat reclamation can disrupt revenue teams if mis-scoped.",
            "AWS savings are thin because a committed-use discount is already in place.",
        ],
        "monitoring_triggers": [
            "Confirm Datadog redline before the 45-day notice for the 2026-08-01 renewal.",
            "Track realized vs. modeled monthly savings each renewal.",
            "Three contracts lacking owner attestation (AUD-17) closed before renewal.",
        ],
    }


def _pb_hire_against_revenue(company: dict, params: dict[str, Any]) -> dict[str, Any]:
    """Release sales/CS capacity only against signed/contracting revenue."""
    signed_mrr = float(params.get("signed_mrr", 64_000.0))  # ~ Contracting weighted ARR ($774K)/12
    steps = [
        PlaybookStep(
            order=1, action="Hire 3 Customer Success against contracting backlog", owner="fpna",
            kind="hire", start_month_index=1,
            financial_effect={
                "monthly_cost_delta": 42_000.0, "roles": 3.0, "ramp_months": 2.0,
                "monthly_revenue_delta": signed_mrr * 0.4, "revenue_ramp_months": 3,
            },
            dependency="Contracting-stage deals: 3 opps, $910K ARR ($774K weighted)", reversible=True,
            detail="Protects/expands NDR on deals already in contracting.",
        ),
        PlaybookStep(
            order=2, action="Hire 2 Sales reps against signed pipeline", owner="fpna",
            kind="hire", start_month_index=3,
            financial_effect={
                "monthly_cost_delta": 38_000.0, "roles": 2.0, "ramp_months": 2.0,
                "monthly_revenue_delta": signed_mrr * 0.6, "revenue_ramp_months": 4,
            },
            dependency="Pipeline conversion above 32%", reversible=True,
            detail="Capacity released only as contracting deals sign.",
        ),
    ]
    return {
        "title": "Hire against signed revenue",
        "objective": "Add only revenue-linked headcount, gated to the Contracting-stage pipeline, satisfying the board headcount rule.",
        "steps": steps,
        "assumptions_overrides": {"pipeline_conversion": 0.36},
        "risks": [
            "Contracting deals can slip on implementation capacity (noted pipeline risk).",
            "Sales ramp (3-4 months) lags the cost, pressuring near-term burn.",
            "If conversion falls below 32%, the board hiring condition is no longer met.",
        ],
        "monitoring_triggers": [
            "Release each hire tranche only as contracts sign.",
            "Pipeline conversion stays above 32%.",
            "New-hire revenue contribution on plan by ramp month 4.",
        ],
    }


def _pb_financing_bridge(company: dict, params: dict[str, Any]) -> dict[str, Any]:
    """Raise a bridge so growth + roadmap continue inside the runway guardrail."""
    amount = float(params.get("raise_amount", 3_000_000.0))
    close_idx = int(params.get("close_month_index", 4))
    post_money = float(((company.get("last_raise") or {}).get("post_money")) or 34_000_000.0)
    dilution = round(amount / (post_money + amount) * 100, 1)
    steps = [
        PlaybookStep(
            order=1, action=f"Close a ${amount/1e6:.1f}M convertible bridge", owner="treasury",
            kind="financing", start_month_index=close_idx,
            financial_effect={"financing_amount": amount},
            dependency="Board carve-out: runway < 9 months allowed only with a signed term sheet", reversible=False,
            detail=f"Bridge off the ${post_money/1e6:.0f}M Series A post-money; ~{dilution}% notional dilution.",
        ),
        PlaybookStep(
            order=2, action="Keep Engineering + CS hires on schedule", owner="fpna",
            kind="hire", start_month_index=1,
            financial_effect={"monthly_cost_delta": 42_000.0, "roles": 3.0, "ramp_months": 2.0},
            dependency="enterprise onboarding backlog", reversible=True,
            detail="Bridge funds continued investment instead of cuts.",
        ),
        PlaybookStep(
            order=3, action="Fund Engineering cohort on the roadmap", owner="fpna",
            kind="hire", start_month_index=2,
            financial_effect={"monthly_cost_delta": 95_000.0, "roles": 5.0, "ramp_months": 2.0},
            dependency="SOC 2 roadmap and customer integrations", reversible=True,
            detail="Investment continues under the bridge.",
        ),
    ]
    capital_plan = CapitalPlan(
        instrument="bridge",
        raise_amount=amount,
        close_month_index=close_idx,
        dilution_pct=dilution,
        triggers=[
            "Arm the raise when projected runway approaches 9 months.",
            "Signed term sheet required before runway crosses the guardrail.",
        ],
        notes=f"Convertible bridge off the {post_money/1e6:.0f}M post-money Series A.",
    )
    return {
        "title": "Prepare a financing bridge",
        "objective": "Raise a bridge to stay inside the 9-month runway guardrail while continuing to invest in growth and the roadmap.",
        "steps": steps,
        "capital_plan": capital_plan,
        "risks": [
            f"Bridge carries ~{dilution}% notional dilution and conversion terms.",
            "Financing markets can move; a slipped close re-exposes the runway guardrail.",
            "Bridge debt can add covenants that constrain spend.",
        ],
        "monitoring_triggers": [
            "Signed term sheet in hand before runway crosses 9 months.",
            f"Bridge closes by plan month {close_idx}.",
            "Use of proceeds tracked against the roadmap, not opex creep.",
        ],
    }


def _pb_growth_to_efficiency(company: dict, params: dict[str, Any]) -> dict[str, Any]:
    """Trade growth for runway: cut S&M, lift margin, slow new business."""
    steps = [
        PlaybookStep(
            order=1, action="Cut sales & marketing program spend", owner="treasury",
            kind="cut", start_month_index=1,
            financial_effect={"monthly_cost_delta": 60_000.0},
            dependency="Magic number 0.8 — efficiency below target", reversible=True,
            detail="Reduce S&M to improve burn efficiency; accepts slower growth.",
        ),
        PlaybookStep(
            order=2, action="Freeze the Sales hiring cohort", owner="fpna",
            kind="cut", start_month_index=3,
            financial_effect={"monthly_cost_delta": 0.0},
            dependency="Pipeline conversion gating not met", reversible=True,
            detail="Hold the 2 Sales roles until efficiency recovers.",
        ),
        PlaybookStep(
            order=3, action="COGS optimization to lift gross margin", owner="fpna",
            kind="policy", start_month_index=2,
            financial_effect={},
            dependency="Infra + support cost program", reversible=True,
            detail="Drive gross margin from 78% toward 80%.",
        ),
        PlaybookStep(
            order=4, action="Keep a lean Customer Success bench", owner="fpna",
            kind="hire", start_month_index=1,
            financial_effect={"monthly_cost_delta": 28_000.0, "roles": 2.0, "ramp_months": 2.0},
            dependency="Retain enterprise NDR", reversible=True,
            detail="Two CS hires (down from three) to protect retention.",
        ),
    ]
    return {
        "title": "Shift from growth to efficiency",
        "objective": "Maximize runway and burn efficiency by cutting S&M and lifting margin, accepting slower ARR growth.",
        "steps": steps,
        "assumptions_overrides": {"mrr_growth_mom": 0.05, "pipeline_conversion": 0.24, "gross_margin": 0.80},
        "risks": [
            "Slower growth can depress the next-round valuation and momentum.",
            "S&M cuts are hard to reverse without re-ramp cost.",
            "Margin gains may take longer than modeled to realize.",
        ],
        "monitoring_triggers": [
            "Net burn falls below $300K/month within two quarters.",
            "Magic number recovers toward 1.0.",
            "Logo churn does not rise as S&M is cut.",
        ],
    }


def _pb_recover_pipeline(company: dict, params: dict[str, Any]) -> dict[str, Any]:
    """Respond to forecast slippage (AUD-21) and reduce churn while re-baselining."""
    steps = [
        PlaybookStep(
            order=1, action="Re-baseline technical-validation conversion (AUD-21)", owner="fpna",
            kind="policy", start_month_index=1,
            financial_effect={},
            dependency="AUD-21: forecast overstates conversion by 8-12 points", reversible=True,
            detail="Correct the forecast so planning uses realistic conversion.",
        ),
        PlaybookStep(
            order=2, action="Add a Customer Success hire to cut mid-market churn", owner="fpna",
            kind="hire", start_month_index=1,
            financial_effect={
                "monthly_cost_delta": 28_000.0, "roles": 2.0, "ramp_months": 2.0,
                "monthly_revenue_delta": 9_000.0, "revenue_ramp_months": 3,
            },
            dependency="Mid-market fulfillment churn 2.6%, support response times", reversible=True,
            detail="Targets the churn driver behind the slippage.",
        ),
        PlaybookStep(
            order=3, action="Pipeline hygiene + security-review fast lane", owner="procurement",
            kind="spend", start_month_index=2,
            financial_effect={"monthly_cost_delta": 6_000.0, "monthly_revenue_delta": 14_000.0, "revenue_ramp_months": 4},
            dependency="Technical-validation stage: security review bottlenecks", reversible=True,
            detail="Unblock the technical-validation stage to claw back weighted pipeline.",
        ),
    ]
    return {
        "title": "Recover from pipeline slippage",
        "objective": "Stabilize after a conversion miss: re-baseline the forecast, cut churn, and unblock the technical-validation stage.",
        "steps": steps,
        "assumptions_overrides": {"pipeline_conversion": 0.24, "logo_churn_mom": 0.022},
        "risks": [
            "Forecast credibility is already flagged (AUD-21); further slippage compounds runway risk.",
            "Churn interventions take a quarter to show in NDR.",
            "Security-review bottlenecks may persist without SOC 2 progress.",
        ],
        "monitoring_triggers": [
            "Weekly pipeline conversion tracked against the 32% target.",
            "Mid-market logo churn trends back below 2%.",
            "Technical-validation cycle time falls after the security fast-lane.",
        ],
    }


PLAYBOOKS: dict[str, PlaybookSpec] = {
    "extend_runway": PlaybookSpec(
        "extend_runway", "Extend runway without freezing growth",
        "Cut vendor waste and slow the largest hire while holding growth and retention.",
        _pb_extend_runway,
        ("extend runway", "more runway", "stretch runway", "conserve cash", "without freezing growth"),
    ),
    "unblock_enterprise": PlaybookSpec(
        "unblock_enterprise", "Unblock enterprise revenue through security spend",
        "Invest in SOC 2 to release security-blocked enterprise procurements.",
        _pb_unblock_enterprise,
        ("soc 2", "soc2", "security", "enterprise revenue", "unblock", "compliance", "audit evidence"),
    ),
    "renegotiate_vendors": PlaybookSpec(
        "renegotiate_vendors", "Renegotiate the vendor stack",
        "Work the SaaS portfolio (Datadog, Salesforce, minor tools, AWS) for recurring savings.",
        _pb_renegotiate_vendors,
        ("vendor", "renegotiate", "datadog", "saas", "contract", "renewal", "procurement savings"),
    ),
    "hire_against_revenue": PlaybookSpec(
        "hire_against_revenue", "Hire against signed revenue",
        "Add only revenue-linked headcount gated to the contracting pipeline.",
        _pb_hire_against_revenue,
        ("hire", "hiring", "headcount", "add reps", "sales capacity", "against revenue", "grow the team"),
    ),
    "financing_bridge": PlaybookSpec(
        "financing_bridge", "Prepare a financing bridge",
        "Raise a bridge to stay inside the runway guardrail while continuing to invest.",
        _pb_financing_bridge,
        ("bridge", "financing", "raise", "fundraise", "term sheet", "venture debt", "extend the round"),
    ),
    "growth_to_efficiency": PlaybookSpec(
        "growth_to_efficiency", "Shift from growth to efficiency",
        "Cut S&M and lift margin to maximize runway, accepting slower growth.",
        _pb_growth_to_efficiency,
        ("efficiency", "cut burn", "reduce burn", "default alive", "profitability", "slow growth", "tighten"),
    ),
    "recover_pipeline": PlaybookSpec(
        "recover_pipeline", "Recover from pipeline slippage",
        "Re-baseline the forecast, cut churn, and unblock the technical-validation stage.",
        _pb_recover_pipeline,
        ("pipeline slippage", "missed forecast", "conversion drop", "slipped", "churn", "recover pipeline", "forecast miss"),
    ),
}


def catalog() -> list[dict[str, Any]]:
    return [
        {"id": pb.id, "label": pb.label, "summary": pb.summary, "keywords": list(pb.keywords)}
        for pb in PLAYBOOKS.values()
    ]


def match_playbook(decision: str) -> str | None:
    """Pick the most relevant playbook for a free-text decision (keyword scoring)."""
    t = (decision or "").lower()
    best, best_score = None, 0
    for pb in PLAYBOOKS.values():
        score = sum(1 for kw in pb.keywords if kw in t)
        if score > best_score:
            best, best_score = pb.id, score
    return best


def build_playbook_plan(
    company: dict, playbook_id: str, *, horizon_months: int = PL.DEFAULT_HORIZON, **params: Any
) -> StrategicPlan:
    if playbook_id not in PLAYBOOKS:
        raise KeyError(f"Unknown playbook '{playbook_id}'. Known: {list(PLAYBOOKS)}")
    spec = PLAYBOOKS[playbook_id]
    kwargs = spec.builder(company, params)
    return PL.build_plan(
        company,
        title=kwargs["title"],
        horizon_months=horizon_months,
        objective=kwargs.get("objective", ""),
        assumptions_overrides=kwargs.get("assumptions_overrides"),
        steps=kwargs["steps"],
        playbook_id=spec.id,
        playbook_label=spec.label,
        capital_plan=kwargs.get("capital_plan"),
        risks=kwargs.get("risks"),
        monitoring_triggers=kwargs.get("monitoring_triggers"),
    )


# --------------------------------------------------------------------------- #
# Decision portfolio — compare playbooks, recommend a sequenced portfolio
# --------------------------------------------------------------------------- #
PORTFOLIO_WEIGHTS = {
    "runway_safety": 0.28,   # lowest runway across the horizon (higher is safer)
    "liquidity": 0.22,       # minimum cash (higher is safer)
    "growth": 0.20,          # ending ARR (higher is better)
    "compliance": 0.15,      # fewer/less-severe policy blockers
    "efficiency": 0.10,      # reaches cash-flow positive sooner
    "dilution": 0.05,        # lower dilution is better
}

_SEVERITY_WEIGHT = {"info": 1, "warning": 2, "high": 4, "critical": 8}


def _normalize(values: list[float], higher_is_better: bool) -> list[float]:
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5 for _ in values]
    return [((v - lo) / (hi - lo)) if higher_is_better else (1 - (v - lo) / (hi - lo)) for v in values]


def _candidate_metrics(plan: StrategicPlan, horizon: int) -> dict[str, float]:
    s = plan.summary
    lowest_runway = s.get("lowest_runway_months")
    # If never burning (cash-flow positive throughout), treat runway as very safe.
    runway_metric = float(lowest_runway) if lowest_runway is not None else float(horizon * 3)
    cfp = s.get("cash_flow_positive_month")
    cfp_idx = next((r.month_index for r in plan.projection if r.month == cfp), horizon + 1) if cfp else horizon + 1
    blocker_load = sum(_SEVERITY_WEIGHT.get(b.get("severity", "info"), 1) for b in plan.policy_blockers)
    return {
        "runway_safety": runway_metric,
        "liquidity": float(s.get("min_cash") or 0.0),
        "growth": float(s.get("ending_arr") or 0.0),
        "compliance": float(blocker_load),
        "efficiency": float(cfp_idx),
        "dilution": float(plan.capital_plan.dilution_pct or 0.0),
    }


def compare_playbooks(
    company: dict, playbook_ids: list[str], decision: str, *, horizon_months: int = PL.DEFAULT_HORIZON, persist: bool = True
) -> tuple[DecisionPortfolio, list[StrategicPlan]]:
    """Score multiple playbooks for one decision and recommend a sequenced portfolio.

    Fully deterministic: scoring is a weighted, min-max-normalized blend of computed
    plan metrics. Returns the portfolio plus the underlying plans (so callers can
    persist them or attach an LLM critique afterwards).
    """
    ids = [pid for pid in playbook_ids if pid in PLAYBOOKS] or list(PLAYBOOKS)
    plans = [build_playbook_plan(company, pid, horizon_months=horizon_months) for pid in ids]
    metrics = [_candidate_metrics(p, horizon_months) for p in plans]

    # Normalize each criterion across candidates.
    norm: dict[str, list[float]] = {}
    for crit in PORTFOLIO_WEIGHTS:
        higher_better = crit not in ("compliance", "efficiency", "dilution")
        norm[crit] = _normalize([m[crit] for m in metrics], higher_better)

    candidates: list[dict[str, Any]] = []
    for i, plan in enumerate(plans):
        breakdown = {crit: round(norm[crit][i], 3) for crit in PORTFOLIO_WEIGHTS}
        score = round(sum(breakdown[c] * w for c, w in PORTFOLIO_WEIGHTS.items()) * 100, 1)
        candidates.append(
            {
                "playbook_id": plan.playbook_id,
                "label": plan.playbook_label,
                "plan_id": plan.id,
                "score": score,
                "score_breakdown": breakdown,
                "metrics": {k: round(v) if k != "dilution" else v for k, v in metrics[i].items()},
                "card": PL.plan_summary_card(plan),
                "policy_blockers": plan.policy_blockers,
            }
        )
    candidates.sort(key=lambda c: c["score"], reverse=True)
    ranking = [c["playbook_id"] for c in candidates]

    portfolio = _recommend_portfolio(candidates)
    tradeoffs = _tradeoffs(candidates)
    rationale = (
        f"Scored {len(candidates)} playbooks on a weighted blend "
        f"({', '.join(f'{c}:{int(w*100)}%' for c, w in PORTFOLIO_WEIGHTS.items())}). "
        f"Top: {candidates[0]['label']} ({candidates[0]['score']}). "
        "Portfolio combines the strongest standalone strategy with no-regret and stabilizing moves."
    )

    dp = DecisionPortfolio(
        id=f"portfolio-{time.strftime('%Y%m%d')}-{abs(hash(decision)) % 10000:04d}",
        decision=decision,
        horizon_months=horizon_months,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        candidates=candidates,
        ranking=ranking,
        recommended_portfolio=portfolio,
        rationale=rationale,
        tradeoffs=tradeoffs,
        scoring_weights=PORTFOLIO_WEIGHTS,
        provenance={
            "data_source": PL.COMPANY_KEY,
            "deterministic": True,
            "engine_version": PL.ENGINE_VERSION,
            "playbooks": ids,
        },
    )
    if persist:
        for plan in plans:
            try:
                PL.save_plan(plan)
            except Exception:
                pass
        try:
            R = PL.R
            R.append_event(
                "portfolios",
                {"decision": decision[:160], "ranking": ranking, "portfolio": portfolio, "source": "planning"},
            )
        except Exception:
            pass
    return dp, plans


def _recommend_portfolio(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic portfolio rule: a primary + complementary/stabilizing moves."""
    by_id = {c["playbook_id"]: c for c in candidates}
    picks: list[dict[str, Any]] = []
    primary = candidates[0]
    picks.append({"playbook_id": primary["playbook_id"], "label": primary["label"], "role": "primary", "weight": 0.6})

    # No-regret: vendor renegotiation is low-risk recurring savings if available.
    if "renegotiate_vendors" in by_id and primary["playbook_id"] != "renegotiate_vendors":
        picks.append({"playbook_id": "renegotiate_vendors", "label": by_id["renegotiate_vendors"]["label"], "role": "no_regret", "weight": 0.2})

    # Stabilizer: if the primary breaches the runway/cash guardrails, add a bridge
    # or runway extension to cover the carve-out.
    breaches = primary["card"].get("breaches_runway_floor") or primary["card"].get("breaches_cash_buffer")
    if breaches:
        for stabilizer in ("financing_bridge", "extend_runway"):
            if stabilizer in by_id and stabilizer != primary["playbook_id"]:
                picks.append({"playbook_id": stabilizer, "label": by_id[stabilizer]["label"], "role": "stabilizer", "weight": 0.2})
                break

    # Normalize weights to sum to 1.0 for clarity.
    total = sum(p["weight"] for p in picks)
    for p in picks:
        p["weight"] = round(p["weight"] / total, 2)
    return picks


def _tradeoffs(candidates: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    best_growth = max(candidates, key=lambda c: c["metrics"]["growth"])
    best_runway = max(candidates, key=lambda c: c["metrics"]["runway_safety"])
    least_dilutive = min(candidates, key=lambda c: c["metrics"]["dilution"])
    out.append(f"Most growth: {best_growth['label']} (ending ARR ${best_growth['metrics']['growth']:,.0f}).")
    out.append(f"Safest runway: {best_runway['label']} (lowest runway {best_runway['metrics']['runway_safety']:.1f} mo).")
    if any(c["metrics"]["dilution"] for c in candidates):
        out.append(f"Least dilutive: {least_dilutive['label']} ({least_dilutive['metrics']['dilution']}% dilution).")
    blocked = [c for c in candidates if c["policy_blockers"]]
    if blocked:
        out.append(f"{len(blocked)} of {len(candidates)} playbooks trip a policy guardrail and need conditions.")
    return out
