"""
OpenAI-native operating committee for Atlas.

This module turns the role-based council into an OpenAI-native committee. It owns
*how* the model is used — well beyond basic chat:

  • Provider-swappable, env-configured ``ChatOpenAI`` (reasoning model + effort).
  • A single ``structured_call`` wrapper that runs every council turn through
    ``with_structured_output(..., include_raw=True)`` so we capture real token
    usage, cost estimates (when pricing is configured), model refusals, and
    parse/transport errors — and retry once before honestly surfacing failure.
  • A planning phase (``classify_and_plan``) that classifies the decision into an
    operating-committee type and decides which Redis-backed tools, policy RAG
    queries, prior decisions, and forecast slices each analyst needs.
  • Live evidence gathering (``gather_role_evidence``) that executes those plans
    against Redis before an analyst speaks (richer, multi-step tool calling).
  • A challenge panel (``challenge_panel``) — a second model pass that verifies
    whether each role cited enough concrete numbers.
  • CFO synthesis (``cfo_recommendation``) + a board memo & operator checklist
    (``board_memo``) grounded in *computed* runway, not LLM guesses.
  • Versioned prompts (``prompt_versions_payload``) hashed for W&B promotion gates.

Strict live-only: there are no canned outputs and no fabricated fallbacks. When a
call cannot produce a grounded result, the failure is recorded in telemetry and
returned to the caller — never replaced with invented content.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI

from src.env import load_env, redact_secrets
from src.structured_models import (
    AgentImprovement,
    BoardMemo,
    ChallengePanelReport,
    CouncilInfluenceReport,
    DecisionPlan,
    DecisionType,
    Position,
    Rebuttals,
    Recommendation,
    ReliabilityReport,
    RoleEvidencePlan,
)
from src.tools import (
    get_company_financials,
    list_vendors,
    search_finance_policies,
)

load_env()

# --------------------------------------------------------------------------- #
# Env-configured model selection (live OpenAI / LangChain only)
# --------------------------------------------------------------------------- #
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.5")
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "xhigh")
LLM_ANALYST_REASONING_EFFORT = os.getenv("LLM_ANALYST_REASONING_EFFORT", "low")
LLM_DEBATE_REASONING_EFFORT = os.getenv("LLM_DEBATE_REASONING_EFFORT", "low")
LLM_PLANNER_REASONING_EFFORT = os.getenv("LLM_PLANNER_REASONING_EFFORT", "low")
LLM_SYNTHESIS_REASONING_EFFORT = os.getenv("LLM_SYNTHESIS_REASONING_EFFORT", "low")
LLM_TEXT_VERBOSITY = os.getenv("LLM_TEXT_VERBOSITY", "low")

# Cost is only reported when real per-token pricing is configured in the
# environment — we never fabricate a dollar figure. Values are USD per 1M tokens.
_PRICE_IN_ENV = "OPENAI_PRICE_INPUT_PER_MTOK"
_PRICE_OUT_ENV = "OPENAI_PRICE_OUTPUT_PER_MTOK"


def llm(temperature: float = 0.3, *, reasoning_effort: str | None = None) -> Any:
    """Construct the env-configured chat model (OpenAI reasoning model by default)."""
    effort = reasoning_effort or LLM_REASONING_EFFORT
    if LLM_PROVIDER.lower() == "openai":
        return ChatOpenAI(
            model=LLM_MODEL,
            temperature=temperature,
            reasoning_effort=effort,
            verbosity=LLM_TEXT_VERBOSITY,
            output_version="responses/v1",
        )
    return init_chat_model(LLM_MODEL, model_provider=LLM_PROVIDER, temperature=temperature)


def model_family(model: str | None = None) -> str:
    """Human-readable model family for observability (e.g. 'GPT-5.5')."""
    name = (model or LLM_MODEL or "").lower()
    if name.startswith("gpt-5.5"):
        return "GPT-5.5"
    if name.startswith("gpt-5"):
        return "GPT-5"
    if name.startswith("gpt-4.1"):
        return "GPT-4.1"
    if name.startswith("gpt-4o"):
        return "GPT-4o"
    if name.startswith("gpt-4"):
        return "GPT-4"
    if name.startswith(("o1", "o3", "o4")):
        return "OpenAI o-series"
    if "claude" in name:
        return "Claude"
    return model or LLM_MODEL or "unknown"


def _pricing() -> tuple[float | None, float | None]:
    def _read(key: str) -> float | None:
        raw = os.getenv(key, "").strip()
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    return _read(_PRICE_IN_ENV), _read(_PRICE_OUT_ENV)


def pricing_configured() -> bool:
    in_price, out_price = _pricing()
    return in_price is not None and out_price is not None


def _estimate_cost(input_tokens: int | None, output_tokens: int | None) -> float | None:
    in_price, out_price = _pricing()
    if in_price is None or out_price is None:
        return None
    cost = ((input_tokens or 0) / 1_000_000) * in_price + ((output_tokens or 0) / 1_000_000) * out_price
    return round(cost, 6)


# --------------------------------------------------------------------------- #
# Structured-call telemetry (token usage, cost, refusals, errors)
# --------------------------------------------------------------------------- #
@dataclass
class CallTelemetry:
    node: str
    role: str
    model: str
    model_family: str
    provider: str
    reasoning_effort: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    refusal: str | None = None
    error: str | None = None
    attempts: int = 0
    ok: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "role": self.role,
            "model": self.model,
            "model_family": self.model_family,
            "provider": self.provider,
            "reasoning_effort": self.reasoning_effort,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "refusal": self.refusal,
            "error": self.error,
            "attempts": self.attempts,
            "ok": self.ok,
        }


@dataclass
class StructuredResult:
    parsed: Any | None
    telemetry: CallTelemetry
    raw: Any | None = None

    @property
    def ok(self) -> bool:
        return self.parsed is not None


def _usage(raw: Any) -> tuple[int | None, int | None, int | None]:
    usage = getattr(raw, "usage_metadata", None)
    if not usage:
        return None, None, None
    return (
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("total_tokens"),
    )


def _refusal(raw: Any, parsing_error: Any) -> str | None:
    kwargs = getattr(raw, "additional_kwargs", None) or {}
    if kwargs.get("refusal"):
        return str(kwargs["refusal"])
    if parsing_error is not None:
        name = type(parsing_error).__name__.lower()
        text = str(parsing_error)
        if "refus" in name or "refus" in text.lower():
            return text
    return None


async def structured_call(
    *,
    node: str,
    role: str,
    schema: type,
    system: str,
    human: str,
    config: RunnableConfig | None = None,
    temperature: float = 0.3,
    reasoning_effort: str | None = None,
    retries: int = 1,
) -> StructuredResult:
    """Run one structured model call, capturing usage/cost/refusal/errors.

    Retries once on a transport error, a parse failure, or a refusal (with a
    stricter instruction). Never fabricates a result: on exhaustion it returns a
    StructuredResult with ``parsed=None`` and the real error/refusal in telemetry.
    """
    messages: list[Any] = [SystemMessage(content=system), HumanMessage(content=human)]
    effort = reasoning_effort or LLM_REASONING_EFFORT
    model = llm(temperature, reasoning_effort=effort).with_structured_output(schema, include_raw=True)

    in_tok = out_tok = tot_tok = None
    last_error: str | None = None
    last_refusal: str | None = None
    raw: Any = None
    attempts = 0

    for attempt in range(retries + 1):
        attempts += 1
        try:
            res = await model.ainvoke(messages, config) if config is not None else await model.ainvoke(messages)
        except Exception as exc:  # transport / API error — record and retry
            last_error = redact_secrets(exc)
            continue

        raw = res.get("raw") if isinstance(res, dict) else None
        usage = _usage(raw)
        if any(value is not None for value in usage):
            in_tok, out_tok, tot_tok = usage

        refusal = _refusal(raw, res.get("parsing_error") if isinstance(res, dict) else None)
        if refusal:
            last_refusal = refusal
            messages = [
                SystemMessage(content=system),
                HumanMessage(
                    content=(
                        human
                        + "\n\nReturn the requested structured analysis grounded ONLY in the "
                        "supplied company data. Do not refuse. If a specific figure is unknown, "
                        "mark it explicitly as unknown rather than declining."
                    )
                ),
            ]
            continue

        parsed = res.get("parsed") if isinstance(res, dict) else None
        if parsed is None:
            last_error = redact_secrets(res.get("parsing_error") if isinstance(res, dict) else "no parsed object")
            continue

        return StructuredResult(
            parsed=parsed,
            raw=raw,
            telemetry=CallTelemetry(
                node=node,
                role=role,
                model=LLM_MODEL,
                model_family=model_family(),
                provider=LLM_PROVIDER,
                reasoning_effort=effort,
                input_tokens=in_tok,
                output_tokens=out_tok,
                total_tokens=tot_tok,
                cost_usd=_estimate_cost(in_tok, out_tok),
                refusal=None,
                error=None,
                attempts=attempts,
                ok=True,
            ),
        )

    return StructuredResult(
        parsed=None,
        raw=raw,
        telemetry=CallTelemetry(
            node=node,
            role=role,
            model=LLM_MODEL,
            model_family=model_family(),
            provider=LLM_PROVIDER,
            reasoning_effort=effort,
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=tot_tok,
            cost_usd=_estimate_cost(in_tok, out_tok),
            refusal=last_refusal,
            error=last_error,
            attempts=attempts,
            ok=False,
        ),
    )


# --------------------------------------------------------------------------- #
# Telemetry accumulation across a debate run (streamed in DebateState)
# --------------------------------------------------------------------------- #
def init_telemetry() -> dict[str, Any]:
    return {
        "provider": LLM_PROVIDER,
        "model": LLM_MODEL,
        "model_family": model_family(),
        "reasoning_effort": LLM_REASONING_EFFORT,
        "text_verbosity": LLM_TEXT_VERBOSITY,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0 if pricing_configured() else None,
        "cost_available": pricing_configured(),
        "pricing_source": "env:OPENAI_PRICE_*" if pricing_configured() else "not configured",
        "model_calls": 0,
        "successful_calls": 0,
        "calls": [],
        "refusals": [],
        "errors": [],
    }


def merge_telemetry(telemetry: dict[str, Any] | None, call: CallTelemetry) -> dict[str, Any]:
    """Fold one structured call's telemetry into the running run-level totals."""
    tel = dict(telemetry or init_telemetry())
    tel["input_tokens"] = (tel.get("input_tokens") or 0) + (call.input_tokens or 0)
    tel["output_tokens"] = (tel.get("output_tokens") or 0) + (call.output_tokens or 0)
    tel["total_tokens"] = (tel.get("total_tokens") or 0) + (call.total_tokens or 0)
    if tel.get("cost_available") and call.cost_usd is not None:
        tel["estimated_cost_usd"] = round((tel.get("estimated_cost_usd") or 0.0) + call.cost_usd, 6)
    tel["model_calls"] = (tel.get("model_calls") or 0) + 1
    if call.ok:
        tel["successful_calls"] = (tel.get("successful_calls") or 0) + 1
    tel["calls"] = [*(tel.get("calls") or []), call.as_dict()][-32:]
    if call.refusal:
        tel["refusals"] = [*(tel.get("refusals") or []), {"node": call.node, "role": call.role, "detail": call.refusal}][-12:]
    if call.error:
        tel["errors"] = [*(tel.get("errors") or []), {"node": call.node, "role": call.role, "detail": call.error}][-12:]
    return tel


