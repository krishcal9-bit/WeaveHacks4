"""
Atlas — the multi-agent finance department debate graph.

A user poses any financial decision. An OpenAI-native committee classifies the
decision, plans which Redis-backed evidence each role needs, takes grounded
positions (Treasury, FP&A, Risk & Audit, Procurement), survives an evidence
challenge panel, cross-examines, and the CFO synthesizes a board-ready, quantified
recommendation plus a board memo and operator action checklist. State streams to
the frontend (CopilotKit useCoAgent) to drive the boardroom view; every node is a
@weave.op so the committee appears as named spans in Weave.

Flow:  intake → planner → committee_parallel → challenge
       → debate → influence → synthesis → governance → reliability
       → self_improvement → persist

``committee_parallel`` runs Treasury, FP&A, Risk, and Procurement concurrently
with live AG-UI streaming so the UI shows every seat thinking at once.

The OpenAI-native lifting (typed prompts, structured calls with token/cost/refusal
telemetry, classification, evidence planning, challenge panel, synthesis, board
memo) lives in ``src/openai_council.py``; this module owns graph orchestration,
AG-UI state streaming, the operator command layer, and Redis writes.
"""

import asyncio
import inspect
import json
import os
import time

import weave
from copilotkit import CopilotKitState
from langchain.chat_models import init_chat_model
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from src.env import load_env, redact_secrets
from src.health import require_live_ready, sponsor_health, weave_status
from src import redis_layer as R
from src import planning as PL
from src import agui_commands as AGUI
from src import promotion_gates as PG
from src import replay_sets as RS
from src import role_distinction_eval as RD
from src import weave_eval as WE
from src import governance as GOV
from src.governance_models import ActorType
from src.realtime import realtime_health
from src.structured_models import DecisionPlan, RoleEvidencePlan
from src import council_influence as CI
from src import self_improvement as SI
from src.openai_council import (
    analyst_position,
    board_memo,
    cfo_recommendation,
    challenge_panel,
    classify_and_plan,
    council_influence,
    cross_examination,
    ensure_role_specific_exchanges,
    gather_role_evidence,
    init_telemetry,
    merge_telemetry,
    model_family,
    fpna_evidence_preferences,
    procurement_evidence_preferences,
    prompt_versions_payload,
    risk_evidence_preferences,
    role_challenge_profile,
    treasury_evidence_preferences,
    tool_plan_entries,
)
from src.tools import (
    compute_runway,
    get_company_financials,
    list_vendors,
    search_finance_policies,
)

load_env()

# Single council room for the live demo; command state is scoped by room so a
# future multi-company build stays compatible (see agui_commands.DEFAULT_ROOM).
ROOM = AGUI.DEFAULT_ROOM
# Bounded, cooperative pause: an operator pause is honored between graph nodes
# for at most this long, and released immediately on resume. Synthesis/persist
# are never paused, so a decision always completes.
PAUSE_MAX_SECONDS = float(os.getenv("ATLAS_PAUSE_MAX_SECONDS", "45"))
PAUSE_POLL_SECONDS = 1.0


def _fast_council() -> bool:
    """Demo-fast path: skip non-essential model passes while keeping live data grounding."""
    return os.getenv("ATLAS_FAST_COUNCIL", "1").strip().lower() in ("1", "true", "yes")


LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.5")
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "xhigh")
LLM_TEXT_VERBOSITY = os.getenv("LLM_TEXT_VERBOSITY", "low")
OPENAI_SERVICE_TIER = os.getenv("OPENAI_SERVICE_TIER", "priority")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2")
OPENAI_REALTIME_REASONING_EFFORT = os.getenv("OPENAI_REALTIME_REASONING_EFFORT", "xhigh")

try:
    from copilotkit.langgraph import copilotkit_emit_state as _copilotkit_emit_state
except Exception:
    try:
        from copilotkit import copilotkit_emit_state as _copilotkit_emit_state
    except Exception:  # CopilotKit Python package versions expose different helpers.
        _copilotkit_emit_state = None


def llm(temperature: float = 0.3):
    if LLM_PROVIDER.lower() == "openai":
        return ChatOpenAI(
            model=LLM_MODEL,
            temperature=temperature,
            reasoning_effort=LLM_REASONING_EFFORT,
            verbosity=LLM_TEXT_VERBOSITY,
            output_version="responses/v1",
            service_tier=OPENAI_SERVICE_TIER,
        )
    return init_chat_model(LLM_MODEL, model_provider=LLM_PROVIDER, temperature=temperature)


# --------------------------------------------------------------------------- #
# The committee — professional roles, not characters (org-chart, not mascots)
# --------------------------------------------------------------------------- #
ROSTER: dict[str, dict] = {
    "cfo": {
        "label": "Office of the CFO",
        "role": "Chief Financial Officer · Chair",
        "monogram": "CF",
        "mandate": "balancing growth, risk, and runway to make the final call",
    },
    "treasury": {
        "label": "Treasury",
        "role": "Treasury",
        "monogram": "TR",
        "mandate": "cash runway, liquidity timing, payment terms, working capital, renewal cash schedules, and late-cash financing risk",
    },
    "fpna": {
        "label": "FP&A",
        "role": "Forecast & Unit Economics",
        "monogram": "FP",
        "mandate": "forecast quality, ARR movement, pipeline probability, ROI, CAC/payback, margin, sensitivity ranges, scenario math, and plan-vs-actual deltas",
    },
    "risk": {
        "label": "Risk & Audit",
        "role": "Controls Adversary",
        "monogram": "RA",
        "mandate": "policy violations, audit trail gaps, approvals, data quality, fraud/error risk, compliance blockers, hidden obligations, security evidence, and source provenance",
    },
    "procurement": {
        "label": "Procurement",
        "role": "Vendor & Commercial Negotiation",
        "monogram": "PR",
        "mandate": "supplier leverage, contract terms, auto-renewal, renewal dates, price benchmarks, consolidation, switching cost, SLAs, termination clauses, volume discounts, and negotiation strategy",
    },
    "reliability": {
        "label": "Reliability Auditor",
        "role": "Reliability & Learning",
        "monogram": "RL",
        "mandate": "scoring agent reliability, packaging W&B evals, and gating self-improvement",
    },
}
ANALYSTS = ["treasury", "fpna", "risk", "procurement"]


# --------------------------------------------------------------------------- #
# Structured outputs (reliable JSON from the model)
#
# These inline models remain the public contract for other modules
# (promotion_gates, council_commands import Position/llm from here). The
# OpenAI-native council in src/openai_council.py uses the richer typed models in
# src/structured_models.py (Position there adds cited_metrics/evidence_used).
# --------------------------------------------------------------------------- #
class StrictStructuredModel(BaseModel):
    """OpenAI strict structured outputs require closed, fully-required schemas."""

    model_config = ConfigDict(extra="forbid")


class Position(StrictStructuredModel):
    stance: str = Field(description="one of: support, oppose, conditional")
    headline: str = Field(description="one-line position, <= 12 words")
    argument: str = Field(description="2-4 sentences citing specific figures")
    key_points: list[str] = Field(description="2-3 crisp bullets")


class Exchange(StrictStructuredModel):
    from_role: str = Field(description="the function raising the challenge")
    to_role: str = Field(description="the function being challenged")
    challenge_type: str = Field(
        description=(
            "one of: cash_timing, forecast_assumptions, controls_policy, vendor_terms, synthesis_question"
        )
    )
    challenge_label: str = Field(description="short display label for the challenge type, <= 4 words")
    challenge_lens: str = Field(description="the role-specific weakness this exchange is testing")
    point: str = Field(description="a sharp, specific, quantified challenge")


class Rebuttals(StrictStructuredModel):
    exchanges: list[Exchange] = Field(description="cross-examination exchanges")


class Recommendation(StrictStructuredModel):
    decision: str = Field(description="one of: APPROVE, REJECT, CONDITIONAL, DEFER")
    confidence: int = Field(ge=0, le=100)
    rationale: str = Field(description="3-5 sentences, decisive and quantified")
    key_risks: list[str] = Field(description="key risks to monitor")
    conditions: list[str] = Field(description="conditions for approval; empty if none")
    estimated_monthly_cost: float = Field(description="incremental recurring monthly cost; 0 if none")
    estimated_one_time_cost: float = Field(description="upfront one-time cost; 0 if none")
    estimated_added_monthly_revenue: float = Field(description="incremental monthly revenue; 0 if none")


class ReliabilityScore(StrictStructuredModel):
    agent_id: str = Field(description="one of: cfo, treasury, fpna, risk, procurement")
    evidence_grounding: int = Field(ge=0, le=100)
    forecast_calibration: int = Field(ge=0, le=100)
    policy_compliance: int = Field(ge=0, le=100)
    debate_value: int = Field(ge=0, le=100)
    outcome_accuracy: int = Field(ge=0, le=100)
    confidence_calibration: int = Field(ge=0, le=100)
    trace_quality: int = Field(ge=0, le=100)
    reliability: int = Field(ge=0, le=100, description="weighted overall score")
    rationale: str = Field(description="specific evidence-backed reason for the score")
    known_weaknesses: list[str] = Field(description="known weaknesses to replay or improve")
    prompt_adjustment: str = Field(description="specific prompt or policy improvement to replay")
    replay_cases: list[str] = Field(
        description="per-agent replay cases that reproduce grounding, calibration, policy, debate, or trace weaknesses"
    )
    prompt_improvement_directive: str = Field(
        description="imperative directive to feed the self-improvement loop for this agent's next prompt"
    )
    promotion_gate: str = Field(description="how W&B Weave evals should decide whether this agent improves")


class ReliabilityReport(StrictStructuredModel):
    audit_scope: str = Field(description="explicit statement that this is an evaluator scorecard, not a case ruling")
    normal_decision_prohibited: bool = Field(description="must be true; Reliability must not approve, reject, or defer")
    summary: str = Field(description="board-ready summary of council reliability and calibration")
    scores: list[ReliabilityScore] = Field(description="per-agent reliability scores")
    eval_dataset: str = Field(description="W&B/Weave eval dataset or replay-set label")
    replay_plan: list[str] = Field(description="replay cases or eval steps to run")
    prompt_improvement_directives: list[str] = Field(
        description="global prompt-improvement directives extracted from the per-agent scorecards"
    )
    promotion_gate: str = Field(description="global gate for accepting future prompt/model changes")


# --------------------------------------------------------------------------- #
# Shared state (extends CopilotKitState → streams to the frontend)
# --------------------------------------------------------------------------- #
class DebateState(CopilotKitState):
    decision: str
    phase: str
    current_phase: str
    context: dict
    positions: list
    transcript: list
    recommendation: dict
    agent_statuses: list
    observability_events: list
    trace_summary: dict
    redis_activity: list
    sponsor_health: dict
    reliability_scores: list
    council_influence: dict
    learning_report: dict
    # W&B Weave-driven agent replacement loop (see src/self_improvement.py). Holds
    # rolling per-sub-agent reliability history, active incarnation directives, and
    # which sub-agent was retired/replaced this round. Loaded at intake (prior
    # rounds), updated by the self_improvement node after the reliability audit.
    # Mirrored in frontend/src/lib/types.ts.
    agent_improvements: dict
    # Governance outcome (policy controls, approval route, audit, obligations).
    # Set by the governance node; mirrored in frontend/src/lib/types.ts.
    governance: dict
    # Deterministic strategic-planning digital twin (see src/planning.py). Set by
    # the CFO synthesis node when the prompt asks for a multi-month plan.
    strategic_plan: dict
    # --- OpenAI-native council expansion (typed + streamed; mirrored in types.ts) #
    decision_type: str
    decision_plan: dict
    evidence_plan: list
    tool_plan: list
    follow_up: dict
    challenge_report: dict
    evidence_gaps: list
    board_memo: dict
    operator_actions: list
    model_telemetry: dict
    realtime_status: dict
    prompt_versions: list
    # --- AG-UI command-and-control layer (see src/agui_commands.py) --------- #
    # Operator commands stream back through these eight keys; they are mirrored
    # in frontend/src/lib/types.ts and appended to STREAM_STATE_KEYS below.
    command_queue: list
    active_command: dict
    pinned_evidence: list
    requested_scenario: dict
    agent_focus: dict
    phase_controls: dict
    export_status: dict
    command_audit_log: list


def _extract_decision(messages: list) -> str:
    """Pull the most recent human message as the decision under review."""
    for m in reversed(messages or []):
        role = getattr(m, "type", None) or (m.get("role") if isinstance(m, dict) else None)
        if role in ("human", "user"):
            content = getattr(m, "content", None)
            if content is None and isinstance(m, dict):
                content = m.get("content", "")
            return content if isinstance(content, str) else str(content)
    return ""


def _turn(role_key: str, **extra) -> dict:
    p = ROSTER[role_key]
    return {"agent": role_key, "label": p["label"], "role": p["role"], "monogram": p["monogram"], **extra}


def _thinking_turn(role_key: str, detail: str) -> dict:
    turn = _turn(role_key, type="thinking", headline="Working…", argument=detail)
    turn["id"] = f"thinking-{role_key}"
    turn["at"] = _now()
    return turn


def _without_thinking(transcript: list | None, role_key: str) -> list:
    token = f"thinking-{role_key}"
    return [item for item in (transcript or []) if item.get("id") != token]


def _now() -> str:
    return time.strftime("%H:%M:%S")


def _company_name(context: dict | None = None) -> str:
    financials = (context or {}).get("financials") or {}
    return financials.get("name") or "Acme Corp"


def _company_stage(context: dict | None = None) -> str:
    financials = (context or {}).get("financials") or {}
    return financials.get("stage") or "Series A"


def _company_id(context: dict | None = None) -> str:
    financials = (context or {}).get("financials") or {}
    return financials.get("id") or ROOM


def _role_plan(decision_plan: dict | None, role_key: str) -> RoleEvidencePlan | None:
    """Reconstruct a role's evidence plan from the planner's streamed plan dict."""
    for item in (decision_plan or {}).get("role_plans", []) or []:
        if (item.get("role") or "").lower() == role_key:
            try:
                return RoleEvidencePlan(**item)
            except Exception:
                return RoleEvidencePlan(
                    role=role_key,
                    tools=item.get("tools") or [],
                    policy_queries=item.get("policy_queries") or [],
                    focus_slices=item.get("focus_slices") or [],
                    prior_decisions=item.get("prior_decisions") or [],
                    rationale=item.get("rationale") or "",
                )
    return None


def _decision_focus(decision_plan: dict | None) -> list[str]:
    return (decision_plan or {}).get("decision_specific_focus") or []


def _event(sponsor: str, label: str, detail: str, tone: str = "info") -> dict:
    return {
        "id": f"{time.time_ns()}-{sponsor.lower().replace(' ', '-')}",
        "at": _now(),
        "sponsor": sponsor,
        "label": label,
        "detail": detail,
        "tone": tone,
    }


def _redis_activity(label: str, detail: str, kind: str) -> dict:
    return {"at": _now(), "label": label, "detail": detail, "kind": kind}


