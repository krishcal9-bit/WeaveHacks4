"""
Council influence — dynamic per-agent deliberation weights for the CFO.

The council is self-improving: analysts earn unequal influence based on how well
they grounded positions in this debate, how they performed in cross-examination,
and their rolling reliability history in Redis. Influence is assigned *before*
CFO synthesis so the ruling is weighted, not equal-voice.
"""

from __future__ import annotations

import time
from typing import Any

from src import redis_layer as R

ANALYSTS = ["treasury", "fpna", "risk", "procurement"]
DEFAULT_BASELINE = {"treasury": 72, "fpna": 78, "risk": 68, "procurement": 65}
HISTORY_BLEND = 0.35  # how much of the next historical score comes from this run
BLEND_FORMULA = {
    "historical_reliability": 0.40,
    "grounding_signal": 0.35,
    "debate_signal": 0.25,
}


def _history_key(company_id: str) -> str:
    return f"{R.NS}:evaluation:reliability_history:{company_id}"


def _normalize_agent_id(value: str) -> str:
    normalized = (value or "").lower().replace("&", "and").replace("-", "_").replace(" ", "_")
    aliases = {
        "financial_planning_and_analysis": "fpna",
        "fpa": "fpna",
        "fp&a": "fpna",
        "risk_audit": "risk",
        "risk_and_audit": "risk",
        "risk_&_audit": "risk",
    }
    return aliases.get(normalized, normalized)


def historical_reliability(company_id: str, context: dict | None = None) -> dict[str, int]:
    """Rolling per-analyst reliability used as a prior for influence assignment."""
    stored = R.get_json(_history_key(company_id)) or {}
    scores = stored.get("scores") if isinstance(stored, dict) else None
    if isinstance(scores, dict) and scores:
        return {agent: int(scores.get(agent, DEFAULT_BASELINE.get(agent, 60))) for agent in ANALYSTS}

    financials = (context or {}).get("financials") or {}
    baseline = financials.get("agent_reliability_baseline") or {}
    if isinstance(baseline, dict) and baseline:
        return {agent: int(baseline.get(agent, DEFAULT_BASELINE.get(agent, 60))) for agent in ANALYSTS}
    return dict(DEFAULT_BASELINE)


def update_historical_reliability(
    company_id: str,
    scorecard: list[dict],
    influence: dict | None = None,
) -> dict[str, int]:
    """EMA-blend this run's reliability scores and earned influence into Redis for the next debate."""
    prior = historical_reliability(company_id)
    influence_by_agent = {
        item.get("agent_id"): item for item in (influence or {}).get("weights") or [] if item.get("agent_id")
    }
    next_scores: dict[str, int] = {}
    for agent in ANALYSTS:
        current = next((item for item in scorecard if item.get("agent_id") == agent), None)
        reliability_obs = int(current.get("reliability", prior.get(agent, 60))) if current else prior.get(agent, 60)
        infl_weight = int((influence_by_agent.get(agent) or {}).get("influence_weight") or 25)
        influence_obs = min(100, infl_weight * 4)
        observed = round(0.62 * reliability_obs + 0.38 * influence_obs)
        blended = round((1 - HISTORY_BLEND) * prior.get(agent, observed) + HISTORY_BLEND * observed)
        next_scores[agent] = max(0, min(100, blended))

    payload = {
        "company_id": company_id,
        "scores": next_scores,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runs_recorded": int((R.get_json(_history_key(company_id)) or {}).get("runs_recorded", 0)) + 1,
    }
    R.set_json(_history_key(company_id), payload)
    return next_scores


