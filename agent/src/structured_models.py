"""
Typed prompt/response models for the Atlas finance council.

Every structured model the OpenAI council emits lives here so the contract
between the model and the graph is explicit, validated by Pydantic, and easy to
mirror into the frontend TypeScript (`frontend/src/lib/types.ts`). These models
are the *only* place the shape of a council utterance is defined — the agents
return reliable JSON via ``ChatOpenAI.with_structured_output(...)`` against them.

Grouped by phase:
  • Planning / classification ... DecisionType, DecisionPlan, RoleEvidencePlan
  • Analyst positions ........... Position (with cited_metrics + evidence_used)
  • Cross-examination ........... Exchange, Rebuttals
  • Evidence challenge panel .... ChallengeFinding, ChallengePanelReport
  • CFO synthesis ............... Recommendation, BoardMemo, OperatorAction
  • Council influence ........... AgentInfluence, CouncilInfluenceReport
  • Reliability / self-improve .. ReliabilityScore, ReliabilityReport
  • Prompt promotion gates ...... PromptVersion
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class StrictStructuredModel(BaseModel):
    """OpenAI strict structured outputs require closed, fully-required schemas."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# Decision typing — the council now reasons about *what kind* of decision it is
# --------------------------------------------------------------------------- #
class DecisionType(str, Enum):
    """Operating-committee decision categories the council can handle."""

    vendor_renewal = "vendor_renewal"
    hiring_plan = "hiring_plan"
    capital_allocation = "capital_allocation"
    security_blocker = "security_blocker"
    pricing_change = "pricing_change"
    financing_scenario = "financing_scenario"
    general = "general"


class RequiredFact(StrictStructuredModel):
    """A fact the council needs to decide responsibly, and whether we have it."""

    name: str = Field(description="short name of the required fact, e.g. 'annual contract cost'")
    why: str = Field(description="why this fact is required for a sound decision")
    available: bool = Field(description="true if the fact is present in the supplied company context")
    source: str = Field(description="context key or tool that supplies it; empty if missing")


class FollowUpQuestion(StrictStructuredModel):
    """A clarifying question surfaced to the operator via AG-UI state."""

    question: str = Field(description="the specific clarifying question to ask the operator")
    fact: str = Field(description="which required fact this resolves")
    blocking: bool = Field(description="true if the decision cannot be sound without it")


class RoleEvidencePlan(StrictStructuredModel):
    """What a single council role should pull from Redis before it speaks."""

    role: str = Field(description="one of: treasury, fpna, risk, procurement")
    tools: list[str] = Field(
        description=(
            "Redis-backed tools to call, from: get_company_financials, compute_runway, list_vendors, "
            "search_finance_policies, list_operations_sources, get_reconciliation_summary, "
            "list_open_discrepancies, build_strategic_plan, compare_finance_playbooks, "
            "run_plan_stress_test, run_plan_sensitivity, list_invoices, list_customer_contracts, "
            "list_arr_movements, list_purchase_orders, search_scenarios, search_finance_knowledge, "
            "required_approvals, check_controls, missing_evidence, obligations_if_approved, "
            "get_operations_data_confidence, search_uploaded_documents"
        ),
    )
    policy_queries: list[str] = Field(description="semantic RAG queries to run against finance policies & precedent (max 2)")
    document_queries: list[str] = Field(
        default_factory=list,
        description="semantic queries for uploaded document chunk retrieval (max 2 executed)",
    )
    document_source_categories: list[str] = Field(
        default_factory=list,
        description=(
            "uploaded document source categories to filter, e.g. vendor_contract, invoice, procurement_note, "
            "headcount_sheet, board_approval, security_evidence, financing_memo"
        ),
    )
    document_kinds: list[str] = Field(
        default_factory=list,
        description="uploaded document kinds to filter: pdf, docx, csv, xlsx, txt, md, json, jsonl",
    )
    document_rationale: str = Field(
        default="",
        description="why uploaded documents matter for this role on this decision",
    )
    focus_slices: list[str] = Field(
        description=(
            "company-context slice names to concentrate on, e.g. cash_forecast, cash_history, opex_monthly, "
            "last_raise, board_constraints, vendors, pipeline_by_stage, customer_cohorts, security_incidents, "
            "audit_findings, decision_outcomes, hiring_plan. Treasury should prefer cash_forecast, cash_history, "
            "opex_monthly, last_raise, board_constraints, vendors, headcount_start_dates, fully_loaded_hiring_cash, "
            "and contractor_cash_timing. FP&A should prefer forecast_assumptions, "
            "pipeline_by_stage, pipeline_quality, slipped_close_dates, stage_aging, stale_opportunities, "
            "probability_overrides, renewal_vs_new_business, weighted_unweighted_arr_gap, customer_cohorts, "
            "arr_movements, customer_contracts, scenario_math, hiring_plan, headcount_plan_quality, recruiting_slippage, "
            "hiring_start_timing, fully_loaded_role_cost, plan_vs_actual_hiring_drift, plan_vs_actual_deltas, decision_outcomes, and hiring_plan. Risk & Audit should prefer "
            "board_constraints, policies, governance_rules, approval_matrix, security_incidents, audit_findings, "
            "reconciliation_discrepancies, operations_sources, source_provenance, data_quality, and "
            "forecast_assumptions to challenge, plus headcount_approval_status, partial_headcount_approvals, "
            "unplanned_headcount, contractor_approvals, and department_mapping_drift for hiring decisions. Procurement should prefer vendors, vendor_exports, invoices, "
            "purchase_orders, contract_metadata, procurement_notes, prior_renewal_outcomes, vendor_clauses, "
            "price_benchmarks, volume_discounts, switching_costs, slas, and termination_clauses."
        ),
    )
    prior_decisions: list[str] = Field(description="prior decision ids/titles whose outcomes are most relevant")
    rationale: str = Field(description="one sentence on why this evidence matters for this role")