# --------------------------------------------------------------------------- #
# Versioned prompts + decision playbooks
# --------------------------------------------------------------------------- #
# Static system-prompt templates. Hashing the template (not the per-decision
# fill) lets W&B replay evals detect prompt drift and gate promotion.
_ANALYST_TEMPLATE = (
    "You are {label} at {company} ({stage}), a member of its investment committee. "
    "Your mandate is {mandate}. Evaluate the decision strictly from your function's perspective. "
    "This is a {decision_type} decision; keep these front-of-mind: {focus}. "
    "Ground every claim in specific figures from the company context and the planned evidence "
    "(forecast, cohort, pipeline, audit, vendor, security, and outcome history when relevant). "
    "Populate cited_metrics with the concrete numbers you used. Take a clear stance "
    "(support / oppose / conditional) and defend it crisply. Speak like a senior finance executive "
    "in a boardroom — precise, quantified, no fluff. Never mention being an AI or a model."
)
_CLASSIFIER_TEMPLATE = (
    "You are the chief of staff to the CFO of {company} ({stage}). Before the committee debates, you "
    "classify the decision and build an evidence plan. Choose the single best decision_type from: "
    "vendor_renewal, hiring_plan, capital_allocation, security_blocker, pricing_change, "
    "financing_scenario, general. List the required_facts a sound decision needs and mark each as "
    "available only if it is present in the supplied company-context keys. For any missing required "
    "fact, write a precise follow_up question and a conservative assumption to proceed with. For each "
    "analyst role (treasury, fpna, risk, procurement), specify which Redis-backed tools to use, up to "
    "two semantic policy/precedent queries, the most relevant company-context slices, and prior "
    "decision ids to weigh. Use the decision-type playbook as a hint but tailor it to the specifics. "
    "Never invent data that is not in the context."
)
_CHALLENGE_TEMPLATE = (
    "You are the evidence challenge panel for {company}'s investment committee. You do not re-decide "
    "the case. For each analyst position (treasury, fpna, risk, procurement), judge whether it cited "
    "ENOUGH concrete numbers to be trusted: set cited_enough_numbers, score grounding 0-100, name the "
    "strongest number used, and list specific figures or facts that were missing. Give one sharp "
    "follow-up challenge per role. Then summarize council grounding and list the unresolved evidence "
    "gaps the CFO must resolve or explicitly accept. Be exacting; reward concrete figures, penalize "
    "vague or unquantified claims."
)
_DEBATE_TEMPLATE = (
    "You are moderating an investment-committee debate at {company}. Given each function's position and "
    "the evidence challenge panel's findings, produce 3-4 sharp cross-examination exchanges where "
    "members challenge each other's reasoning with specific numbers and trade-offs. Press hardest on the "
    "evidence gaps the challenge panel flagged. Keep it professional, substantive, and concrete — like a "
    "real boardroom, not small talk."
)
_INFLUENCE_TEMPLATE = (
    "You are the council moderator for {company}'s investment committee. After cross-examination, assign "
    "each analyst (treasury, fpna, risk, procurement) a deliberation influence weight for the CFO's ruling. "
    "Weights must sum to exactly 100 across the four analysts — they are NOT equal by default. Reward roles "
    "that cited concrete numbers, survived challenge well, and advanced the decision with quantified ideas. "
    "Penalize vague or under-evidenced roles. Factor in the supplied historical reliability priors and "
    "decision-type relevance. Explain who earned the most influence and why."
)
_CFO_TEMPLATE = (
    "You are the Chief Financial Officer of {company}, chairing the investment committee for a "
    "{decision_type} decision. You have heard each function's position, the cross-examination, the "
    "evidence challenge panel, and the council's assigned influence weights. Weigh positions by those "
    "influence shares — higher-weight analysts should move your ruling more; low-weight roles should "
    "not dominate even if loud. Resolve disagreements, and issue a final, board-ready decision. Be "
    "decisive and quantified. Honor the stated assumptions where facts were missing and let unresolved "
    "evidence gaps lower your confidence and shape your conditions. Estimate the decision's incremental "
    "monthly cost, one-time cost, and added monthly revenue (numbers only, 0 if none) so runway impact "
    "can be computed by tool. Use the richer operating data: forecast downside, churn cohorts, "
    "pipeline-stage risk, vendor obligations, security incidents, audit findings, board constraints, and "
    "prior outcomes."
)
_MEMO_TEMPLATE = (
    "You are the Chief Financial Officer of {company} writing the board memo and operator action "
    "checklist for a {decision_type} decision that has already been ruled on. Use ONLY the supplied "
    "recommendation, the tool-computed runway impact, the committee positions, and the company context. "
    "The runway numbers are computed by tool — quote them exactly, never invent them. Produce a tight, "
    "board-ready memo (context, recommendation, key_figures, risks, conditions), an operator action "
    "checklist with owners/priorities/timeframes, financing or next-step implications, and an honest "
    "note of the strongest dissent."
)
_RELIABILITY_TEMPLATE = (
    "You are the Reliability Auditor for {company}. You do not re-decide the case; you score the "
    "reliability of each decision-making agent: cfo, treasury, fpna, risk, procurement. Use a live "
    "self-improvement rubric: outcome_accuracy 30%, evidence_grounding 20%, forecast_calibration 15%, "
    "policy_compliance 15%, debate_value 10%, confidence_calibration 5%, trace_quality 5%. Cite concrete "
    "evidence from the decision, positions, debate, challenge-panel findings, company context, prior "
    "outcomes, audit findings, board constraints, and W&B/Weave trace quality. For every agent include a "
    "specific prompt_adjustment and promotion_gate evaluable by W&B Weave replay runs. If current outcome "
    "accuracy cannot yet be observed, calibrate from historical analogous outcomes and say so. Never "
    "invent external facts."
)

