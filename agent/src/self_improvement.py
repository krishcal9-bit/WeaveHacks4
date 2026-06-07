"""
Atlas — W&B Weave-driven council self-improvement loop.

The council is a fixed roster of **five agents**: one CFO (the main, always-on
decision-maker) and four conditional sub-agents — Treasury, FP&A, Risk & Audit,
and Procurement. Every round (one decision sent to the council and ruled on), the
Reliability Auditor scores each agent against the live **W&B Weave** rubric. This
module takes those Weave reliability scores and, after the CFO has decided:

1. Records every sub-agent's reliability for the round (so the score *fluctuates*
   and trends across rounds — the visible signal of improvement).
2. Identifies the single **least-reliable** sub-agent for the round.
3. **Improves** that sub-agent (it is never removed) by rewriting a short standing
   directive — grounded only in that agent's Weave reliability trace (its
   known weaknesses, lowest-scoring dimensions, and the auditor's prompt
   adjustment) — that is grafted onto its system prompt for the *next* round.

State is persisted under the dedicated ``atlas:evaluation:agent_improvement:*``
Redis namespace and mirrored to the append-only ``atlas:stream:agent_improvements``
stream, then published to Weave so the improvement history is queryable. This is a
**live** integration: the improved directive is produced by the live OpenAI
council model, never mocked. When the model path is skipped (fast demo path), the
directive is derived deterministically from the *same* live Weave reliability
trace — never fabricated.
"""

from __future__ import annotations

import time
from typing import Any

import weave
from pydantic import BaseModel, Field

from src import redis_layer as R
from src.env import redact_secrets

# The four conditional sub-agents (the CFO is the always-on main agent and is
# scored but never targeted for sub-agent improvement).
SUBAGENTS = ["treasury", "fpna", "risk", "procurement"]

ROLE_LABELS = {
    "treasury": "Treasury",
    "fpna": "FP&A",
    "risk": "Risk & Audit",
    "procurement": "Procurement",
}

# Incumbent prompt-version anchors (mirror src.openai_council._PROMPT_VERSION_IDS).
# Improvement versions are suffixed onto these, e.g. ``treasury.v4-evidence-plan+imp3``.
BASE_VERSIONS = {
    "treasury": "treasury.v4-evidence-plan",
    "fpna": "fpna.v4-cohort-calibration",
    "risk": "risk.v5-control-evidence",
    "procurement": "procurement.v3-renewal-redlines",
}

IMPROVE_NS = f"{R.NS}:evaluation:agent_improvement"
IMPROVE_STREAM = "agent_improvements"  # → atlas:stream:agent_improvements
HISTORY_CAP = 30


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _key(company_id: str) -> str:
    return f"{IMPROVE_NS}:{company_id}"


def _blank_agent(role: str) -> dict[str, Any]:
    return {
        "agent_id": role,
        "label": ROLE_LABELS.get(role, role),
        "version": 0,
        "version_label": BASE_VERSIONS.get(role, f"{role}.v1"),
        "directive": "",
        "focus": "",
        "targeted_dimension": "",
        "applied_round": 0,
        "reliability_history": [],
        "improvement_history": [],
    }


def agent_improvement_state(company_id: str) -> dict[str, Any]:
    """Load the rolling per-agent improvement state (blank default if absent)."""
    stored = R.get_json(_key(company_id))
    if isinstance(stored, dict) and stored.get("agents"):
        agents = stored.get("agents") or {}
        for role in SUBAGENTS:
            if role not in agents:
                agents[role] = _blank_agent(role)
        stored["agents"] = agents
        return stored
    return {
        "company_id": company_id,
        "round": 0,
        "updated_at": _now(),
        "last_improved": None,
        "agents": {role: _blank_agent(role) for role in SUBAGENTS},
        "rounds": [],
    }


def active_directives(company_id: str) -> dict[str, str]:
    """Per-sub-agent standing directive to inject into prompts this round."""
    state = agent_improvement_state(company_id)
    return {role: str((state["agents"].get(role) or {}).get("directive") or "") for role in SUBAGENTS}