OBSERVABILITY_AGENTS = {
    **ROSTER,
    "planner": {
        "label": "Evidence Planner",
        "role": "Chief of Staff · Planning",
        "monogram": "EP",
        "mandate": "classifying the decision and routing Redis-backed evidence before the council speaks",
    },
    "challenge": {
        "label": "Evidence Challenge Panel",
        "role": "Grounding QA",
        "monogram": "EC",
        "mandate": "verifying each role cited enough concrete numbers before the CFO rules",
    },
    "governance": {
        "label": "Governance & Controls",
        "role": "Controls, Approvals & Audit",
        "monogram": "GV",
        "mandate": "enforcing board policy, routing approvals, recording the audit trail, and setting obligations",
    },
    "system": {
        "label": "Atlas Runtime",
        "role": "Persistence & State Streaming",
        "monogram": "AT",
        "mandate": "publishing decisions, Redis activity, sponsor health, and AG-UI state",
    },
}

STREAM_STATE_KEYS = (
    "decision",
    "phase",
    "current_phase",
    "context",
    "positions",
    "transcript",
    "recommendation",
    "agent_statuses",
    "observability_events",
    "trace_summary",
    "redis_activity",
    "sponsor_health",
    "reliability_scores",
    "council_influence",
    "learning_report",
    "agent_improvements",
    "strategic_plan",
    "governance",
    # OpenAI-native council expansion keys (mirrored in frontend types.ts).
    "decision_type",
    "decision_plan",
    "evidence_plan",
    "tool_plan",
    "follow_up",
    "challenge_report",
    "evidence_gaps",
    "board_memo",
    "operator_actions",
    "model_telemetry",
    "realtime_status",
    "prompt_versions",
    # AG-UI command-and-control keys (single source of truth in agui_commands).
    *AGUI.COMMAND_STATE_KEYS,
)


def _initial_agent_statuses() -> list[dict]:
    return [
        {
            "id": key,
            "label": meta["label"],
            "role": meta["role"],
            "monogram": meta["monogram"],
            "mandate": meta["mandate"],
            "status": "waiting",
            "detail": "Awaiting council turn",
            "last_update": _now(),
        }
        for key, meta in OBSERVABILITY_AGENTS.items()
    ]


def _set_agent_status(statuses: list | None, role_key: str, **updates) -> list[dict]:
    current = [dict(item) for item in (statuses or _initial_agent_statuses())]
    seen = False
    for item in current:
        if item.get("id") == role_key:
            item.update(updates)
            item["last_update"] = _now()
            seen = True
            break
    if not seen and role_key in OBSERVABILITY_AGENTS:
        meta = OBSERVABILITY_AGENTS[role_key]
        current.append({"id": role_key, **meta, **updates, "last_update": _now()})
    return current


def _trace_summary(
    node: str,
    status: str,
    model_calls: int = 0,
    tool_calls: int = 0,
    telemetry: dict | None = None,
    tool_plan: list | None = None,
    evidence_gaps: list | None = None,
) -> dict:
    weave = weave_status()
    tel = telemetry or {}
    return {
        "node": node,
        "status": status,
        "model": f"{LLM_PROVIDER}:{LLM_MODEL}",
        "model_family": tel.get("model_family") or model_family(),
        "reasoning_effort": LLM_REASONING_EFFORT,
        "text_verbosity": LLM_TEXT_VERBOSITY,
        "realtime_model": OPENAI_REALTIME_MODEL,
        "realtime_reasoning_effort": OPENAI_REALTIME_REASONING_EFFORT,
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "input_tokens": tel.get("input_tokens"),
        "output_tokens": tel.get("output_tokens"),
        "total_tokens": tel.get("total_tokens"),
        "cost_usd": tel.get("estimated_cost_usd"),
        "tool_plan_size": len(tool_plan) if tool_plan is not None else None,
        "evidence_gap_count": len(evidence_gaps) if evidence_gaps is not None else None,
        "refusals": len(tel.get("refusals") or []) if tel else 0,
        "errors": len(tel.get("errors") or []) if tel else 0,
        "weave_project": weave.get("project"),
        "weave_url": weave.get("url"),
        "state_streaming": "copilotkit_emit_state" if _copilotkit_emit_state else "langgraph_state_delta",
        "updated_at": _now(),
        "spans": _span_statuses(node, status),
    }


def _span_statuses(active_node: str, active_status: str) -> list[dict]:
    parallel_analysts = {
        "analyst_treasury",
        "analyst_fpna",
        "analyst_risk",
        "analyst_procurement",
    }
    spans = [
        "intake",
        "planner",
        "committee_parallel",
        "challenge_panel",
        "debate_round",
        "cfo_synthesis",
        "governance",
        "reliability_auditor",
        "self_improvement",
        "persist_decision",
    ]
    active_index = spans.index(active_node) if active_node in spans else -1
    out: list[dict] = []
    for index, span in enumerate(spans):
        if span == active_node:
            span_status = active_status
        elif active_node == "committee_parallel" and span in parallel_analysts:
            span_status = active_status
        elif active_index >= 0 and index < active_index:
            span_status = "complete"
        else:
            span_status = "waiting"
        out.append({"node": span, "status": span_status})
    return out


def _append(items: list | None, *new_items: dict, keep: int = 48) -> list:
    return [*(items or []), *new_items][-keep:]


def _normalize_agent_id(value: str) -> str:
    normalized = (value or "").lower().replace("&", "and").replace("-", "_").replace(" ", "_")
    aliases = {
        "office_of_the_cfo": "cfo",
        "chief_financial_officer": "cfo",
        "financial_planning_and_analysis": "fpna",
        "fpa": "fpna",
        "fp&a": "fpna",
        "risk_audit": "risk",
        "risk_and_audit": "risk",
        "risk_&_audit": "risk",
    }
    return aliases.get(normalized, normalized)


def _weighted_reliability(score: dict) -> int:
    weights = {
        "outcome_accuracy": 0.30,
        "evidence_grounding": 0.20,
        "forecast_calibration": 0.15,
        "policy_compliance": 0.15,
        "debate_value": 0.10,
        "confidence_calibration": 0.05,
        "trace_quality": 0.05,
    }
    value = sum(float(score.get(key, 0) or 0) * weight for key, weight in weights.items())
    return max(0, min(100, round(value)))


def _fast_challenge_report(positions: list) -> dict:
    findings: list[dict] = []
    gaps: list[str] = []
    scores: list[int] = []
    for pos in positions:
        role = str(pos.get("agent") or pos.get("role") or "unknown")
        profile = role_challenge_profile(role)
        metrics = pos.get("cited_metrics") or []
        cited = len(metrics) >= 1
        score = min(100, 42 + len(metrics) * 14)
        scores.append(score)
        if not cited:
            gaps.append(f"{role}: no cited metrics")
        findings.append(
            {
                "role": role,
                **profile,
                "cited_enough_numbers": cited,
                "grounding_score": score,
                "strongest_number": metrics[0] if metrics else "n/a",
                "missing_evidence": [] if cited else ["Cite concrete figures from Redis context"],
                "challenge": (
                    f"{profile['challenge_label']}: verify {profile['challenge_lens']} with role-specific evidence."
                    if cited
                    else f"{profile['challenge_label']}: ground this lane in live role-specific figures."
                ),
            }
        )
    overall = round(sum(scores) / len(scores)) if scores else 72
    return {
        "summary": f"Fast-path grounding check across {len(findings)} committee positions.",
        "overall_grounding": overall,
        "findings": findings,
        "unresolved_gaps": gaps,
    }


def _fast_reliability_scorecard(positions: list, recommendation: dict) -> tuple[list[dict], str]:
    scorecard: list[dict] = []
    rec_conf = int(recommendation.get("confidence") or 72)
    for agent_id in ["cfo", *ANALYSTS]:
        pos = next((p for p in positions if (p.get("agent") or "") == agent_id), None)
        cited = len((pos or {}).get("cited_metrics") or [])
        if agent_id == "cfo":
            rel = min(98, max(64, rec_conf))
            grounding = rel
        else:
            rel = min(96, 56 + cited * 10)
            grounding = min(100, 44 + cited * 16)
        item = {
            "agent_id": agent_id,
            "evidence_grounding": grounding,
            "forecast_calibration": max(50, rel - 5),
            "policy_compliance": max(52, rel - 3),
            "debate_value": max(48, rel - 8),
            "outcome_accuracy": 72,
            "confidence_calibration": rec_conf,
            "trace_quality": 90,
            "reliability": rel,
            "rationale": "Fast-path score from live citations and council confidence.",
            "known_weaknesses": [] if cited or agent_id == "cfo" else ["Needs more cited figures"],
            "prompt_adjustment": "Keep citing Redis-backed metrics in every position.",
            "replay_cases": [
                f"Replay {agent_id} with cited-metric redaction and require the auditor to flag missing Redis grounding.",
                f"Replay {agent_id} against trace telemetry to verify evidence, calibration, policy, debate, and trace-quality scoring.",
            ],
            "prompt_improvement_directive": (
                "Preserve role-specific reasoning while citing at least two Redis-backed figures and the trace span that supports them."
            ),
            "promotion_gate": "Replay eval recommended before prompt promotion.",
        }
        item["reliability"] = _weighted_reliability(item)
        scorecard.append(item)
    average = round(sum(s["reliability"] for s in scorecard) / len(scorecard)) if scorecard else 0
    return scorecard, f"Fast-path reliability scorecard · {average}% council average"


def _default_reliability_score(agent_id: str) -> dict:
    return {
        "agent_id": agent_id,
        "evidence_grounding": 0,
        "forecast_calibration": 0,
        "policy_compliance": 0,
        "debate_value": 0,
        "outcome_accuracy": 0,
        "confidence_calibration": 0,
        "trace_quality": 0,
        "reliability": 0,
        "rationale": "Reliability auditor did not return a score for this agent; promotion is blocked until replay evidence exists.",
        "known_weaknesses": ["Missing reliability score"],
        "prompt_adjustment": "Require this role to cite evidence and replay outcomes in every recommendation.",
        "replay_cases": ["Replay the decision with this agent required to emit a complete reliability scorecard row."],
        "prompt_improvement_directive": "Block promotion until this agent produces evidence-grounded scorecard outputs with trace provenance.",
        "promotion_gate": "Blocked until W&B Weave replay eval produces a complete scorecard.",
    }


def _normalize_reliability_scores(scores: list[ReliabilityScore]) -> list[dict]:
    expected = ["cfo", *ANALYSTS]
    by_agent: dict[str, dict] = {}
    for score in scores:
        item = score.model_dump()
        agent_id = _normalize_agent_id(item.get("agent_id", ""))
        if agent_id not in expected:
            continue
        item["agent_id"] = agent_id
        item["reliability"] = _weighted_reliability(item)
        item.setdefault("replay_cases", [])
        item.setdefault("prompt_improvement_directive", item.get("prompt_adjustment") or "")
        by_agent[agent_id] = item
    return [by_agent.get(agent_id) or _default_reliability_score(agent_id) for agent_id in expected]


def _attach_influence_to_statuses(statuses: list | None, influence: dict | None) -> list[dict]:
    current = [dict(item) for item in (statuses or _initial_agent_statuses())]
    by_agent = {item.get("agent_id"): item for item in (influence or {}).get("weights") or []}
    updated: list[dict] = []
    for item in current:
        weight = by_agent.get(item.get("id"))
        if weight:
            item.update(
                influence_weight=weight.get("influence_weight"),
                influence_rationale=weight.get("rationale"),
                grounding_signal=weight.get("grounding_signal"),
                debate_signal=weight.get("debate_signal"),
                historical_reliability=weight.get("historical_reliability"),
            )
        updated.append(item)
    return updated


def _attach_reliability_to_statuses(statuses: list | None, scores: list[dict]) -> list[dict]:
    current = [dict(item) for item in (statuses or _initial_agent_statuses())]
    by_agent = {item["agent_id"]: item for item in scores}
    updated: list[dict] = []
    for item in current:
        score = by_agent.get(item.get("id"))
        if score:
            item.update(
                reliability_score=score["reliability"],
                reliability_dimensions={
                    "outcome_accuracy": score["outcome_accuracy"],
                    "evidence_grounding": score["evidence_grounding"],
                    "forecast_calibration": score["forecast_calibration"],
                    "policy_compliance": score["policy_compliance"],
                    "debate_value": score["debate_value"],
                    "confidence_calibration": score["confidence_calibration"],
                    "trace_quality": score["trace_quality"],
                },
                reliability_rationale=score["rationale"],
                known_weaknesses=score["known_weaknesses"],
                prompt_adjustment=score["prompt_adjustment"],
                prompt_improvement_directive=score.get("prompt_improvement_directive"),
                replay_cases=score.get("replay_cases", []),
                promotion_gate=score["promotion_gate"],
            )
        updated.append(item)
    return updated


def _tool_body(tool_obj, *args, **kwargs) -> str:
    """Run local Redis-backed LangChain tools without emitting AG-UI tool events."""
    func = getattr(tool_obj, "func", None)
    if func is not None:
        return func(*args, **kwargs)
    payload = kwargs if kwargs else (args[0] if args else {})
    return tool_obj.invoke(payload)


async def _emit(config: RunnableConfig, state: dict) -> None:
    """Best-effort CopilotKit state streaming across package versions."""
    if _copilotkit_emit_state is None:
        return
    try:
        result = _copilotkit_emit_state(config, state)
    except TypeError as exc:
        try:
            result = _copilotkit_emit_state(state)
        except Exception:
            print(f"[observability] CopilotKit state emit skipped: {exc}")
            return
    except Exception as exc:
        print(f"[observability] CopilotKit state emit skipped: {exc}")
        return
    try:
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        print(f"[observability] CopilotKit state emit skipped: {exc}")


def _stream_state(state: DebateState, patch: dict) -> dict:
    merged = {}
    for key in STREAM_STATE_KEYS:
        if key in patch:
            merged[key] = patch[key]
        elif key in state:
            merged[key] = state.get(key)
    return json.loads(json.dumps(merged, default=str))


async def _emit_patch(state: DebateState, config: RunnableConfig, **patch) -> None:
    # Fold the live operator command-state (Redis) into every emit so commands
    # issued mid-debate stream straight back through the same AG-UI channel.
    await _emit(config, _stream_state(state, AGUI.merge_command_state(patch, ROOM)))


def _with_command_state(return_dict: dict) -> dict:
    """Attach the live command-state keys to a node's return dict.

    Keeps the eight command keys current in the merged LangGraph state between
    nodes so the UI never flickers back to empty between streamed emits.
    """
    return AGUI.merge_command_state(return_dict, ROOM)


def _command_focus_prompt() -> str:
    """Render outstanding operator commands as a prompt block the council reads.

    This is what makes the command layer a real agent/UI contract rather than a
    display: pinned evidence, the requested scenario, a routed/clarify focus, and
    any standing challenge are injected into the live council prompts.
    """
    state = AGUI.load_command_state(ROOM)
    parts: list[str] = []
    focus = state.get("agent_focus") or {}
    if focus.get("question"):
        lens = f" Lens: {focus.get('role_lens')}." if focus.get("role_lens") else ""
        instruction = f" Instruction: {focus.get('role_instruction')}." if focus.get("role_instruction") else ""
        parts.append(
            f"OPERATOR {str(focus.get('mode', 'focus')).upper()} → {focus.get('label', focus.get('agent'))}: "
            f"{focus.get('question')}.{lens}{instruction}"
        )
    pins = state.get("pinned_evidence") or []
    if pins:
        pinned = "; ".join(f"{p.get('title')}: {p.get('detail')}" for p in pins[-4:])
        parts.append(f"OPERATOR-PINNED EVIDENCE (weigh explicitly): {pinned}")
    scenario = state.get("requested_scenario") or {}
    if scenario.get("mode") == "single" and scenario.get("impact"):
        parts.append(
            f"OPERATOR SCENARIO '{scenario.get('label')}': {json.dumps(scenario.get('impact'))}"
        )
    elif scenario.get("mode") == "compare" and scenario.get("options"):
        labels = ", ".join(o.get("label", "?") for o in scenario.get("options", []))
        parts.append(f"OPERATOR IS COMPARING OPTIONS: {labels}")
    if not parts:
        return ""
    return (
        "\n\nLIVE OPERATOR DIRECTIVES (the human steering this debate — address them "
        "explicitly and ground any response in the figures):\n- " + "\n- ".join(parts)
    )


