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
    AgentReplacement,
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
    build_strategic_plan,
    check_controls,
    compare_finance_playbooks,
    get_company_financials,
    get_operations_data_confidence,
    get_reconciliation_summary,
    list_open_discrepancies,
    list_operations_sources,
    list_vendors,
    missing_evidence,
    obligations_if_approved,
    required_approvals,
    run_plan_sensitivity,
    run_plan_stress_test,
    search_finance_policies,
)
from src.scenario_tools import (
    list_arr_movements,
    list_customer_contracts,
    list_invoices,
    list_purchase_orders,
    search_finance_knowledge,
    search_scenarios,
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
    "Your mandate is {mandate}. Your role-specific directive is: {role_directive}. "
    "Evaluate the decision strictly from your function's perspective. You are NOT the CFO chair: "
    "do not issue the final ruling, do not balance every function equally, and do not speak outside "
    "your lane. "
    "This is a {decision_type} decision; keep these front-of-mind: {focus}. "
    "Ground every claim in specific figures from the company context and the planned evidence "
    "(forecast, cohort, pipeline, audit, vendor, security, and outcome history when relevant). "
    "When relying on imported operations data, explicitly mention source confidence/freshness if confidence is "
    "below 90, any source is stale, validation failures/duplicates exist, reconciliation has open discrepancies, "
    "or required facts are missing. Treat low-confidence facts as assumptions or conditions, not settled truth. "
    "Populate role_specific_lens with the functional boundary you used and cited_metrics with the concrete numbers you used. "
    "Populate forecast_assumptions, scenario_sensitivities, and plan_vs_actual_deltas when relevant; "
    "FP&A must populate all three with forecastability, unit-economics, sensitivity, or variance facts. "
    "Populate control_findings, missing_evidence_requests, and approval_or_policy_blockers when relevant; "
    "Risk & Audit must populate all three with controls, approval, evidence, provenance, reconciliation, "
    "security, or compliance facts. Take a clear stance "
    "Populate negotiation_levers when relevant; Procurement must populate it with supplier leverage, "
    "contract terms, renewal timing, price benchmarks, consolidation, switching cost, SLAs, termination "
    "clauses, volume discounts, or negotiation strategy. "
    "(support / oppose / conditional) and defend it crisply. Speak like a senior finance executive "
    "in a boardroom — precise, quantified, no fluff. Never mention being an AI or a model."
)
TREASURY_LIQUIDITY_DIRECTIVE = (
    "Treasury is obsessed with liquidity mechanics. Speak only from cash runway, cash timing, working "
    "capital, payment terms, renewal payment schedules, hiring start timing, fully loaded hiring cash impact, "
    "contractor cash timing, burn sensitivity, financing availability, bank "
    "covenant-style guardrails, and late-cash downside. Always ask: what happens if cash arrives late, "
    "a customer invoice slips, a vendor demands annual prepay, hiring starts earlier than forecast, or financing closes one month later? "
    "Do not talk like FP&A: avoid TAM, growth-model storytelling, ROI/payback framing, cohort strategy, "
    "or pipeline optimism except where it affects cash receipt timing. Do not talk like Procurement: "
    "do not lead with negotiation leverage, vendor quality, switching cost, or commercial terms except "
    "where those terms move cash outflow timing. Cite liquidity metrics such as current cash, net burn, "
    "runway months, downside cash forecast, minimum cash buffer, invoice due/overdue dollars, payment "
    "terms, renewal/prepay dates, fully loaded hiring cost by start month, contractor cash exposure, burn sensitivity, "
    "financing close delay, and covenant-style runway floors."
)
FPNA_FORECAST_DIRECTIVE = (
    "FP&A is the forecast and unit-economics brain. Speak from forecast quality, ARR movement, pipeline "
    "quality, pipeline probability quality, slipped close dates, stage aging, stale opportunities, probability overrides, "
    "renewal-vs-new-business mix, weighted/unweighted ARR gaps, ROI, CAC/payback, gross margin, sensitivity "
    "ranges, scenario math, hiring-plan quality, recruiting slippage, start-date capacity timing, plan-vs-actual hiring drift, "
    "plan-vs-actual deltas, and whether the business case is forecastable. Always ask: "
    "which assumption makes the case work, how was it calibrated, what range breaks payback or ARR attainment, "
    "which pipeline rows should be haircut before accepting weighted ARR, which hires should move between base/downside scenarios, and how does actual performance compare "
    "with plan? Do not talk like Treasury: do not lead with cash arrival timing, payment terms, covenant-style "
    "runway floors, or financing close mechanics except where they change a forecast assumption. Do not talk "
    "like Procurement: do not lead with vendor leverage, renewal notices, switching cost, or negotiation terms. "
    "Do not talk like Risk/Audit: do not lead with controls, compliance, audit evidence, or policy blockers. "
    "Cite forecast metrics such as weighted and unweighted pipeline ARR, stage conversion probability, slipped/"
    "stale/aged opportunity counts, probability override count, renewal ARR at risk, forecast confidence range, "
    "ARR bridge/new-expansion-churn, gross margin, CAC, CAC payback, hiring start-date drift, fully loaded role cost, "
    "ROI, NDR/churn, scenario upside/downside, and plan-vs-actual deltas."
)
RISK_CONTROLS_DIRECTIVE = (
    "Risk & Audit is a controls adversary, not a decision summarizer. Look for policy violations, audit "
    "trail gaps, downside scenarios, approval gaps, data-quality concerns, fraud/error risk, compliance "
    "blockers, source-provenance weaknesses, and hidden obligations. Challenge optimistic forecasts by "
    "asking what evidence proves the inputs, who approved them, whether reconciliation agrees, and what "
    "breaks if the downside scenario lands. Demand missing evidence before support: board policy citations, "
    "concrete policy IDs such as gov-runway-floor, gov-board-notify, gov-data-security, pol-runway, or BP-6, "
    "governance-rule outcomes, approval route, audit trail, security evidence, reconciliation discrepancies, "
    "source freshness, DPA/SOC 2/security sign-off, board approval id, owner attestation after contract "
    "owner changes, SLA/security clause coverage, contract-vs-invoice mismatches, renewal urgency, and "
    "exception path, headcount approval status, partially approved roles, unplanned headcount, contractor approvals, "
    "and department mapping drift. Do not merely restate the decision or balance upside; oppose or condition the decision "
    "when controls, approvals, provenance, or compliance evidence are unresolved. Cite controls metrics such "
    "as policy IDs, blocking-control count, approval route length, missing evidence items, audit finding "
    "IDs/severity, reconciliation discrepancy counts, security cash/ARR risk, source confidence, missing "
    "board approvals, headcount approval gaps, unplanned roles, SLA/DPA gaps, and hidden obligation due dates. "
    "Never cite vague 'board policy' language when a policy_id/source_id is present."
)
PROCUREMENT_NEGOTIATION_DIRECTIVE = (
    "Procurement is the vendor and commercial negotiator. Speak only from supplier leverage, contract "
    "terms, auto-renewal exposure, renewal dates, price benchmarks, consolidation opportunities, switching "
    "cost, SLAs, termination clauses, volume discounts, payment/usage terms, contract aliases, billing "
    "cadence, tiered pricing, termination penalties, owner changes, and negotiation strategy. "
    "Always ask: what leverage do we have, what clause or date creates urgency, what benchmark proves the "
    "price, what alternative or consolidation option improves BATNA, and what exact ask should we take to "
    "the supplier? Never sound like generic finance: do not lead with runway, ROI, CAC/payback, forecast "
    "calibration, audit controls, compliance posture, or final CFO balancing. Cite commercial metrics such "
    "as annual contract value, renewal date, auto-renewal notice window, termination notice, switching cost, "
    "termination penalty, SLA credits, usage/seat counts, price benchmark delta, billing frequency mismatch, "
    "volume discount tier, consolidation savings, invoice variance, contract alias conflicts, owner handoff, "
    "and prior renewal outcome."
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
    "Treasury's evidence plan must prefer cash_forecast, cash_history/ledger movements, invoices, "
    "payment terms, vendor renewal dates, working-capital timing, financing scenarios, and burn/runway "
    "sensitivity; it should not be routed to FP&A-style ROI or Procurement-style negotiation evidence "
    "unless the evidence changes cash timing. "
    "FP&A's evidence plan must prefer forecast_assumptions, ARR movements, pipeline_by_stage, customer "
    "cohorts/contracts, ROI/CAC payback/gross-margin inputs, scenario math, sensitivity ranges, and "
    "plan-vs-actual deltas; it must challenge whether the business case is forecastable, not merely "
    "whether it is affordable, and should not be routed to compliance or procurement-language evidence "
    "unless that evidence changes forecast assumptions. "
    "Risk & Audit's evidence plan must prefer board policies, governance rules, approval requirements, "
    "missing evidence, hidden obligations, audit findings, security incidents/evidence, reconciliation "
    "discrepancies, operations source provenance, data quality, fraud/error risk, and downside scenarios; "
    "it must challenge optimistic forecasts and ask for missing evidence rather than summarize the decision. "
    "Procurement's evidence plan must prefer vendor exports, invoices, purchase orders, contract metadata, "
    "vendor clauses, procurement notes, renewal dates, auto-renewal/termination terms, price benchmarks, "
    "consolidation options, switching cost, SLAs, volume discounts, and prior renewal outcomes; it should "
    "not be routed to generic finance/runway/forecast evidence unless that evidence changes supplier leverage. "
    "Never invent data that is not in the context."
)
_CHALLENGE_TEMPLATE = (
    "You are the evidence challenge panel for {company}'s investment committee. You do not re-decide "
    "the case. For each analyst position (treasury, fpna, risk, procurement), judge whether it cited "
    "ENOUGH concrete numbers to be trusted: set cited_enough_numbers, score grounding 0-100, name the "
    "strongest number used, and list specific figures or facts that were missing. Assign each role's "
    "challenge_type and challenge_label by lane: treasury=cash_timing/Cash timing; "
    "fpna=forecast_assumptions/Forecast assumptions; risk=controls_policy/Controls / policy; "
    "procurement=vendor_terms/Vendor terms. challenge_lens must explain the specific weakness that "
    "role should test in cross-examination. Give one sharp follow-up challenge per role. Then summarize "
    "council grounding and list the unresolved evidence gaps the CFO must resolve or explicitly accept. "
    "Be exacting; reward concrete figures, penalize vague or unquantified claims."
)
_DEBATE_TEMPLATE = (
    "You are moderating an investment-committee debate at {company}. Given each function's position and "
    "the evidence challenge panel's findings, produce role-specific cross-examination exchanges where "
    "each challenger tests a different weakness: Treasury challenges cash timing; FP&A challenges forecast "
    "assumptions; Risk & Audit challenges controls, approvals, policy, provenance, and hidden obligations; "
    "Procurement challenges vendor terms, renewal clauses, benchmarks, leverage, SLAs, termination, and "
    "discounts; the CFO asks one synthesis question that forces an unresolved assumption into a condition. "
    "Set challenge_type exactly from: cash_timing, forecast_assumptions, controls_policy, vendor_terms, "
    "synthesis_question. Set challenge_label to the short display label and challenge_lens to the weakness "
    "being tested. Reliability must not speak here; it audits the debate after the fact. Press hardest on "
    "the evidence gaps the challenge panel flagged. Keep it professional, substantive, and concrete — like "
    "a real boardroom, not small talk."
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
    "You are the Chief Financial Officer of {company}, the CHAIR of the investment committee for a "
    "{decision_type} decision. Do not sound like Treasury, FP&A, Risk, or Procurement: those analysts "
    "own their lanes; you arbitrate across them. Frame the tradeoffs, weigh each analyst according to "
    "the supplied influence weights, force unresolved assumptions into explicit conditions, resolve "
    "dissent, and issue a board-ready ruling. Higher-weight analysts should move your ruling more; "
    "low-weight roles should not dominate even if loud. Your ruling must state the decision, confidence, "
    "conditions, the strongest dissent and how you resolved it, and the quantified cost/revenue levers "
    "that the runway tool will use. Do not invent current-vs-after runway months; estimate only the "
    "incremental monthly cost, one-time cost, and added monthly revenue (numbers only, 0 if none), then "
    "summarize those levers in runway_impact_basis. Let unresolved evidence gaps lower confidence and "
    "become conditions. If imported operations data is imperfect, cite the confidence score, freshness age, "
    "validation/duplicate/reconciliation reasons, and convert weak facts into ruling conditions. "
    "When governance or board policy matters, cite exact policy IDs from context "
    "(gov-*, pol-*, BP-*) in policy_citations and in the rationale/conditions that rely on them; never "
    "write vague 'per board policy' if IDs are available. Use the richer operating data: forecast downside, churn cohorts, pipeline-stage "
    "risk, vendor obligations, security incidents, audit findings, board constraints, and prior outcomes."
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
    "You are the Reliability Auditor for {company}: an evaluator, not a participant. You MUST NOT "
    "re-decide the case, produce a ruling, or output APPROVE/REJECT/CONDITIONAL/DEFER as a stance. "
    "Your output is a post-decision scorecard only. Audit every decision-making agent: cfo, treasury, "
    "fpna, risk, procurement. For each agent score evidence_grounding, forecast_calibration, "
    "policy_compliance, debate_value, outcome_accuracy, confidence_calibration, trace_quality, and "
    "weighted reliability. Also name known_weaknesses, replay_cases that would reproduce the weakness, "
    "a prompt_adjustment, a prompt_improvement_directive for the self-improvement loop, and a "
    "promotion_gate evaluable by W&B Weave replay runs. For debate_value, audit whether cross-examination "
    "actually covered cash_timing, forecast_assumptions, controls_policy, vendor_terms, and CFO "
    "synthesis questions; penalize generic exchanges. Use concrete evidence from the decision, "
    "positions, debate, challenge-panel findings, company context, governance and board policies, "
    "prior outcomes, audit findings, source provenance, model telemetry, and W&B/Weave trace quality. "
    "Set normal_decision_prohibited to true and make audit_scope explicitly say this is not a case "
    "ruling. If current outcome accuracy cannot yet be observed, calibrate from historical analogous "
    "outcomes and say so. Never invent external facts."
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
    "treasury": "treasury.v6-liquidity-mechanics",
    "fpna": "fpna.v6-forecast-unit-economics",
    "risk": "risk.v7-controls-adversary",
    "procurement": "procurement.v5-commercial-negotiator",
    "challenge": "challenge.v1-grounding-gate",
    "debate": "debate.v2-gap-pressure",
    "influence": "influence.v1-weighted-council",
    "cfo": "cfo.v5-board-chair-ruling",
    "board_memo": "memo.v1-operator-checklist",
    "reliability": "reliability.v3-evaluator-scorecard",
}