def _select_weakest(scorecard: list[dict]) -> tuple[str, dict]:
    """Pick the least-reliable sub-agent from the Weave-scored scorecard.

    Ties break toward the agent improved least recently (lowest applied_round is
    not known here, so we keep the deterministic SUBAGENTS order as a stable
    secondary key), keeping the loop fair over many rounds.
    """
    by_agent = {s.get("agent_id"): s for s in scorecard}
    ranked = sorted(
        SUBAGENTS,
        key=lambda role: (int((by_agent.get(role) or {}).get("reliability", 100)), SUBAGENTS.index(role)),
    )
    weakest = ranked[0]
    return weakest, (by_agent.get(weakest) or {})


def _deterministic_directive(score: dict) -> tuple[str, str, str]:
    """Derive an improvement directive from the live Weave trace without a model call."""
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
    directive = adjustment or (
        f"Lift {targeted.replace('_', ' ')}: cite at least two concrete Redis-backed figures and the "
        f"policy/precedent that grounds them before taking a stance."
    )
    return focus, directive, targeted


class AgentImprovementSnapshot(BaseModel):
    """A queryable, versioned snapshot of the self-improvement loop for Weave."""

    company_id: str
    round: int
    improved_agent: str
    version_label: str
    focus: str
    directive: str
    targeted_dimension: str = ""
    expected_gain: str = ""
    prior_reliability: int = 0
    council_average: int = 0
    source: str = "openai"
    scores: dict[str, int] = Field(default_factory=dict)
    created_at: str = ""


def _publish_snapshot(snapshot: AgentImprovementSnapshot) -> dict[str, Any]:
    """Publish the improvement snapshot to Weave (redacted, never faked)."""
    try:
        from src.weave_eval import publish_to_weave

        return publish_to_weave(
            snapshot,
            name=f"atlas-agent-improvement-{snapshot.company_id}-r{snapshot.round}",
        )
    except Exception as exc:  # surfaced redacted, never fabricated
        return {"published": False, "error": redact_secrets(exc)}