def _role_distinction_summary(meta: dict | None) -> dict:
    report = (meta or {}).get("report") or {}
    cases = report.get("cases") or []
    return {
        "id": report.get("id"),
        "overall_score": report.get("overall_score"),
        "passed": report.get("passed"),
        "case_count": report.get("case_count"),
        "role_average_scores": report.get("role_average_scores") or {},
        "collapse_flags": [
            {"case": case.get("id"), "flags": case.get("collapse_flags")}
            for case in cases
            if case.get("collapse_flags")
        ],
        "artifact_path": meta.get("artifact_path") if meta else None,
        "event_id": meta.get("event_id") if meta else None,
        "redis_error": meta.get("redis_error") if meta else None,
        "weave": meta.get("weave") if meta else None,
    }


def _runway_impact_summary(impact: dict | None) -> str:
    """Compact board-facing summary from the compute_runway tool result."""
    if not impact:
        return "Runway impact unavailable"
    current = impact.get("current_runway_months")
    scenario = impact.get("scenario_runway_months")
    delta = impact.get("delta_months")
    if scenario is None:
        note = impact.get("note") or "scenario becomes cash-flow positive"
        return f"Runway: {current if current is not None else 'n/a'} months -> cash-flow positive ({note})"
    delta_text = f"{delta:+.1f} months" if isinstance(delta, (int, float)) else "delta n/a"
    current_text = f"{current:.1f}" if isinstance(current, (int, float)) else "n/a"
    scenario_text = f"{scenario:.1f}" if isinstance(scenario, (int, float)) else "n/a"
    return f"Runway: {current_text} months -> {scenario_text} months ({delta_text})"


async def _honor_pause(state: DebateState, config: RunnableConfig, node_label: str) -> None:
    """Cooperatively hold at a node boundary while an operator pause is active.

    Bounded by ``PAUSE_MAX_SECONDS`` and released the instant the operator
    resumes. Only the analysis/debate boundaries call this; synthesis and
    persistence never pause, so a submitted decision always reaches a ruling.
    """
    if not AGUI.is_paused(ROOM):
        return
    await _emit_patch(
        state,
        config,
        current_phase=f"Paused by operator before {node_label}",
    )
    waited = 0.0
    while AGUI.is_paused(ROOM) and waited < PAUSE_MAX_SECONDS:
        await asyncio.sleep(PAUSE_POLL_SECONDS)
        waited += PAUSE_POLL_SECONDS


def _sponsor_event(health: dict) -> dict:
    if health.get("ready"):
        return _event("Sponsors", "Strict-live health checked", "All required sponsors are ready", "positive")
    return _event(
        "Sponsors",
        "Strict-live blockers visible",
        "; ".join(health.get("blockers") or ["Sponsor health unavailable"]),
        "warning",
    )


def _redis_ping_activity(health: dict) -> dict:
    redis = next((item for item in health.get("sponsors", []) if item.get("id") == "redis"), {})
    return _redis_activity(
        "Redis PING",
        redis.get("detail") or "Redis health checked",
        "health-ok" if redis.get("ready") else "health-error",
    )


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #
@weave.op(name="intake")
async def intake_node(state: DebateState, config: RunnableConfig) -> dict:
    require_live_ready()
    # Fresh debate → clear any command state from a prior run so the operator
    # command panel starts empty and accumulates commands for this run only.
    AGUI.reset_command_state(ROOM)
    decision = _extract_decision(state.get("messages", []))
    health = sponsor_health()
    realtime = realtime_health()
    telemetry = init_telemetry()
    agent_statuses = _set_agent_status(
        _initial_agent_statuses(),
        "cfo",
        status="speaking",
        detail="Convening the council and loading sponsor-backed context",
    )
    events = [
        _event("OpenAI", "Reasoning model selected", f"{LLM_PROVIDER}:{LLM_MODEL} · {model_family()} · {LLM_REASONING_EFFORT}", "positive"),
        _event("OpenAI", "Realtime 2 voice armed" if realtime.get("ready") else "Realtime 2 voice config incomplete", realtime.get("detail") or OPENAI_REALTIME_MODEL, "positive" if realtime.get("ready") else "warning"),
        _event("W&B Weave", "Trace span opened", "intake", "positive"),
        _event("CopilotKit", "AG-UI state stream", "finance_department", "positive"),
        _sponsor_event(health),
    ]
    await _emit_patch(
        state,
        config,
        decision=decision,
        phase="intake",
        current_phase="Convening council",
        agent_statuses=agent_statuses,
        observability_events=events,
        trace_summary=_trace_summary("intake", "running", tool_calls=3, telemetry=telemetry),
        redis_activity=[_redis_ping_activity(health)],
        sponsor_health=health,
        reliability_scores=[],
        council_influence={},
        learning_report={},
        model_telemetry=telemetry,
        realtime_status=realtime,
    )
    context = {
        "financials": json.loads(_tool_body(get_company_financials)),
        "vendors": json.loads(_tool_body(list_vendors)),
        "policies": json.loads(_tool_body(search_finance_policies, query=decision or "financial decision")),
    }
    # Fold in imported finance-operations data + reconciliation, but only when a
    # connector has actually ingested data — keeps the core demo unchanged and
    # never invents operations facts. Best-effort: must not fail the debate.
    try:
        from src.integrations.service import operations_context_snapshot

        operations = operations_context_snapshot()
        if operations:
            context["operations"] = operations
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[intake] operations snapshot skipped: {exc}")
    prompt_versions = prompt_versions_payload(context)
    # Load the rolling replacement overlay earned from prior W&B Weave reliability
    # traces. Standing directives from the current incarnation are grafted onto
    # each analyst this round; the self_improvement node retires the weakest and
    # spawns a replacement after the reliability audit.
    try:
        improvement_state = SI.agent_improvement_state(_company_id(context))
    except Exception as exc:
        improvement_state = {}
        print(f"[intake] improvement overlay warning: {redact_secrets(exc)}")
    framing = _turn(
        "cfo",
        type="framing",
        headline="Convening the committee",
        argument=f"The committee will evaluate: “{decision}”. The evidence planner routes Redis-backed facts, then Treasury, FP&A, Risk & Audit, and Procurement weigh in, the challenge panel verifies grounding, and I rule.",
        key_points=[],
    )
    return {
        "decision": decision,
        "context": context,
        "phase": "analysis",
        "current_phase": "Council analysis",
        "positions": [],
        "transcript": [framing],
        "recommendation": {},
        "reliability_scores": [],
        "council_influence": {},
        "learning_report": {},
        "agent_improvements": improvement_state,
        "strategic_plan": {},
        "decision_type": "",
        "decision_plan": {},
        "evidence_plan": [],
        "tool_plan": [],
        "follow_up": {},
        "challenge_report": {},
        "evidence_gaps": [],
        "board_memo": {},
        "operator_actions": [],
        "model_telemetry": telemetry,
        "realtime_status": realtime,
        "prompt_versions": prompt_versions,
        "agent_statuses": _set_agent_status(
            agent_statuses,
            "cfo",
            status="done",
            headline="Council convened",
            detail="Context loaded from Redis and policy memory",
        ),
        "observability_events": _append(
            events,
            _event("Redis", "Financial context loaded", "JSON company record, vendor search, vector policy RAG", "positive"),
            _event("OpenAI", "Prompt versions pinned", f"{len(prompt_versions)} versioned prompts for W&B promotion gates", "info"),
            _event(
                "W&B Weave",
                "Self-improvement overlay loaded",
                (
                    f"Round {int((improvement_state or {}).get('round', 0))} · "
                    f"{sum(1 for a in (improvement_state or {}).get('agents', {}).values() if (a or {}).get('directive'))}/4 "
                    "sub-agents carry an earned directive"
                ),
                "positive",
            ),
            _event("W&B Weave", "Trace span closed", "intake", "positive"),
        ),
        "trace_summary": _trace_summary("intake", "complete", tool_calls=3, telemetry=telemetry),
        "redis_activity": [
            _redis_ping_activity(health),
            _redis_activity("RedisJSON", f"Loaded {_company_name(context)} financial system of record", "json"),
            _redis_activity("RediSearch", f"Loaded {len(context['vendors'])} vendor contracts", "search"),
            _redis_activity("Vector RAG", f"Loaded {len(context['policies'])} policy/precedent hits", "vector"),
        ],
        "sponsor_health": health,
    }


@weave.op(name="planner")
async def planner_node(state: DebateState, config: RunnableConfig) -> dict:
    """Classify the decision and decide which Redis-backed evidence each role needs."""
    require_live_ready()
    await _honor_pause(state, config, "evidence planning")
    health = sponsor_health()
    company = _company_name(state.get("context"))
    stage = _company_stage(state.get("context"))
    agent_statuses = _set_agent_status(
        state.get("agent_statuses"),
        "planner",
        status="thinking",
        detail="Classifying the decision and routing evidence",
    )
    events = _append(
        state.get("observability_events"),
        _event("W&B Weave", "Trace span opened", "planner", "positive"),
        _event("OpenAI", "Structured planning call", "decision type + evidence plan", "info"),
        _sponsor_event(health),
    )
    await _emit_patch(
        state,
        config,
        phase="planning",
        current_phase="Evidence planning",
        agent_statuses=agent_statuses,
        observability_events=events,
        trace_summary=_trace_summary("planner", "running", model_calls=1, telemetry=state.get("model_telemetry")),
        sponsor_health=health,
    )

    result = await classify_and_plan(
        decision=state.get("decision", ""),
        context=state.get("context", {}),
        company=company,
        stage=stage,
        config=config,
    )
    telemetry = merge_telemetry(state.get("model_telemetry"), result.telemetry)

    if not result.ok:
        # Honest degradation: no plan → analysts still run on full live context.
        detail = result.telemetry.error or result.telemetry.refusal or "Planner returned no plan"
        agent_statuses = _set_agent_status(agent_statuses, "planner", status="warning", headline="Planning unavailable", detail=detail)
        treasury_prefs = treasury_evidence_preferences()
        fpna_prefs = fpna_evidence_preferences()
        risk_prefs = risk_evidence_preferences()
        procurement_prefs = procurement_evidence_preferences()
        fallback_treasury = RoleEvidencePlan(
            role="treasury",
            tools=treasury_prefs["tools"],
            policy_queries=treasury_prefs["policy_queries"],
            focus_slices=treasury_prefs["focus_slices"],
            prior_decisions=[],
            rationale="Fallback Treasury route: preserve liquidity mechanics evidence when the planner degrades.",
        )
        fallback_fpna = RoleEvidencePlan(
            role="fpna",
            tools=fpna_prefs["tools"],
            policy_queries=fpna_prefs["policy_queries"],
            focus_slices=fpna_prefs["focus_slices"],
            prior_decisions=[],
            rationale="Fallback FP&A route: preserve forecast and unit-economics evidence when the planner degrades.",
        )
        fallback_risk = RoleEvidencePlan(
            role="risk",
            tools=risk_prefs["tools"],
            policy_queries=risk_prefs["policy_queries"],
            focus_slices=risk_prefs["focus_slices"],
            prior_decisions=[],
            rationale="Fallback Risk route: preserve controls, approvals, evidence, and provenance checks when the planner degrades.",
        )
        fallback_procurement = RoleEvidencePlan(
            role="procurement",
            tools=procurement_prefs["tools"],
            policy_queries=procurement_prefs["policy_queries"],
            focus_slices=procurement_prefs["focus_slices"],
            prior_decisions=[],
            rationale="Fallback Procurement route: preserve vendor, contract, invoice, renewal, and negotiation evidence when the planner degrades.",
        )
        fallback = DecisionPlan(
            decision_type="general",
            title=(state.get("decision", "") or "decision")[:60],
            summary=state.get("decision", ""),
            entities=[],
            required_facts=[],
            assumptions=[],
            follow_up_questions=[],
            role_plans=[fallback_treasury, fallback_fpna, fallback_risk, fallback_procurement],
            decision_specific_focus=[],
        )
        return {
            "decision_type": "general",
            "decision_plan": fallback.model_dump(),
            "evidence_plan": [
                fallback_treasury.model_dump(),
                fallback_fpna.model_dump(),
                fallback_risk.model_dump(),
                fallback_procurement.model_dump(),
            ],
            "tool_plan": tool_plan_entries(fallback),
            "follow_up": {},
            "phase": "analysis",
            "current_phase": "Evidence planning degraded",
            "agent_statuses": agent_statuses,
            "model_telemetry": telemetry,
            "observability_events": _append(
                events,
                _event("OpenAI", "Planner degraded", detail, "warning"),
                _event("W&B Weave", "Trace span closed", "planner", "positive"),
            ),
            "trace_summary": _trace_summary("planner", "warning", model_calls=1, telemetry=telemetry),
            "sponsor_health": health,
        }

    plan: DecisionPlan = result.parsed
    plan_dict = plan.model_dump()
    plan_dict["decision_type"] = plan.decision_type.value
    role_plans = plan_dict.get("role_plans", [])
    tool_plan = tool_plan_entries(plan)
    missing_facts = [fact.name for fact in plan.required_facts if not fact.available]
    follow_up_questions = [question.model_dump() for question in plan.follow_up_questions]
    follow_up = {
        "needed": bool(missing_facts or follow_up_questions),
        "questions": follow_up_questions,
        "missing_facts": missing_facts,
        "assumptions": plan.assumptions,
        "source": "planner",
    }
    framing = _turn(
        "cfo",
        type="framing",
        headline=f"Classified: {plan.decision_type.value.replace('_', ' ')}",
        argument=(
            f"{plan.summary} "
            + (
                f"Proceeding with {len(missing_facts)} required fact(s) missing under explicit assumptions."
                if missing_facts
                else "All required facts are present in the system of record."
            )
        ),
        key_points=plan.decision_specific_focus[:3],
    )
    agent_statuses = _set_agent_status(
        agent_statuses,
        "planner",
        status="done",
        stance="conditional" if missing_facts else "support",
        headline=f"{plan.decision_type.value.replace('_', ' ')} · {len(tool_plan)} planned tool steps",
        detail=plan.summary,
    )
    follow_up_events = []
    if follow_up["needed"]:
        follow_up_events.append(
            _event("CopilotKit", "Follow-up requested via AG-UI", f"{len(follow_up_questions)} clarifying question(s); proceeding under assumptions", "warning")
        )
    return {
        "decision_type": plan.decision_type.value,
        "decision_plan": plan_dict,
        "evidence_plan": role_plans,
        "tool_plan": tool_plan,
        "follow_up": follow_up,
        "transcript": state.get("transcript", []) + [framing],
        "phase": "analysis",
        "current_phase": f"Evidence plan ready · {plan.decision_type.value.replace('_', ' ')}",
        "agent_statuses": agent_statuses,
        "model_telemetry": telemetry,
        "observability_events": _append(
            events,
            *follow_up_events,
            _event("OpenAI", "Decision classified", plan.decision_type.value, "positive"),
            _event("Redis", "Evidence routed", f"{len(tool_plan)} planned tool/RAG steps across roles", "positive"),
            _event("W&B Weave", "Trace span closed", "planner", "positive"),
        ),
        "trace_summary": _trace_summary("planner", "complete", model_calls=1, telemetry=telemetry, tool_plan=tool_plan),
        "redis_activity": _append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("Evidence plan", f"{len(role_plans)} role plans, {len(tool_plan)} tool steps", "plan"),
        ),
        "sponsor_health": health,
    }