class DecisionPlan(StrictStructuredModel):
    """Output of the planning phase: classify the decision and route evidence."""

    decision_type: DecisionType = Field(description="the operating-committee category for this decision")
    title: str = Field(description="a short, normalized title for the decision (<= 12 words)")
    summary: str = Field(description="1-2 sentence neutral restatement of what is being decided")
    entities: list[str] = Field(description="concrete entities referenced: vendor names, team names, dollar amounts, dates")
    required_facts: list[RequiredFact] = Field(description="facts the council needs to decide responsibly")
    assumptions: list[str] = Field(description="explicit assumptions to proceed with when a required fact is missing")
    follow_up_questions: list[FollowUpQuestion] = Field(description="clarifying questions for missing or uncertain facts")
    role_plans: list[RoleEvidencePlan] = Field(description="evidence plans for each council role")
    decision_specific_focus: list[str] = Field(description="2-4 bullets the whole committee should keep front-of-mind for this decision type")


# --------------------------------------------------------------------------- #
# Analyst positions
# --------------------------------------------------------------------------- #
class Position(StrictStructuredModel):
    role_specific_lens: str = Field(
        description=(
            "one sentence naming this analyst's functional lens and boundary, "
            "e.g. Treasury only on liquidity/runway, not the final CFO ruling"
        )
    )
    stance: str = Field(description="one of: support, oppose, conditional")
    headline: str = Field(description="one-line position, <= 10 words")
    argument: str = Field(description="1-2 short sentences citing specific figures")
    key_points: list[str] = Field(description="exactly 2 crisp bullets")
    cited_metrics: list[str] = Field(
        description=(
            "the concrete numbers cited, each as a short string. Treasury examples: '$410K net burn', "
            "'10.2 mo runway', '$28K invoice due 2026-06-30', '45-day renewal notice', '$5M bridge close delay'. "
            "FP&A examples: '$1.7M weighted pipeline ARR', '$7.6M unweighted pipeline ARR', "
            "'6 slipped close dates', '8 aged-stage opportunities', '3 slipped hiring starts', '$263K/mo fully loaded hiring cost', "
            "'42% proposal conversion', '78% gross margin', '6.5 mo CAC payback', 'ARR +$420K base / +$180K downside'. Risk examples: 'AUD-21 high severity', "
            "'8-12 pt forecast overstatement', '3 owner attestations missing', '$310K security-blocked ARR risk'. "
            "'3 partial headcount approvals', '2 unplanned contractor seats'. "
            "Procurement examples: '$180K annual contract', '45-day termination notice', '$70K switching cost', "
            "'22% committed-use discount', '3 vendors eligible for consolidation'."
        )
    )
    evidence_used: list[str] = Field(description="which policies, prior decisions, or context slices grounded this position")
    forecast_assumptions: list[str] = Field(
        description=(
            "forecast/unit-economics assumptions used. FP&A must include conversion probability, pipeline quality "
            "(such as slipped close dates, stage aging, stale opportunities, probability overrides, or renewal/new/expansion mix), hiring plan quality "
            "(such as recruiting slippage, start-date capacity timing, fully loaded role cost, contractor/backfill mix, or plan-vs-actual hiring drift), ARR movement, "
            "ROI, CAC/payback, margin, sensitivity range, scenario math, or plan-vs-actual assumptions; "
            "other roles may state [] unless the assumption is inside their lane."
        )
    )
    scenario_sensitivities: list[str] = Field(
        description=(
            "quantified sensitivity ranges or scenario math. FP&A must include at least one relevant range, "
            "e.g. 'if proposal conversion falls from 42% to 30%, ARR lands $180K lower'; other roles may state []."
        )
    )
    plan_vs_actual_deltas: list[str] = Field(
        description=(
            "plan-vs-actual variances cited. FP&A should populate when forecast quality depends on budget, "
            "ARR, pipeline, margin, or CAC/payback deltas; other roles may state []."
        )
    )
    control_findings: list[str] = Field(
        description=(
            "policy violations, control gaps, audit trail gaps, fraud/error risks, data-quality concerns, "
            "or compliance blockers. Risk & Audit must populate; other roles may state []."
        )
    )
    missing_evidence_requests: list[str] = Field(
        description=(
            "specific evidence Risk & Audit needs before support, e.g. approval record, source provenance, "
            "security sign-off, reconciliation detail, DPA, or board exception. Other roles may state []."
        )
    )
    approval_or_policy_blockers: list[str] = Field(
        description=(
            "approval gates, board policies, governance rules, hidden obligations, or exception paths that "
            "condition or block the decision. Risk & Audit must populate when any exists; other roles may state []."
        )
    )
    negotiation_levers: list[str] = Field(
        description=(
            "supplier leverage, contract terms, renewal timing, auto-renewal/termination clauses, price benchmarks, "
            "consolidation options, switching costs, SLAs, volume discounts, or negotiation strategy. Procurement "
            "must populate; other roles may state []."
        )
    )