@weave.op(name="self_improvement.improve_weakest_agent")
async def improve_weakest_agent(
    *,
    company_id: str,
    company: str,
    scorecard: list[dict],
    decision: str,
    use_llm: bool = True,
    config: Any = None,
) -> dict[str, Any]:
    """Score-driven, Weave-cored self-improvement for one round.

    Records this round's reliability for all four sub-agents, picks the weakest,
    rewrites its standing directive from its Weave reliability trace, bumps its
    version, and persists the rolling history. Runs as a child ``@weave.op`` span
    of the ``self_improvement`` node so the loop is visible in the Weave trace.
    """
    state = agent_improvement_state(company_id)
    round_no = int(state.get("round", 0)) + 1
    by_agent = {s.get("agent_id"): s for s in scorecard}

    # 1) Record every sub-agent's Weave reliability for this round (fluctuation signal).
    for role in SUBAGENTS:
        rel = int((by_agent.get(role) or {}).get("reliability", 0) or 0)
        history = list(state["agents"][role].get("reliability_history") or [])
        history.append({"round": round_no, "reliability": rel})
        state["agents"][role]["reliability_history"] = history[-HISTORY_CAP:]

    # 2) Identify the least-reliable sub-agent from the Weave scorecard.
    weakest, weak_score = _select_weakest(scorecard)
    prior = state["agents"][weakest]
    prior_reliability = int((weak_score or {}).get("reliability", 0) or 0)

    # 3) Improve it — grounded ONLY in its Weave reliability trace.
    directive = ""
    focus = ""
    targeted_dimension = ""
    expected_gain = ""
    source = "deterministic"
    if use_llm:
        try:
            from src.openai_council import improve_agent

            result = await improve_agent(
                company=company,
                agent_id=weakest,
                persona_label=ROLE_LABELS.get(weakest, weakest),
                reliability_score=weak_score,
                prior_directive=str(prior.get("directive") or ""),
                decision=decision,
                round_no=round_no,
                config=config,
            )
            if result.ok and result.parsed is not None:
                directive = (result.parsed.directive or "").strip()
                focus = (result.parsed.focus or "").strip()
                targeted_dimension = (result.parsed.targeted_dimension or "").strip()
                expected_gain = (result.parsed.expected_gain or "").strip()
                source = "openai"
        except Exception as exc:
            print(f"[self_improvement] model directive warning: {redact_secrets(exc)}")

    if not directive:
        focus_d, directive, targeted_dimension = _deterministic_directive(weak_score)
        focus = focus or focus_d
        if not expected_gain:
            expected_gain = f"Higher {targeted_dimension.replace('_', ' ')} on the next decision."

    # 4) Bump version, store the new directive, append improvement history.
    new_version = int(prior.get("version", 0)) + 1
    base = BASE_VERSIONS.get(weakest, f"{weakest}.v1")
    version_label = f"{base}+imp{new_version}"
    improvement_entry = {
        "round": round_no,
        "from_version": int(prior.get("version", 0)),
        "to_version": new_version,
        "version_label": version_label,
        "focus": focus,
        "directive": directive,
        "targeted_dimension": targeted_dimension,
        "expected_gain": expected_gain,
        "prior_reliability": prior_reliability,
        "source": source,
        "at": _now(),
    }
    improvement_history = list(prior.get("improvement_history") or []) + [improvement_entry]
    state["agents"][weakest].update(
        version=new_version,
        version_label=version_label,
        directive=directive,
        focus=focus,
        targeted_dimension=targeted_dimension,
        applied_round=round_no,
        improvement_history=improvement_history[-HISTORY_CAP:],
    )

    council_scores = {role: int((by_agent.get(role) or {}).get("reliability", 0) or 0) for role in SUBAGENTS}
    council_average = round(sum(council_scores.values()) / len(SUBAGENTS)) if SUBAGENTS else 0

    round_entry = {
        "round": round_no,
        "improved": weakest,
        "improved_label": ROLE_LABELS.get(weakest, weakest),
        "focus": focus,
        "targeted_dimension": targeted_dimension,
        "prior_reliability": prior_reliability,
        "council_average": council_average,
        "scores": council_scores,
        "version_label": version_label,
        "source": source,
        "at": _now(),
    }
    rounds = list(state.get("rounds") or []) + [round_entry]
    state.update(
        round=round_no,
        last_improved=weakest,
        updated_at=_now(),
        rounds=rounds[-HISTORY_CAP:],
    )

    # 5) Persist (atlas:evaluation:*) + mirror to the append-only stream.
    try:
        R.set_json(_key(company_id), state)
        R.append_event(IMPROVE_STREAM, round_entry)
    except Exception as exc:
        print(f"[self_improvement] persist warning: {redact_secrets(exc)}")

    # 6) Publish a queryable snapshot to Weave.
    snapshot = AgentImprovementSnapshot(
        company_id=company_id,
        round=round_no,
        improved_agent=weakest,
        version_label=version_label,
        focus=focus,
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
        "improved_agent": weakest,
        "improved_label": ROLE_LABELS.get(weakest, weakest),
        "focus": focus,
        "directive": directive,
        "targeted_dimension": targeted_dimension,
        "expected_gain": expected_gain,
        "version_label": version_label,
        "prior_reliability": prior_reliability,
        "council_average": council_average,
        "source": source,
        "agents": {
            role: {
                "agent_id": role,
                "label": ROLE_LABELS.get(role, role),
                "version": state["agents"][role]["version"],
                "version_label": state["agents"][role]["version_label"],
                "directive": state["agents"][role]["directive"],
                "focus": state["agents"][role]["focus"],
                "reliability_history": state["agents"][role]["reliability_history"],
                "improved_this_round": role == weakest,
            }
            for role in SUBAGENTS
        },
        "rounds": state["rounds"],
        "weave": weave_info,
    }


def seed_agent_improvement_state(company_id: str) -> dict[str, Any]:
    """Initialize blank improvement state during Redis seed (idempotent)."""
    key = _key(company_id)
    existing = R.get_json(key)
    if isinstance(existing, dict) and existing.get("agents"):
        return existing
    state = {
        "company_id": company_id,
        "round": 0,
        "updated_at": _now(),
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
        "last_improved": state.get("last_improved"),
        "namespace": IMPROVE_NS,
        "stream": f"{R.NS}:stream:{IMPROVE_STREAM}",
        "agents": {
            role: {
                "version_label": (state["agents"].get(role) or {}).get("version_label"),
                "latest_reliability": (
                    ((state["agents"].get(role) or {}).get("reliability_history") or [{}])[-1].get("reliability")
                ),
            }
            for role in SUBAGENTS
        },
    }