class _ParallelCouncil:
    """Mutable streamed state while four analysts run concurrently."""

    def __init__(self, state: DebateState, config: RunnableConfig, events: list, health: dict) -> None:
        self.state = state
        self.config = config
        self.lock = asyncio.Lock()
        self.health = health
        self.agent_statuses = [dict(item) for item in (state.get("agent_statuses") or _initial_agent_statuses())]
        self.transcript = list(state.get("transcript") or [])
        self.positions = list(state.get("positions") or [])
        self.redis_activity = list(state.get("redis_activity") or [])
        self.observability_events = list(events)
        self.model_telemetry = state.get("model_telemetry")

    async def emit(self, *, current_phase: str, **extra) -> None:
        async with self.lock:
            await _emit_patch(
                self.state,
                self.config,
                phase="analysis",
                current_phase=current_phase,
                agent_statuses=self.agent_statuses,
                transcript=self.transcript,
                positions=self.positions,
                observability_events=self.observability_events,
                redis_activity=self.redis_activity,
                model_telemetry=self.model_telemetry,
                sponsor_health=self.health,
                trace_summary=_trace_summary(
                    "committee_parallel",
                    "running",
                    model_calls=len(self.positions),
                    telemetry=self.model_telemetry,
                ),
                **extra,
            )

    async def set_role(self, role_key: str, **updates) -> None:
        async with self.lock:
            self.agent_statuses = _set_agent_status(self.agent_statuses, role_key, **updates)

    async def show_thinking(self, role_key: str, detail: str) -> None:
        async with self.lock:
            self.transcript = _without_thinking(self.transcript, role_key) + [_thinking_turn(role_key, detail)]

    async def record_position(
        self,
        role_key: str,
        entry: dict,
        *,
        agent_updates: dict,
        redis_items: list[dict],
        events: list[dict],
        telemetry: dict,
    ) -> None:
        async with self.lock:
            self.transcript = _without_thinking(self.transcript, role_key) + [entry]
            self.positions = [*self.positions, entry]
            self.agent_statuses = _set_agent_status(self.agent_statuses, role_key, **agent_updates)
            self.redis_activity = _append(self.redis_activity, *redis_items)
            self.observability_events = _append(self.observability_events, *events)
            self.model_telemetry = merge_telemetry(self.model_telemetry, telemetry)


async def _run_analyst_parallel(role_key: str, state: DebateState, config: RunnableConfig, live: _ParallelCouncil) -> None:
    persona = ROSTER[role_key]
    company_name = _company_name(state.get("context"))
    decision_type = state.get("decision_type") or "general"
    role_plan = _role_plan(state.get("decision_plan"), role_key)
    health = live.health

    await live.set_role(role_key, status="thinking", detail=f"{persona['label']}: pulling live Redis evidence")
    await live.show_thinking(role_key, f"{persona['label']} is querying vendors, policies, and financials from Redis…")
    await live.emit(current_phase="All four analysts working in parallel")

    bundle = gather_role_evidence(
        role_plan,
        state.get("context", {}),
        decision=state.get("decision", ""),
        decision_type=decision_type,
        entities=(state.get("decision_plan") or {}).get("entities") or [],
    )
    redis_items = [
        _redis_ping_activity(health),
        *[_redis_activity(item["label"], item["detail"], item["kind"]) for item in bundle.redis_activity],
    ]

    await live.set_role(
        role_key,
        status="thinking",
        detail=f"{persona['label']}: forming position ({bundle.policy_hits} policy hit(s))",
    )
    await live.show_thinking(
        role_key,
        f"{persona['label']} has the numbers — drafting a grounded stance now…",
    )
    await live.emit(current_phase=f"{persona['label']} + peers analyzing in parallel")

    overlay = (state.get("agent_improvements") or {}).get("agents", {}).get(role_key) or {}
    improvement_directive = str(overlay.get("directive") or "")
    mandate_emphasis = str(overlay.get("mandate_emphasis") or "").strip()
    if mandate_emphasis:
        improvement_directive = f"{mandate_emphasis}\n\n{improvement_directive}".strip()
    result = await analyst_position(
        role_key=role_key,
        persona=persona,
        decision=state.get("decision", ""),
        context=state.get("context", {}),
        company=company_name,
        stage=_company_stage(state.get("context")),
        decision_type=decision_type,
        decision_focus=_decision_focus(state.get("decision_plan")),
        evidence=bundle.evidence,
        operator_directives=_command_focus_prompt(),
        improvement_directive=improvement_directive,
        config=config,
    )
    prompt_version = next((pv for pv in (state.get("prompt_versions") or []) if pv.get("role") == role_key), {})
    # Surface the replacement generation label (e.g. treasury.v4-evidence-plan+gen3)
    # so the transcript shows which incarnation is active this round.
    displayed_version = overlay.get("version_label") if overlay.get("directive") else prompt_version.get("version")

    if not result.ok:
        detail = result.telemetry.refusal or result.telemetry.error or "No grounded position produced"
        entry = _turn(
            role_key,
            type="position",
            headline="Could not produce a grounded position",
            argument=detail,
            key_points=[],
            cited_metrics=[],
            prompt_version=displayed_version,
            error=detail,
        )
        await live.record_position(
            role_key,
            entry,
            agent_updates={"status": "error", "headline": "Position unavailable", "detail": detail},
            redis_items=redis_items,
            events=[_event("OpenAI", f"{persona['label']} call failed", detail, "warning")],
            telemetry=result.telemetry,
        )
        await live.emit(current_phase=f"{persona['label']} could not finish — peers continue")
        return

    pos = result.parsed
    entry = _turn(
        role_key,
        type="position",
        stance=pos.stance,
        headline=pos.headline,
        argument=pos.argument,
        key_points=pos.key_points,
        role_specific_lens=pos.role_specific_lens,
        cited_metrics=pos.cited_metrics,
        evidence_used=pos.evidence_used,
        forecast_assumptions=pos.forecast_assumptions,
        scenario_sensitivities=pos.scenario_sensitivities,
        plan_vs_actual_deltas=pos.plan_vs_actual_deltas,
        control_findings=pos.control_findings,
        missing_evidence_requests=pos.missing_evidence_requests,
        approval_or_policy_blockers=pos.approval_or_policy_blockers,
        negotiation_levers=pos.negotiation_levers,
        prompt_version=displayed_version,
        tokens=result.telemetry.total_tokens,
        cost_usd=result.telemetry.cost_usd,
    )
    await live.record_position(
        role_key,
        entry,
        agent_updates={
            "status": "speaking",
            "stance": pos.stance,
            "headline": pos.headline,
            "detail": pos.argument,
        },
        redis_items=redis_items,
        events=[
            _event(
                "Redis",
                "Evidence grounded",
                f"{persona['label']} cited {len(pos.cited_metrics)} figure(s)",
                "positive",
            ),
        ],
        telemetry=result.telemetry,
    )
    await live.emit(current_phase=f"{persona['label']} position in — {len(live.positions)}/{len(ANALYSTS)} complete")


@weave.op(name="committee_parallel")
async def committee_parallel_node(state: DebateState, config: RunnableConfig) -> dict:
    """Run Treasury, FP&A, Risk, and Procurement concurrently with live streaming."""
    require_live_ready()
    await _honor_pause(state, config, "parallel committee analysis")
    health = sponsor_health()

    agent_statuses = state.get("agent_statuses") or _initial_agent_statuses()
    for role_key in ANALYSTS:
        persona = ROSTER[role_key]
        agent_statuses = _set_agent_status(
            agent_statuses,
            role_key,
            status="thinking",
            detail=f"{persona['label']}: joining the live council room",
        )

    thinking_turns = [_thinking_turn(role_key, f"{ROSTER[role_key]['label']} is opening the books…") for role_key in ANALYSTS]
    events = _append(
        state.get("observability_events"),
        _event("W&B Weave", "Trace span opened", "committee_parallel", "positive"),
        _event("OpenAI", "Parallel analyst calls", f"{len(ANALYSTS)} roles · low reasoning effort", "info"),
        _event("Redis", "Redis-grounded context active", "All roles query live financials, vendors, and policies", "positive"),
        _sponsor_event(health),
    )
    await _emit_patch(
        state,
        config,
        phase="analysis",
        current_phase="All four analysts working in parallel",
        agent_statuses=agent_statuses,
        transcript=(state.get("transcript") or []) + thinking_turns,
        observability_events=events,
        trace_summary=_trace_summary("committee_parallel", "running", telemetry=state.get("model_telemetry")),
        redis_activity=_append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("Parallel council", "Treasury · FP&A · Risk · Procurement in session", "state"),
        ),
        sponsor_health=health,
    )

    live = _ParallelCouncil(state, config, events, health)
    live.agent_statuses = agent_statuses
    live.transcript = (state.get("transcript") or []) + thinking_turns

    await asyncio.gather(*[_run_analyst_parallel(role_key, state, config, live) for role_key in ANALYSTS])

    model_calls = len(live.positions)
    closing = _append(
        live.observability_events,
        _event("OpenAI", "Parallel analysis complete", f"{model_calls}/{len(ANALYSTS)} positions recorded", "positive"),
        _event("W&B Weave", "Trace span closed", "committee_parallel", "positive"),
    )
    return {
        "positions": live.positions,
        "transcript": live.transcript,
        "phase": "analysis",
        "current_phase": f"Committee positions ready · {model_calls}/{len(ANALYSTS)}",
        "agent_statuses": live.agent_statuses,
        "model_telemetry": live.model_telemetry,
        "observability_events": closing,
        "trace_summary": _trace_summary("committee_parallel", "complete", model_calls=model_calls, telemetry=live.model_telemetry),
        "redis_activity": live.redis_activity,
        "sponsor_health": health,
    }


def make_analyst_node(role_key: str):
    persona = ROSTER[role_key]

    @weave.op(name=f"analyst_{role_key}")
    async def node(state: DebateState, config: RunnableConfig) -> dict:
        require_live_ready()
        await _honor_pause(state, config, persona["label"])
        health = sponsor_health()
        company_name = _company_name(state.get("context"))
        decision_type = state.get("decision_type") or "general"
        role_plan = _role_plan(state.get("decision_plan"), role_key)
        agent_statuses = _set_agent_status(
            state.get("agent_statuses"),
            role_key,
            status="thinking",
            detail=f"{persona['label']} is gathering planned evidence",
        )
        events = _append(
            state.get("observability_events"),
            _event("W&B Weave", "Trace span opened", f"analyst_{role_key}", "positive"),
            _event("OpenAI", "Structured position call", f"{persona['label']} · {decision_type}", "info"),
            _event("Redis", "Redis-grounded context active", "Using intake financials, vendors, and policy hits", "positive"),
            _sponsor_event(health),
        )
        await _emit_patch(
            state,
            config,
            phase="analysis",
            current_phase=f"{persona['label']} analysis",
            agent_statuses=agent_statuses,
            observability_events=events,
            trace_summary=_trace_summary(f"analyst_{role_key}", "running", model_calls=1, telemetry=state.get("model_telemetry")),
            redis_activity=_append(
                state.get("redis_activity"),
                _redis_ping_activity(health),
                _redis_activity("Evidence gather", f"{persona['label']} executing planned Redis evidence", "state"),
            ),
            sponsor_health=health,
        )

        # Multi-step, live evidence gathering against Redis before the model speaks.
        bundle = gather_role_evidence(
        role_plan,
        state.get("context", {}),
        decision=state.get("decision", ""),
        decision_type=decision_type,
        entities=(state.get("decision_plan") or {}).get("entities") or [],
    )
        redis_activity = _append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            *[_redis_activity(item["label"], item["detail"], item["kind"]) for item in bundle.redis_activity],
        )
        overlay = (state.get("agent_improvements") or {}).get("agents", {}).get(role_key) or {}
        improvement_directive = str(overlay.get("directive") or "")
        mandate_emphasis = str(overlay.get("mandate_emphasis") or "").strip()
        if mandate_emphasis:
            improvement_directive = f"{mandate_emphasis}\n\n{improvement_directive}".strip()
        result = await analyst_position(
            role_key=role_key,
            persona=persona,
            decision=state.get("decision", ""),
            context=state.get("context", {}),
            company=company_name,
            stage=_company_stage(state.get("context")),
            decision_type=decision_type,
            decision_focus=_decision_focus(state.get("decision_plan")),
            evidence=bundle.evidence,
            operator_directives=_command_focus_prompt(),
            improvement_directive=improvement_directive,
            config=config,
        )
        telemetry = merge_telemetry(state.get("model_telemetry"), result.telemetry)
        prompt_version = next((pv for pv in (state.get("prompt_versions") or []) if pv.get("role") == role_key), {})
        displayed_version = overlay.get("version_label") if overlay.get("directive") else prompt_version.get("version")

        if not result.ok:
            detail = result.telemetry.refusal or result.telemetry.error or "No grounded position produced"
            entry = _turn(role_key, type="position", headline="Model could not produce a grounded position", argument=detail, key_points=[], cited_metrics=[], prompt_version=displayed_version, error=detail)
            agent_statuses = _set_agent_status(agent_statuses, role_key, status="error", headline="Position unavailable", detail=detail)
            return {
                "transcript": state.get("transcript", []) + [entry],
                "phase": "analysis",
                "current_phase": f"{persona['label']} position unavailable",
                "agent_statuses": agent_statuses,
                "model_telemetry": telemetry,
                "observability_events": _append(events, _event("OpenAI", f"{persona['label']} call failed", detail, "warning"), _event("W&B Weave", "Trace span closed", f"analyst_{role_key}", "positive")),
                "trace_summary": _trace_summary(f"analyst_{role_key}", "warning", model_calls=1, telemetry=telemetry),
                "redis_activity": redis_activity,
                "sponsor_health": health,
            }

        pos = result.parsed
        entry = _turn(
            role_key,
            type="position",
            stance=pos.stance,
            headline=pos.headline,
            argument=pos.argument,
            key_points=pos.key_points,
            role_specific_lens=pos.role_specific_lens,
            cited_metrics=pos.cited_metrics,
            evidence_used=pos.evidence_used,
            forecast_assumptions=pos.forecast_assumptions,
            scenario_sensitivities=pos.scenario_sensitivities,
            plan_vs_actual_deltas=pos.plan_vs_actual_deltas,
            control_findings=pos.control_findings,
            missing_evidence_requests=pos.missing_evidence_requests,
            approval_or_policy_blockers=pos.approval_or_policy_blockers,
            negotiation_levers=pos.negotiation_levers,
            prompt_version=displayed_version,
            tokens=result.telemetry.total_tokens,
            cost_usd=result.telemetry.cost_usd,
        )
        agent_statuses = _set_agent_status(
            agent_statuses,
            role_key,
            status="speaking",
            stance=pos.stance,
            headline=pos.headline,
            detail=pos.argument,
        )
        return {
            "positions": state.get("positions", []) + [entry],
            "transcript": state.get("transcript", []) + [entry],
            "phase": "analysis",
            "current_phase": f"{persona['label']} position recorded",
            "agent_statuses": agent_statuses,
            "model_telemetry": telemetry,
            "observability_events": _append(
                events,
                _event("Redis", "Evidence grounded", f"{persona['label']} cited {len(pos.cited_metrics)} figure(s); {bundle.policy_hits} policy hit(s)", "positive"),
                _event("W&B Weave", "Trace span closed", f"analyst_{role_key}", "positive"),
            ),
            "trace_summary": _trace_summary(f"analyst_{role_key}", "complete", model_calls=1, telemetry=telemetry),
            "redis_activity": redis_activity,
            "sponsor_health": health,
        }

    return node