_PROMPT_TEMPLATES: dict[str, str] = {
    "classifier": _CLASSIFIER_TEMPLATE,
    "treasury": _ANALYST_TEMPLATE,
    "fpna": _ANALYST_TEMPLATE,
    "risk": _ANALYST_TEMPLATE,
    "procurement": _ANALYST_TEMPLATE,
    "challenge": _CHALLENGE_TEMPLATE,
    "debate": _DEBATE_TEMPLATE,
    "influence": _INFLUENCE_TEMPLATE,
    "cfo": _CFO_TEMPLATE,
    "board_memo": _MEMO_TEMPLATE,
    "reliability": _RELIABILITY_TEMPLATE,
}

_PROMPT_VERSION_IDS: dict[str, str] = {
    "classifier": "classifier.v1-evidence-plan",
    "treasury": "treasury.v4-evidence-plan",
    "fpna": "fpna.v4-cohort-calibration",
    "risk": "risk.v5-control-evidence",
    "procurement": "procurement.v3-renewal-redlines",
    "challenge": "challenge.v1-grounding-gate",
    "debate": "debate.v2-gap-pressure",
    "influence": "influence.v1-weighted-council",
    "cfo": "cfo.v4-influence-weighted",
    "board_memo": "memo.v1-operator-checklist",
    "reliability": "reliability.v2-replay-gate",
}


