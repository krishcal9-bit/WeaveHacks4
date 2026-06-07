"""
Typed models for the Atlas orchestration engine.

Two families:
  * Strict structured-output models (``StrictModel`` — ``extra="forbid"`` + all
    fields required) used directly as OpenAI ``.with_structured_output`` schemas:
    ``ConductorPlan``, ``RedTeamReport``, ``VoteBallot``.
  * Persistence / streaming records (``OrchModel`` — tolerant, defaulted) stored as
    JSON in the ``atlas:orch:*`` namespace and streamed to the UI: ``Topology``,
    ``DebateRound``, ``OrchestrationTrace``, ``EpisodicMemoryRecord``,
    ``OrchestrationEvalResult`` …

Native 3.12 typing (``list[...]``, ``X | None``) is used so Pydantic v2 resolves
every annotation eagerly with no ``__future__`` forward-ref subtleties. This module
is pure (no Redis/OpenAI/heavy imports) and safe to import + unit-test offline.
"""

import time
import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class StrictModel(BaseModel):
    """Closed schema for OpenAI strict structured outputs (all fields required)."""

    model_config = ConfigDict(extra="forbid")


class OrchModel(BaseModel):
    """Tolerant record model for persistence + streaming (extra keys ignored so
    older JSON in Redis still loads across schema versions)."""

    model_config = ConfigDict(extra="ignore")


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class EdgeKind(str, Enum):
    sequential = "sequential"
    conditional = "conditional"
    parallel = "parallel"
    loop_back = "loop_back"


class NodeKind(str, Enum):
    conductor = "conductor"
    analyst = "analyst"
    specialist = "specialist"
    red_team = "red_team"
    negotiation = "negotiation"
    vote = "vote"
    synthesis = "synthesis"


class Stance(str, Enum):
    support = "support"
    oppose = "oppose"
    conditional = "conditional"
    abstain = "abstain"


class StopReason(str, Enum):
    converged = "converged"
    max_rounds = "max_rounds"
    red_team_satisfied = "red_team_satisfied"
    unresolved = "unresolved"


# --------------------------------------------------------------------------- #
# Conductor plan (OpenAI structured output) — the debate TOPOLOGY per decision
# --------------------------------------------------------------------------- #
class SeatPlan(StrictModel):
    role: str = Field(description="seat id: cfo/treasury/fpna/risk/procurement, or a specialist id (tax/legal/hedging/mna)")
    is_specialist: bool = Field(description="true if seated on demand beyond the base committee")
    rationale: str = Field(description="why this seat is needed for THIS specific decision")


class ConductorPlan(StrictModel):
    topology_name: str = Field(description="short human label for the debate shape, e.g. 'parallel+redteam+vote'")
    decision_type: str = Field(description="classified decision type")
    seats: list[SeatPlan] = Field(description="the seats to convene for this decision (always include cfo)")
    rounds: int = Field(ge=1, le=5, description="max debate rounds before forcing a vote")
    fan_out: bool = Field(description="true to run analysts concurrently in each round")
    allow_loops: bool = Field(description="permit a loop-back round to re-debate after red-team challenges")
    requires_red_team: bool = Field(description="seat a dedicated adversarial red-team that must be satisfied")
    convergence_threshold: float = Field(ge=0.0, le=1.0, description="weighted agreement ratio that ends debate early")
    stop_conditions: list[str] = Field(description="explicit human-readable stop conditions")
    rationale: str = Field(description="why this topology fits the decision (cost/risk/complexity trade-off)")


# --------------------------------------------------------------------------- #
# Red-team (OpenAI structured output) — adversarial seat that must be satisfied
# --------------------------------------------------------------------------- #
class RedTeamChallenge(StrictModel):
    target_role: str = Field(description="the seat being challenged")
    attack: str = Field(description="the strongest specific, quantified objection to that seat's position")
    severity: str = Field(description="one of: low, medium, high")
    must_address: bool = Field(description="true if the CFO cannot rule until this is answered")


class RedTeamReport(StrictModel):
    summary: str = Field(description="one-paragraph adversarial read of the committee's consensus")
    challenges: list[RedTeamChallenge] = Field(description="specific challenges raised against the seats")
    satisfied: bool = Field(description="true only if every must_address challenge has a credible answer")


