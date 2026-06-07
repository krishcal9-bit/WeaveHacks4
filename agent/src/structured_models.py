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
        description="Redis-backed tools to call, from: get_company_financials, compute_runway, list_vendors, search_finance_policies",
    )
    policy_queries: list[str] = Field(description="semantic RAG queries to run against finance policies & precedent (max 2)")
    focus_slices: list[str] = Field(
        description="company-context slice names to concentrate on, e.g. cash_forecast, pipeline_by_stage, customer_cohorts, security_incidents, audit_findings, decision_outcomes, hiring_plan",
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
    stance: str = Field(description="one of: support, oppose, conditional")
    headline: str = Field(description="one-line position, <= 10 words")
    argument: str = Field(description="1-2 short sentences citing specific figures")
    key_points: list[str] = Field(description="exactly 2 crisp bullets")
    cited_metrics: list[str] = Field(description="the concrete numbers cited, each as a short string e.g. '$410K net burn', '10.2 mo runway', '78% gross margin'")
    evidence_used: list[str] = Field(description="which policies, prior decisions, or context slices grounded this position")


# --------------------------------------------------------------------------- #
# Cross-examination
# --------------------------------------------------------------------------- #
class Exchange(StrictStructuredModel):
    from_role: str = Field(description="the function raising the challenge")
    to_role: str = Field(description="the function being challenged")
    point: str = Field(description="a sharp, specific, quantified challenge")


class Rebuttals(StrictStructuredModel):
    exchanges: list[Exchange] = Field(description="cross-examination exchanges")


# --------------------------------------------------------------------------- #
# Evidence challenge panel — verifies each role cited enough concrete numbers
# --------------------------------------------------------------------------- #
class ChallengeFinding(StrictStructuredModel):
    role: str = Field(description="one of: treasury, fpna, risk, procurement")
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
class Recommendation(StrictStructuredModel):
    decision: str = Field(description="one of: APPROVE, REJECT, CONDITIONAL, DEFER")
    confidence: int = Field(ge=0, le=100)
    rationale: str = Field(description="3-5 sentences, decisive and quantified")
    key_risks: list[str] = Field(description="key risks to monitor")
    conditions: list[str] = Field(description="conditions for approval; empty if none")
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
    promotion_gate: str = Field(description="how W&B Weave evals should decide whether this agent improves")


class ReliabilityReport(StrictStructuredModel):
    summary: str = Field(description="board-ready summary of council reliability")
    scores: list[ReliabilityScore] = Field(description="per-agent reliability scores")
    eval_dataset: str = Field(description="W&B/Weave eval dataset or replay-set label")
    replay_plan: list[str] = Field(description="replay cases or eval steps to run")
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


# --------------------------------------------------------------------------- #
# Prompt-version metadata — compatible with the W&B promotion gates
# --------------------------------------------------------------------------- #
class PromptVersion(StrictStructuredModel):
    """Versioned-prompt provenance streamed so W&B replay evals can gate promotion."""

    role: str = Field(description="council role / phase the prompt belongs to")
    version: str = Field(description="human-readable prompt version id, e.g. treasury.v4-evidence-plan")
    prompt_hash: str = Field(description="short sha256 of the active system prompt for drift detection")
    candidate: str = Field(default="", description="candidate prompt version under evaluation, if any")
    promotion_gate: str = Field(default="", description="condition a candidate must beat to be promoted")