@weave.op(name="challenge_panel")
async def challenge_node(state: DebateState, config: RunnableConfig) -> dict:
    """A second model pass that verifies whether each role cited enough concrete numbers."""
    require_live_ready()
    await _honor_pause(state, config, "challenge panel")
    health = sponsor_health()
    company = _company_name(state.get("context"))
    positions = state.get("positions", [])
    agent_statuses = _set_agent_status(
        state.get("agent_statuses"),
        "challenge",
        status="thinking",
        detail="Verifying each role cited enough concrete numbers",
    )
    events = _append(
        state.get("observability_events"),
        _event("W&B Weave", "Trace span opened", "challenge_panel", "positive"),
        _event("OpenAI", "Evidence grounding call", f"{len(positions)} positions audited", "info"),
        _sponsor_event(health),
    )
    await _emit_patch(
        state,
        config,
        phase="challenge",
        current_phase="Evidence challenge panel",
        agent_statuses=agent_statuses,
        observability_events=events,
        trace_summary=_trace_summary("challenge_panel", "running", model_calls=1, telemetry=state.get("model_telemetry")),
        sponsor_health=health,
    )

    if _fast_council():
        challenge_report = _fast_challenge_report(positions)
        evidence_gaps = challenge_report.get("unresolved_gaps") or []
        challenge_turn = {
            "agent": "challenge",
            "label": "Evidence Challenge Panel",
            "role": "Grounding QA",
            "monogram": "EC",
            "type": "challenge",
            "headline": f"Council grounding · {challenge_report.get('overall_grounding')}%",
            "argument": challenge_report.get("summary", "Fast-path evidence check"),
            "key_points": [
                f"{finding.get('challenge_label')}: {finding.get('challenge')}"
                for finding in (challenge_report.get("findings") or [])
            ][:4],
            "challenge_findings": challenge_report.get("findings") or [],
        }
        agent_statuses = _set_agent_status(
            agent_statuses,
            "challenge",
            status="done",
            headline=f"Grounding {challenge_report.get('overall_grounding')}%",
            detail=challenge_report.get("summary", "Fast-path evidence check"),
        )
        return {
            "challenge_report": challenge_report,
            "evidence_gaps": evidence_gaps,
            "transcript": state.get("transcript", []) + [challenge_turn],
            "phase": "challenge",
            "current_phase": "Evidence check complete",
            "agent_statuses": agent_statuses,
            "observability_events": _append(
                events,
                _event("OpenAI", "Challenge fast-path", f"{len(positions)} positions checked without extra model pass", "positive"),
                _event("W&B Weave", "Trace span closed", "challenge_panel", "positive"),
            ),
            "trace_summary": _trace_summary("challenge_panel", "complete", model_calls=0, telemetry=state.get("model_telemetry"), evidence_gaps=evidence_gaps),
            "sponsor_health": health,
        }

    result = await challenge_panel(decision=state.get("decision", ""), positions=positions, company=company, config=config)
    telemetry = merge_telemetry(state.get("model_telemetry"), result.telemetry)

    if not result.ok:
        detail = result.telemetry.refusal or result.telemetry.error or "Challenge panel unavailable"
        evidence_gaps = [f"Challenge panel unavailable: {detail}"]
        challenge_report = {"summary": detail, "overall_grounding": None, "findings": [], "unresolved_gaps": evidence_gaps, "error": detail}
        agent_statuses = _set_agent_status(agent_statuses, "challenge", status="warning", headline="Challenge unavailable", detail=detail)
        return {
            "challenge_report": challenge_report,
            "evidence_gaps": evidence_gaps,
            "phase": "challenge",
            "current_phase": "Evidence challenge degraded",
            "agent_statuses": agent_statuses,
            "model_telemetry": telemetry,
            "observability_events": _append(events, _event("OpenAI", "Challenge panel degraded", detail, "warning"), _event("W&B Weave", "Trace span closed", "challenge_panel", "positive")),
            "trace_summary": _trace_summary("challenge_panel", "warning", model_calls=1, telemetry=telemetry, evidence_gaps=evidence_gaps),
            "sponsor_health": health,
        }

    report = result.parsed
    challenge_report = report.model_dump()
    evidence_gaps = list(report.unresolved_gaps)
    for finding in report.findings:
        for gap in finding.missing_evidence:
            evidence_gaps.append(f"{finding.role}: {gap}")
    weak = [finding.role for finding in report.findings if not finding.cited_enough_numbers]

    challenge_turn = {
        "agent": "challenge",
        "label": "Evidence Challenge Panel",
        "role": "Grounding QA",
        "monogram": "EC",
        "type": "challenge",
        "headline": f"Council grounding · {report.overall_grounding}%",
        "argument": report.summary,
        "key_points": [f"{finding.challenge_label}: {finding.challenge}" for finding in report.findings][:4],
        "challenge_findings": [finding.model_dump() for finding in report.findings],
    }
    agent_statuses = _set_agent_status(
        agent_statuses,
        "challenge",
        status="done" if (report.overall_grounding or 0) >= 60 else "warning",
        headline=f"Grounding {report.overall_grounding}%",
        detail=report.summary,
    )
    # Flag under-grounded analysts on their own seats so the UI reflects it.
    for finding in report.findings:
        role = _normalize_agent_id(finding.role)
        if role in ANALYSTS and not finding.cited_enough_numbers:
            agent_statuses = _set_agent_status(agent_statuses, role, evidence_flagged=True, grounding_score=finding.grounding_score)
    return {
        "challenge_report": challenge_report,
        "evidence_gaps": evidence_gaps[:24],
        "transcript": state.get("transcript", []) + [challenge_turn],
        "phase": "challenge",
        "current_phase": "Evidence verified" if not weak else f"Evidence gaps flagged ({len(weak)})",
        "agent_statuses": agent_statuses,
        "model_telemetry": telemetry,
        "observability_events": _append(
            events,
            _event(
                "OpenAI",
                "Grounding verified" if not weak else "Evidence gaps flagged",
                f"overall {report.overall_grounding}%" + (f"; weak: {', '.join(weak)}" if weak else ""),
                "positive" if not weak else "warning",
            ),
            _event("W&B Weave", "Trace span closed", "challenge_panel", "positive"),
        ),
        "trace_summary": _trace_summary("challenge_panel", "complete", model_calls=1, telemetry=telemetry, evidence_gaps=evidence_gaps),
        "sponsor_health": health,
    }


@weave.op(name="debate_round")
async def debate_node(state: DebateState, config: RunnableConfig) -> dict:
    require_live_ready()
    await _honor_pause(state, config, "cross-examination")
    health = sponsor_health()
    company_name = _company_name(state.get("context"))
    positions = state.get("positions", [])
    agent_statuses = state.get("agent_statuses") or _initial_agent_statuses()
    for role_key in ANALYSTS:
        agent_statuses = _set_agent_status(
            agent_statuses,
            role_key,
            status="thinking",
            detail="Cross-examining peer assumptions",
        )
    events = _append(
        state.get("observability_events"),
        _event("W&B Weave", "Trace span opened", "debate_round", "positive"),
        _event("OpenAI", "Cross-examination call", f"{len(positions)} positions", "info"),
        _event("Redis", "Redis-grounded positions active", "Debate uses analyst positions derived from intake context", "positive"),
        _sponsor_event(health),
    )
    await _emit_patch(
        state,
        config,
        phase="debate",
        current_phase="Committee cross-examination",
        agent_statuses=agent_statuses,
        observability_events=events,
        trace_summary=_trace_summary("debate_round", "running", model_calls=1, telemetry=state.get("model_telemetry")),
        redis_activity=_append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("Redis context", "Debate using Redis-grounded analyst positions", "state"),
        ),
        sponsor_health=health,
    )
    result = await cross_examination(
        decision=state.get("decision", ""),
        positions=positions,
        challenge_report=state.get("challenge_report"),
        company=company_name,
        operator_directives=_command_focus_prompt(),
        config=config,
    )
    telemetry = merge_telemetry(state.get("model_telemetry"), result.telemetry)
    turns: list[dict] = []
    if result.ok:
        exchanges = ensure_role_specific_exchanges(
            result.parsed.exchanges,
            positions=positions,
            challenge_report=state.get("challenge_report"),
        )
        turns = [
            {
                "agent": "debate",
                "type": "rebuttal",
                "from_role": exchange.get("from_role"),
                "to_role": exchange.get("to_role"),
                "challenge_type": exchange.get("challenge_type"),
                "challenge_label": exchange.get("challenge_label"),
                "challenge_lens": exchange.get("challenge_lens"),
                "point": exchange.get("point"),
            }
            for exchange in exchanges
        ]
    for role_key in ANALYSTS:
        agent_statuses = _set_agent_status(
            agent_statuses,
            role_key,
            status="speaking",
            detail="Challenge recorded in the debate transcript",
        )
    status = "complete" if result.ok else "warning"
    closing_events = [_event("W&B Weave", "Trace span closed", "debate_round", "positive")]
    if not result.ok:
        closing_events.insert(0, _event("OpenAI", "Cross-examination degraded", result.telemetry.error or result.telemetry.refusal or "no exchanges", "warning"))
    return {
        "transcript": state.get("transcript", []) + turns,
        "phase": "debate",
        "current_phase": "Cross-examination complete" if result.ok else "Cross-examination degraded",
        "agent_statuses": agent_statuses,
        "model_telemetry": telemetry,
        "observability_events": _append(events, *closing_events),
        "trace_summary": _trace_summary("debate_round", status, model_calls=1, telemetry=telemetry),
        "redis_activity": _append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("Redis context", "Cross-examination grounded in intake context", "state"),
        ),
        "sponsor_health": health,
    }


@weave.op(name="council_influence")
async def influence_node(state: DebateState, config: RunnableConfig) -> dict:
    require_live_ready()
    health = sponsor_health()
    company_name = _company_name(state.get("context"))
    company_id = _company_id(state.get("context"))
    decision_type = state.get("decision_type") or "general"
    positions = state.get("positions", [])
    debate_turns = [t for t in state.get("transcript", []) if t.get("type") == "rebuttal"]
    historical = CI.weave_reliability_priors(
        company_id,
        state.get("context"),
        state.get("agent_improvements"),
    )
    signals = CI.debate_signals(
        positions=positions,
        challenge_report=state.get("challenge_report"),
        debate_turns=debate_turns,
    )
    agent_statuses = _set_agent_status(
        state.get("agent_statuses"),
        "cfo",
        status="thinking",
        detail="Assigning unequal council influence weights before the ruling",
    )
    events = _append(
        state.get("observability_events"),
        _event("W&B Weave", "Trace span opened", "council_influence", "positive"),
        _event("Redis", "Weave reliability priors loaded", json.dumps(historical), "positive"),
        _sponsor_event(health),
    )
    await _emit_patch(
        state,
        config,
        phase="influence",
        current_phase="Council influence weighting",
        agent_statuses=agent_statuses,
        observability_events=events,
        trace_summary=_trace_summary("council_influence", "running", model_calls=1, telemetry=state.get("model_telemetry")),
        redis_activity=_append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("Council influence", "Blending debate signals with W&B Weave reliability priors", "state"),
        ),
        sponsor_health=health,
    )

    baseline_weights = CI.compute_baseline_influence(historical=historical, signals=signals, decision_type=decision_type)

    if _fast_council():
        weights = CI.apply_signal_floors(baseline_weights, signals)
        top = max(weights, key=lambda item: item["influence_weight"])
        influence = CI.influence_report_payload(
            weights=weights,
            summary=f"Fast-path influence · {top['agent_id']} leads at {top['influence_weight']}%",
            decision_type_fit=f"{decision_type} decision routed through deterministic council weighting",
            historical=historical,
            signals=signals,
            decision_type=decision_type,
        )
        telemetry = state.get("model_telemetry")
        status = "complete"
    else:
        result = await council_influence(
            decision=state.get("decision", ""),
            positions=positions,
            debate_turns=debate_turns,
            challenge_report=state.get("challenge_report"),
            historical_reliability=historical,
            decision_type=decision_type,
            company=company_name,
            operator_directives=_command_focus_prompt(),
            config=config,
        )
        telemetry = merge_telemetry(state.get("model_telemetry"), result.telemetry)
        if result.ok:
            report = result.parsed
            merged = CI.reconcile_with_baseline(
                [item.model_dump() for item in report.weights],
                baseline_weights,
            )
            normalized = CI.apply_signal_floors(merged, signals)
            influence = CI.influence_report_payload(
                weights=normalized,
                summary=report.summary,
                decision_type_fit=report.decision_type_fit,
                historical=historical,
                signals=signals,
                decision_type=decision_type,
            )
            status = "complete"
        else:
            weights = CI.apply_signal_floors(baseline_weights, signals)
            detail = result.telemetry.refusal or result.telemetry.error or "Council influence assignment unavailable"
            influence = CI.influence_report_payload(
                weights=weights,
                summary=f"Influence fallback after live assignment failure: {detail}",
                decision_type_fit=f"{decision_type} decision used deterministic weighting",
                historical=historical,
                signals=signals,
                decision_type=decision_type,
            )
            status = "warning"

    agent_statuses = _attach_influence_to_statuses(state.get("agent_statuses"), influence)
    top = max(influence["weights"], key=lambda item: item["influence_weight"])
    influence_turn = _turn(
        "cfo",
        type="influence",
        headline=f"Council influence · {top['agent_id']} {top['influence_weight']}%",
        argument=influence["summary"],
        key_points=[f"{item['agent_id']}: {item['influence_weight']}%" for item in influence["weights"]],
    )
    return {
        "council_influence": influence,
        "transcript": state.get("transcript", []) + [influence_turn],
        "phase": "influence",
        "current_phase": "Council influence assigned",
        "agent_statuses": agent_statuses,
        "model_telemetry": telemetry,
        "observability_events": _append(
            events,
            _event(
                "OpenAI",
                "Council influence assigned",
                f"{top['agent_id']} leads at {top['influence_weight']}%",
                "positive" if status == "complete" else "warning",
            ),
            _event("W&B Weave", "Trace span closed", "council_influence", "positive"),
        ),
        "trace_summary": _trace_summary("council_influence", status, model_calls=0 if _fast_council() else 1, telemetry=telemetry),
        "redis_activity": _append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("Council influence", influence["summary"], "state"),
        ),
        "sponsor_health": health,
    }