# --------------------------------------------------------------------------- #
# Cross-examination
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Evidence challenge panel — verifies each role cited enough concrete numbers
# --------------------------------------------------------------------------- #
class ChallengeFinding(StrictStructuredModel):
    role: str = Field(description="one of: treasury, fpna, risk, procurement")
    challenge_type: str = Field(
        description="role-specific weakness category: cash_timing, forecast_assumptions, controls_policy, or vendor_terms"
    )
    challenge_label: str = Field(description="short display label for the role-specific challenge, <= 4 words")
    challenge_lens: str = Field(description="what this role's challenge should test in debate")
    cited_enough_numbers: bool = Field(description="true if the position is sufficiently grounded in concrete figures")
    grounding_score: int = Field(ge=0, le=100, description="how well-grounded in real numbers this position is")
    strongest_number: str = Field(description="the single most decision-relevant figure this role cited")
    missing_evidence: list[str] = Field(description="specific figures or facts the role should have cited but did not")
    challenge: str = Field(description="the sharp follow-up the committee should put to this role")


class ChallengePanelReport(StrictStructuredModel):
    summary: str = Field(description="board-ready summary of how well-grounded the council's analysis is")
    overall_grounding: int = Field(ge=0, le=100, description="weighted overall grounding score for the council")
    findings: list[ChallengeFinding] = Field(description="per-role grounding findings")
    unresolved_gaps: list[str] = Field(description="evidence gaps the CFO must resolve or explicitly accept before deciding")


# --------------------------------------------------------------------------- #
# CFO synthesis
# --------------------------------------------------------------------------- #
class AnalystInfluenceView(StrictStructuredModel):
    """How one analyst's input affected the CFO chair's final ruling."""

    role: str = Field(description="one of: treasury, fpna, risk, procurement")
    influence_weight: int = Field(ge=0, le=100, description="the analyst's deliberation weight used by the CFO")
    effect_on_ruling: str = Field(description="how this role moved, constrained, or failed to move the CFO ruling")