# --------------------------------------------------------------------------- #
# Voting (OpenAI structured output per seat) — reliability-weighted aggregation
# --------------------------------------------------------------------------- #
class VoteBallot(StrictModel):
    value: str = Field(description="one of: support, oppose, conditional, abstain")
    confidence: int = Field(ge=0, le=100, description="0-100 confidence in this vote")
    rationale: str = Field(description="one-sentence, evidence-grounded reason for the vote")


class SeatPosition(StrictModel):
    """One seat's structured position in a debate round (OpenAI structured output)."""

    stance: str = Field(description="one of: support, oppose, conditional, abstain")
    confidence: int = Field(ge=0, le=100, description="0-100 confidence in the stance")
    headline: str = Field(description="one-line position, <= 14 words")
    argument: str = Field(description="2-4 sentences citing specific figures from the live context")
    cited_metrics: list[str] = Field(description="concrete figures cited from the live context")


class NegotiationOutcome(StrictModel):
    """Result of a structured proposal/counter-proposal between two conflicting seats."""

    proposal: str = Field(description="the concrete proposal from the first seat")
    counter: str = Field(description="the counter-proposal from the second seat")
    resolved: bool = Field(description="true if the two seats reached a workable compromise")
    terms: str = Field(description="the agreed terms, or the remaining gap if unresolved")


# --------------------------------------------------------------------------- #
# Debate dynamics (persistence / streaming records)
# --------------------------------------------------------------------------- #
class RoundStance(OrchModel):
    role: str
    label: str = ""
    stance: Stance = Stance.abstain
    confidence: int = 0
    headline: str = ""
    argument: str = ""
    cited_metrics: list[str] = []
    changed: bool = False  # stance changed vs the previous round


class ConvergenceSignal(OrchModel):
    round_index: int = 0
    agreement_ratio: float = 0.0   # weighted fraction sharing the modal stance
    divergence_score: float = 0.0  # 1 - agreement_ratio
    stance_migrations: int = 0     # seats that changed stance vs the prior round
    confidence_spread: float = 0.0
    converged: bool = False
    rationale: str = ""


class DebateRound(OrchModel):
    index: int = 0
    stances: list[RoundStance] = []
    convergence: ConvergenceSignal | None = None
    notes: str = ""


class Vote(OrchModel):
    role: str
    value: Stance = Stance.abstain
    confidence: int = 0
    weight: float = 1.0   # reliability-derived voting weight
    rationale: str = ""


class MinorityReport(OrchModel):
    role: str
    dissent: str = ""
    weight: float = 0.0
    rationale: str = ""


class VoteTally(OrchModel):
    votes: list[Vote] = []
    weighted_support: float = 0.0
    weighted_oppose: float = 0.0
    weighted_conditional: float = 0.0
    weighted_abstain: float = 0.0
    total_weight: float = 0.0
    decision: str = "DEFER"  # APPROVE / REJECT / CONDITIONAL / DEFER
    margin: float = 0.0
    unanimous: bool = False
    minority_reports: list[MinorityReport] = []


class NegotiationMove(OrchModel):
    from_role: str
    to_role: str
    proposal: str = ""
    counter: str = ""
    resolved: bool = False
    terms: str = ""


# --------------------------------------------------------------------------- #
# Topology (persisted, versioned, searchable) — the graph shape itself
# --------------------------------------------------------------------------- #
class NodeSpec(OrchModel):
    id: str
    kind: NodeKind = NodeKind.analyst
    role: str = ""
    label: str = ""
    mandate: str = ""
    is_specialist: bool = False


class EdgeSpec(OrchModel):
    source: str
    target: str
    kind: EdgeKind = EdgeKind.sequential
    condition: str = ""
    label: str = ""


class Topology(OrchModel):
    id: str = Field(default_factory=lambda: new_id("topo"))
    version: int = 1
    name: str = "linear-committee"
    decision_type: str = "general"
    nodes: list[NodeSpec] = []
    edges: list[EdgeSpec] = []
    max_rounds: int = 2
    convergence_threshold: float = 0.75
    requires_red_team: bool = True
    allow_loops: bool = False
    fan_out: bool = True
    parent_version: int | None = None
    description: str = ""
    created_at: str = Field(default_factory=now_iso)

    def text_blob(self) -> str:
        seats = ", ".join(n.role or n.id for n in self.nodes)
        return f"{self.name} [{self.decision_type}] seats={seats} rounds={self.max_rounds} redteam={self.requires_red_team} :: {self.description}"