def _prompt_hash(template: str) -> str:
    return hashlib.sha256(template.encode("utf-8")).hexdigest()[:12]


def prompt_versions_payload(company_context: dict | None = None) -> list[dict[str, Any]]:
    """Versioned-prompt provenance, merged with the company's seeded promotion gates."""
    seeded = {}
    for item in ((company_context or {}).get("financials") or {}).get("prompt_versions") or []:
        seeded[item.get("agent")] = item
    payload: list[dict[str, Any]] = []
    for role, template in _PROMPT_TEMPLATES.items():
        seed = seeded.get(role, {})
        payload.append(
            {
                "role": role,
                "version": _PROMPT_VERSION_IDS.get(role, f"{role}.v1"),
                "prompt_hash": _prompt_hash(template),
                "candidate": seed.get("candidate", ""),
                "promotion_gate": seed.get(
                    "promotion_gate",
                    "Beat incumbent on grounding, policy compliance, and calibration on the W&B replay set.",
                ),
            }
        )
    return payload


# Decision-type playbooks steer the planner and tailor analyst prompts.
DECISION_PLAYBOOKS: dict[DecisionType, dict[str, Any]] = {
    DecisionType.vendor_renewal: {
        "focus": "contract cost vs usage, switching cost, termination-notice timing, renewal leverage",
        "policies": ["vendor renewal review", "spend approval thresholds"],
        "slices": ["vendors", "decision_outcomes", "audit_findings"],
    },
    DecisionType.hiring_plan: {
        "focus": "burn impact, runway guardrail, revenue/compliance mapping of each role, ramp time",
        "policies": ["headcount and burn discipline", "runway guardrail"],
        "slices": ["hiring_plan", "cash_forecast", "pipeline_by_stage"],
    },
    DecisionType.capital_allocation: {
        "focus": "ROI and payback, opportunity cost, runway sensitivity, cash buffer policy",
        "policies": ["cash management", "runway guardrail"],
        "slices": ["cash_forecast", "decision_outcomes", "opex_monthly"],
    },
    DecisionType.security_blocker: {
        "focus": "revenue unblocked vs cost, control gap severity, audit due dates, board priority",
        "policies": ["security-blocked revenue priority", "AI council promotion gate"],
        "slices": ["security_incidents", "audit_findings", "pipeline_by_stage"],
    },
    DecisionType.pricing_change: {
        "focus": "net revenue retention, cohort elasticity, churn risk, gross-margin and CAC payback",
        "policies": ["runway guardrail"],
        "slices": ["customer_cohorts", "pipeline_by_stage", "cash_forecast"],
    },
    DecisionType.financing_scenario: {
        "focus": "dilution vs runway extension, terms, board constraints, downside-cash trigger",
        "policies": ["runway guardrail", "cash management"],
        "slices": ["cash_forecast", "last_raise", "board_constraints"],
    },
    DecisionType.general: {
        "focus": "runway impact, policy compliance, ROI, and downside risk",
        "policies": ["spend approval thresholds", "runway guardrail"],
        "slices": ["cash_forecast", "decision_outcomes"],
    },
}