@weave.op(name="cfo_synthesis")
async def synthesis_node(state: DebateState, config: RunnableConfig) -> dict:
    require_live_ready()
    health = sponsor_health()
    company_name = _company_name(state.get("context"))
    decision_type = state.get("decision_type") or "general"
    agent_statuses = _set_agent_status(
        state.get("agent_statuses"),
        "cfo",
        status="thinking",
        detail="Reconciling positions into a board-ready recommendation",
    )
    events = _append(
        state.get("observability_events"),
        _event("W&B Weave", "Trace span opened", "cfo_synthesis", "positive"),
        _event("OpenAI", "Structured CFO synthesis call", "recommendation + cost estimates", "info"),
        _event("Redis", "Runway model ready", "compute_runway will read the company cash record", "positive"),
        _sponsor_event(health),
    )
    await _emit_patch(
        state,
        config,
        phase="synthesis",
        current_phase="CFO synthesis",
        agent_statuses=agent_statuses,
        observability_events=events,
        trace_summary=_trace_summary("cfo_synthesis", "running", model_calls=1, tool_calls=1, telemetry=state.get("model_telemetry")),
        redis_activity=_append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("Runway model", "Ready to compute current vs scenario runway", "tool"),
        ),
        sponsor_health=health,
    )
    positions = state.get("positions", [])
    debate_turns = [t for t in state.get("transcript", []) if t.get("type") == "rebuttal"]

    # Strategic-planning digital twin: when the prompt asks for a multi-month plan,
    # compute it deterministically *first* (cash / burn / runway / ARR / churn /
    # hiring ramps / vendor savings / financing, month by month) so the CFO
    # narrates real figures instead of inventing them. No model output feeds these
    # numbers; the planning engine persists the plan to Redis with provenance.
    strategic_plan: dict = {}
    plan_prompt = ""
    if PL.is_strategic_request(state.get("decision", "")):
        try:
            _plan = PL.plan_from_decision(state["decision"])
            strategic_plan = _plan.model_dump()
            plan_prompt = (
                "\n\nDETERMINISTIC STRATEGIC PLAN (authoritative figures computed by the planning "
                "engine — ground your recommendation in these and do not invent numbers):\n"
                + json.dumps(PL.summarize_for_model(_plan), default=str)
            )
            events = _append(
                events,
                _event(
                    "Redis",
                    "Strategic plan computed",
                    f"{_plan.horizon_months}-month deterministic plan {_plan.id} "
                    f"({_plan.playbook_label or 'base operating plan'})",
                    "positive",
                ),
            )
        except Exception as exc:  # planning must never break the debate
            print(f"[synthesis] strategic plan skipped: {exc}")

    # OpenAI-native CFO synthesis (typed, telemetry-captured). Operator directives
    # and the deterministic plan are injected so the ruling is grounded, not guessed.
    rec_result = await cfo_recommendation(
        decision=state.get("decision", ""),
        context=state.get("context", {}),
        positions=positions,
        debate_turns=debate_turns,
        challenge_report=state.get("challenge_report"),
        decision_plan=state.get("decision_plan"),
        council_influence=state.get("council_influence"),
        company=company_name,
        decision_type=decision_type,
        operator_directives=_command_focus_prompt() + plan_prompt,
        config=config,
    )
    telemetry = merge_telemetry(state.get("model_telemetry"), rec_result.telemetry)
    if rec_result.ok:
        rec = rec_result.parsed
        rec_decision, rec_ruling, rec_rationale = rec.decision, rec.ruling, rec.rationale
        rec_confidence, confidence_factors = CI.confidence_adjustment(
            base_confidence=rec.confidence,
            influence=state.get("council_influence"),
            evidence_gaps=state.get("evidence_gaps"),
        )
        rec_key_risks, rec_conditions = rec.key_risks, rec.conditions
        rec_tradeoffs = rec.tradeoffs
        rec_analyst_influence = [
            item.model_dump() if hasattr(item, "model_dump") else dict(item)
            for item in rec.analyst_influence
        ]
        rec_dissent = rec.dissent
        rec_assumption_conditions = rec.assumptions_converted_to_conditions
        rec_runway_basis = rec.runway_impact_basis
        extra_monthly, one_time, added_rev = rec.estimated_monthly_cost, rec.estimated_one_time_cost, rec.estimated_added_monthly_revenue
    else:
        # Honest surfacing: DEFER with the real error as rationale (not fabricated analysis).
        detail = rec_result.telemetry.refusal or rec_result.telemetry.error or "CFO synthesis returned no recommendation"
        rec_decision, rec_confidence = "DEFER", 0
        rec_ruling = "DEFER until live CFO synthesis completes."
        rec_rationale = f"Synthesis could not complete live: {detail}"
        rec_key_risks, rec_conditions = ["CFO synthesis call did not complete"], []
        rec_tradeoffs = []
        rec_analyst_influence = []
        rec_dissent = "No dissent could be resolved because the CFO synthesis call did not complete."
        rec_assumption_conditions = []
        rec_runway_basis = "No incremental cost or revenue levers accepted; model synthesis failed."
        confidence_factors = {}
        extra_monthly = one_time = added_rev = 0.0

    # Precise runway impact, computed (not hallucinated) from the CFO's estimates.
    impact = json.loads(
        _tool_body(
            compute_runway,
            extra_monthly_spend=extra_monthly,
            one_time_cost=one_time,
            added_monthly_revenue=added_rev,
        )
    )
    runway_summary = _runway_impact_summary(impact)
    recommendation = {
        "decision": rec_decision,
        "ruling": rec_ruling,
        "confidence": rec_confidence,
        "rationale": rec_rationale,
        "tradeoffs": rec_tradeoffs,
        "analyst_influence": rec_analyst_influence,
        "dissent": rec_dissent,
        "key_risks": rec_key_risks,
        "conditions": rec_conditions,
        "assumptions_converted_to_conditions": rec_assumption_conditions,
        "runway_impact_basis": rec_runway_basis,
        "runway_impact_summary": runway_summary,
        "impact": impact,
        "decision_type": decision_type,
        "council_influence": state.get("council_influence") or {},
        "confidence_factors": confidence_factors if rec_result.ok else {},
        "source": "cfo" if rec_result.ok else "model_error",
    }

    # Board memo + operator action checklist (second structured pass, grounded in computed runway).
    memo_dict: dict = {}
    operator_actions: list = []
    memo_status = "skipped"
    if rec_result.ok and not _fast_council():
        memo_result = await board_memo(
            decision=state.get("decision", ""),
            company=company_name,
            decision_type=decision_type,
            recommendation=recommendation,
            impact=impact,
            positions=positions,
            challenge_report=state.get("challenge_report"),
            config=config,
        )
        telemetry = merge_telemetry(telemetry, memo_result.telemetry)
        if memo_result.ok:
            memo_dict = memo_result.parsed.model_dump()
            operator_actions = memo_dict.get("operator_actions", [])
            memo_status = "ready"
        else:
            memo_status = memo_result.telemetry.refusal or memo_result.telemetry.error or "memo unavailable"

    closing = _turn(
        "cfo",
        type="decision",
        headline=f"{rec_decision} · {rec_confidence}% confidence",
        argument=rec_ruling,
        key_points=rec_conditions or rec_key_risks,
        ruling=rec_ruling,
        confidence=rec_confidence,
        rationale=rec_rationale,
        tradeoffs=rec_tradeoffs,
        analyst_influence=rec_analyst_influence,
        dissent=rec_dissent,
        conditions=rec_conditions,
        assumptions_converted_to_conditions=rec_assumption_conditions,
        runway_impact_basis=rec_runway_basis,
        runway_impact_summary=runway_summary,
        impact=impact,
        tokens=telemetry.get("total_tokens"),
        cost_usd=telemetry.get("estimated_cost_usd"),
    )
    summary = (
        f"**Recommendation: {rec_decision}** ({rec_confidence}% confidence)\n\n"
        f"{rec_ruling}\n\n{runway_summary}\n\n{rec_rationale}"
    )
    agent_statuses = _set_agent_status(
        agent_statuses,
        "cfo",
        status="speaking" if rec_result.ok else "warning",
        stance=rec_decision.lower(),
        headline=f"{rec_decision} · {rec_confidence}% confidence",
        detail=rec_ruling,
    )
    return {
        "recommendation": recommendation,
        "board_memo": memo_dict,
        "operator_actions": operator_actions,
        "strategic_plan": strategic_plan,
        "transcript": state.get("transcript", []) + [closing],
        "phase": "synthesis",
        "current_phase": "Committee resolution issued",
        "agent_statuses": agent_statuses,
        "model_telemetry": telemetry,
        "observability_events": _append(
            events,
            _event("Redis", "Runway impact computed", "compute_runway tool returned scenario deltas", "positive"),
            _event("OpenAI", "Board memo ready" if memo_status == "ready" else "Board memo skipped", f"{len(operator_actions)} operator actions" if memo_status == "ready" else memo_status, "positive" if memo_status == "ready" else "warning"),
            _event("W&B Weave", "Trace span closed", "cfo_synthesis", "positive"),
        ),
        "trace_summary": _trace_summary("cfo_synthesis", "complete" if rec_result.ok else "warning", model_calls=2, tool_calls=1, telemetry=telemetry),
        "redis_activity": _append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("Runway model", "Computed current vs scenario runway", "tool"),
        ),
        "sponsor_health": health,
        "messages": [AIMessage(content=summary)],
    }


@weave.op(name="governance")
async def governance_node(state: DebateState, config: RunnableConfig) -> dict:
    """Turn the CFO recommendation into a governed decision: evaluate board policy
    controls, route the approval, write the immutable audit trail, and set
    obligations + monitoring. Deterministic and grounded in the Redis policy rules —
    no LLM call, and no human approval is ever fabricated (the request is recorded as
    pending or system-cleared)."""
    require_live_ready()
    health = sponsor_health()
    rec = state.get("recommendation") or {}
    decision = state.get("decision") or ""
    agent_statuses = _set_agent_status(
        state.get("agent_statuses"),
        "governance",
        status="thinking",
        detail="Checking policy controls, routing approvals, and writing the audit trail",
    )
    events = _append(
        state.get("observability_events"),
        _event("W&B Weave", "Trace span opened", "governance", "positive"),
        _event("Redis", "Policy controls active", "RediSearch board policy rules + approval matrix", "positive"),
        _sponsor_event(health),
    )
    await _emit_patch(
        state,
        config,
        phase="governance",
        current_phase="Governance & controls",
        agent_statuses=agent_statuses,
        observability_events=events,
        trace_summary=_trace_summary("governance", "running", tool_calls=3),
        redis_activity=_append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("Policy rules", "Evaluating board controls (RediSearch)", "search"),
        ),
        sponsor_health=health,
    )
    try:
        req = GOV.govern_recommendation(
            decision,
            rec,
            state.get("context"),
            created_by="Atlas Council",
            created_by_type=ActorType.AGENT,
            source="council_debate",
        )
        governance_payload = GOV.governance_state(req)
        gov_turn = GOV.governance_turn(req)
        blocking = sum(1 for v in req.violations if v.blocking)
        stance = "oppose" if req.blocked else ("conditional" if req.human_approvals_pending() else "support")
        agent_statuses = _set_agent_status(
            agent_statuses,
            "governance",
            status="done",
            stance=stance,
            headline=gov_turn["headline"],
            detail=governance_payload["summary"],
        )
        return {
            "governance": governance_payload,
            "transcript": state.get("transcript", []) + [gov_turn],
            "phase": "governance",
            "current_phase": "Governance controls applied",
            "agent_statuses": agent_statuses,
            "observability_events": _append(
                events,
                _event("Redis", "Approval request recorded", f"atlas:approval:{req.id} · {req.status.value}", "positive"),
                _event("Redis", "Audit trail appended", f"atlas:stream:audit · {len(req.violations) + 1} event(s)", "positive"),
                _event("W&B Weave", "Trace span closed", "governance", "positive"),
            ),
            "trace_summary": _trace_summary("governance", "complete", tool_calls=3),
            "redis_activity": _append(
                state.get("redis_activity"),
                _redis_activity("RedisJSON", f"Approval {req.id} · {req.status.value} ({len(req.route)} step route)", "json"),
                _redis_activity("Redis Stream", f"Audit events for {req.id} ({blocking} blocking control)", "stream"),
                _redis_activity("Redis Pub/Sub", "Published governance dashboard update", "pubsub"),
            ),
            "sponsor_health": health,
        }
    except Exception as exc:  # governance must not fail the debate
        print(f"[governance] warning: {exc}")
        agent_statuses = _set_agent_status(
            agent_statuses,
            "governance",
            status="warning",
            detail=f"Governance completed with warning: {exc}",
        )
        return {
            "governance": {"status": "error", "error": str(exc)},
            "phase": "governance",
            "current_phase": "Governance warning",
            "agent_statuses": agent_statuses,
            "observability_events": _append(events, _event("Redis", "Governance warning", str(exc), "warning")),
            "trace_summary": _trace_summary("governance", "warning", tool_calls=3),
            "redis_activity": _append(
                state.get("redis_activity"),
                _redis_ping_activity(health),
                _redis_activity("Governance warning", str(exc), "warning"),
            ),
            "sponsor_health": health,
        }