class Recommendation(StrictStructuredModel):
    decision: str = Field(description="one of: APPROVE, REJECT, CONDITIONAL, DEFER")
    ruling: str = Field(description="one board-ready sentence stating the CFO chair's final ruling and why")
    confidence: int = Field(ge=0, le=100)
    rationale: str = Field(description="3-5 sentences, decisive and quantified; written as the CFO chair resolving the committee, not as an analyst")
    tradeoffs: list[str] = Field(description="2-4 explicit tradeoffs the CFO chair weighed across growth, runway, risk, and execution")
    analyst_influence: list[AnalystInfluenceView] = Field(
        description="how the CFO weighed treasury, fpna, risk, and procurement inputs; include all four roles"
    )
    dissent: str = Field(description="the strongest dissenting analyst view and why the CFO accepted, overrode, or conditioned it")
    key_risks: list[str] = Field(description="key risks to monitor")
    conditions: list[str] = Field(description="explicit conditions/guardrails for the ruling; convert unresolved assumptions into conditions")
    policy_citations: list[str] = Field(
        description="concrete board/governance policy IDs cited in the ruling, e.g. gov-runway-floor, gov-board-notify, pol-hiring"
    )
    assumptions_converted_to_conditions: list[str] = Field(
        description="missing facts or unresolved assumptions that the CFO converted into explicit conditions"
    )
    runway_impact_basis: str = Field(
        description=(
            "quantified cost/revenue levers used for runway computation, e.g. '$80K monthly spend, "
            "$120K one-time cost, $35K added monthly revenue'; do not invent current-vs-after runway months"
        )
    )
    estimated_monthly_cost: float = Field(description="incremental recurring monthly cost; 0 if none")
    estimated_one_time_cost: float = Field(description="upfront one-time cost; 0 if none")
    estimated_added_monthly_revenue: float = Field(description="incremental monthly revenue; 0 if none")


class OperatorAction(StrictStructuredModel):
    """A single line item in the operator action checklist."""

    owner: str = Field(description="function/role accountable, e.g. Treasury, FP&A, Procurement, CFO")
    action: str = Field(description="the concrete next step to execute")
    due: str = Field(description="relative timeframe or date, e.g. 'within 7 days', '2026-07-15'")
    priority: str = Field(description="one of: high, medium, low")
    depends_on: str = Field(description="prerequisite action or fact, if any")


class BoardMemo(StrictStructuredModel):
    """A board-ready memo plus operator action checklist, grounded in computed numbers."""

    title: str = Field(description="memo title, e.g. 'Datadog renewal — board recommendation'")
    decision_type: str = Field(description="the decision category this memo covers")
    headline: str = Field(description="one-line decision + confidence, e.g. 'CONDITIONAL approve at 72% confidence'")
    context: str = Field(description="2-3 sentences of situational context grounded in the company's real position")
    recommendation: str = Field(description="the decisive recommendation in prose, quantified")
    key_figures: list[str] = Field(description="the concrete numbers a board would want: runway today vs after, cost, payback, margin impact")
    risks: list[str] = Field(description="risks the board should understand")
    conditions: list[str] = Field(description="conditions or guardrails")
    operator_actions: list[OperatorAction] = Field(description="operator action checklist")
    financing_or_next_steps: list[str] = Field(description="financing implications or sequencing the operator must plan for")
    dissent: str = Field(description="the strongest dissenting view from the committee, noted honestly")


# --------------------------------------------------------------------------- #
# Council influence — unequal deliberation weights assigned before CFO synthesis
# --------------------------------------------------------------------------- #
class AgentInfluence(StrictStructuredModel):
    agent_id: str = Field(description="one of: treasury, fpna, risk, procurement")
    influence_weight: int = Field(
        ge=0,
        le=100,
        description="share of CFO deliberation weight among analysts; all four analysts must sum to 100",
    )
    grounding_signal: int = Field(ge=0, le=100, description="how well-grounded this role was in this debate")
    debate_signal: int = Field(ge=0, le=100, description="how valuable this role was in cross-examination")
    historical_reliability: int = Field(ge=0, le=100, description="rolling reliability prior from prior council runs")
    rationale: str = Field(description="why this agent earned this influence share on this decision")