def playbook_for(decision_type: DecisionType | str) -> dict[str, Any]:
    if isinstance(decision_type, str):
        try:
            decision_type = DecisionType(decision_type)
        except ValueError:
            decision_type = DecisionType.general
    return DECISION_PLAYBOOKS.get(decision_type, DECISION_PLAYBOOKS[DecisionType.general])


# --------------------------------------------------------------------------- #
# Local Redis-backed tool execution (no AG-UI tool events)
# --------------------------------------------------------------------------- #
def _tool_text(tool_obj: Any, *args: Any, **kwargs: Any) -> str:
    func = getattr(tool_obj, "func", None)
    if func is not None:
        return func(*args, **kwargs)
    payload = kwargs if kwargs else (args[0] if args else {})
    return tool_obj.invoke(payload)


def context_slice_names() -> list[str]:
    """The company-context slices the planner is allowed to focus on."""
    return [
        "cash_history",
        "cash_forecast",
        "pipeline_by_stage",
        "customer_cohorts",
        "hiring_plan",
        "security_incidents",
        "audit_findings",
        "board_constraints",
        "decision_outcomes",
        "opex_monthly",
        "last_raise",
        "vendors",
        "policies",
    ]


# --------------------------------------------------------------------------- #
# Planning phase — classify the decision and route evidence
# --------------------------------------------------------------------------- #
async def classify_and_plan(
    *,
    decision: str,
    context: dict,
    company: str,
    stage: str,
    config: RunnableConfig | None = None,
) -> StructuredResult:
    available = [name for name in context_slice_names() if _slice_available(name, context)]
    playbooks = {
        dt.value: {"focus": pb["focus"], "policies": pb["policies"], "slices": pb["slices"]}
        for dt, pb in DECISION_PLAYBOOKS.items()
    }
    system = _PROMPT_TEMPLATES["classifier"].format(company=company, stage=stage)
    human = (
        f"DECISION UNDER REVIEW:\n{decision}\n\n"
        f"AVAILABLE COMPANY-CONTEXT KEYS (a fact is 'available' only if its data lives here):\n"
        f"{json.dumps(available)}\n\n"
        f"DECISION-TYPE PLAYBOOKS (hints, tailor to specifics):\n{json.dumps(playbooks)}\n\n"
        f"COMPANY CONTEXT:\n{json.dumps(context, default=str)[:12000]}\n\n"
        "Classify the decision and produce the evidence plan."
    )
    return await structured_call(
        node="planner",
        role="classifier",
        schema=DecisionPlan,
        system=system,
        human=human,
        config=config,
        temperature=0.2,
        reasoning_effort=LLM_PLANNER_REASONING_EFFORT,
    )


def _slice_available(name: str, context: dict) -> bool:
    if name == "vendors":
        return bool(context.get("vendors"))
    if name == "policies":
        return bool(context.get("policies"))
    return (context.get("financials") or {}).get(name) not in (None, [], {}, "")


def tool_plan_entries(plan: DecisionPlan) -> list[dict[str, Any]]:
    """The planned (pre-execution) tool plan, streamed so the UI shows intent."""
    entries: list[dict[str, Any]] = []
    for role_plan in plan.role_plans:
        for query in role_plan.policy_queries[:2]:
            entries.append({"role": role_plan.role, "tool": "search_finance_policies", "target": query, "rationale": role_plan.rationale, "kind": "vector"})
        for slice_name in role_plan.focus_slices:
            entries.append({"role": role_plan.role, "tool": "context_slice", "target": slice_name, "rationale": role_plan.rationale, "kind": "json"})
        for prior in role_plan.prior_decisions:
            entries.append({"role": role_plan.role, "tool": "decision_outcomes", "target": prior, "rationale": "weigh prior outcome", "kind": "stream"})
        for tool_name in role_plan.tools:
            if tool_name not in {"search_finance_policies"}:
                entries.append({"role": role_plan.role, "tool": tool_name, "target": "planned", "rationale": role_plan.rationale, "kind": "tool"})
    # The CFO always computes runway from a tool, never guesses it.
    entries.append({"role": "cfo", "tool": "compute_runway", "target": "scenario vs current runway", "rationale": "quantify runway impact from real cash record", "kind": "tool"})
    return entries[:40]


# --------------------------------------------------------------------------- #
# Live evidence gathering for one analyst role
# --------------------------------------------------------------------------- #
@dataclass
class EvidenceBundle:
    evidence: dict[str, Any] = field(default_factory=dict)
    redis_activity: list[dict[str, Any]] = field(default_factory=list)
    policy_hits: int = 0
    queries: list[str] = field(default_factory=list)