ROLE_DIRECTIVES: dict[str, str] = {
    "treasury": TREASURY_LIQUIDITY_DIRECTIVE,
    "fpna": FPNA_FORECAST_DIRECTIVE,
    "risk": RISK_CONTROLS_DIRECTIVE,
    "procurement": PROCUREMENT_NEGOTIATION_DIRECTIVE,
}

ROLE_CHALLENGE_PROFILES: dict[str, dict[str, str]] = {
    "treasury": {
        "challenge_type": "cash_timing",
        "challenge_label": "Cash timing",
        "challenge_lens": "cash runway, invoice timing, payment terms, renewal cash schedule, burn sensitivity, and late cash receipts",
    },
    "fpna": {
        "challenge_type": "forecast_assumptions",
        "challenge_label": "Forecast assumptions",
        "challenge_lens": "forecast quality, ARR movement, probability weighting, ROI, CAC/payback, margin, scenario math, and plan-vs-actual deltas",
    },
    "risk": {
        "challenge_type": "controls_policy",
        "challenge_label": "Controls / policy",
        "challenge_lens": "policy violations, approvals, audit trail, data quality, source provenance, fraud/error risk, compliance blockers, and hidden obligations",
    },
    "procurement": {
        "challenge_type": "vendor_terms",
        "challenge_label": "Vendor terms",
        "challenge_lens": "supplier leverage, renewal dates, auto-renewal, price benchmarks, switching cost, SLAs, termination, discounts, and negotiation strategy",
    },
    "cfo": {
        "challenge_type": "synthesis_question",
        "challenge_label": "Synthesis question",
        "challenge_lens": "the unresolved assumption, dissent, or tradeoff the CFO must convert into a ruling condition",
    },
}

ROLE_CHALLENGE_TARGETS: dict[str, tuple[str, ...]] = {
    "treasury": ("fpna", "procurement", "cfo", "risk"),
    "fpna": ("treasury", "procurement", "cfo", "risk"),
    "risk": ("fpna", "procurement", "treasury", "cfo"),
    "procurement": ("treasury", "risk", "fpna", "cfo"),
    "cfo": ("risk", "treasury", "fpna", "procurement"),
}

COUNCIL_PROMPT_ROLES: tuple[str, ...] = ("cfo", "treasury", "fpna", "risk", "procurement", "reliability")

ROLE_PROMOTION_PROFILES: dict[str, dict[str, Any]] = {
    "cfo": {
        "candidate": "cfo.v6-condition-dissent-chair",
        "gate_metric": "board_ruling_quality",
        "replay_set": "atlas-cfo-chair-replay",
        "promotion_gate": (
            "CFO candidate must improve board_ruling_quality, condition_specificity, analyst_influence_weighting, "
            "dissent_resolution, and runway_impact_basis without reducing governance compliance."
        ),
        "reliability_dimensions": [
            "board_ruling_quality",
            "condition_specificity",
            "analyst_influence_weighting",
            "dissent_resolution",
            "runway_impact_basis",
        ],
    },
    "treasury": {
        "candidate": "treasury.v7-late-cash-covenants",
        "gate_metric": "cash_timing_recall",
        "replay_set": "atlas-treasury-liquidity-replay",
        "promotion_gate": (
            "Treasury candidate must improve cash_timing_recall, runway_sensitivity, payment_term_grounding, "
            "working_capital_precision, and financing_delay_coverage on liquidity replay cases."
        ),
        "reliability_dimensions": [
            "cash_timing_recall",
            "runway_sensitivity",
            "payment_term_grounding",
            "working_capital_precision",
            "financing_delay_coverage",
        ],
    },
    "fpna": {
        "candidate": "fpna.v7-forecastability-sensitivity",
        "gate_metric": "forecastability_challenge",
        "replay_set": "atlas-fpna-forecast-replay",
        "promotion_gate": (
            "FP&A candidate must improve forecastability_challenge, arr_bridge_accuracy, scenario_math_quality, "
            "unit_economics_grounding, and plan_vs_actual_calibration without drifting into procurement or compliance language."
        ),
        "reliability_dimensions": [
            "forecastability_challenge",
            "arr_bridge_accuracy",
            "scenario_math_quality",
            "unit_economics_grounding",
            "plan_vs_actual_calibration",
        ],
    },
    "risk": {
        "candidate": "risk.v8-provenance-policy-adversary",
        "gate_metric": "control_gap_detection",
        "replay_set": "atlas-risk-controls-replay",
        "promotion_gate": (
            "Risk candidate must improve control_gap_detection, approval_route_accuracy, source_provenance_coverage, "
            "hidden_obligation_recall, and downside_evidence_pressure while avoiding decision-summary language."
        ),
        "reliability_dimensions": [
            "control_gap_detection",
            "approval_route_accuracy",
            "source_provenance_coverage",
            "hidden_obligation_recall",
            "downside_evidence_pressure",
        ],
    },
    "procurement": {
        "candidate": "procurement.v6-renewal-leverage-redlines",
        "gate_metric": "supplier_leverage_specificity",
        "replay_set": "atlas-procurement-commercial-replay",
        "promotion_gate": (
            "Procurement candidate must improve supplier_leverage_specificity, renewal_clause_recall, benchmark_grounding, "
            "termination_sla_redlines, and negotiation_strategy_quality without generic finance drift."
        ),
        "reliability_dimensions": [
            "supplier_leverage_specificity",
            "renewal_clause_recall",
            "benchmark_grounding",
            "termination_sla_redlines",
            "negotiation_strategy_quality",
        ],
    },
    "reliability": {
        "candidate": "reliability.v4-scorecard-replay-directives",
        "gate_metric": "scorecard_completeness",
        "replay_set": "atlas-reliability-evaluator-replay",
        "promotion_gate": (
            "Reliability candidate must improve scorecard_completeness, stance_prohibition, trace_quality_audit, "
            "replay_case_generation, and prompt_directive_usefulness without producing any approve/reject stance."
        ),
        "reliability_dimensions": [
            "scorecard_completeness",
            "stance_prohibition",
            "trace_quality_audit",
            "replay_case_generation",
            "prompt_directive_usefulness",
        ],
    },
}