@weave.op(name="reliability_auditor")
async def reliability_node(state: DebateState, config: RunnableConfig) -> dict:
    require_live_ready()
    health = sponsor_health()
    company_name = _company_name(state.get("context"))
    persona = ROSTER["reliability"]
    agent_statuses = _set_agent_status(
        state.get("agent_statuses"),
        "reliability",
        status="thinking",
        detail="Scoring council reliability and packaging W&B replay evals",
    )
    events = _append(
        state.get("observability_events"),
        _event("W&B Weave", "Trace span opened", "reliability_auditor", "positive"),
        _event("W&B Weave", "Eval packet assembling", "Council reliability + prompt promotion gate", "positive"),
        _event("Redis", "Historical outcomes active", "Using prior decision outcomes and prompt-version gates", "positive"),
        _sponsor_event(health),
    )
    await _emit_patch(
        state,
        config,
        phase="reliability",
        current_phase="Reliability audit and W&B eval packaging",
        agent_statuses=agent_statuses,
        observability_events=events,
        trace_summary=_trace_summary("reliability_auditor", "running", model_calls=1, tool_calls=2, telemetry=state.get("model_telemetry")),
        redis_activity=_append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("W&B eval packet", "Preparing reliability scorecard for replay comparison", "eval"),
        ),
        sponsor_health=health,
    )

    positions = state.get("positions", [])
    recommendation = state.get("recommendation") or {}

    if _fast_council():
        scorecard, summary = _fast_reliability_scorecard(positions, recommendation)
        scorecard = WE.blend_weave_trace_scores(
            scorecard,
            positions=positions,
            transcript=state.get("transcript") or [],
            recommendation=recommendation,
        )
        average_score = round(sum(score["reliability"] for score in scorecard) / len(scorecard)) if scorecard else 0
        weave_meta = weave_status()
        try:
            replay = RS.replay_summary()
        except Exception:
            replay = {}
        try:
            gate_status = PG.promotion_status_summary()
        except Exception:
            gate_status = {}
        latest_gate = (gate_status or {}).get("latest") or {}
        learning_report = {
            "audit_scope": "Post-decision evaluator scorecard only; Reliability does not re-decide or take a stance.",
            "normal_decision_prohibited": True,
            "summary": summary,
            "eval_dataset": RS.DEFAULT_SLUG,
            "replay_plan": ["Fast-path scorecard — run full replay eval for promotion gates"],
            "prompt_improvement_directives": [
                score.get("prompt_improvement_directive") or score.get("prompt_adjustment") or ""
                for score in scorecard
                if (score.get("prompt_improvement_directive") or score.get("prompt_adjustment"))
            ],
            "promotion_gate": "Full W&B replay required before auto-promotion",
            "weave_project": weave_meta.get("project"),
            "weave_entity": weave_meta.get("entity"),
            "weave_url": weave_meta.get("url"),
            "replay_set": RS.DEFAULT_REPLAY_SET,
            "replay_set_slug": RS.DEFAULT_SLUG,
            "replay_sets": replay.get("sets", []),
            "enforced_gates": (gate_status or {}).get("enforced_gates") or PG.summarize_gates(),
            "gate_status": (gate_status or {}).get("counts", {}),
            "promotion_candidates": (gate_status or {}).get("candidate_count", 0),
            "prompt_versions": state.get("prompt_versions", []),
            "model_telemetry": state.get("model_telemetry", {}),
            "evidence_gaps": state.get("evidence_gaps", []),
        }
        try:
            role_distinction_meta = RD.capture_role_distinction_eval(
                decision=state.get("decision") or "",
                decision_type=str(state.get("decision_type") or "live"),
                positions=positions,
                recommendation=recommendation,
                reliability_scores=scorecard,
                learning_report=learning_report,
                source="live",
                artifact_path=None,
                persist=True,
                publish=True,
            )
            learning_report["role_distinction_eval"] = _role_distinction_summary(role_distinction_meta)
        except Exception as exc:
            learning_report["role_distinction_warning"] = redact_secrets(exc)
            print(f"[reliability] role distinction warning: {learning_report['role_distinction_warning']}")

        eval_event_id = None
        eval_warning = None
        try:
            eval_meta = WE.capture_eval_packet(
                decision=state.get("decision") or "",
                context=state.get("context") or {},
                positions=positions,
                transcript=state.get("transcript") or [],
                recommendation=recommendation,
                reliability_scores=scorecard,
                trace_summary=state.get("trace_summary") or {},
                learning_report=learning_report,
                source="live",
                replay_set=RS.DEFAULT_SLUG,
                prompt_versions=state.get("prompt_versions") or ((state.get("context") or {}).get("financials") or {}).get("prompt_versions") or [],
            )
            eval_event_id = eval_meta.get("event_id")
        except Exception as exc:
            eval_warning = redact_secrets(exc)
            print(f"[reliability] eval capture warning: {eval_warning}")

        agent_statuses = _attach_reliability_to_statuses(agent_statuses, scorecard)
        try:
            updated_history = CI.update_historical_reliability(
                _company_id(state.get("context")),
                scorecard,
                state.get("council_influence"),
            )
        except Exception as exc:
            updated_history = {}
            print(f"[reliability] history update warning: {exc}")
        agent_statuses = _set_agent_status(
            agent_statuses,
            "reliability",
            status="done" if not eval_warning else "warning",
            headline=f"Reliability · {average_score}%",
            detail=summary,
            reliability_score=average_score,
        )
        audit_turn = _turn(
            "reliability",
            type="reliability",
            headline=f"Reliability scorecard · {average_score}%",
            argument=summary,
            key_points=["Evaluator scorecard attached", "No approve/reject stance produced", *learning_report["replay_plan"][:1]],
        )
        return {
            "reliability_scores": scorecard,
            "learning_report": learning_report,
            "transcript": state.get("transcript", []) + [audit_turn],
            "phase": "reliability",
            "current_phase": "Reliability scorecard attached",
            "agent_statuses": agent_statuses,
            "observability_events": _append(
                events,
                _event("OpenAI", "Reliability fast-path", summary, "positive"),
                _event("W&B Weave", "Trace span closed", "reliability_auditor", "positive"),
            ),
            "trace_summary": _trace_summary("reliability_auditor", "complete", model_calls=0, tool_calls=2, telemetry=state.get("model_telemetry")),
            "redis_activity": _append(
                state.get("redis_activity"),
                _redis_activity(
                    "Reliability history",
                    f"Updated rolling priors for {len(updated_history)} analysts" if updated_history else "History update skipped",
                    "json",
                ),
            ),
            "sponsor_health": health,
        }

    model = llm(0.2).with_structured_output(ReliabilityReport)
    debate_turns = [t for t in state.get("transcript", []) if t.get("type") == "rebuttal"]
    system = SystemMessage(
        content=(
            f"You are {persona['label']} for {company_name}: an evaluator, not a participant. Your job is "
            "not to re-decide the case, not to produce a ruling, and not to output APPROVE/REJECT/"
            "CONDITIONAL/DEFER as a Reliability stance. Produce a post-decision scorecard only. Score "
            "the reliability of each decision-making agent: cfo, treasury, fpna, risk, procurement. Use a "
            "live self-improvement rubric: outcome_accuracy 30%, evidence_grounding 20%, forecast_calibration "
            "15%, policy_compliance 15%, debate_value 10%, confidence_calibration 5%, trace_quality 5%. "
            "Cite concrete evidence from the decision, positions, debate, challenge-panel findings, company "
            "context, governance and board policies, prior outcomes, audit findings, source provenance, model "
            "telemetry, and W&B/Weave trace quality. For debate_value, audit whether cross-examination "
            "covered cash_timing, forecast_assumptions, controls_policy, vendor_terms, and CFO synthesis "
            "questions; penalize generic exchanges. For every agent, include known_weaknesses, replay_cases, "
            "a prompt_adjustment, a prompt_improvement_directive for the self-improvement loop, and a "
            "promotion_gate that can be evaluated by W&B Weave replay runs. Set normal_decision_prohibited "
            "to true and make audit_scope explicitly say this is not a case ruling. If current outcome "
            "accuracy cannot yet be observed, calibrate it from historical analogous outcomes and say so. "
            "Never invent external facts."
        )
    )
    human = HumanMessage(
        content=(
            f"DECISION:\n{state.get('decision')}\n\n"
            f"COMPANY CONTEXT:\n{json.dumps(state.get('context'))}\n\n"
            f"POSITIONS:\n{json.dumps([{'agent': p.get('agent'), 'role': p.get('role'), 'stance': p.get('stance'), 'headline': p.get('headline'), 'argument': p.get('argument'), 'key_points': p.get('key_points')} for p in positions])}\n\n"
            f"CROSS-EXAMINATION:\n{json.dumps([{'from': t.get('from_role'), 'to': t.get('to_role'), 'challenge_type': t.get('challenge_type'), 'challenge_label': t.get('challenge_label'), 'challenge_lens': t.get('challenge_lens'), 'point': t.get('point')} for t in debate_turns])}\n\n"
            f"CFO RECOMMENDATION:\n{json.dumps(state.get('recommendation') or {})}\n\n"
            f"TRACE SUMMARY:\n{json.dumps(state.get('trace_summary') or {})}\n\n"
            "EVALUATOR OUTPUT CONTRACT:\n"
            "- audit_scope must state that Reliability is auditing the council after the CFO ruling, not taking a decision stance.\n"
            "- normal_decision_prohibited must be true.\n"
            "- Do not output stance, decision, ruling, approve, reject, conditional approval, or deferral as Reliability's view.\n"
            "- Produce one scorecard row for each of cfo, treasury, fpna, risk, procurement.\n"
            "- For each score include known_weaknesses, replay_cases, prompt_adjustment, prompt_improvement_directive, and promotion_gate.\n"
            "- Audit whether debate_value included role-specific challenge coverage: cash_timing, forecast_assumptions, controls_policy, vendor_terms, and CFO synthesis questions.\n"
            "- Global replay_plan and prompt_improvement_directives must be usable by the self-improvement loop."
        )
    )
    report: ReliabilityReport = await model.ainvoke([system, human], config)
    scorecard = _normalize_reliability_scores(report.scores)
    scorecard = WE.blend_weave_trace_scores(
        scorecard,
        positions=positions,
        transcript=state.get("transcript") or [],
        recommendation=state.get("recommendation") or {},
    )
    average_score = round(sum(score["reliability"] for score in scorecard) / len(scorecard)) if scorecard else 0
    # W&B Weave learning layer: enrich the report with replay-set + promotion-gate
    # context so the streamed scorecard carries the eval/replay/gate story.
    weave_meta = weave_status()
    try:
        replay = RS.replay_summary()
    except Exception:
        replay = {}
    try:
        gate_status = PG.promotion_status_summary()
    except Exception:
        gate_status = {}
    latest_gate = (gate_status or {}).get("latest") or {}
    learning_report = {
        "audit_scope": report.audit_scope,
        "normal_decision_prohibited": report.normal_decision_prohibited,
        "summary": report.summary,
        "eval_dataset": report.eval_dataset,
        "replay_plan": report.replay_plan,
        "prompt_improvement_directives": report.prompt_improvement_directives,
        "promotion_gate": report.promotion_gate,
        "score_formula": {
            "outcome_accuracy": 0.30,
            "evidence_grounding": 0.20,
            "forecast_calibration": 0.15,
            "policy_compliance": 0.15,
            "debate_value": 0.10,
            "confidence_calibration": 0.05,
            "trace_quality": 0.05,
        },
        "weave_project": weave_meta.get("project"),
        "weave_entity": weave_meta.get("entity"),
        "weave_url": weave_meta.get("url"),
        "replay_set": RS.DEFAULT_REPLAY_SET,
        "replay_set_slug": RS.DEFAULT_SLUG,
        "replay_sets": replay.get("sets", []),
        "enforced_gates": (gate_status or {}).get("enforced_gates") or PG.summarize_gates(),
        "gate_status": (gate_status or {}).get("counts", {}),
        "promotion_candidates": (gate_status or {}).get("candidate_count", 0),
        "score_deltas": latest_gate.get("score_deltas"),
        "latest_gate": (
            {
                "candidate": latest_gate.get("candidate_label"),
                "status": latest_gate.get("status"),
                "replay_set": latest_gate.get("replay_set"),
            }
            if latest_gate
            else None
        ),
        # OpenAI-native council provenance: prompt versions + model telemetry feed the gate.
        "prompt_versions": state.get("prompt_versions", []),
        "model_telemetry": state.get("model_telemetry", {}),
        "evidence_gaps": state.get("evidence_gaps", []),
    }
    try:
        role_distinction_meta = RD.capture_role_distinction_eval(
            decision=state.get("decision") or "",
            decision_type=str(state.get("decision_type") or "live"),
            positions=state.get("positions") or [],
            recommendation=state.get("recommendation") or {},
            reliability_scores=scorecard,
            learning_report=learning_report,
            source="live",
            artifact_path=None,
            persist=True,
            publish=True,
        )
        learning_report["role_distinction_eval"] = _role_distinction_summary(role_distinction_meta)
    except Exception as exc:
        learning_report["role_distinction_warning"] = redact_secrets(exc)
        print(f"[reliability] role distinction warning: {learning_report['role_distinction_warning']}")

    # Capture this run as a durable, queryable W&B Weave eval packet. The six rubric
    # scorers run as nested @weave.op child spans under the reliability_auditor span.
    eval_event_id = None
    eval_warning = None
    try:
        eval_meta = WE.capture_eval_packet(
            decision=state.get("decision") or "",
            context=state.get("context") or {},
            positions=state.get("positions") or [],
            transcript=state.get("transcript") or [],
            recommendation=state.get("recommendation") or {},
            reliability_scores=scorecard,
            trace_summary=state.get("trace_summary") or {},
            learning_report=learning_report,
            source="live",
            replay_set=RS.DEFAULT_SLUG,
            prompt_versions=state.get("prompt_versions") or ((state.get("context") or {}).get("financials") or {}).get("prompt_versions") or [],
        )
        eval_event_id = eval_meta.get("event_id")
        packet = eval_meta.get("packet") or {}
        learning_report["eval_packet_id"] = packet.get("id")
        learning_report["eval_overall_score"] = packet.get("overall_score")
        learning_report["rubric_scores"] = [
            {"dimension": s.get("dimension"), "label": s.get("label"), "score": s.get("score"), "passed": s.get("passed")}
            for s in (packet.get("rubric_scores") or [])
        ]
        learning_report["trace_quality_issues"] = packet.get("trace_quality_issues") or []
        learning_report["weave_eval"] = eval_meta.get("weave") or {}
        R.publish("dashboard", {"event": "reliability", "average_score": average_score})
    except Exception as exc:
        eval_warning = redact_secrets(exc)
        print(f"[reliability] eval capture warning: {eval_warning}")

    agent_statuses = _attach_reliability_to_statuses(agent_statuses, scorecard)
    try:
        updated_history = CI.update_historical_reliability(
            _company_id(state.get("context")),
            scorecard,
            state.get("council_influence"),
        )
    except Exception as exc:
        updated_history = {}
        print(f"[reliability] history update warning: {exc}")
    agent_statuses = _set_agent_status(
        agent_statuses,
        "reliability",
        status="done" if not eval_warning else "warning",
        headline=f"Reliability scorecard · {average_score}%",
        detail=report.summary,
        reliability_score=average_score,
    )
    audit_turn = _turn(
        "reliability",
        type="reliability",
        headline=f"Reliability scorecard · {average_score}%",
        argument=report.summary,
        key_points=["Evaluator scorecard only", *report.replay_plan[:2]],
    )
    return {
        "reliability_scores": scorecard,
        "learning_report": learning_report,
        "transcript": state.get("transcript", []) + [audit_turn],
        "phase": "reliability",
        "current_phase": "Reliability scorecard attached",
        "agent_statuses": agent_statuses,
        "observability_events": _append(
            events,
            _event("W&B Weave", "Reliability eval ready", report.promotion_gate, "positive"),
            _event(
                "W&B Weave",
                "Replay + promotion gates",
                f"{RS.DEFAULT_REPLAY_SET} · {len(PG.summarize_gates())} enforced gates",
                "positive",
            ),
            _event(
                "Redis",
                "Reliability eval persisted" if eval_event_id else "Reliability eval persistence warning",
                f"atlas:stream:eval_packets · {eval_event_id}" if eval_event_id else (eval_warning or "Unknown warning"),
                "positive" if eval_event_id else "warning",
            ),
            _event("W&B Weave", "Trace span closed", "reliability_auditor", "positive"),
        ),
        "trace_summary": _trace_summary("reliability_auditor", "complete" if not eval_warning else "warning", model_calls=1, tool_calls=2, telemetry=state.get("model_telemetry")),
        "redis_activity": _append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity(
                "Reliability history",
                f"Updated rolling priors for {len(updated_history)} analysts" if updated_history else "History update skipped",
                "json",
            ),
            _redis_activity(
                "W&B eval packet",
                f"Persisted reliability scorecard {eval_event_id}" if eval_event_id else (eval_warning or "Persistence warning"),
                "eval" if eval_event_id else "warning",
            ),
        ),
        "sponsor_health": health,
    }