def gather_role_evidence(role_plan: RoleEvidencePlan | None, context: dict) -> EvidenceBundle:
    """Execute a role's evidence plan against Redis before the analyst speaks.

    Runs the planned semantic policy/precedent RAG queries live, extracts the
    requested company-context slices, and pulls the named prior-decision
    outcomes. All grounded in the Redis system of record — no fabrication.
    """
    bundle = EvidenceBundle()
    if role_plan is None:
        return bundle

    financials = context.get("financials") or {}

    # 1) Live semantic policy / precedent RAG (capped at 2 queries per role).
    policy_hits: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for query in (role_plan.policy_queries or [])[:2]:
        try:
            raw = _tool_text(search_finance_policies, query=query)
            for hit in json.loads(raw):
                title = hit.get("title")
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    policy_hits.append(hit)
            bundle.queries.append(query)
            bundle.redis_activity.append(
                {"label": "Vector RAG", "detail": f"{role_plan.role}: '{query[:48]}'", "kind": "vector"}
            )
        except Exception as exc:  # surface, do not fabricate
            bundle.redis_activity.append(
                {"label": "Vector RAG warning", "detail": redact_secrets(exc), "kind": "warning"}
            )
    if policy_hits:
        bundle.evidence["policies"] = policy_hits
        bundle.policy_hits = len(policy_hits)

    # 2) Focused company-context slices.
    slices: dict[str, Any] = {}
    for slice_name in role_plan.focus_slices or []:
        if slice_name == "vendors" and context.get("vendors"):
            slices["vendors"] = context["vendors"]
        elif slice_name in financials and financials.get(slice_name) not in (None, [], {}, ""):
            slices[slice_name] = financials[slice_name]
    if slices:
        bundle.evidence["focus_slices"] = slices
        bundle.redis_activity.append(
            {"label": "RedisJSON slices", "detail": f"{role_plan.role}: {', '.join(slices.keys())}", "kind": "json"}
        )

    # 3) Prior decision outcomes the plan flagged as relevant.
    outcomes = financials.get("decision_outcomes") or []
    wanted = [str(item).lower() for item in (role_plan.prior_decisions or [])]
    if wanted and outcomes:
        relevant = [
            outcome
            for outcome in outcomes
            if any(token in json.dumps(outcome, default=str).lower() for token in wanted)
        ]
        if relevant:
            bundle.evidence["prior_decisions"] = relevant
            bundle.redis_activity.append(
                {"label": "Decision outcomes", "detail": f"{role_plan.role}: {len(relevant)} prior outcome(s)", "kind": "stream"}
            )

    return bundle


# --------------------------------------------------------------------------- #
# Council turns
# --------------------------------------------------------------------------- #
async def analyst_position(
    *,
    role_key: str,
    persona: dict,
    decision: str,
    context: dict,
    company: str,
    stage: str,
    decision_type: str,
    decision_focus: list[str],
    evidence: dict,
    operator_directives: str = "",
    improvement_directive: str = "",
    config: RunnableConfig | None = None,
) -> StructuredResult:
    focus = "; ".join(decision_focus) if decision_focus else playbook_for(decision_type)["focus"]
    system = _PROMPT_TEMPLATES.get(role_key, _ANALYST_TEMPLATE).format(
        label=persona["label"],
        company=company,
        stage=stage,
        mandate=persona["mandate"],
        decision_type=decision_type,
        focus=focus,
    )
    # Self-improvement overlay: a standing directive earned from this seat's prior
    # W&B Weave reliability traces, grafted on so the agent gets more reliable
    # each round (see src/self_improvement.py). Never fabricated — empty until the
    # reliability auditor has scored at least one prior round.
    if improvement_directive.strip():
        system += (
            "\n\nSELF-IMPROVEMENT DIRECTIVE (learned from your own W&B Weave reliability traces in "
            "prior council rounds — follow it to raise your reliability this round):\n"
            f"{improvement_directive.strip()}"
        )
    human = (
        f"DECISION UNDER REVIEW:\n{decision}\n\n"
        f"COMPANY CONTEXT ({company}):\n{json.dumps(context, default=str)[:12000]}\n\n"
        f"PLANNED EVIDENCE (gathered live from Redis for your seat — lean on this):\n"
        f"{json.dumps(evidence, default=str)[:6000]}\n\n"
        "Give a concise position: headline ≤10 words, argument ≤2 short sentences, exactly 2 key_points, "
        "and cited_metrics with the concrete numbers you relied on. Be direct — no preamble."
        f"{operator_directives}"
    )
    return await structured_call(
        node=f"analyst_{role_key}",
        role=role_key,
        schema=Position,
        system=system,
        human=human,
        config=config,
        temperature=0.4,
        reasoning_effort=LLM_ANALYST_REASONING_EFFORT,
    )


async def challenge_panel(
    *,
    decision: str,
    positions: list[dict],
    company: str,
    config: RunnableConfig | None = None,
) -> StructuredResult:
    system = _PROMPT_TEMPLATES["challenge"].format(company=company)
    slim = [
        {
            "role": position.get("agent") or position.get("role"),
            "stance": position.get("stance"),
            "headline": position.get("headline"),
            "argument": position.get("argument"),
            "cited_metrics": position.get("cited_metrics", []),
            "key_points": position.get("key_points", []),
        }
        for position in positions
    ]
    human = f"DECISION:\n{decision}\n\nANALYST POSITIONS:\n{json.dumps(slim, default=str)}"
    return await structured_call(
        node="challenge",
        role="challenge",
        schema=ChallengePanelReport,
        system=system,
        human=human,
        config=config,
        temperature=0.2,
        reasoning_effort=LLM_DEBATE_REASONING_EFFORT,
    )


