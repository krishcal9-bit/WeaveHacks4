"""
Atlas — W&B Weave-driven council agent selection and replacement.

The council is a fixed roster of **five agents**: one CFO (the main, always-on
decision-maker) and four conditional sub-agents — Treasury, FP&A, Risk & Audit,
and Procurement. Every round (one decision sent to the council and ruled on), the
Reliability Auditor scores each agent against the live **W&B Weave** rubric. This
module takes those Weave reliability scores and, after the CFO has decided:

1. Records every sub-agent's reliability for the round (so the score *fluctuates*
   and trends across rounds — the visible signal of Weave-backed performance).
2. Identifies the single **least-reliable** sub-agent for the round.
3. **Retires** that incarnation and **replaces** it with a brand-new one in the
   same role slot — grounded only in the retired agent's Weave reliability trace
   (known weaknesses, lowest-scoring dimensions, and the auditor's prompt
   adjustment). The replacement's standing directive is grafted onto its system
   prompt for the *next* round.

State is persisted under the dedicated ``atlas:evaluation:agent_improvement:*``
Redis namespace and mirrored to the append-only ``atlas:stream:agent_improvements``
stream, then published to Weave so the replacement lineage is queryable. This is a
**live** integration: the replacement is produced by the live OpenAI council model,
never mocked. When the model path is skipped (fast demo path), the replacement is
derived deterministically from the *same* live Weave reliability trace — never
fabricated.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import weave
from pydantic import BaseModel, Field

from src import council_influence as CI
from src import redis_layer as R
from src.env import redact_secrets

# The four conditional sub-agents (the CFO is the always-on main agent and is
# scored but never targeted for replacement).
SUBAGENTS = ["treasury", "fpna", "risk", "procurement"]

ROLE_LABELS = {
    "treasury": "Treasury",
    "fpna": "FP&A",
    "risk": "Risk & Audit",
    "procurement": "Procurement",
}

# Incumbent prompt-version anchors (mirror src.openai_council._PROMPT_VERSION_IDS).
BASE_VERSIONS = {
    "treasury": "treasury.v4-evidence-plan",
    "fpna": "fpna.v4-cohort-calibration",
    "risk": "risk.v5-control-evidence",
    "procurement": "procurement.v3-renewal-redlines",
}

IMPROVE_NS = f"{R.NS}:evaluation:agent_improvement"
IMPROVE_STREAM = "agent_improvements"  # → atlas:stream:agent_improvements
HISTORY_CAP = 30
REPLACEMENT_BASELINE_FLOOR = 52


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _key(company_id: str) -> str:
    return f"{IMPROVE_NS}:{company_id}"


def _new_incarnation_id(role: str, generation: int) -> str:
    return f"{role}-gen{generation}-{uuid.uuid4().hex[:6]}"


def _blank_agent(role: str) -> dict[str, Any]:
    return {
        "agent_id": role,
        "label": ROLE_LABELS.get(role, role),
        "generation": 1,
        "incarnation_id": _new_incarnation_id(role, 1),
        "version": 0,
        "version_label": BASE_VERSIONS.get(role, f"{role}.v1"),
        "directive": "",
        "mandate_emphasis": "",
        "focus": "",
        "targeted_dimension": "",
        "applied_round": 0,
        "reliability_history": [],
        "improvement_history": [],
        "lineage": [],
    }


def agent_improvement_state(company_id: str) -> dict[str, Any]:
    """Load the rolling per-agent replacement state (blank default if absent)."""
    stored = R.get_json(_key(company_id))
    if isinstance(stored, dict) and stored.get("agents"):
        agents = stored.get("agents") or {}
        for role in SUBAGENTS:
            if role not in agents:
                agents[role] = _blank_agent(role)
            else:
                seat = agents[role]
                seat.setdefault("generation", 1)
                seat.setdefault("incarnation_id", _new_incarnation_id(role, int(seat.get("generation", 1))))
                seat.setdefault("mandate_emphasis", "")
                seat.setdefault("lineage", [])
        stored["agents"] = agents
        return stored
    return {
        "company_id": company_id,
        "round": 0,
        "updated_at": _now(),
        "last_replaced": None,
        "last_improved": None,
        "agents": {role: _blank_agent(role) for role in SUBAGENTS},
        "rounds": [],
    }


def active_directives(company_id: str) -> dict[str, str]:
    """Per-sub-agent standing directive to inject into prompts this round."""
    state = agent_improvement_state(company_id)
    return {role: str((state["agents"].get(role) or {}).get("directive") or "") for role in SUBAGENTS}


def _select_weakest(scorecard: list[dict]) -> tuple[str, dict]:
    """Pick the least-reliable sub-agent from the Weave-scored scorecard."""
    by_agent = {s.get("agent_id"): s for s in scorecard}
    ranked = sorted(
        SUBAGENTS,
        key=lambda role: (int((by_agent.get(role) or {}).get("reliability", 100)), SUBAGENTS.index(role)),
    )
    weakest = ranked[0]
    return weakest, (by_agent.get(weakest) or {})


def _deterministic_replacement(score: dict, persona_label: str) -> tuple[str, str, str, str, str, str]:
    """Derive a replacement incarnation from the live Weave trace without a model call."""
    weaknesses = [w for w in (score.get("known_weaknesses") or []) if w]
    adjustment = str(score.get("prompt_adjustment") or "").strip()
    dimensions = {
        "evidence_grounding": score.get("evidence_grounding"),
        "forecast_calibration": score.get("forecast_calibration"),
        "policy_compliance": score.get("policy_compliance"),
        "debate_value": score.get("debate_value"),
        "outcome_accuracy": score.get("outcome_accuracy"),
        "confidence_calibration": score.get("confidence_calibration"),
        "trace_quality": score.get("trace_quality"),
    }
    scored = [(k, int(v)) for k, v in dimensions.items() if isinstance(v, (int, float))]
    targeted = min(scored, key=lambda kv: kv[1])[0] if scored else "evidence_grounding"
    focus = (weaknesses[0] if weaknesses else targeted.replace("_", " ").title())[:80]
    prior_rel = int(score.get("reliability", 0) or 0)
    rationale = (
        f"Retired {persona_label} at {prior_rel}% Weave reliability — weakest sub-agent this round."
    )
    mandate = (
        f"New {persona_label} incarnation must lead with {targeted.replace('_', ' ')} and cite at least "
        f"two Redis-backed figures before taking a stance."
    )
    directive = adjustment or (
        f"Lift {targeted.replace('_', ' ')}: cite at least two concrete Redis-backed figures and the "
        f"policy/precedent that grounds them before taking a stance."
    )
    expected_gain = f"Higher {targeted.replace('_', ' ')} on the next decision."
    return focus, rationale, mandate, directive, targeted, expected_gain


class AgentReplacementSnapshot(BaseModel):
    """A queryable, versioned snapshot of the replacement loop for Weave."""

    company_id: str
    round: int
    replaced_agent: str
    retired_generation: int
    new_generation: int
    incarnation_id: str
    version_label: str
    focus: str
    replacement_rationale: str = ""
    mandate_emphasis: str = ""
    directive: str
    targeted_dimension: str = ""
    expected_gain: str = ""
    prior_reliability: int = 0
    council_average: int = 0
    source: str = "openai"
    scores: dict[str, int] = Field(default_factory=dict)
    created_at: str = ""


def _publish_snapshot(snapshot: AgentReplacementSnapshot) -> dict[str, Any]:
    """Publish the replacement snapshot to Weave (redacted, never faked)."""
    try:
        from src.weave_eval import publish_to_weave

        return publish_to_weave(
            snapshot,
            name=f"atlas-agent-replacement-{snapshot.company_id}-r{snapshot.round}",
        )
    except Exception as exc:  # surfaced redacted, never fabricated
        return {"published": False, "error": redact_secrets(exc)}


def _replacement_baseline(prior_reliability: int, targeted_dimension: str) -> int:
    """Fresh reliability prior for a newly spawned incarnation."""
    lift = {
        "evidence_grounding": 8,
        "forecast_calibration": 6,
        "policy_compliance": 7,
        "debate_value": 5,
        "outcome_accuracy": 4,
        "confidence_calibration": 3,
        "trace_quality": 4,
    }.get(targeted_dimension, 6)
    return max(REPLACEMENT_BASELINE_FLOOR, min(78, REPLACEMENT_BASELINE_FLOOR + lift + max(0, 62 - prior_reliability) // 4))


@weave.op(name="self_improvement.replace_weakest_agent")
async def replace_weakest_agent(
    *,
    company_id: str,
    company: str,
    scorecard: list[dict],
    decision: str,
    use_llm: bool = True,
    config: Any = None,
) -> dict[str, Any]:
    """Weave-cored replacement for one round: retire the weakest, spawn a new incarnation."""
    state = agent_improvement_state(company_id)
    round_no = int(state.get("round", 0)) + 1
    by_agent = {s.get("agent_id"): s for s in scorecard}

    for role in SUBAGENTS:
        rel = int((by_agent.get(role) or {}).get("reliability", 0) or 0)
        history = list(state["agents"][role].get("reliability_history") or [])
        history.append({"round": round_no, "reliability": rel})
        state["agents"][role]["reliability_history"] = history[-HISTORY_CAP:]

    weakest, weak_score = _select_weakest(scorecard)
    prior = state["agents"][weakest]
    prior_reliability = int((weak_score or {}).get("reliability", 0) or 0)
    retired_generation = int(prior.get("generation", 1))
    retired_incarnation = str(prior.get("incarnation_id") or _new_incarnation_id(weakest, retired_generation))

    lineage_entry = {
        "round": round_no,
        "generation": retired_generation,
        "incarnation_id": retired_incarnation,
        "version_label": prior.get("version_label"),
        "directive": prior.get("directive"),
        "mandate_emphasis": prior.get("mandate_emphasis"),
        "focus": prior.get("focus"),
        "targeted_dimension": prior.get("targeted_dimension"),
        "final_reliability": prior_reliability,
        "retired_at": _now(),
        "weave_trace": {
            "reliability": prior_reliability,
            "known_weaknesses": weak_score.get("known_weaknesses") or [],
            "prompt_adjustment": weak_score.get("prompt_adjustment"),
        },
    }
    lineage = list(prior.get("lineage") or []) + [lineage_entry]

    directive = ""
    focus = ""
    targeted_dimension = ""
    expected_gain = ""
    replacement_rationale = ""
    mandate_emphasis = ""
    source = "deterministic"
    if use_llm:
        try:
            from src.openai_council import spawn_replacement_agent

            result = await spawn_replacement_agent(
                company=company,
                agent_id=weakest,
                persona_label=ROLE_LABELS.get(weakest, weakest),
                reliability_score=weak_score,
                retired_directive=str(prior.get("directive") or ""),
                retired_generation=retired_generation,
                decision=decision,
                round_no=round_no,
                config=config,
            )
            if result.ok and result.parsed is not None:
                directive = (result.parsed.directive or "").strip()
                focus = (result.parsed.focus or "").strip()
                targeted_dimension = (result.parsed.targeted_dimension or "").strip()
                expected_gain = (result.parsed.expected_gain or "").strip()
                replacement_rationale = (result.parsed.replacement_rationale or "").strip()
                mandate_emphasis = (result.parsed.mandate_emphasis or "").strip()
                source = "openai"
        except Exception as exc:
            print(f"[self_improvement] replacement model warning: {redact_secrets(exc)}")

    if not directive:
        focus_d, rationale, mandate, directive, targeted_dimension, expected_gain = _deterministic_replacement(
            weak_score,
            ROLE_LABELS.get(weakest, weakest),
        )
        focus = focus or focus_d
        replacement_rationale = replacement_rationale or rationale
        mandate_emphasis = mandate_emphasis or mandate

    new_generation = retired_generation + 1
    new_version = int(prior.get("version", 0)) + 1
    base = BASE_VERSIONS.get(weakest, f"{weakest}.v1")
    version_label = f"{base}+gen{new_generation}"
    incarnation_id = _new_incarnation_id(weakest, new_generation)
    replacement_entry = {
        "round": round_no,
        "action": "replaced",
        "from_generation": retired_generation,
        "to_generation": new_generation,
        "from_version": int(prior.get("version", 0)),
        "to_version": new_version,
        "version_label": version_label,
        "incarnation_id": incarnation_id,
        "focus": focus,
        "replacement_rationale": replacement_rationale,
        "mandate_emphasis": mandate_emphasis,
        "directive": directive,
        "targeted_dimension": targeted_dimension,
        "expected_gain": expected_gain,
        "prior_reliability": prior_reliability,
        "source": source,
        "at": _now(),
    }
    improvement_history = list(prior.get("improvement_history") or []) + [replacement_entry]

    state["agents"][weakest].update(
        generation=new_generation,
        incarnation_id=incarnation_id,
        version=new_version,
        version_label=version_label,
        directive=directive,
        mandate_emphasis=mandate_emphasis,
        focus=focus,
        targeted_dimension=targeted_dimension,
        applied_round=round_no,
        improvement_history=improvement_history[-HISTORY_CAP:],
        lineage=lineage[-HISTORY_CAP:],
        reliability_history=[{"round": round_no, "reliability": _replacement_baseline(prior_reliability, targeted_dimension)}],
    )

    try:
        CI.reset_agent_reliability(
            company_id,
            weakest,
            _replacement_baseline(prior_reliability, targeted_dimension),
        )
    except Exception as exc:
        print(f"[self_improvement] reliability reset warning: {redact_secrets(exc)}")

    council_scores = {role: int((by_agent.get(role) or {}).get("reliability", 0) or 0) for role in SUBAGENTS}
    council_average = round(sum(council_scores.values()) / len(SUBAGENTS)) if SUBAGENTS else 0

    round_entry = {
        "round": round_no,
        "action": "replaced",
        "replaced": weakest,
        "replaced_label": ROLE_LABELS.get(weakest, weakest),
        "improved": weakest,
        "improved_label": ROLE_LABELS.get(weakest, weakest),
        "focus": focus,
        "replacement_rationale": replacement_rationale,
        "mandate_emphasis": mandate_emphasis,
        "targeted_dimension": targeted_dimension,
        "prior_reliability": prior_reliability,
        "council_average": council_average,
        "scores": council_scores,
        "version_label": version_label,
        "generation": new_generation,
        "incarnation_id": incarnation_id,
        "source": source,
        "at": _now(),
    }
    rounds = list(state.get("rounds") or []) + [round_entry]
    state.update(
        round=round_no,
        last_replaced=weakest,
        last_improved=weakest,
        updated_at=_now(),
        rounds=rounds[-HISTORY_CAP:],
    )

    try:
        R.set_json(_key(company_id), state)
        R.append_event(IMPROVE_STREAM, round_entry)
    except Exception as exc:
        print(f"[self_improvement] persist warning: {redact_secrets(exc)}")

    snapshot = AgentReplacementSnapshot(
        company_id=company_id,
        round=round_no,
        replaced_agent=weakest,
        retired_generation=retired_generation,
        new_generation=new_generation,
        incarnation_id=incarnation_id,
        version_label=version_label,
        focus=focus,
        replacement_rationale=replacement_rationale,
        mandate_emphasis=mandate_emphasis,
        directive=directive,
        targeted_dimension=targeted_dimension,
        expected_gain=expected_gain,
        prior_reliability=prior_reliability,
        council_average=council_average,
        source=source,
        scores=council_scores,
        created_at=_now(),
    )
    weave_info = _publish_snapshot(snapshot)

    return {
        "round": round_no,
        "replaced_agent": weakest,
        "replaced_label": ROLE_LABELS.get(weakest, weakest),
        "improved_agent": weakest,
        "improved_label": ROLE_LABELS.get(weakest, weakest),
        "focus": focus,
        "replacement_rationale": replacement_rationale,
        "mandate_emphasis": mandate_emphasis,
        "directive": directive,
        "targeted_dimension": targeted_dimension,
        "expected_gain": expected_gain,
        "version_label": version_label,
        "generation": new_generation,
        "incarnation_id": incarnation_id,
        "prior_reliability": prior_reliability,
        "council_average": council_average,
        "source": source,
        "agents": {
            role: {
                "agent_id": role,
                "label": ROLE_LABELS.get(role, role),
                "generation": state["agents"][role]["generation"],
                "incarnation_id": state["agents"][role]["incarnation_id"],
                "version": state["agents"][role]["version"],
                "version_label": state["agents"][role]["version_label"],
                "directive": state["agents"][role]["directive"],
                "mandate_emphasis": state["agents"][role]["mandate_emphasis"],
                "focus": state["agents"][role]["focus"],
                "reliability_history": state["agents"][role]["reliability_history"],
                "replaced_this_round": role == weakest,
                "improved_this_round": role == weakest,
            }
            for role in SUBAGENTS
        },
        "rounds": state["rounds"],
        "weave": weave_info,
    }


# Backward-compatible alias used by older call sites.
improve_weakest_agent = replace_weakest_agent


def seed_agent_improvement_state(company_id: str) -> dict[str, Any]:
    """Initialize blank replacement state during Redis seed (idempotent)."""
    key = _key(company_id)
    existing = R.get_json(key)
    if isinstance(existing, dict) and existing.get("agents"):
        return existing
    state = {
        "company_id": company_id,
        "round": 0,
        "updated_at": _now(),
        "last_replaced": None,
        "last_improved": None,
        "agents": {role: _blank_agent(role) for role in SUBAGENTS},
        "rounds": [],
        "seeded": True,
    }
    R.set_json(key, state)
    return state


def improvement_summary(company_id: str) -> dict[str, Any]:
    """Compact, non-secret summary for health / observability surfaces."""
    state = agent_improvement_state(company_id)
    return {
        "round": int(state.get("round", 0)),
        "last_replaced": state.get("last_replaced") or state.get("last_improved"),
        "last_improved": state.get("last_improved"),
        "namespace": IMPROVE_NS,
        "stream": f"{R.NS}:stream:{IMPROVE_STREAM}",
        "agents": {
            role: {
                "generation": (state["agents"].get(role) or {}).get("generation"),
                "version_label": (state["agents"].get(role) or {}).get("version_label"),
                "latest_reliability": (
                    ((state["agents"].get(role) or {}).get("reliability_history") or [{}])[-1].get("reliability")
                ),
            }
            for role in SUBAGENTS
        },
    }