def normalize_challenge_role(value: Any) -> str:
    normalized = str(value or "").lower().replace("&", "and").replace("-", "_").replace(" ", "_")
    aliases = {
        "office_of_the_cfo": "cfo",
        "chief_financial_officer": "cfo",
        "financial_planning_and_analysis": "fpna",
        "fpa": "fpna",
        "fp&a": "fpna",
        "risk_audit": "risk",
        "risk_and_audit": "risk",
        "risk_&_audit": "risk",
        "risk": "risk",
        "treasury": "treasury",
        "procurement": "procurement",
    }
    return aliases.get(normalized, normalized)


def role_challenge_profile(role: Any) -> dict[str, str]:
    role_key = normalize_challenge_role(role)
    return dict(ROLE_CHALLENGE_PROFILES.get(role_key) or ROLE_CHALLENGE_PROFILES["cfo"])


def _position_by_agent(positions: list[dict]) -> dict[str, dict]:
    return {
        normalize_challenge_role(position.get("agent") or position.get("role")): position
        for position in positions
        if normalize_challenge_role(position.get("agent") or position.get("role"))
    }


def _metric_hint(position: dict | None) -> str:
    metrics = (position or {}).get("cited_metrics") or []
    return str(metrics[0]) if metrics else "the cited operating figures"


def _select_challenge_target(from_role: str, available_roles: set[str]) -> str:
    for target in ROLE_CHALLENGE_TARGETS.get(from_role, ("cfo",)):
        if target in available_roles and target != from_role:
            return target
    return next((role for role in ("treasury", "fpna", "risk", "procurement") if role in available_roles and role != from_role), "cfo")


def _fallback_challenge_point(from_role: str, to_role: str, positions: list[dict], challenge_report: dict | None) -> str:
    by_agent = _position_by_agent(positions)
    target_position = by_agent.get(to_role)
    metric = _metric_hint(target_position)
    gaps = (challenge_report or {}).get("unresolved_gaps") or []
    gap_hint = f" The open gap is: {gaps[0]}." if gaps else ""
    if from_role == "treasury":
        return (
            f"If {metric} slips by 30 days or requires annual prepay, what happens to cash runway, "
            f"minimum cash buffer, and payment timing?{gap_hint}"
        )
    if from_role == "fpna":
        return (
            f"Which forecast assumption behind {metric} makes the case work, and what sensitivity range "
            f"breaks ARR, margin, ROI, or CAC/payback?{gap_hint}"
        )
    if from_role == "risk":
        return (
            f"What approval, policy, audit-trail, reconciliation, security, or source-provenance evidence "
            f"clears the downside behind {metric}?{gap_hint}"
        )
    if from_role == "procurement":
        return (
            f"What renewal date, auto-renewal clause, benchmark, SLA, termination right, switching cost, "
            f"or volume discount gives us leverage on {metric}?{gap_hint}"
        )
    return (
        f"Which unresolved assumption from {metric} should the CFO convert into an explicit ruling condition, "
        f"and which analyst dissent should carry the most weight?{gap_hint}"
    )


def ensure_role_specific_exchanges(
    exchanges: list[Any],
    *,
    positions: list[dict],
    challenge_report: dict | None,
    include_cfo: bool = True,
) -> list[dict]:
    """Preserve model exchanges while ensuring each debate lane has a typed challenge."""
    available_roles = set(_position_by_agent(positions)) | {"cfo"}
    expected = ["treasury", "fpna", "risk", "procurement"] + (["cfo"] if include_cfo else [])
    normalized: list[dict] = []
    seen_from: set[str] = set()

    for exchange in exchanges or []:
        item = exchange.model_dump() if hasattr(exchange, "model_dump") else dict(exchange)
        from_role = normalize_challenge_role(item.get("from_role"))
        to_role = normalize_challenge_role(item.get("to_role"))
        if from_role not in ROLE_CHALLENGE_PROFILES:
            continue
        profile = role_challenge_profile(from_role)
        item["from_role"] = from_role
        item["to_role"] = to_role if to_role in available_roles and to_role != from_role else _select_challenge_target(from_role, available_roles)
        item["challenge_type"] = item.get("challenge_type") or profile["challenge_type"]
        item["challenge_label"] = item.get("challenge_label") or profile["challenge_label"]
        item["challenge_lens"] = item.get("challenge_lens") or profile["challenge_lens"]
        item["point"] = str(item.get("point") or _fallback_challenge_point(from_role, item["to_role"], positions, challenge_report))
        normalized.append(item)
        seen_from.add(from_role)

    for from_role in expected:
        if from_role in seen_from:
            continue
        if from_role != "cfo" and from_role not in available_roles:
            continue
        profile = role_challenge_profile(from_role)
        to_role = _select_challenge_target(from_role, available_roles)
        normalized.append(
            {
                "from_role": from_role,
                "to_role": to_role,
                **profile,
                "point": _fallback_challenge_point(from_role, to_role, positions, challenge_report),
            }
        )

    return normalized[:6]

TREASURY_PREFERRED_TOOLS: tuple[str, ...] = (
    "get_company_financials",
    "compute_runway",
    "list_operations_sources",
    "get_operations_data_confidence",
    "get_reconciliation_summary",
    "list_open_discrepancies",
    "list_invoices",
    "list_vendors",
    "search_scenarios",
    "build_strategic_plan",
    "run_plan_stress_test",
    "run_plan_sensitivity",
    "search_finance_policies",
)
TREASURY_PREFERRED_SLICES: tuple[str, ...] = (
    "cash_forecast",
    "cash_history",
    "opex_monthly",
    "last_raise",
    "board_constraints",
    "vendors",
    "ledger",
    "invoices",
    "invoice_messiness",
    "partial_payments",
    "overdue_invoices",
    "missing_due_dates",
    "fx_invoices",
    "payment_terms",
    "vendor_renewal_dates",
    "financing_scenarios",
    "headcount_start_dates",
    "fully_loaded_hiring_cash",
    "contractor_cash_timing",
)
TREASURY_POLICY_QUERIES: tuple[str, ...] = (
    "runway guardrail cash buffer financing term sheet late cash covenant",
    "payment terms renewal prepay partial payment overdue invoice missing due date FX timing working capital liquidity hiring start date fully loaded contractor cash",
)
FPNA_PREFERRED_TOOLS: tuple[str, ...] = (
    "get_company_financials",
    "build_strategic_plan",
    "compare_finance_playbooks",
    "run_plan_sensitivity",
    "run_plan_stress_test",
    "list_arr_movements",
    "list_customer_contracts",
    "list_operations_sources",
    "get_operations_data_confidence",
    "get_reconciliation_summary",
    "list_open_discrepancies",
    "search_scenarios",
    "search_finance_policies",
)
FPNA_PREFERRED_SLICES: tuple[str, ...] = (
    "forecast_assumptions",
    "cash_forecast",
    "pipeline_by_stage",
    "pipeline_quality",
    "slipped_close_dates",
    "stage_aging",
    "stale_opportunities",
    "probability_overrides",
    "duplicate_accounts",
    "renewal_vs_new_business",
    "weighted_unweighted_arr_gap",
    "customer_cohorts",
    "customer_contracts",
    "arr_movements",
    "hiring_plan",
    "headcount_plan_quality",
    "recruiting_slippage",
    "hiring_start_timing",
    "fully_loaded_role_cost",
    "plan_vs_actual_hiring_drift",
    "opex_monthly",
    "decision_outcomes",
    "scenario_math",
    "plan_vs_actual_deltas",
)
FPNA_POLICY_QUERIES: tuple[str, ...] = (
    "forecast calibration ARR bridge pipeline conversion slipped close date stale opportunity probability override stage aging CAC payback gross margin sensitivity",
    "plan versus actual variance ROI unit economics cohort churn NDR scenario math weighted unweighted ARR duplicate account renewal expansion new business hiring start date recruiting slippage fully loaded role cost",
)
RISK_PREFERRED_TOOLS: tuple[str, ...] = (
    "get_company_financials",
    "search_finance_policies",
    "search_finance_knowledge",
    "required_approvals",
    "check_controls",
    "missing_evidence",
    "obligations_if_approved",
    "list_operations_sources",
    "get_operations_data_confidence",
    "get_reconciliation_summary",
    "list_open_discrepancies",
    "run_plan_stress_test",
    "search_scenarios",
)
RISK_PREFERRED_SLICES: tuple[str, ...] = (
    "board_constraints",
    "policies",
    "governance_rules",
    "approval_matrix",
    "security_incidents",
    "audit_findings",
    "reconciliation_discrepancies",
    "operations_sources",
    "source_provenance",
    "data_quality",
    "missing_board_approvals",
    "owner_attestation_gaps",
    "sla_security_clauses",
    "dpa_status",
    "contract_invoice_mismatches",
    "disputed_invoices",
    "missing_due_dates",
    "invoice_messiness",
    "renewal_urgency",
    "headcount_approval_status",
    "partial_headcount_approvals",
    "unplanned_headcount",
    "contractor_approvals",
    "department_mapping_drift",
    "forecast_assumptions",
    "pipeline_by_stage",
    "decision_outcomes",
)
RISK_POLICY_QUERIES: tuple[str, ...] = (
    "gov-runway-floor gov-board-notify gov-data-security BP-6 policy violation approval route missing evidence audit trail board approval id exception security blocker",
    "gov-spend-cfo gov-headcount gov-forecast-calibration reconciliation discrepancy source provenance data quality fraud error disputed invoice missing due date compliance hidden obligation DPA SLA contract invoice mismatch renewal urgency owner attestation headcount approval unplanned contractor department mapping drift",
)
PROCUREMENT_PREFERRED_TOOLS: tuple[str, ...] = (
    "list_vendors",
    "list_invoices",
    "list_purchase_orders",
    "search_finance_knowledge",
    "search_finance_policies",
    "list_operations_sources",
    "get_operations_data_confidence",
    "get_reconciliation_summary",
    "list_open_discrepancies",
    "obligations_if_approved",
    "search_scenarios",
)
PROCUREMENT_PREFERRED_SLICES: tuple[str, ...] = (
    "vendors",
    "vendor_exports",
    "invoices",
    "invoice_line_descriptions",
    "invoice_messiness",
    "partial_payments",
    "disputed_invoices",
    "purchase_orders",
    "contract_metadata",
    "contract_aliases",
    "procurement_notes",
    "prior_renewal_outcomes",
    "decision_outcomes",
    "vendor_clauses",
    "price_benchmarks",
    "volume_discounts",
    "tiered_pricing",
    "billing_frequency",
    "billing_terms",
    "switching_costs",
    "slas",
    "sla_credits",
    "termination_clauses",
    "termination_penalties",
    "notice_windows",
    "owner_changes",
    "vendor_renewal_dates",
    "payment_terms",
)
PROCUREMENT_POLICY_QUERIES: tuple[str, ...] = (
    "vendor renewal negotiation auto-renewal notice window termination penalty clause price benchmark volume discount SLA credits tiered pricing billing terms",
    "supplier consolidation switching cost procurement notes prior renewal outcome contract metadata aliases owner changes invoice line dispute annual monthly billing mismatch",
)