async def cross_examination(
    *,
    decision: str,
    positions: list[dict],
    challenge_report: dict | None,
    company: str,
    operator_directives: str = "",
    config: RunnableConfig | None = None,
) -> StructuredResult:
    system = _PROMPT_TEMPLATES["debate"].format(company=company)
    slim = [
        {"role": position.get("role"), "stance": position.get("stance"), "headline": position.get("headline"), "key_points": position.get("key_points")}
        for position in positions
    ]
    gaps = (challenge_report or {}).get("unresolved_gaps") or []
    findings = [
        {"role": finding.get("role"), "cited_enough_numbers": finding.get("cited_enough_numbers"), "challenge": finding.get("challenge")}
        for finding in ((challenge_report or {}).get("findings") or [])
    ]
    human = (
        f"DECISION:\n{decision}\n\nPOSITIONS:\n{json.dumps(slim, default=str)}\n\n"
        f"CHALLENGE-PANEL FINDINGS:\n{json.dumps(findings, default=str)}\n\n"
        f"UNRESOLVED EVIDENCE GAPS:\n{json.dumps(gaps, default=str)}"
        f"{operator_directives}"
    )
    return await structured_call(
        node="debate",
        role="debate",
        schema=Rebuttals,
        system=system,
        human=human,
        config=config,
        temperature=0.55,
        reasoning_effort=LLM_DEBATE_REASONING_EFFORT,
    )


async def council_influence(
    *,
    decision: str,
    positions: list[dict],
    debate_turns: list[dict],
    challenge_report: dict | None,
    historical_reliability: dict[str, int],
    decision_type: str,
    company: str,
    operator_directives: str = "",
    config: RunnableConfig | None = None,
) -> StructuredResult:
    system = _PROMPT_TEMPLATES["influence"].format(company=company)
    findings = [
        {
            "role": finding.get("role"),
            "grounding_score": finding.get("grounding_score"),
            "cited_enough_numbers": finding.get("cited_enough_numbers"),
            "challenge": finding.get("challenge"),
        }
        for finding in ((challenge_report or {}).get("findings") or [])
    ]
    human = (
        f"DECISION:\n{decision}\n\n"
        f"DECISION TYPE:\n{decision_type}\n\n"
        f"HISTORICAL RELIABILITY PRIORS (rolling council track record):\n"
        f"{json.dumps(historical_reliability, default=str)}\n\n"
        f"POSITIONS:\n"
        f"{json.dumps([{'agent': p.get('agent'), 'role': p.get('role'), 'stance': p.get('stance'), 'headline': p.get('headline'), 'cited_metrics': p.get('cited_metrics', []), 'key_points': p.get('key_points')} for p in positions], default=str)}\n\n"
        f"CHALLENGE-PANEL FINDINGS:\n{json.dumps(findings, default=str)}\n\n"
        f"CROSS-EXAMINATION:\n"
        f"{json.dumps([{'from': t.get('from_role'), 'to': t.get('to_role'), 'point': t.get('point')} for t in debate_turns], default=str)}\n\n"
        "Assign influence_weight for treasury, fpna, risk, and procurement so the four weights sum to 100."
        f"{operator_directives}"
    )
    return await structured_call(
        node="influence",
        role="influence",
        schema=CouncilInfluenceReport,
        system=system,
        human=human,
        config=config,
        temperature=0.25,
        reasoning_effort=LLM_DEBATE_REASONING_EFFORT,
    )


async def cfo_recommendation(
    *,
    decision: str,
    context: dict,
    positions: list[dict],
    debate_turns: list[dict],
    challenge_report: dict | None,
    decision_plan: dict | None,
    council_influence: dict | None,
    company: str,
    decision_type: str,
    operator_directives: str = "",
    config: RunnableConfig | None = None,
) -> StructuredResult:
    system = _PROMPT_TEMPLATES["cfo"].format(company=company, decision_type=decision_type)
    assumptions = (decision_plan or {}).get("assumptions") or []
    gaps = (challenge_report or {}).get("unresolved_gaps") or []
    influence_weights = (council_influence or {}).get("weights") or []
    human = (
        f"DECISION:\n{decision}\n\n"
        f"COMPANY CONTEXT:\n{json.dumps(context, default=str)[:12000]}\n\n"
        f"COUNCIL INFLUENCE WEIGHTS (must shape your ruling — not equal voice):\n"
        f"{json.dumps(council_influence or {}, default=str)}\n\n"
        f"POSITIONS:\n"
        f"{json.dumps([{'role': p.get('role'), 'agent': p.get('agent'), 'stance': p.get('stance'), 'headline': p.get('headline'), 'argument': p.get('argument'), 'cited_metrics': p.get('cited_metrics', []), 'influence_weight': next((w.get('influence_weight') for w in influence_weights if w.get('agent_id') == p.get('agent')), None)} for p in positions], default=str)}\n\n"
        f"CROSS-EXAMINATION:\n{json.dumps([{'from': t.get('from_role'), 'to': t.get('to_role'), 'point': t.get('point')} for t in debate_turns], default=str)}\n\n"
        f"STATED ASSUMPTIONS (facts that were missing):\n{json.dumps(assumptions, default=str)}\n\n"
        f"UNRESOLVED EVIDENCE GAPS (resolve or explicitly accept; they should lower confidence):\n{json.dumps(gaps, default=str)}"
        f"{operator_directives}"
    )
    return await structured_call(
        node="synthesis",
        role="cfo",
        schema=Recommendation,
        system=system,
        human=human,
        config=config,
        temperature=0.3,
        reasoning_effort=LLM_SYNTHESIS_REASONING_EFFORT,
    )