def debate_signals(
    *,
    positions: list[dict],
    challenge_report: dict | None,
    debate_turns: list[dict],
) -> dict[str, dict[str, int]]:
    """Derive per-analyst grounding and debate-performance signals from this run."""
    signals = {
        agent: {"grounding_signal": 55, "debate_signal": 50, "cited_metrics": 0}
        for agent in ANALYSTS
    }

    for finding in (challenge_report or {}).get("findings") or []:
        role = _normalize_agent_id(str(finding.get("role") or ""))
        if role not in signals:
            continue
        grounding = int(finding.get("grounding_score") or 55)
        if not finding.get("cited_enough_numbers"):
            grounding = max(35, grounding - 12)
        signals[role]["grounding_signal"] = max(0, min(100, grounding))

    for pos in positions:
        agent = _normalize_agent_id(str(pos.get("agent") or pos.get("role") or ""))
        if agent not in signals:
            continue
        cited = len(pos.get("cited_metrics") or [])
        signals[agent]["cited_metrics"] = cited
        signals[agent]["grounding_signal"] = max(
            signals[agent]["grounding_signal"],
            min(100, 42 + cited * 14),
        )

    for turn in debate_turns:
        challenger = _normalize_agent_id(str(turn.get("from_role") or ""))
        challenged = _normalize_agent_id(str(turn.get("to_role") or ""))
        if challenger in signals:
            signals[challenger]["debate_signal"] = min(100, signals[challenger]["debate_signal"] + 8)
        if challenged in signals:
            signals[challenged]["debate_signal"] = max(20, signals[challenged]["debate_signal"] - 5)

    return signals


def influence_spread(weights: list[dict]) -> int:
    values = [int(item.get("influence_weight") or 0) for item in weights]
    if not values:
        return 0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return round(variance**0.5)


def rank_weights(weights: list[dict]) -> list[dict]:
    return sorted(weights, key=lambda item: int(item.get("influence_weight") or 0), reverse=True)


def apply_signal_floors(weights: list[dict], signals: dict[str, dict[str, int]]) -> list[dict]:
    """Cap under-grounded analysts so weak ideas cannot dominate the CFO ruling."""
    adjusted: list[dict] = []
    for item in weights:
        agent = item.get("agent_id")
        if agent not in signals:
            adjusted.append(dict(item))
            continue
        grounding = int(signals[agent]["grounding_signal"])
        cap = 100
        if grounding < 42:
            cap = 12
        elif grounding < 52:
            cap = 18
        elif grounding < 62:
            cap = 28
        adjusted.append({**item, "influence_weight": min(int(item.get("influence_weight") or 0), cap)})
    return normalize_analyst_weights(adjusted)


def reconcile_with_baseline(llm_weights: list[dict], baseline_weights: list[dict], llm_share: float = 0.65) -> list[dict]:
    """Blend model-assigned weights with deterministic signals so influence stays grounded."""
    baseline_by_agent = {item["agent_id"]: item for item in baseline_weights}
    merged: list[dict] = []
    for item in llm_weights:
        agent = item.get("agent_id")
        base = baseline_by_agent.get(agent) or {}
        llm_value = float(item.get("influence_weight") or 0)
        base_value = float(base.get("influence_weight") or 25)
        merged.append(
            {
                **item,
                "influence_weight": round(llm_share * llm_value + (1 - llm_share) * base_value),
                "grounding_signal": item.get("grounding_signal") or base.get("grounding_signal"),
                "debate_signal": item.get("debate_signal") or base.get("debate_signal"),
                "historical_reliability": item.get("historical_reliability") or base.get("historical_reliability"),
            }
        )
    return merged


def confidence_adjustment(
    *,
    base_confidence: int,
    influence: dict | None,
    evidence_gaps: list | None,
) -> tuple[int, dict[str, int | str]]:
    """Small deterministic confidence nudge from council cohesion and evidence quality."""
    spread = int((influence or {}).get("spread") or 0)
    gap_count = len(evidence_gaps or [])
    adjustment = 0
    if spread >= 16:
        adjustment += 4
    elif spread <= 8:
        adjustment -= 5
    adjustment -= min(14, gap_count * 2)
    adjusted = max(0, min(100, int(base_confidence) + adjustment))
    return adjusted, {
        "base_confidence": int(base_confidence),
        "adjusted_confidence": adjusted,
        "spread": spread,
        "evidence_gap_count": gap_count,
        "delta": adjusted - int(base_confidence),
    }