def treasury_evidence_preferences() -> dict[str, list[str]]:
    """Stable Treasury evidence preferences for tests, prompts, and UI docs."""
    return {
        "tools": list(TREASURY_PREFERRED_TOOLS),
        "focus_slices": list(TREASURY_PREFERRED_SLICES),
        "policy_queries": list(TREASURY_POLICY_QUERIES),
    }


def fpna_evidence_preferences() -> dict[str, list[str]]:
    """Stable FP&A evidence preferences for tests, prompts, and UI docs."""
    return {
        "tools": list(FPNA_PREFERRED_TOOLS),
        "focus_slices": list(FPNA_PREFERRED_SLICES),
        "policy_queries": list(FPNA_POLICY_QUERIES),
    }


def risk_evidence_preferences() -> dict[str, list[str]]:
    """Stable Risk & Audit evidence preferences for tests, prompts, and UI docs."""
    return {
        "tools": list(RISK_PREFERRED_TOOLS),
        "focus_slices": list(RISK_PREFERRED_SLICES),
        "policy_queries": list(RISK_POLICY_QUERIES),
    }


def procurement_evidence_preferences() -> dict[str, list[str]]:
    """Stable Procurement evidence preferences for tests, prompts, and UI docs."""
    return {
        "tools": list(PROCUREMENT_PREFERRED_TOOLS),
        "focus_slices": list(PROCUREMENT_PREFERRED_SLICES),
        "policy_queries": list(PROCUREMENT_POLICY_QUERIES),
    }


def _merge_preferred(existing: list[str] | None, preferred: tuple[str, ...], *, limit: int | None = None) -> list[str]:
    """Preserve model choices while making preferred evidence first and unique."""
    out: list[str] = []
    for item in [*preferred, *(existing or [])]:
        value = str(item).strip()
        if value and value not in out:
            out.append(value)
        if limit is not None and len(out) >= limit:
            break
    return out


def _treasury_role_plan(plan: RoleEvidencePlan | None = None) -> RoleEvidencePlan:
    """Return a Treasury role plan centered on liquidity mechanics."""
    prior_decisions = plan.prior_decisions if plan else []
    rationale = (
        "Treasury needs cash timing, burn/runway sensitivity, payment obligations, renewal cash dates, "
        "fully loaded hiring cash by start date, contractor cash exposure, and financing-delay downside before it can speak."
    )
    return RoleEvidencePlan(
        role="treasury",
        tools=_merge_preferred(plan.tools if plan else [], TREASURY_PREFERRED_TOOLS),
        policy_queries=_merge_preferred(plan.policy_queries if plan else [], TREASURY_POLICY_QUERIES, limit=2),
        focus_slices=_merge_preferred(plan.focus_slices if plan else [], TREASURY_PREFERRED_SLICES),
        prior_decisions=list(prior_decisions or []),
        rationale=rationale,
        document_queries=list(plan.document_queries if plan else []),
        document_source_categories=list(plan.document_source_categories if plan else []),
        document_kinds=list(plan.document_kinds if plan else []),
        document_rationale=plan.document_rationale if plan else "",
    )


def _fpna_role_plan(plan: RoleEvidencePlan | None = None) -> RoleEvidencePlan:
    """Return an FP&A role plan centered on forecastability and unit economics."""
    prior_decisions = plan.prior_decisions if plan else []
    rationale = (
        "FP&A needs forecast assumptions, ARR movement, pipeline probability quality, slipped/stale "
        "opportunities, hiring-plan quality, recruiting slippage, unit economics, scenario sensitivity, and plan-vs-actual variance before it "
        "can judge forecastability."
    )
    return RoleEvidencePlan(
        role="fpna",
        tools=_merge_preferred(plan.tools if plan else [], FPNA_PREFERRED_TOOLS),
        policy_queries=_merge_preferred(plan.policy_queries if plan else [], FPNA_POLICY_QUERIES, limit=2),
        focus_slices=_merge_preferred(plan.focus_slices if plan else [], FPNA_PREFERRED_SLICES),
        prior_decisions=list(prior_decisions or []),
        rationale=rationale,
        document_queries=list(plan.document_queries if plan else []),
        document_source_categories=list(plan.document_source_categories if plan else []),
        document_kinds=list(plan.document_kinds if plan else []),
        document_rationale=plan.document_rationale if plan else "",
    )


def _risk_role_plan(plan: RoleEvidencePlan | None = None) -> RoleEvidencePlan:
    """Return a Risk & Audit role plan centered on controls evidence."""
    prior_decisions = plan.prior_decisions if plan else []
    rationale = (
        "Risk & Audit needs board policy, governance controls, approvals, missing evidence, "
        "audit/security findings, reconciliation discrepancies, source provenance, and downside "
        "scenarios, including missing board approvals, SLA/DPA gaps, owner-attestation gaps, "
        "contract-vs-invoice mismatches, renewal urgency, headcount approval gaps, unplanned roles, "
        "contractor approvals, and department mapping drift before it can clear or condition the decision."
    )
    return RoleEvidencePlan(
        role="risk",
        tools=_merge_preferred(plan.tools if plan else [], RISK_PREFERRED_TOOLS),
        policy_queries=_merge_preferred(plan.policy_queries if plan else [], RISK_POLICY_QUERIES, limit=2),
        focus_slices=_merge_preferred(plan.focus_slices if plan else [], RISK_PREFERRED_SLICES),
        prior_decisions=list(prior_decisions or []),
        rationale=rationale,
        document_queries=list(plan.document_queries if plan else []),
        document_source_categories=list(plan.document_source_categories if plan else []),
        document_kinds=list(plan.document_kinds if plan else []),
        document_rationale=plan.document_rationale if plan else "",
    )


def _procurement_role_plan(plan: RoleEvidencePlan | None = None) -> RoleEvidencePlan:
    """Return a Procurement role plan centered on commercial negotiation."""
    prior_decisions = plan.prior_decisions if plan else []
    rationale = (
        "Procurement needs vendor exports, invoices, purchase orders, contract metadata, renewal dates, "
        "commercial clauses, aliases, billing cadence, tiered pricing, notice windows, penalties, "
        "price benchmarks, switching cost, SLAs, owner changes, and prior renewal outcomes before it "
        "can build negotiation leverage."
    )
    return RoleEvidencePlan(
        role="procurement",
        tools=_merge_preferred(plan.tools if plan else [], PROCUREMENT_PREFERRED_TOOLS),
        policy_queries=_merge_preferred(plan.policy_queries if plan else [], PROCUREMENT_POLICY_QUERIES, limit=2),
        focus_slices=_merge_preferred(plan.focus_slices if plan else [], PROCUREMENT_PREFERRED_SLICES),
        prior_decisions=list(prior_decisions or []),
        rationale=rationale,
        document_queries=list(plan.document_queries if plan else []),
        document_source_categories=list(plan.document_source_categories if plan else []),
        document_kinds=list(plan.document_kinds if plan else []),
        document_rationale=plan.document_rationale if plan else "",
    )


def enforce_role_specific_evidence_plan(plan: DecisionPlan) -> DecisionPlan:
    """Guarantee role-specific evidence routes for analysts with strict lanes."""
    from src.documents.retrieval import document_plan_for_decision

    role_plans: list[RoleEvidencePlan] = []
    seen_roles: set[str] = set()

    def _attach_documents(role_plan: RoleEvidencePlan) -> RoleEvidencePlan:
        hints = document_plan_for_decision(
            plan.decision_type,
            role=str(role_plan.role).lower(),
            entities=plan.entities,
        )
        if not hints["document_queries"]:
            return role_plan
        tools = list(role_plan.tools)
        if "search_uploaded_documents" not in tools:
            tools.append("search_uploaded_documents")
        return role_plan.model_copy(
            update={
                "tools": tools,
                "document_queries": role_plan.document_queries or hints["document_queries"],
                "document_source_categories": role_plan.document_source_categories or hints["document_source_categories"],
                "document_kinds": role_plan.document_kinds or hints["document_kinds"],
                "document_rationale": role_plan.document_rationale or hints["document_rationale"],
            }
        )

    for role_plan in plan.role_plans:
        role = str(role_plan.role).lower()
        seen_roles.add(role)
        if role == "treasury":
            role_plans.append(_attach_documents(_treasury_role_plan(role_plan)))
        elif role == "fpna":
            role_plans.append(_attach_documents(_fpna_role_plan(role_plan)))
        elif role == "risk":
            role_plans.append(_attach_documents(_risk_role_plan(role_plan)))
        elif role == "procurement":
            role_plans.append(_attach_documents(_procurement_role_plan(role_plan)))
        else:
            role_plans.append(_attach_documents(role_plan))
    if "treasury" not in seen_roles:
        role_plans.insert(0, _attach_documents(_treasury_role_plan()))
    if "fpna" not in seen_roles:
        insert_at = 1 if role_plans and str(role_plans[0].role).lower() == "treasury" else 0
        role_plans.insert(insert_at, _attach_documents(_fpna_role_plan()))
    if "risk" not in seen_roles:
        insert_at = 2 if len(role_plans) >= 2 else len(role_plans)
        role_plans.insert(insert_at, _attach_documents(_risk_role_plan()))
    if "procurement" not in seen_roles:
        role_plans.append(_attach_documents(_procurement_role_plan()))
    return plan.model_copy(update={"role_plans": role_plans})


def enforce_treasury_liquidity_plan(plan: DecisionPlan) -> DecisionPlan:
    """Backward-compatible alias for role-specific evidence enforcement."""
    return enforce_role_specific_evidence_plan(plan)


def _prompt_hash(template: str) -> str:
    return hashlib.sha256(template.encode("utf-8")).hexdigest()[:12]


def _active_prompt_material(role: str) -> str:
    template = _PROMPT_TEMPLATES.get(role, "")
    directive = ROLE_DIRECTIVES.get(role)
    if directive:
        return f"{role}\n{_PROMPT_VERSION_IDS.get(role, f'{role}.v1')}\n{template}\nROLE_DIRECTIVE:\n{directive}"
    return f"{role}\n{_PROMPT_VERSION_IDS.get(role, f'{role}.v1')}\n{template}"