async def board_memo(
    *,
    decision: str,
    company: str,
    decision_type: str,
    recommendation: dict,
    impact: dict,
    positions: list[dict],
    challenge_report: dict | None,
    config: RunnableConfig | None = None,
) -> StructuredResult:
    system = _PROMPT_TEMPLATES["board_memo"].format(company=company, decision_type=decision_type)
    human = (
        f"DECISION:\n{decision}\n\n"
        f"CFO RECOMMENDATION (already ruled):\n{json.dumps(recommendation, default=str)}\n\n"
        f"TOOL-COMPUTED RUNWAY IMPACT (quote exactly, do not invent):\n{json.dumps(impact, default=str)}\n\n"
        f"COMMITTEE POSITIONS:\n{json.dumps([{'role': p.get('role'), 'stance': p.get('stance'), 'headline': p.get('headline')} for p in positions], default=str)}\n\n"
        f"CHALLENGE-PANEL SUMMARY:\n{json.dumps((challenge_report or {}).get('summary', ''), default=str)}\n\n"
        "Write the board memo and operator action checklist."
    )
    return await structured_call(
        node="synthesis",
        role="board_memo",
        schema=BoardMemo,
        system=system,
        human=human,
        config=config,
        temperature=0.3,
    )


async def reliability_report(
    *,
    decision: str,
    context: dict,
    positions: list[dict],
    debate_turns: list[dict],
    recommendation: dict,
    challenge_report: dict | None,
    trace_summary: dict,
    company: str,
    config: RunnableConfig | None = None,
) -> StructuredResult:
    system = _PROMPT_TEMPLATES["reliability"].format(company=company)
    human = (
        f"DECISION:\n{decision}\n\n"
        f"COMPANY CONTEXT:\n{json.dumps(context, default=str)[:12000]}\n\n"
        f"POSITIONS:\n{json.dumps([{'agent': p.get('agent'), 'role': p.get('role'), 'stance': p.get('stance'), 'headline': p.get('headline'), 'argument': p.get('argument'), 'cited_metrics': p.get('cited_metrics', []), 'key_points': p.get('key_points')} for p in positions], default=str)}\n\n"
        f"CROSS-EXAMINATION:\n{json.dumps([{'from': t.get('from_role'), 'to': t.get('to_role'), 'point': t.get('point')} for t in debate_turns], default=str)}\n\n"
        f"CHALLENGE PANEL:\n{json.dumps(challenge_report or {}, default=str)}\n\n"
        f"CFO RECOMMENDATION:\n{json.dumps(recommendation or {}, default=str)}\n\n"
        f"TRACE SUMMARY:\n{json.dumps(trace_summary or {}, default=str)}"
    )
    return await structured_call(
        node="reliability",
        role="reliability",
        schema=ReliabilityReport,
        system=system,
        human=human,
        config=config,
        temperature=0.2,
    )


async def improve_agent(
    *,
    company: str,
    agent_id: str,
    persona_label: str,
    reliability_score: dict,
    prior_directive: str,
    decision: str,
    round_no: int,
    config: RunnableConfig | None = None,
) -> StructuredResult:
    """Rewrite the least-reliable sub-agent's standing directive from its Weave trace.

    Live OpenAI call grounded ONLY in the agent's W&B Weave reliability score
    (its lowest dimensions, known weaknesses, and the auditor's prompt
    adjustment). The result is grafted onto the agent's system prompt next round.
    """
    system = (
        f"You are the self-improvement engine for {company}'s AI finance council. After the CFO rules, "
        "the Reliability Auditor scores every agent against a W&B Weave rubric (evidence_grounding, "
        "forecast_calibration, policy_compliance, debate_value, outcome_accuracy, confidence_calibration, "
        "trace_quality). Your job is to make the single weakest sub-agent measurably more reliable on the "
        "NEXT decision by rewriting a short standing directive grafted onto its system prompt. Target its "
        "lowest-scoring dimensions and its known weaknesses. Be concrete and operational; preserve the "
        "still-useful parts of its current directive; never invent facts or external data."
    )
    human = (
        f"WEAKEST SUB-AGENT: {agent_id} ({persona_label})\n"
        f"ROUND: {round_no}\n\n"
        f"MOST RECENT DECISION:\n{decision}\n\n"
        f"ITS W&B WEAVE RELIABILITY TRACE (this round's score — improve from here):\n"
        f"{json.dumps(reliability_score, default=str)}\n\n"
        f"ITS CURRENT STANDING SELF-IMPROVEMENT DIRECTIVE (empty if this is its first improvement):\n"
        f"{prior_directive or '(none yet)'}\n\n"
        "Produce an improved standing directive that supersedes the prior one but keeps any still-relevant "
        "guidance, and name the single reliability dimension it most targets."
    )
    return await structured_call(
        node="self_improvement",
        role=agent_id,
        schema=AgentImprovement,
        system=system,
        human=human,
        config=config,
        temperature=0.3,
        reasoning_effort=LLM_DEBATE_REASONING_EFFORT,
    )