@weave.op(name="self_improvement")
async def self_improvement_node(state: DebateState, config: RunnableConfig) -> dict:
    """W&B Weave-driven agent selection: retire and replace the weakest sub-agent.

    The roster is fixed at five agents (CFO + four sub-agents). After the CFO
    rules and the reliability auditor scores the council against the W&B Weave
    rubric, this node records every sub-agent's reliability for the round, retires
    the weakest incarnation, and spawns a brand-new replacement from its Weave trace.
    """
    require_live_ready()
    health = sponsor_health()
    company_id = _company_id(state.get("context"))
    company_name = _company_name(state.get("context"))
    scorecard = [s for s in (state.get("reliability_scores") or []) if (s.get("agent_id") in SI.SUBAGENTS)]

    agent_statuses = _set_agent_status(
        state.get("agent_statuses"),
        "reliability",
        status="thinking",
        detail="Selecting the least-reliable sub-agent to retire and replace from its W&B Weave trace",
    )
    events = _append(
        state.get("observability_events"),
        _event("W&B Weave", "Replacement span opened", "self_improvement", "positive"),
        _event("W&B Weave", "Scoring sub-agents for replacement", "Lowest Weave reliability is retired and replaced", "positive"),
        _sponsor_event(health),
    )
    await _emit_patch(
        state,
        config,
        phase="self_improvement",
        current_phase="Retiring the least-reliable sub-agent and spawning its replacement from W&B Weave",
        agent_statuses=agent_statuses,
        observability_events=events,
        trace_summary=_trace_summary("self_improvement", "running", model_calls=0 if _fast_council() else 1, tool_calls=1, telemetry=state.get("model_telemetry")),
        redis_activity=_append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("Agent replacement", "Reading rolling reliability history and active incarnations", "json"),
        ),
        sponsor_health=health,
    )

    if not scorecard:
        # No sub-agent scores → nothing to replace this round; pass through.
        improvement_state = state.get("agent_improvements") or SI.agent_improvement_state(company_id)
        agent_statuses = _set_agent_status(
            agent_statuses,
            "reliability",
            status="done",
            detail="No sub-agent scores available to replace this round",
        )
        return {
            "agent_improvements": improvement_state,
            "phase": "self_improvement",
            "current_phase": "Agent replacement skipped — no sub-agent scores",
            "agent_statuses": agent_statuses,
            "observability_events": _append(
                events,
                _event("W&B Weave", "Replacement skipped", "No sub-agent reliability scores in this run", "warning"),
                _event("W&B Weave", "Replacement span closed", "self_improvement", "positive"),
            ),
            "trace_summary": _trace_summary("self_improvement", "warning", telemetry=state.get("model_telemetry")),
            "sponsor_health": health,
        }

    improvement = None
    warning = None
    try:
        improvement = await SI.replace_weakest_agent(
            company_id=company_id,
            company=company_name,
            scorecard=scorecard,
            decision=state.get("decision") or "",
            use_llm=not _fast_council(),
            config=config,
        )
    except Exception as exc:
        warning = redact_secrets(exc)
        print(f"[self_improvement] warning: {warning}")

    if improvement is None:
        improvement_state = state.get("agent_improvements") or SI.agent_improvement_state(company_id)
        agent_statuses = _set_agent_status(
            agent_statuses,
            "reliability",
            status="warning",
            detail=warning or "Self-improvement could not complete this round",
        )
        return {
            "agent_improvements": improvement_state,
            "phase": "self_improvement",
            "current_phase": "Self-improvement warning",
            "agent_statuses": agent_statuses,
            "observability_events": _append(
                events,
                _event("W&B Weave", "Self-improvement warning", warning or "Unknown warning", "warning"),
                _event("W&B Weave", "Self-improvement span closed", "self_improvement", "positive"),
            ),
            "trace_summary": _trace_summary("self_improvement", "warning", telemetry=state.get("model_telemetry")),
            "sponsor_health": health,
        }

    # Reload the authoritative, freshly-persisted overlay for downstream streaming.
    improvement_state = SI.agent_improvement_state(company_id)
    replaced_label = improvement.get("replaced_label") or improvement.get("replaced_agent")
    round_no = improvement.get("round")
    focus = improvement.get("focus") or ""
    prior_reliability = improvement.get("prior_reliability")
    council_average = improvement.get("council_average")
    generation = improvement.get("generation")
    weave_published = bool((improvement.get("weave") or {}).get("published"))

    agent_statuses = _set_agent_status(
        agent_statuses,
        "reliability",
        status="done",
        headline=f"Round {round_no} · replaced {replaced_label}",
        detail=(
            f"{replaced_label} retired at {prior_reliability}% → generation {generation} "
            f"({improvement.get('version_label')})"
        ),
    )
    replace_turn = _turn(
        "reliability",
        type="reliability",
        headline=f"Agent replacement · round {round_no}: {replaced_label}",
        argument=(
            f"{replaced_label} was the least reliable sub-agent at {prior_reliability}% on this decision. "
            f"Its incarnation was retired and replaced with generation {generation} — a brand-new agent grounded in "
            f"its W&B Weave trace, targeting {focus or 'its weakest dimension'} "
            f"({improvement.get('version_label')})."
        ),
        key_points=[
            f"Council sub-agent average: {council_average}%",
            improvement.get("replacement_rationale") or improvement.get("mandate_emphasis") or "",
            f"Replacement directive: {improvement.get('directive', '')[:160]}",
        ],
    )
    return {
        "agent_improvements": improvement_state,
        "phase": "self_improvement",
        "current_phase": f"Round {round_no}: replaced {replaced_label} from its W&B Weave trace",
        "agent_statuses": agent_statuses,
        "transcript": state.get("transcript", []) + [replace_turn],
        "observability_events": _append(
            events,
            _event(
                "W&B Weave",
                f"Sub-agent replaced · round {round_no}",
                f"{replaced_label} {prior_reliability}% retired → gen {generation} ({improvement.get('version_label')})",
                "positive",
            ),
            _event(
                "W&B Weave",
                "Replacement snapshot published" if weave_published else "Replacement snapshot persisted",
                "atlas:stream:agent_improvements" if not weave_published else (improvement.get("weave") or {}).get("url") or "W&B Weave",
                "positive",
            ),
            _event("W&B Weave", "Replacement span closed", "self_improvement", "positive"),
        ),
        "trace_summary": _trace_summary("self_improvement", "complete", model_calls=0 if _fast_council() else 1, tool_calls=1, telemetry=state.get("model_telemetry")),
        "redis_activity": _append(
            state.get("redis_activity"),
            _redis_activity(
                "Agent replacement",
                f"Round {round_no}: {replaced_label} retired → gen {generation} ({improvement.get('version_label')})",
                "json",
            ),
        ),
        "sponsor_health": health,
    }


@weave.op(name="persist_decision")
async def persist_node(state: DebateState, config: RunnableConfig) -> dict:
    require_live_ready()
    health = sponsor_health()
    rec = state.get("recommendation", {})
    telemetry = state.get("model_telemetry") or {}
    agent_statuses = _set_agent_status(
        state.get("agent_statuses"),
        "system",
        status="persisting",
        detail="Appending the decision stream event and notifying the dashboard",
    )
    events = _append(
        state.get("observability_events"),
        _event("W&B Weave", "Trace span opened", "persist_decision", "positive"),
        _event("Redis", "Persisting decision", "Streams + Pub/Sub", "info"),
        _sponsor_event(health),
    )
    await _emit_patch(
        state,
        config,
        phase="persist",
        current_phase="Persisting decision",
        agent_statuses=agent_statuses,
        observability_events=events,
        trace_summary=_trace_summary("persist_decision", "running", tool_calls=2, telemetry=telemetry),
        redis_activity=_append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("Redis Stream", "Preparing atlas:stream:decisions append", "stream"),
            _redis_activity("Redis Pub/Sub", "Preparing atlas:dashboard publish", "pubsub"),
        ),
        sponsor_health=health,
    )
    gov = state.get("governance") or {}
    try:
        # Full debate snapshot for the export-memo command (server-authoritative,
        # so the board memo is assembled from Redis, not browser-supplied data).
        R.set_json(
            f"{R.NS}:debate:latest",
            {
                "decision": state.get("decision"),
                "decision_type": state.get("decision_type"),
                "company": _company_name(state.get("context")),
                "positions": state.get("positions", []),
                "transcript": state.get("transcript", []),
                "recommendation": rec,
                "board_memo": state.get("board_memo", {}),
                "operator_actions": state.get("operator_actions", []),
                "challenge_report": state.get("challenge_report", {}),
                "evidence_gaps": state.get("evidence_gaps", []),
                "tool_plan": state.get("tool_plan", []),
                "follow_up": state.get("follow_up", {}),
                "prompt_versions": state.get("prompt_versions", []),
                "model_telemetry": telemetry,
                "reliability_scores": state.get("reliability_scores", []),
                "council_influence": state.get("council_influence", {}),
                "learning_report": state.get("learning_report", {}),
                "agent_improvements": state.get("agent_improvements", {}),
                "governance": gov,
                "pinned_evidence": AGUI.load_command_state(ROOM).get("pinned_evidence", []),
                "generated_at": _now(),
            },
        )
        event_id = R.append_event("decisions", {
            "title": (state.get("decision") or "")[:140],
            "summary": (rec.get("rationale") or "")[:400],
            "decision": rec.get("decision"),
            "decision_type": state.get("decision_type"),
            "confidence": rec.get("confidence"),
            "board_memo": state.get("board_memo", {}),
            "operator_actions": state.get("operator_actions", []),
            "evidence_gaps": state.get("evidence_gaps", []),
            "reliability_scores": state.get("reliability_scores", []),
            "council_influence": state.get("council_influence", {}),
            "learning_report": state.get("learning_report", {}),
            "prompt_versions": state.get("prompt_versions", []),
            "model_telemetry": {
                "model": telemetry.get("model"),
                "model_family": telemetry.get("model_family"),
                "total_tokens": telemetry.get("total_tokens"),
                "estimated_cost_usd": telemetry.get("estimated_cost_usd"),
                "model_calls": telemetry.get("model_calls"),
            },
            # Governance status so the decision log shows how the recommendation is
            # being governed (pending sign-off vs. system-cleared) — never "approved by a human".
            "approval_status": gov.get("status"),
            "approval_id": gov.get("id"),
            "approval_status_label": gov.get("status_label"),
            "controls_flagged": len(gov.get("violations", []) or []),
            "human_approvals_pending": gov.get("human_approvals_pending"),
            "source": "debate",
        })
        R.publish("dashboard", {"event": "decision", "decision": rec.get("decision")})
        agent_statuses = _set_agent_status(
            agent_statuses,
            "system",
            status="done",
            detail=f"Decision persisted to Redis stream event {event_id}",
        )
        return {
            "phase": "done",
            "current_phase": "Decision persisted",
            "agent_statuses": agent_statuses,
            "observability_events": _append(
                events,
                _event("Redis", "Decision appended", f"atlas:stream:decisions · {event_id}", "positive"),
                _event("Redis", "Dashboard notified", "atlas:dashboard Pub/Sub", "positive"),
                _event("W&B Weave", "Trace span closed", "persist_decision", "positive"),
            ),
            "redis_activity": _append(
                state.get("redis_activity"),
                _redis_activity("Redis Stream", f"Decision event {event_id}", "stream"),
                _redis_activity("Redis Pub/Sub", "Published dashboard update", "pubsub"),
            ),
            "trace_summary": _trace_summary("persist_decision", "complete", tool_calls=2, telemetry=telemetry),
            "sponsor_health": health,
        }
    except Exception as exc:  # persistence must not fail the run
        print(f"[persist] warning: {exc}")
        agent_statuses = _set_agent_status(
            agent_statuses,
            "system",
            status="warning",
            detail=f"Decision completed with Redis persistence warning: {exc}",
        )
        return {
            "phase": "done",
            "current_phase": "Decision completed; persistence warning",
            "agent_statuses": agent_statuses,
            "observability_events": _append(
                events,
                _event("Redis", "Persistence warning", str(exc), "warning"),
            ),
            "redis_activity": _append(
                state.get("redis_activity"),
                _redis_ping_activity(health),
                _redis_activity("Redis persistence warning", str(exc), "warning"),
            ),
            "trace_summary": _trace_summary("persist_decision", "warning", tool_calls=2, telemetry=telemetry),
            "sponsor_health": health,
        }


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #
def _command_wrapped(node):
    """Augment a node's return with the live command-state keys.

    Keeps the eight AG-UI command keys present and current in the merged
    LangGraph state at every node boundary (the weave.op span still fires inside
    the wrapped node, so tracing is unchanged). The wrapper keeps an explicit
    ``(state, config)`` signature — we deliberately do not use functools.wraps,
    so LangGraph's signature inspection sees ``config`` here and keeps injecting
    the RunnableConfig rather than following ``__wrapped__`` into the weave op.
    """

    async def wrapper(state: DebateState, config: RunnableConfig) -> dict:
        result = await node(state, config)
        if isinstance(result, dict):
            return _with_command_state(result)
        return result

    return wrapper


workflow = StateGraph(DebateState)
workflow.add_node("intake", _command_wrapped(intake_node))
workflow.add_node("planner", _command_wrapped(planner_node))
workflow.add_node("committee_parallel", _command_wrapped(committee_parallel_node))
workflow.add_node("challenge", _command_wrapped(challenge_node))
workflow.add_node("debate", _command_wrapped(debate_node))
workflow.add_node("influence", _command_wrapped(influence_node))
workflow.add_node("synthesis", _command_wrapped(synthesis_node))
workflow.add_node("governance", _command_wrapped(governance_node))
workflow.add_node("reliability", _command_wrapped(reliability_node))
workflow.add_node("self_improvement", _command_wrapped(self_improvement_node))
workflow.add_node("persist", _command_wrapped(persist_node))

workflow.add_edge(START, "intake")
workflow.add_edge("intake", "planner")
workflow.add_edge("planner", "committee_parallel")
workflow.add_edge("committee_parallel", "challenge")
workflow.add_edge("challenge", "debate")
workflow.add_edge("debate", "influence")
workflow.add_edge("influence", "synthesis")
workflow.add_edge("synthesis", "governance")
workflow.add_edge("governance", "reliability")
workflow.add_edge("reliability", "self_improvement")
workflow.add_edge("self_improvement", "persist")
workflow.add_edge("persist", END)

checkpointer = MemorySaver()
graph = workflow.compile(checkpointer=checkpointer)


# --------------------------------------------------------------------------- #
# Optional orchestration engine (ATLAS_ORCHESTRATOR, default OFF).
# When enabled, swap in the deep orchestration graph (Conductor → dynamic roster →
# multi-round debate → red-team → reliability-weighted vote → CFO synthesis), built
# in the isolated src/orchestration package over this same DebateState so the AG-UI
# bridge and frontend are unchanged. Additive and fail-safe: any import/build error
# falls back to the linear graph above, so the live demo is byte-for-byte unchanged
# whenever the flag is off or the engine is unavailable.
# See agent/src/orchestration/ and .cursor/rules/atlas-orchestration.mdc.
# --------------------------------------------------------------------------- #
if os.getenv("ATLAS_ORCHESTRATOR", "").strip().lower() in ("1", "true", "yes", "on"):
    try:
        from src.orchestration.graph import build_orchestrator_graph as _build_orchestrator_graph

        graph = _build_orchestrator_graph(base_graph=graph)
        print("[atlas] ATLAS_ORCHESTRATOR enabled — orchestration engine graph active.")
    except Exception as _orchestration_exc:  # fall back to the linear graph, never break the demo
        print(f"[atlas] orchestration engine unavailable; using linear graph: {_orchestration_exc}")