# --------------------------------------------------------------------------- #
# Episodic memory — vector recall of prior decisions/outcomes (cited as precedent)
# --------------------------------------------------------------------------- #
class EpisodicMemoryRecord(OrchModel):
    id: str = Field(default_factory=lambda: new_id("mem"))
    company_id: str = "northwind"
    decision: str = ""
    decision_type: str = "general"
    recommendation: str = ""
    outcome: str = ""  # realized outcome, if known
    confidence: int = 0
    key_metrics: list[str] = []
    lessons: list[str] = []
    topology_id: str = ""
    run_id: str = ""
    created_at: str = Field(default_factory=now_iso)

    def embedding_text(self) -> str:
        parts = [
            self.decision,
            f"Recommendation: {self.recommendation}" if self.recommendation else "",
            f"Outcome: {self.outcome}" if self.outcome else "",
            ("Lessons: " + "; ".join(self.lessons)) if self.lessons else "",
            ("Key metrics: " + "; ".join(self.key_metrics)) if self.key_metrics else "",
        ]
        return "\n".join(p for p in parts if p)


# --------------------------------------------------------------------------- #
# Orchestration trace / run record (persisted + streamed)
# --------------------------------------------------------------------------- #
class OrchestrationTrace(OrchModel):
    run_id: str = Field(default_factory=lambda: new_id("run"))
    thread_id: str = ""
    decision: str = ""
    decision_type: str = "general"
    topology_id: str = ""
    topology_version: int = 1
    topology_name: str = ""
    seats: list[str] = []
    rounds: list[DebateRound] = []
    convergence: ConvergenceSignal | None = None
    red_team: RedTeamReport | None = None
    tally: VoteTally | None = None
    negotiations: list = []
    recommendation: dict = {}
    precedents: list[str] = []  # episodic memory ids cited as precedent
    stop_reason: StopReason = StopReason.max_rounds
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    model: str = ""
    weave_url: str = ""
    checkpoints: list[str] = []
    created_at: str = Field(default_factory=now_iso)


# --------------------------------------------------------------------------- #
# Eval — the orchestration TOPOLOGY is the evaluatable unit
# --------------------------------------------------------------------------- #
class TopologyScore(OrchModel):
    topology_id: str = ""
    version: int = 1
    name: str = ""
    decision_quality: float = 0.0
    grounding: float = 0.0
    convergence_speed: float = 0.0
    cost_score: float = 0.0
    latency_score: float = 0.0
    overall: float = 0.0
    samples: int = 0
    rationale: str = ""


class OrchestrationEvalResult(OrchModel):
    eval_id: str = Field(default_factory=lambda: new_id("eval"))
    dataset: str = ""
    scores: list[TopologyScore] = []
    incumbent: str = ""
    challenger: str = ""
    winner: str = ""
    promoted: bool = False
    gate_rationale: str = ""
    weave_url: str = ""
    created_at: str = Field(default_factory=now_iso)


# --------------------------------------------------------------------------- #
# Hierarchical orchestration — decompose a decision into sub-debates
# --------------------------------------------------------------------------- #
class Decomposition(StrictModel):
    """OpenAI structured output: how to split a complex decision into sub-decisions."""

    sub_questions: list[str] = Field(
        description="2-4 focused, independently-analyzable sub-decisions whose answers together determine the parent decision"
    )
    rationale: str = Field(description="why this decomposition fully covers the parent decision")


class HierarchicalTrace(OrchModel):
    run_id: str = Field(default_factory=lambda: new_id("hrun"))
    parent_decision: str = ""
    decision_type: str = "hierarchical"
    sub_questions: list[str] = []
    sub_run_ids: list[str] = []
    sub_rulings: list[dict] = []
    parent_recommendation: dict = {}
    cost_usd: float = 0.0
    latency_ms: int = 0
    created_at: str = Field(default_factory=now_iso)