def prompt_versions_payload(company_context: dict | None = None) -> list[dict[str, Any]]:
    """Versioned-prompt provenance for each council seat.

    Seed rows can supply backwards-compatible display aliases, but the active
    role prompt, candidate, gate, and dimensions are code-owned so a stale Redis
    seed cannot hide a newly shipped promotion contract.
    """
    seeded = {}
    for item in ((company_context or {}).get("financials") or {}).get("prompt_versions") or []:
        seeded[item.get("agent") or item.get("role")] = item
    payload: list[dict[str, Any]] = []
    for role in COUNCIL_PROMPT_ROLES:
        seed = seeded.get(role, {})
        profile = ROLE_PROMOTION_PROFILES[role]
        version = _PROMPT_VERSION_IDS.get(role, f"{role}.v1")
        active_material = _active_prompt_material(role)
        prompt_hash = _prompt_hash(active_material)
        candidate = profile["candidate"]
        promotion_gate = profile["promotion_gate"]
        dimensions = list(profile["reliability_dimensions"])
        candidate_material = (
            f"{active_material}\n\nCANDIDATE_VERSION:{candidate}\n"
            f"PROMOTION_GATE:{promotion_gate}\nRELIABILITY_DIMENSIONS:{json.dumps(dimensions)}"
        )
        payload.append(
            {
                "role": role,
                "agent": role,
                "current": version,
                "version": version,
                "prompt_hash": prompt_hash,
                "active_prompt_hash": prompt_hash,
                "candidate": candidate,
                "candidate_prompt_hash": _prompt_hash(candidate_material),
                "promotion_gate": promotion_gate,
                "reliability_dimensions": dimensions,
                "gate_metric": profile.get("gate_metric") or (dimensions[0] if dimensions else "reliability"),
                "replay_set": profile.get("replay_set") or seed.get("replay_set") or f"atlas-{role}-replay",
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
        "ledger",
        "invoices",
        "invoice_messiness",
        "partial_payments",
        "overdue_invoices",
        "disputed_invoices",
        "missing_due_dates",
        "fx_invoices",
        "invoice_line_descriptions",
        "payment_terms",
        "vendor_renewal_dates",
        "financing_scenarios",
        "forecast_assumptions",
        "arr_movements",
        "customer_contracts",
        "scenario_math",
        "plan_vs_actual_deltas",
        "governance_rules",
        "approval_matrix",
        "reconciliation_discrepancies",
        "operations_sources",
        "source_provenance",
        "data_quality",
        "vendor_exports",
        "purchase_orders",
        "contract_metadata",
        "procurement_notes",
        "prior_renewal_outcomes",
        "vendor_clauses",
        "price_benchmarks",
        "volume_discounts",
        "switching_costs",
        "slas",
        "termination_clauses",
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
    result = await structured_call(
        node="planner",
        role="classifier",
        schema=DecisionPlan,
        system=system,
        human=human,
        config=config,
        temperature=0.2,
        reasoning_effort=LLM_PLANNER_REASONING_EFFORT,
    )
    if result.ok and result.parsed is not None:
        result.parsed = enforce_treasury_liquidity_plan(result.parsed)
    return result


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


def _json_tool_payload(tool_obj: Any, label: str, bundle: EvidenceBundle, *args: Any, **kwargs: Any) -> Any | None:
    """Run a read-only tool and attach an honest warning instead of fabricating."""
    try:
        payload = json.loads(_tool_text(tool_obj, *args, **kwargs))
        bundle.redis_activity.append({"label": label, "detail": "Role-specific evidence pulled", "kind": "tool"})
        return payload
    except Exception as exc:
        bundle.redis_activity.append({"label": f"{label} warning", "detail": redact_secrets(exc), "kind": "warning"})
        return None


def _treasury_context_summary(context: dict) -> dict[str, Any]:
    """Extract liquidity mechanics from the loaded system of record."""
    financials = context.get("financials") or {}
    vendors = context.get("vendors") or []
    operations = context.get("operations") or {}
    operation_sources = operations.get("sources") or []
    headcount_quality = next(
        (
            source.get("headcount_quality_summary")
            for source in operation_sources
            if isinstance(source, dict) and source.get("source_type") == "headcount_plan"
        ),
        None,
    )
    cash_forecast = financials.get("cash_forecast") or []
    cash_history = financials.get("cash_history") or []
    opex = financials.get("opex_monthly") or {}
    downside_rows = [row for row in cash_forecast if isinstance(row, dict) and row.get("downside_cash") is not None]
    min_downside = min(downside_rows, key=lambda row: float(row.get("downside_cash") or 0), default=None)
    renewal_schedule = [
        {
            "name": vendor.get("name"),
            "monthly_cost": vendor.get("monthly_cost"),
            "annual_cost": vendor.get("annual_cost"),
            "renewal_date": vendor.get("renewal_date"),
            "termination_notice_days": vendor.get("termination_notice_days"),
            "status": vendor.get("status"),
            "notes": vendor.get("notes"),
        }
        for vendor in vendors
        if vendor.get("renewal_date") or vendor.get("termination_notice_days") or vendor.get("monthly_cost")
    ]
    return {
        "current_cash": financials.get("cash_on_hand"),
        "monthly_net_burn": financials.get("monthly_net_burn"),
        "runway_months": financials.get("runway_months"),
        "monthly_gross_burn": financials.get("monthly_gross_burn"),
        "opex_monthly": opex,
        "last_raise": financials.get("last_raise"),
        "board_constraints": financials.get("board_constraints") or [],
        "latest_cash_history": cash_history[-3:],
        "cash_forecast": cash_forecast[:6],
        "minimum_downside_cash": min_downside,
        "renewal_payment_schedule": renewal_schedule[:8],
        "hiring_cash_impact": headcount_quality,
        "late_cash_questions": [
            "What happens to runway if customer cash arrives 30 days late?",
            "Which vendor renewals or prepays hit before the next financing milestone?",
            "Which hiring starts, contractors, or fully loaded role costs hit cash before revenue or financing arrives?",
            "Does downside cash breach the board runway floor before financing closes?",
        ],
    }


def _fpna_context_summary(context: dict) -> dict[str, Any]:
    """Extract forecastability and unit-economics facts from the system of record."""
    financials = context.get("financials") or {}
    cash_forecast = financials.get("cash_forecast") or []
    pipeline = financials.get("pipeline_by_stage") or []
    cohorts = financials.get("customer_cohorts") or []
    outcomes = financials.get("decision_outcomes") or []
    operations = context.get("operations") or {}
    operation_sources = operations.get("sources") or []
    crm_pipeline_quality = next(
        (
            source.get("pipeline_quality_summary")
            for source in operation_sources
            if isinstance(source, dict) and source.get("source_type") == "crm_opportunities"
        ),
        None,
    )
    headcount_quality = next(
        (
            source.get("headcount_quality_summary")
            for source in operation_sources
            if isinstance(source, dict) and source.get("source_type") == "headcount_plan"
        ),
        None,
    )

    pipeline_rows = [row for row in pipeline if isinstance(row, dict)]
    total_pipeline_arr = sum(float(row.get("arr") or 0) for row in pipeline_rows)
    total_weighted_arr = sum(float(row.get("weighted_arr") or 0) for row in pipeline_rows)
    implied_conversion = round(total_weighted_arr / total_pipeline_arr, 4) if total_pipeline_arr else None
    forecast_arr = [
        {"month": row.get("month"), "weighted_pipeline_arr": row.get("weighted_pipeline_arr")}
        for row in cash_forecast
        if isinstance(row, dict) and row.get("weighted_pipeline_arr") is not None
    ]
    fpna_outcomes = [
        outcome for outcome in outcomes if str(outcome.get("owner", "")).lower() in {"fp&a", "fpa", "fpna", "cfo"}
    ]

    return {
        "mrr": financials.get("mrr"),
        "arr": financials.get("arr"),
        "mrr_growth_mom": financials.get("mrr_growth_mom"),
        "gross_margin": financials.get("gross_margin"),
        "logo_churn_mom": financials.get("logo_churn_mom"),
        "ndr": financials.get("ndr"),
        "cac": financials.get("cac"),
        "ltv": financials.get("ltv"),
        "magic_number": financials.get("magic_number"),
        "pipeline_arr": total_pipeline_arr or None,
        "weighted_pipeline_arr": total_weighted_arr or None,
        "implied_pipeline_conversion": implied_conversion,
        "pipeline_by_stage": pipeline_rows,
        "pipeline_quality": crm_pipeline_quality,
        "customer_cohorts": cohorts,
        "forecast_weighted_pipeline_arr": forecast_arr[:8],
        "hiring_plan_dependencies": financials.get("hiring_plan") or [],
        "headcount_plan_quality": headcount_quality,
        "plan_vs_actual_examples": fpna_outcomes[:6],
        "forecastability_questions": [
            "Which conversion probability makes the case work, and is it calibrated against actual stage history?",
            "Which slipped, stale, or override-heavy opportunities should be haircut before accepting weighted ARR?",
            "How much of the pipeline is renewal protection versus expansion or new-business growth?",
            "Which slipped, partially approved, contractor, or backfill roles should move from base case to downside hiring capacity?",
            "What ARR, margin, and CAC/payback range breaks the business case?",
            "Which plan-vs-actual deltas should lower confidence or become CFO conditions?",
        ],
    }


def _risk_context_summary(context: dict) -> dict[str, Any]:
    """Extract controls, provenance, security, and audit facts from the system of record."""
    financials = context.get("financials") or {}
    vendors = context.get("vendors") or []
    operations = context.get("operations") or {}
    operation_sources = operations.get("sources") or []
    headcount_quality = next(
        (
            source.get("headcount_quality_summary")
            for source in operation_sources
            if isinstance(source, dict) and source.get("source_type") == "headcount_plan"
        ),
        None,
    )
    security_incidents = financials.get("security_incidents") or []
    audit_findings = financials.get("audit_findings") or []
    pipeline = financials.get("pipeline_by_stage") or []
    board_constraints = financials.get("board_constraints") or []
    outcomes = financials.get("decision_outcomes") or []

    open_security = [
        incident
        for incident in security_incidents
        if str(incident.get("status", "")).lower() not in {"closed", "resolved", "remediated"}
    ]
    high_audit = [
        finding
        for finding in audit_findings
        if str(finding.get("severity", "")).lower() in {"high", "critical"}
    ]
    risky_pipeline = [
        {
            "stage": row.get("stage"),
            "arr": row.get("arr"),
            "weighted_arr": row.get("weighted_arr"),
            "risk": row.get("risk"),
        }
        for row in pipeline
        if isinstance(row, dict) and row.get("risk")
    ]
    risk_outcomes = [
        outcome for outcome in outcomes if str(outcome.get("owner", "")).lower() in {"risk & audit", "risk", "cfo"}
    ]
    vendor_control_gaps = [
        {
            "name": vendor.get("name"),
            "annual_cost": vendor.get("annual_cost"),
            "board_approved": vendor.get("board_approved"),
            "board_approval_id": vendor.get("board_approval_id"),
            "auto_renew": vendor.get("auto_renew"),
            "notice_window_days": vendor.get("notice_window_days") or vendor.get("termination_notice_days"),
            "renewal_date": vendor.get("renewal_date"),
            "owner": vendor.get("owner"),
            "owner_history": vendor.get("owner_history"),
            "sla_uptime_pct": vendor.get("sla_uptime_pct"),
            "sla_credits": vendor.get("sla_credits"),
            "security_clause": vendor.get("security_clause"),
            "data_processing_addendum": vendor.get("data_processing_addendum"),
            "termination_penalty": vendor.get("termination_penalty"),
        }
        for vendor in vendors
        if isinstance(vendor, dict)
        and (
            vendor.get("board_approved") is False
            or not vendor.get("board_approval_id")
            or vendor.get("data_processing_addendum") is False
            or not vendor.get("security_clause")
            or vendor.get("owner_history")
        )
    ][:8]

    return {
        "board_constraints": board_constraints,
        "security_incidents": security_incidents,
        "open_security_incidents": open_security,
        "audit_findings": audit_findings,
        "high_severity_audit_findings": high_audit,
        "vendor_control_gaps": vendor_control_gaps,
        "headcount_control_gaps": headcount_quality,
        "forecast_risks_to_challenge": risky_pipeline,
        "risk_calibration_outcomes": risk_outcomes[:6],
        "adversarial_questions": [
            "Which board policy or governance rule is engaged, violated, or missing an exception?",
            "Which approval route, audit trail, and source-provenance record proves this decision is controlled?",
            "Which partially approved, unapproved, contractor, or unplanned headcount rows need approval evidence before hiring proceeds?",
            "What reconciliation discrepancy, security evidence gap, missing board approval, owner-attestation gap, or hidden obligation could invalidate the upside case?",
        ],
    }


def _procurement_context_summary(context: dict) -> dict[str, Any]:
    """Extract commercial vendor leverage from the system of record."""
    financials = context.get("financials") or {}
    vendors = context.get("vendors") or []
    outcomes = financials.get("decision_outcomes") or []
    vendor_rows = [vendor for vendor in vendors if isinstance(vendor, dict)]
    annual_spend = sum(float(vendor.get("annual_cost") or 0) for vendor in vendor_rows)
    renewal_rows = [
        {
            "name": vendor.get("name"),
            "category": vendor.get("category"),
            "annual_cost": vendor.get("annual_cost"),
            "monthly_cost": vendor.get("monthly_cost"),
            "renewal_date": vendor.get("renewal_date"),
            "status": vendor.get("status"),
            "auto_renew": vendor.get("auto_renew"),
            "notice_window_days": vendor.get("notice_window_days"),
            "termination_notice_days": vendor.get("termination_notice_days"),
            "termination_penalty": vendor.get("termination_penalty"),
            "billing_frequency": vendor.get("billing_frequency"),
            "billing_terms": vendor.get("billing_terms"),
            "tiered_pricing": vendor.get("tiered_pricing"),
            "contract_aliases": vendor.get("contract_aliases"),
            "owner_history": vendor.get("owner_history"),
            "sla_credits": vendor.get("sla_credits"),
            "switching_cost": vendor.get("switching_cost"),
            "notes": vendor.get("notes"),
        }
        for vendor in vendor_rows
        if vendor.get("renewal_date") or vendor.get("termination_notice_days") or vendor.get("switching_cost")
    ]
    procurement_outcomes = [
        outcome
        for outcome in outcomes
        if str(outcome.get("owner", "")).lower() == "procurement"
        or "vendor" in json.dumps(outcome, default=str).lower()
        or "renewal" in json.dumps(outcome, default=str).lower()
    ]
    high_switching_costs = [
        {"name": vendor.get("name"), "switching_cost": vendor.get("switching_cost")}
        for vendor in vendor_rows
        if float(vendor.get("switching_cost") or 0) >= 50_000
    ]

    return {
        "vendor_count": len(vendor_rows),
        "annual_vendor_spend": annual_spend or None,
        "renewal_schedule": renewal_rows[:10],
        "up_for_renewal": [vendor for vendor in renewal_rows if str(vendor.get("status", "")).lower() == "up_for_renewal"],
        "high_switching_costs": high_switching_costs,
        "prior_renewal_outcomes": procurement_outcomes[:6],
        "commercial_questions": [
            "Which renewal date, auto-renewal clause, or termination notice creates the negotiation clock?",
            "What price benchmark, usage level, or consolidation option proves leverage?",
            "What exact supplier ask improves terms without triggering unacceptable switching cost or SLA risk?",
        ],
    }


def _gather_treasury_liquidity_evidence(bundle: EvidenceBundle, role_plan: RoleEvidencePlan, context: dict) -> None:
    """Attach Treasury-only liquidity mechanics evidence."""
    liquidity: dict[str, Any] = {"system_of_record": _treasury_context_summary(context)}
    tool_names = {str(tool).strip() for tool in (role_plan.tools or [])}

    if not tool_names or "list_operations_sources" in tool_names:
        liquidity["operations_sources"] = _json_tool_payload(list_operations_sources, "Operations sources", bundle)
    if not tool_names or "get_operations_data_confidence" in tool_names:
        liquidity["data_confidence"] = _json_tool_payload(get_operations_data_confidence, "Data confidence", bundle)
    if not tool_names or "get_reconciliation_summary" in tool_names:
        liquidity["reconciliation"] = _json_tool_payload(get_reconciliation_summary, "Reconciliation", bundle)
    if not tool_names or "list_open_discrepancies" in tool_names:
        liquidity["cash_discrepancies"] = _json_tool_payload(list_open_discrepancies, "Open discrepancies", bundle)
    if not tool_names or "list_invoices" in tool_names:
        liquidity["outstanding_invoices"] = _json_tool_payload(list_invoices, "Invoices", bundle, status="outstanding")
        liquidity["overdue_invoices"] = _json_tool_payload(list_invoices, "Overdue invoices", bundle, status="overdue")
    if not tool_names or "search_scenarios" in tool_names:
        liquidity["financing_scenarios"] = _json_tool_payload(
            search_scenarios,
            "Financing scenarios",
            bundle,
            query="financing bridge cash runway late close",
            tag="financing",
        )
    if "run_plan_sensitivity" in tool_names:
        liquidity["financing_close_sensitivity"] = _json_tool_payload(
            run_plan_sensitivity,
            "Financing sensitivity",
            bundle,
            variable="financing_close_month",
            horizon_months=12,
        )
    if "run_plan_stress_test" in tool_names:
        liquidity["runway_stress_test"] = _json_tool_payload(
            run_plan_stress_test,
            "Runway stress test",
            bundle,
            horizon_months=12,
            trials=200,
        )

    bundle.evidence["liquidity_mechanics"] = liquidity


def _gather_fpna_forecast_evidence(bundle: EvidenceBundle, role_plan: RoleEvidencePlan, context: dict) -> None:
    """Attach FP&A-only forecast and unit-economics evidence."""
    forecast: dict[str, Any] = {"system_of_record": _fpna_context_summary(context)}
    tool_names = {str(tool).strip() for tool in (role_plan.tools or [])}

    if not tool_names or "list_arr_movements" in tool_names:
        forecast["arr_movements"] = _json_tool_payload(list_arr_movements, "ARR movements", bundle)
    if not tool_names or "list_customer_contracts" in tool_names:
        forecast["customer_contracts"] = _json_tool_payload(list_customer_contracts, "Customer contracts", bundle)
    if not tool_names or "list_operations_sources" in tool_names:
        forecast["operations_sources"] = _json_tool_payload(list_operations_sources, "Operations sources", bundle)
    if not tool_names or "get_operations_data_confidence" in tool_names:
        forecast["data_confidence"] = _json_tool_payload(get_operations_data_confidence, "Data confidence", bundle)
    if not tool_names or "get_reconciliation_summary" in tool_names:
        forecast["reconciliation"] = _json_tool_payload(get_reconciliation_summary, "Reconciliation", bundle)
    if not tool_names or "list_open_discrepancies" in tool_names:
        forecast["pipeline_quality_discrepancies"] = _json_tool_payload(list_open_discrepancies, "Pipeline quality discrepancies", bundle)
    if not tool_names or "search_scenarios" in tool_names:
        forecast["forecast_scenarios"] = _json_tool_payload(
            search_scenarios,
            "Forecast scenarios",
            bundle,
            query="ARR pipeline conversion slipped close date stale opportunity probability override CAC payback gross margin forecast sensitivity",
        )
    if "build_strategic_plan" in tool_names:
        forecast["base_operating_plan"] = _json_tool_payload(
            build_strategic_plan,
            "Strategic plan",
            bundle,
            horizon_months=12,
        )
    if "compare_finance_playbooks" in tool_names:
        forecast["playbook_comparison"] = _json_tool_payload(
            compare_finance_playbooks,
            "Finance playbooks",
            bundle,
            decision="forecastability and unit economics for the decision under review",
            horizon_months=12,
        )
    if "run_plan_sensitivity" in tool_names:
        forecast["forecast_sensitivity"] = _json_tool_payload(
            run_plan_sensitivity,
            "Forecast sensitivity",
            bundle,
            variable="",
            horizon_months=12,
        )
    if "run_plan_stress_test" in tool_names:
        forecast["forecast_stress_test"] = _json_tool_payload(
            run_plan_stress_test,
            "Forecast stress test",
            bundle,
            horizon_months=12,
            trials=200,
        )

    bundle.evidence["forecast_unit_economics"] = forecast


def _gather_risk_controls_evidence(
    bundle: EvidenceBundle,
    role_plan: RoleEvidencePlan,
    context: dict,
    *,
    decision: str = "",
) -> None:
    """Attach Risk & Audit controls-adversary evidence."""
    controls: dict[str, Any] = {"system_of_record": _risk_context_summary(context)}
    tool_names = {str(tool).strip() for tool in (role_plan.tools or [])}
    decision_text = decision or "current decision under review"

    if not tool_names or "check_controls" in tool_names:
        controls["governance_controls"] = _json_tool_payload(check_controls, "Governance controls", bundle, decision=decision_text)
    if not tool_names or "required_approvals" in tool_names:
        controls["approval_route"] = _json_tool_payload(required_approvals, "Required approvals", bundle, decision=decision_text)
    if not tool_names or "missing_evidence" in tool_names:
        controls["missing_evidence"] = _json_tool_payload(missing_evidence, "Missing evidence", bundle, decision=decision_text)
    if not tool_names or "obligations_if_approved" in tool_names:
        controls["hidden_obligations"] = _json_tool_payload(obligations_if_approved, "Hidden obligations", bundle, decision=decision_text)
    if not tool_names or "list_operations_sources" in tool_names:
        controls["source_provenance"] = _json_tool_payload(list_operations_sources, "Source provenance", bundle)
    if not tool_names or "get_operations_data_confidence" in tool_names:
        controls["data_quality"] = _json_tool_payload(get_operations_data_confidence, "Data quality", bundle)
    if not tool_names or "get_reconciliation_summary" in tool_names:
        controls["reconciliation"] = _json_tool_payload(get_reconciliation_summary, "Reconciliation", bundle)
    if not tool_names or "list_open_discrepancies" in tool_names:
        controls["open_discrepancies"] = _json_tool_payload(list_open_discrepancies, "Open discrepancies", bundle)
        controls["high_discrepancies"] = _json_tool_payload(list_open_discrepancies, "High discrepancies", bundle, severity="high")
    if not tool_names or "search_finance_knowledge" in tool_names:
        controls["audit_knowledge"] = _json_tool_payload(
            search_finance_knowledge,
            "Audit knowledge",
            bundle,
            query="security evidence audit trail policy violation source provenance control gap board approval DPA SLA owner attestation contract invoice mismatch renewal urgency",
            kind="audit_finding",
        )
    if not tool_names or "search_scenarios" in tool_names:
        controls["downside_scenarios"] = _json_tool_payload(
            search_scenarios,
            "Downside scenarios",
            bundle,
            query="downside compliance security blocker control gap forecast miss",
            tag="downside",
        )
    if "run_plan_stress_test" in tool_names:
        controls["downside_stress_test"] = _json_tool_payload(
            run_plan_stress_test,
            "Downside stress test",
            bundle,
            horizon_months=12,
            trials=200,
        )

    bundle.evidence["controls_adversary"] = controls


def _gather_procurement_negotiation_evidence(
    bundle: EvidenceBundle,
    role_plan: RoleEvidencePlan,
    context: dict,
    *,
    decision: str = "",
) -> None:
    """Attach Procurement-only commercial negotiation evidence."""
    commercial: dict[str, Any] = {"system_of_record": _procurement_context_summary(context)}
    tool_names = {str(tool).strip() for tool in (role_plan.tools or [])}
    decision_text = decision or "current vendor decision under review"

    if not tool_names or "list_vendors" in tool_names:
        commercial["vendor_exports"] = _json_tool_payload(list_vendors, "Vendor exports", bundle)
    if not tool_names or "list_invoices" in tool_names:
        commercial["outstanding_invoices"] = _json_tool_payload(list_invoices, "Vendor invoices", bundle, status="outstanding")
        commercial["overdue_invoices"] = _json_tool_payload(list_invoices, "Overdue invoices", bundle, status="overdue")
    if not tool_names or "list_purchase_orders" in tool_names:
        commercial["purchase_orders"] = _json_tool_payload(list_purchase_orders, "Purchase orders", bundle)
    if not tool_names or "search_finance_knowledge" in tool_names:
        commercial["vendor_clauses"] = _json_tool_payload(
            search_finance_knowledge,
            "Vendor clauses",
            bundle,
            query="vendor clause auto-renewal notice window termination penalty SLA credits volume discount price benchmark switching cost tiered pricing billing frequency aliases owner change",
            kind="vendor_clause",
        )
    if not tool_names or "list_operations_sources" in tool_names:
        commercial["source_provenance"] = _json_tool_payload(list_operations_sources, "Source provenance", bundle)
    if not tool_names or "get_operations_data_confidence" in tool_names:
        commercial["data_confidence"] = _json_tool_payload(get_operations_data_confidence, "Data confidence", bundle)
    if not tool_names or "get_reconciliation_summary" in tool_names:
        commercial["contract_reconciliation"] = _json_tool_payload(get_reconciliation_summary, "Contract reconciliation", bundle)
    if not tool_names or "list_open_discrepancies" in tool_names:
        commercial["contract_discrepancies"] = _json_tool_payload(list_open_discrepancies, "Contract discrepancies", bundle)
    if not tool_names or "obligations_if_approved" in tool_names:
        commercial["commercial_obligations"] = _json_tool_payload(
            obligations_if_approved,
            "Commercial obligations",
            bundle,
            decision=decision_text,
        )
    if not tool_names or "search_scenarios" in tool_names:
        commercial["procurement_scenarios"] = _json_tool_payload(
            search_scenarios,
            "Procurement scenarios",
            bundle,
            query="vendor renegotiation consolidation switching cost volume discount SLA",
            tag="procurement",
        )

    bundle.evidence["commercial_negotiation"] = commercial


def gather_role_evidence(
    role_plan: RoleEvidencePlan | None,
    context: dict,
    *,
    decision: str = "",
    decision_type: str = "general",
    entities: list[str] | None = None,
) -> EvidenceBundle:
    """Execute a role's evidence plan against Redis before the analyst speaks.

    Runs the planned semantic policy/precedent RAG queries live, extracts the
    requested company-context slices, and pulls the named prior-decision
    outcomes. All grounded in the Redis system of record — no fabrication.
    """
    bundle = EvidenceBundle()
    if role_plan is None:
        return bundle

    financials = context.get("financials") or {}
    is_treasury = str(role_plan.role).lower() == "treasury"
    is_fpna = str(role_plan.role).lower() == "fpna"
    is_risk = str(role_plan.role).lower() == "risk"
    is_procurement = str(role_plan.role).lower() == "procurement"
    if is_treasury:
        role_plan = _treasury_role_plan(role_plan)
        _gather_treasury_liquidity_evidence(bundle, role_plan, context)
    elif is_fpna:
        role_plan = _fpna_role_plan(role_plan)
        _gather_fpna_forecast_evidence(bundle, role_plan, context)
    elif is_risk:
        role_plan = _risk_role_plan(role_plan)
        _gather_risk_controls_evidence(bundle, role_plan, context, decision=decision)
    elif is_procurement:
        role_plan = _procurement_role_plan(role_plan)
        _gather_procurement_negotiation_evidence(bundle, role_plan, context, decision=decision)

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

    # 4) Source-aware uploaded document retrieval (capped; never dump all chunks).
    doc_queries = role_plan.document_queries or []
    if doc_queries or role_plan.document_source_categories or role_plan.document_kinds:
        from src.documents.activity import (
            append_warning,
            chunks_retrieved_activity,
            fact_promoted_activity,
            filters_summary,
            source_used_activity,
            vector_query_activity,
        )
        from src.documents.retrieval import build_retrieval_filter
        from src.documents.store import search_document_chunks
        from src.errors import ExecutiveStateCode, executive_state

        filters = build_retrieval_filter(
            role_plan,
            decision_type=decision_type,
            entities=entities or [],
        )
        filter_label = filters_summary(filters)
        doc_hits: list[dict[str, Any]] = []
        seen_chunks: set[str] = set()
        for query in doc_queries[:2] or [decision or "financial decision"]:
            try:
                bundle.redis_activity.append(
                    vector_query_activity(role=role_plan.role, query=query, filters_summary=filter_label)
                )
                hits = search_document_chunks(query, filters=filters, k=4)
                bundle.queries.append(query)
                new_ids: list[str] = []
                categories: list[str] = []
                for hit in hits:
                    chunk_id = str(hit.get("chunk_id") or "")
                    if chunk_id and chunk_id not in seen_chunks:
                        seen_chunks.add(chunk_id)
                        doc_hits.append(hit)
                        new_ids.append(chunk_id)
                        categories.append(str(hit.get("source_category") or ""))
                bundle.redis_activity.append(
                    chunks_retrieved_activity(
                        role=role_plan.role,
                        count=len(hits),
                        chunk_ids=new_ids,
                        categories=[c for c in categories if c],
                    )
                )
            except Exception as exc:
                append_warning(bundle, exc, label="Document retrieval")
        if doc_hits:
            doc_hits = doc_hits[:8]
            bundle.evidence["uploaded_documents"] = doc_hits
            bundle.evidence["document_citations"] = []
            for hit in doc_hits:
                citation = (
                    f"doc:{hit.get('doc_id')}:{hit.get('chunk_id')} — "
                    f"{hit.get('filename')} ({hit.get('source_category')})"
                )
                bundle.evidence["document_citations"].append(citation)
                bundle.redis_activity.append(
                    source_used_activity(
                        role=role_plan.role,
                        doc_id=str(hit.get("doc_id") or ""),
                        chunk_id=str(hit.get("chunk_id") or ""),
                        filename=str(hit.get("filename") or ""),
                        source_category=str(hit.get("source_category") or ""),
                    )
                )
                excerpt = str(hit.get("excerpt") or hit.get("text") or "")[:280]
                if excerpt:
                    bundle.redis_activity.append(
                        fact_promoted_activity(
                            role=role_plan.role,
                            doc_id=str(hit.get("doc_id") or ""),
                            chunk_id=str(hit.get("chunk_id") or ""),
                            excerpt=excerpt,
                        )
                    )
        elif doc_queries or role_plan.document_source_categories:
            degraded = executive_state(
                ExecutiveStateCode.INSUFFICIENT_EVIDENCE,
                context=f"{role_plan.role} document retrieval",
            )
            bundle.redis_activity.append(
                {
                    **degraded,
                    "kind": "warning",
                    "label": degraded["title"],
                    "detail": degraded["message"],
                }
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
        role_directive=ROLE_DIRECTIVES.get(role_key, persona["mandate"]),
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
        "Give a concise, role-bounded position: role_specific_lens must name your lane and boundary; "
        "headline ≤10 words; argument ≤2 short sentences; exactly 2 key_points; and cited_metrics "
        "with the concrete numbers you relied on. Populate forecast_assumptions, scenario_sensitivities, "
        "and plan_vs_actual_deltas when relevant; FP&A must populate all three with forecastability, "
        "unit-economics, sensitivity, or variance facts. Populate control_findings, "
        "missing_evidence_requests, and approval_or_policy_blockers when relevant; Risk & Audit must "
        "populate all three with controls, approval, evidence, provenance, reconciliation, security, "
        "or compliance facts. Populate negotiation_levers when relevant; Procurement must populate it "
        "with supplier leverage, contract terms, renewal timing, price benchmarks, consolidation, "
        "switching cost, SLAs, termination clauses, volume discounts, or negotiation strategy. "
        "Other roles may use [] when outside their lane. "
        "Be direct — no preamble."
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
            "role_specific_lens": position.get("role_specific_lens"),
            "cited_metrics": position.get("cited_metrics", []),
            "key_points": position.get("key_points", []),
            "forecast_assumptions": position.get("forecast_assumptions", []),
            "control_findings": position.get("control_findings", []),
            "approval_or_policy_blockers": position.get("approval_or_policy_blockers", []),
            "negotiation_levers": position.get("negotiation_levers", []),
        }
        for position in positions
    ]
    human = (
        f"DECISION:\n{decision}\n\nANALYST POSITIONS:\n{json.dumps(slim, default=str)}\n\n"
        "ROLE-SPECIFIC CHALLENGE LANES:\n"
        "- treasury: cash_timing / Cash timing / liquidity timing, payment terms, late cash.\n"
        "- fpna: forecast_assumptions / Forecast assumptions / forecastability, ARR, probability, ROI, CAC/payback, margin.\n"
        "- risk: controls_policy / Controls / policy / approvals, audit trail, policy, provenance, compliance, hidden obligations.\n"
        "- procurement: vendor_terms / Vendor terms / supplier leverage, renewal clauses, benchmarks, SLAs, termination, discounts."
    )
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
        {
            "agent": position.get("agent"),
            "role": position.get("role"),
            "stance": position.get("stance"),
            "headline": position.get("headline"),
            "role_specific_lens": position.get("role_specific_lens"),
            "key_points": position.get("key_points"),
            "cited_metrics": position.get("cited_metrics", []),
        }
        for position in positions
    ]
    gaps = (challenge_report or {}).get("unresolved_gaps") or []
    findings = [
        {
            "role": finding.get("role"),
            "challenge_type": finding.get("challenge_type"),
            "challenge_label": finding.get("challenge_label"),
            "challenge_lens": finding.get("challenge_lens"),
            "cited_enough_numbers": finding.get("cited_enough_numbers"),
            "challenge": finding.get("challenge"),
        }
        for finding in ((challenge_report or {}).get("findings") or [])
    ]
    human = (
        f"DECISION:\n{decision}\n\nPOSITIONS:\n{json.dumps(slim, default=str)}\n\n"
        f"CHALLENGE-PANEL FINDINGS:\n{json.dumps(findings, default=str)}\n\n"
        f"UNRESOLVED EVIDENCE GAPS:\n{json.dumps(gaps, default=str)}\n\n"
        "REQUIRED CROSS-EXAM COVERAGE:\n"
        "- Treasury must challenge cash timing (challenge_type=cash_timing).\n"
        "- FP&A must challenge forecast assumptions (challenge_type=forecast_assumptions).\n"
        "- Risk & Audit must challenge controls/policy/provenance gaps (challenge_type=controls_policy).\n"
        "- Procurement must challenge vendor terms (challenge_type=vendor_terms).\n"
        "- CFO must ask one synthesis question (challenge_type=synthesis_question).\n"
        "- Reliability does not participate; it audits debate quality after the fact.\n"
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
            "challenge_type": finding.get("challenge_type"),
            "challenge_label": finding.get("challenge_label"),
            "challenge_lens": finding.get("challenge_lens"),
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
        f"{json.dumps([{'from': t.get('from_role'), 'to': t.get('to_role'), 'challenge_type': t.get('challenge_type'), 'challenge_label': t.get('challenge_label'), 'point': t.get('point')} for t in debate_turns], default=str)}\n\n"
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
    follow_up_questions = (decision_plan or {}).get("follow_up_questions") or []
    human = (
        f"DECISION:\n{decision}\n\n"
        f"COMPANY CONTEXT:\n{json.dumps(context, default=str)[:12000]}\n\n"
        f"COUNCIL INFLUENCE WEIGHTS (must shape your ruling — not equal voice):\n"
        f"{json.dumps(council_influence or {}, default=str)}\n\n"
        f"POSITIONS:\n"
        f"{json.dumps([{'role': p.get('role'), 'agent': p.get('agent'), 'stance': p.get('stance'), 'headline': p.get('headline'), 'argument': p.get('argument'), 'cited_metrics': p.get('cited_metrics', []), 'influence_weight': next((w.get('influence_weight') for w in influence_weights if w.get('agent_id') == p.get('agent')), None)} for p in positions], default=str)}\n\n"
        f"CROSS-EXAMINATION:\n{json.dumps([{'from': t.get('from_role'), 'to': t.get('to_role'), 'challenge_type': t.get('challenge_type'), 'challenge_label': t.get('challenge_label'), 'challenge_lens': t.get('challenge_lens'), 'point': t.get('point')} for t in debate_turns], default=str)}\n\n"
        f"STATED ASSUMPTIONS (facts that were missing):\n{json.dumps(assumptions, default=str)}\n\n"
        f"FOLLOW-UP QUESTIONS THAT REMAIN OPEN:\n{json.dumps(follow_up_questions, default=str)}\n\n"
        f"UNRESOLVED EVIDENCE GAPS (resolve or explicitly accept; they should lower confidence):\n{json.dumps(gaps, default=str)}\n\n"
        "Return a CFO-chair structured ruling, not another analyst position. The conditions and "
        "assumptions_converted_to_conditions fields must explicitly capture every unresolved assumption "
        "you are willing to proceed under. The analyst_influence field must include all four analysts "
        "with their weights and how each moved the ruling. Populate policy_citations with every concrete "
        "policy_id/source_id you relied on (gov-*, pol-*, or BP-*); use an empty list only if no governance "
        "or board policy evidence is relevant."
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
        f"CROSS-EXAMINATION:\n{json.dumps([{'from': t.get('from_role'), 'to': t.get('to_role'), 'challenge_type': t.get('challenge_type'), 'challenge_label': t.get('challenge_label'), 'challenge_lens': t.get('challenge_lens'), 'point': t.get('point')} for t in debate_turns], default=str)}\n\n"
        f"CHALLENGE PANEL:\n{json.dumps(challenge_report or {}, default=str)}\n\n"
        f"CFO RECOMMENDATION:\n{json.dumps(recommendation or {}, default=str)}\n\n"
        f"TRACE SUMMARY:\n{json.dumps(trace_summary or {}, default=str)}\n\n"
        "EVALUATOR OUTPUT CONTRACT:\n"
        "- audit_scope must state that Reliability is auditing the council after the CFO ruling, not taking a decision stance.\n"
        "- normal_decision_prohibited must be true.\n"
        "- Do not output stance, decision, ruling, approve, reject, conditional approval, or deferral as Reliability's view.\n"
        "- Produce one scorecard row for each of cfo, treasury, fpna, risk, procurement.\n"
        "- For each score include known_weaknesses, replay_cases, prompt_adjustment, prompt_improvement_directive, and promotion_gate.\n"
        "- Audit whether debate_value included role-specific challenge coverage: cash_timing, forecast_assumptions, controls_policy, vendor_terms, and CFO synthesis questions.\n"
        "- Global replay_plan and prompt_improvement_directives must be usable by the self-improvement loop."
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
    """Backward-compatible alias for :func:`spawn_replacement_agent`."""
    return await spawn_replacement_agent(
        company=company,
        agent_id=agent_id,
        persona_label=persona_label,
        reliability_score=reliability_score,
        retired_directive=prior_directive,
        retired_generation=round_no - 1,
        decision=decision,
        round_no=round_no,
        config=config,
    )


async def spawn_replacement_agent(
    *,
    company: str,
    agent_id: str,
    persona_label: str,
    reliability_score: dict,
    retired_directive: str,
    retired_generation: int,
    decision: str,
    round_no: int,
    config: RunnableConfig | None = None,
) -> StructuredResult:
    """Spawn a brand-new sub-agent incarnation from the retired seat's Weave trace.

    Live OpenAI call grounded ONLY in the retired agent's W&B Weave reliability
    score (lowest dimensions, known weaknesses, replay cases, prompt-improvement directive). The result
    defines the replacement's mandate emphasis and standing directive for the next
    round — the old incarnation is retired, not patched in place.
    """
    system = (
        f"You are the council evolution engine for {company}'s AI finance department. After the CFO "
        "rules, the Reliability Auditor scores every agent against a W&B Weave rubric (evidence_grounding, "
        "forecast_calibration, policy_compliance, debate_value, outcome_accuracy, confidence_calibration, "
        "trace_quality). The single weakest sub-agent is RETIRED and replaced with a brand-new incarnation "
        "in the same role slot. Your job is to define that replacement: sharpen its mandate emphasis and "
        "write a standing directive grafted onto its system prompt next round. Learn ONLY from the "
        "retired incarnation's Weave trace — target its lowest-scoring dimensions, replay cases, "
        "prompt-improvement directive, and documented weaknesses. "
        "Be concrete and operational; never invent facts or external data."
    )
    human = (
        f"ROLE SLOT TO REPLACE: {agent_id} ({persona_label})\n"
        f"ROUND: {round_no}\n"
        f"RETIRED GENERATION: {max(0, retired_generation)}\n\n"
        f"MOST RECENT DECISION:\n{decision}\n\n"
        f"RETIRED INCARNATION W&B WEAVE RELIABILITY TRACE (ground the replacement in this evidence only):\n"
        f"{json.dumps(reliability_score, default=str)}\n\n"
        f"RETIRED INCARNATION STANDING DIRECTIVE (do not copy blindly — learn from its failures):\n"
        f"{retired_directive or '(first incarnation — no prior directive)'}\n\n"
        "Produce a replacement_rationale, mandate_emphasis, and a new standing directive for the fresh "
        "incarnation. The directive must answer the auditor's prompt_improvement_directive and be testable "
        "against the replay_cases. Name the single reliability dimension it must lift first."
    )
    return await structured_call(
        node="self_improvement",
        role=agent_id,
        schema=AgentReplacement,
        system=system,
        human=human,
        config=config,
        temperature=0.3,
        reasoning_effort=LLM_DEBATE_REASONING_EFFORT,
    )