def compute_baseline_influence(
    *,
    historical: dict[str, int],
    signals: dict[str, dict[str, int]],
    decision_type: str = "general",
) -> list[dict]:
    """Deterministic influence weights when the LLM path is skipped."""
    type_bias = {
        "vendor_renewal": {"procurement": 10, "treasury": 4},
        "hiring_plan": {"fpna": 10, "treasury": 6},
        "capital_allocation": {"fpna": 8, "treasury": 8},
        "security_blocker": {"risk": 12, "procurement": 4},
        "pricing_change": {"fpna": 10, "procurement": 4},
        "financing_scenario": {"treasury": 12, "fpna": 6},
    }.get(decision_type, {})

    raw: dict[str, float] = {}
    for agent in ANALYSTS:
        hist = float(historical.get(agent, DEFAULT_BASELINE.get(agent, 60)))
        grounding = float(signals[agent]["grounding_signal"])
        debate = float(signals[agent]["debate_signal"])
        raw[agent] = 0.40 * hist + 0.35 * grounding + 0.25 * debate + float(type_bias.get(agent, 0))

    total = sum(raw.values()) or 1.0
    weights: list[dict] = []
    for agent in ANALYSTS:
        influence = max(5, round((raw[agent] / total) * 100))
        weights.append(
            {
                "agent_id": agent,
                "influence_weight": influence,
                "grounding_signal": signals[agent]["grounding_signal"],
                "debate_signal": signals[agent]["debate_signal"],
                "historical_reliability": int(historical.get(agent, DEFAULT_BASELINE.get(agent, 60))),
                "rationale": (
                    f"Grounding {signals[agent]['grounding_signal']} · debate {signals[agent]['debate_signal']} · "
                    f"history {historical.get(agent, 60)}"
                ),
            }
        )
    return normalize_analyst_weights(weights)


def normalize_analyst_weights(weights: list[dict]) -> list[dict]:
    """Ensure analyst influence shares sum to 100 with a sensible minimum per seat."""
    cleaned = [dict(item) for item in weights if item.get("agent_id") in ANALYSTS]
    if not cleaned:
        even = 100 // len(ANALYSTS)
        return [
            {
                "agent_id": agent,
                "influence_weight": even,
                "grounding_signal": 50,
                "debate_signal": 50,
                "historical_reliability": DEFAULT_BASELINE.get(agent, 60),
                "rationale": "Equal fallback — no influence signals available.",
            }
            for agent in ANALYSTS
        ]

    total = sum(max(1, int(item.get("influence_weight") or 0)) for item in cleaned) or 1
    normalized: list[dict] = []
    allocated = 0
    for index, item in enumerate(cleaned):
        if index == len(cleaned) - 1:
            share = max(5, 100 - allocated)
        else:
            share = max(5, round((max(1, int(item.get("influence_weight") or 0)) / total) * 100))
            allocated += share
        normalized.append({**item, "influence_weight": share})
    return normalized


def influence_report_payload(
    *,
    weights: list[dict],
    summary: str,
    decision_type_fit: str = "",
    historical: dict[str, int] | None = None,
    signals: dict[str, dict[str, int]] | None = None,
    decision_type: str = "general",
) -> dict[str, Any]:
    normalized = normalize_analyst_weights(weights)
    ranked = rank_weights(normalized)
    leader = ranked[0] if ranked else None
    return {
        "summary": summary,
        "weights": normalized,
        "ranked_weights": ranked,
        "spread": influence_spread(normalized),
        "leader": (
            {"agent_id": leader["agent_id"], "influence_weight": leader["influence_weight"], "rationale": leader.get("rationale", "")}
            if leader
            else None
        ),
        "historical_priors": historical or {},
        "signals": signals or {},
        "blend_formula": BLEND_FORMULA,
        "decision_type": decision_type,
        "decision_type_fit": decision_type_fit,
    }


def seed_historical_reliability(company_id: str, baseline: dict[str, int] | None = None) -> dict[str, int]:
    """Initialize rolling reliability priors during Redis seed (idempotent)."""
    key = _history_key(company_id)
    existing = R.get_json(key)
    if isinstance(existing, dict) and existing.get("scores"):
        return {agent: int(existing["scores"].get(agent, DEFAULT_BASELINE[agent])) for agent in ANALYSTS}
    scores = {agent: int((baseline or DEFAULT_BASELINE).get(agent, DEFAULT_BASELINE[agent])) for agent in ANALYSTS}
    R.set_json(
        key,
        {
            "company_id": company_id,
            "scores": scores,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "runs_recorded": 0,
            "seeded": True,
        },
    )
    return scores