class CouncilInfluenceReport(StrictStructuredModel):
    summary: str = Field(description="board-ready summary of who earned the most influence and why")
    weights: list[AgentInfluence] = Field(description="per-analyst influence weights that sum to 100")
    decision_type_fit: str = Field(
        description="which roles were most relevant for this decision type and how that shaped the weights",
    )


# --------------------------------------------------------------------------- #
# Reliability / self-improvement (W&B Weave replay evals)
# --------------------------------------------------------------------------- #
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


class AgentImprovement(StrictStructuredModel):
    """A rewritten standing directive for the least-reliable sub-agent.

    Produced by the self-improvement engine after the CFO rules and the
    Reliability Auditor (W&B Weave) scores the council. It is grafted onto the
    targeted analyst's system prompt for the *next* round so its reliability can
    climb over successive decisions.
    """

    agent_id: str = Field(description="the sub-agent being improved: one of treasury, fpna, risk, procurement")
    focus: str = Field(description="the single biggest weakness this revision targets, <= 12 words")
    directive: str = Field(
        description=(
            "a concise standing instruction (<= 3 sentences) to graft onto this agent's system "
            "prompt next round, derived strictly from its W&B Weave reliability trace; concrete, "
            "operational, and quantified where possible — no fluff and no invented facts"
        )
    )
    targeted_dimension: str = Field(
        description="the lowest-scoring reliability dimension this directive should lift (e.g. evidence_grounding)"
    )
    expected_gain: str = Field(description="what should measurably improve next round and why")


class AgentReplacement(StrictStructuredModel):
    """A brand-new sub-agent incarnation spawned after the weakest seat is retired.

    Produced after the CFO rules and the Reliability Auditor scores the council
    against the W&B Weave rubric. The retired incarnation's trace is the only
    grounding for the replacement — mandate emphasis and standing directive must
    address its documented weaknesses.
    """

    agent_id: str = Field(description="role slot being replaced: one of treasury, fpna, risk, procurement")
    focus: str = Field(description="the single biggest weakness the retired agent failed on, <= 12 words")
    replacement_rationale: str = Field(
        description="one sentence on why the retired incarnation is being replaced (Weave evidence only)"
    )
    mandate_emphasis: str = Field(
        description="how the new incarnation should sharpen its professional mandate (<= 2 sentences)"
    )
    directive: str = Field(
        description=(
            "standing instruction (<= 3 sentences) for the NEW incarnation's system prompt, derived "
            "strictly from the retired agent's W&B Weave reliability trace; concrete and operational"
        )
    )
    targeted_dimension: str = Field(
        description="the lowest-scoring reliability dimension the replacement must lift (e.g. evidence_grounding)"
    )
    expected_gain: str = Field(description="what the new incarnation should measurably improve next round and why")


# --------------------------------------------------------------------------- #
# Prompt-version metadata — compatible with the W&B promotion gates
# --------------------------------------------------------------------------- #
class PromptVersion(StrictStructuredModel):
    """Versioned-prompt provenance streamed so W&B replay evals can gate promotion."""

    role: str = Field(description="council role / phase the prompt belongs to")
    agent: str = Field(default="", description="backward-compatible alias for role when this prompt belongs to a council seat")
    current: str = Field(default="", description="current active prompt version label")
    version: str = Field(description="human-readable prompt version id, e.g. treasury.v4-evidence-plan")
    prompt_hash: str = Field(description="short sha256 of the active system prompt for drift detection")
    active_prompt_hash: str = Field(default="", description="alias of prompt_hash for UI surfaces")
    candidate: str = Field(default="", description="candidate prompt version under evaluation, if any")
    candidate_prompt_hash: str = Field(default="", description="short sha256 of active prompt plus candidate directive/gate")
    promotion_gate: str = Field(default="", description="condition a candidate must beat to be promoted")
    reliability_dimensions: list[str] = Field(
        default_factory=list,
        description="role-specific reliability dimensions this prompt's promotion gate evaluates",
    )
    gate_metric: str = Field(default="", description="primary role-specific reliability dimension for the gate")
    replay_set: str = Field(default="", description="replay dataset slug used for promotion evaluation")
