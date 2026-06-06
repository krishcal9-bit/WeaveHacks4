"""
Atlas — W&B Weave evaluation layer.

Captures every completed finance-council run as a durable, queryable **EvalPacket**
and scores the run against explicit rubrics via nested ``@weave.op`` child spans.
Non-secret eval metadata is persisted under the dedicated ``atlas:evaluation``
Redis namespace and mirrored to an append-only Redis Stream.

This is a **live** integration, not a mock:

- Weave is initialized in ``main.py`` *before* ``src.agent`` is imported, so the
  ``@weave.op`` spans here nest inside the existing node spans
  (``intake`` → ``analyst_*`` → ``debate_round`` → ``cfo_synthesis`` →
  ``reliability_auditor`` → ``persist_decision``). The eval scorers therefore show
  up as real child spans of ``reliability_auditor`` in the Weave trace tree.
- Eval objects are published to Weave (``weave.publish``) so they are queryable in
  the W&B UI. All Weave links come from :func:`src.health.weave_status` and are
  redacted via :func:`src.env.redact_secrets`; no secret is ever printed.

The rubric here scores *run / trace quality* (did the council actually ground its
reasoning, debate, quantify, and stay observable). It is complementary to the
per-agent reliability scorecard the LLM auditor produces in ``src.agent``.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

import weave
from pydantic import BaseModel, Field

from src import redis_layer as R
from src.env import redact_secrets
from src.health import weave_status

# --------------------------------------------------------------------------- #
# Namespacing — everything lives under atlas:evaluation:*
# --------------------------------------------------------------------------- #
EVAL_NS = f"{R.NS}:evaluation"
PACKET_PREFIX = f"{EVAL_NS}:packet:"
PACKET_INDEX = f"{EVAL_NS}:packets"        # Redis list of packet ids (recent last)
LATEST_PACKET = f"{EVAL_NS}:latest"
PACKET_STREAM = "eval_packets"             # → atlas:stream:eval_packets

EXPECTED_AGENTS = ("cfo", "treasury", "fpna", "risk", "procurement")

# Run-quality rubric dimensions (weights sum to 1.0). These measure how well a
# completed council run grounded, debated, quantified, and stayed observable.
RUBRIC: dict[str, dict[str, Any]] = {
    "context_retrieval": {"label": "Context retrieval", "weight": 0.15, "threshold": 60},
    "policy_grounding": {"label": "Policy grounding", "weight": 0.20, "threshold": 60},
    "debate_quality": {"label": "Debate quality", "weight": 0.15, "threshold": 55},
    "cfo_synthesis": {"label": "CFO synthesis", "weight": 0.25, "threshold": 65},
    "reliability_scoring": {"label": "Reliability scoring", "weight": 0.15, "threshold": 60},
    "persistence": {"label": "Persistence & observability", "weight": 0.10, "threshold": 70},
}

_FIGURE_RE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?|\b\d+(?:\.\d+)?\s?%|\b\d+(?:\.\d+)?\s?(?:months?|mo|x|weeks?|days?)\b",
    re.IGNORECASE,
)
_POLICY_TERMS = (
    "runway", "threshold", "approval", "board", "policy", "compliance", "covenant",
    "constraint", "guardrail", "payback", "retention", "ndr", "churn", "margin",
    "burn", "renewal", "audit", "control", "soc 2", "soc2",
)


# --------------------------------------------------------------------------- #
# Stable Pydantic models
# --------------------------------------------------------------------------- #
class RubricScore(BaseModel):
    """One scored dimension of a council run's trace/reasoning quality."""

    dimension: str
    label: str
    score: int = Field(ge=0, le=100)
    weight: float = 0.0
    threshold: int = 60
    passed: bool = True
    evidence: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)


class TraceQualityIssue(BaseModel):
    """A specific, actionable defect found while scoring a run's trace."""

    id: str
    node: str
    severity: str = Field(description="low | medium | high")
    summary: str
    recommendation: str


class EvalPacket(BaseModel):
    """A durable, queryable evaluation record for one completed council run."""

    id: str
    created_at: str
    created_ts: float
    source: str = Field(description="live | history | replay | seed")
    decision: str
    decision_label: str
    recommendation: dict[str, Any] = Field(default_factory=dict)
    company: str = "Acme Corp"
    model: str = ""
    weave: dict[str, Any] = Field(default_factory=dict)
    rubric_scores: list[RubricScore] = Field(default_factory=list)
    overall_score: int = 0
    reliability_scores: list[dict[str, Any]] = Field(default_factory=list)
    council_average: int = 0
    trace_quality_issues: list[TraceQualityIssue] = Field(default_factory=list)
    replay_set: str | None = None
    prompt_versions: list[dict[str, Any]] = Field(default_factory=list)
    learning_report: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _new_id(prefix: str = "eval") -> str:
    return f"{prefix}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"


def _text_of(turn: dict) -> str:
    return " ".join(
        str(turn.get(k, ""))
        for k in ("headline", "argument", "point")
    ) + " " + " ".join(str(p) for p in (turn.get("key_points") or []))


def _count_figures(text: str) -> int:
    return len(_FIGURE_RE.findall(text or ""))


def _policy_hits(text: str) -> int:
    lowered = (text or "").lower()
    return sum(1 for term in _POLICY_TERMS if term in lowered)


def _clamp(value: float) -> int:
    return max(0, min(100, round(value)))


def _rubric(dimension: str, score: int, evidence: str, metrics: dict[str, Any]) -> RubricScore:
    spec = RUBRIC[dimension]
    return RubricScore(
        dimension=dimension,
        label=spec["label"],
        score=_clamp(score),
        weight=spec["weight"],
        threshold=spec["threshold"],
        passed=_clamp(score) >= spec["threshold"],
        evidence=evidence,
        metrics=metrics,
    )


def weave_links() -> dict[str, Any]:
    """Redacted Weave project metadata for embedding in eval packets/links."""
    status = weave_status()
    return {
        "initialized": bool(status.get("initialized")),
        "project": status.get("project"),
        "entity": status.get("entity"),
        "url": status.get("url"),
    }


def publish_to_weave(obj: Any, name: str) -> dict[str, Any]:
    """Publish a versioned object to Weave; return only redacted, non-secret link metadata.

    Failures are surfaced (redacted), never silently mocked — the live-only
    contract forbids fabricating Weave references.
    """
    info: dict[str, Any] = {"name": name, "published": False}
    try:
        ref = weave.publish(obj, name=name)
    except Exception as exc:  # network/auth/init issues are reported, not faked
        info["error"] = redact_secrets(exc)
        return info
    info["published"] = True
    try:
        info["uri"] = ref.uri()
    except Exception:
        pass
    entity = getattr(ref, "entity", None)
    project = getattr(ref, "project", None)
    obj_name = getattr(ref, "name", None) or name
    digest = getattr(ref, "digest", None) or getattr(ref, "_digest", None)
    info["digest"] = digest
    try:
        from weave.trace import urls as weave_urls

        if entity and project and digest:
            path = weave_urls.object_version_path(entity, project, obj_name, str(digest))
            info["url"] = f"https://wandb.ai{path}" if path.startswith("/") else path
    except Exception:
        # Fall back to the project-level weave URL (always valid for judges).
        info.setdefault("url", weave_links().get("url"))
    info.setdefault("url", weave_links().get("url"))
    return info


# --------------------------------------------------------------------------- #
# Rubric scorers — each is a @weave.op child span, deterministic & evidence-based
# --------------------------------------------------------------------------- #
@weave.op(name="eval.context_retrieval")
def score_context_retrieval(context: dict) -> RubricScore:
    financials = (context or {}).get("financials") or {}
    vendors = (context or {}).get("vendors") or []
    policies = (context or {}).get("policies") or []
    have_core = sum(
        1 for k in ("cash_on_hand", "monthly_net_burn", "runway_months", "mrr", "arr")
        if financials.get(k) is not None
    )
    n_vendors = len(vendors)
    n_policies = len(policies)
    score = 0
    score += 35 if have_core >= 4 else have_core * 8
    score += min(30, n_vendors * 5)
    score += min(35, n_policies * 12)
    evidence = (
        f"Loaded {have_core}/5 core financial fields, {n_vendors} vendor records, "
        f"{n_policies} policy/precedent RAG hits."
    )
    return _rubric(
        "context_retrieval",
        score,
        evidence,
        {"core_financials": have_core, "vendors": n_vendors, "policy_hits": n_policies},
    )


@weave.op(name="eval.policy_grounding")
def score_policy_grounding(positions: list, recommendation: dict) -> RubricScore:
    blob = " ".join(_text_of(p) for p in (positions or []))
    blob += " " + str((recommendation or {}).get("rationale", ""))
    figures = _count_figures(blob)
    policy_hits = _policy_hits(blob)
    cited_positions = sum(1 for p in (positions or []) if _count_figures(_text_of(p)) >= 1)
    score = min(60, figures * 6) + min(25, policy_hits * 5) + min(15, cited_positions * 4)
    evidence = (
        f"{figures} quantified figures, {policy_hits} policy/precedent references, "
        f"{cited_positions}/{len(positions or [])} positions cite concrete numbers."
    )
    return _rubric(
        "policy_grounding",
        score,
        evidence,
        {"figures": figures, "policy_terms": policy_hits, "cited_positions": cited_positions},
    )


@weave.op(name="eval.debate_quality")
def score_debate_quality(transcript: list) -> RubricScore:
    rebuttals = [t for t in (transcript or []) if t.get("type") == "rebuttal"]
    pairs = {(t.get("from_role"), t.get("to_role")) for t in rebuttals}
    quantified = sum(1 for t in rebuttals if _count_figures(t.get("point", "")) >= 1)
    score = min(45, len(rebuttals) * 12) + min(30, len(pairs) * 10) + min(25, quantified * 9)
    evidence = (
        f"{len(rebuttals)} cross-examination exchanges across {len(pairs)} distinct role pairs, "
        f"{quantified} with quantified challenges."
    )
    return _rubric(
        "debate_quality",
        score,
        evidence,
        {"exchanges": len(rebuttals), "role_pairs": len(pairs), "quantified": quantified},
    )


@weave.op(name="eval.cfo_synthesis")
def score_cfo_synthesis(recommendation: dict) -> RubricScore:
    rec = recommendation or {}
    decision = str(rec.get("decision") or "").upper()
    valid_decision = decision in {"APPROVE", "REJECT", "CONDITIONAL", "DEFER"}
    confidence = rec.get("confidence")
    valid_confidence = isinstance(confidence, (int, float)) and 0 <= confidence <= 100
    rationale_len = len(str(rec.get("rationale") or ""))
    has_guardrails = bool(rec.get("conditions")) or bool(rec.get("key_risks"))
    impact = rec.get("impact") or {}
    runway_computed = (
        impact.get("scenario_runway_months") is not None
        or impact.get("delta_months") is not None
        or "note" in impact
    )
    score = (
        (25 if valid_decision else 0)
        + (15 if valid_confidence else 0)
        + min(20, rationale_len // 12)
        + (15 if has_guardrails else 0)
        + (25 if runway_computed else 0)
    )
    evidence = (
        f"decision={decision or 'missing'}, confidence={'ok' if valid_confidence else 'missing'}, "
        f"guardrails={'present' if has_guardrails else 'none'}, "
        f"runway_impact={'computed' if runway_computed else 'not computed'}."
    )
    return _rubric(
        "cfo_synthesis",
        score,
        evidence,
        {
            "valid_decision": valid_decision,
            "valid_confidence": valid_confidence,
            "has_guardrails": has_guardrails,
            "runway_computed": runway_computed,
        },
    )


@weave.op(name="eval.reliability_scoring")
def score_reliability_scoring(reliability_scores: list) -> RubricScore:
    scores = reliability_scores or []
    by_agent = {s.get("agent_id"): s for s in scores}
    present = sum(1 for a in EXPECTED_AGENTS if a in by_agent)
    placeholders = sum(
        1
        for a in EXPECTED_AGENTS
        if by_agent.get(a, {}).get("reliability", 0) == 0
        and "did not return a score" in str(by_agent.get(a, {}).get("rationale", ""))
    )
    nonzero = sum(1 for s in scores if (s.get("reliability") or 0) > 0)
    coverage = present / len(EXPECTED_AGENTS)
    score = round(coverage * 70) + min(30, nonzero * 6) - placeholders * 15
    evidence = (
        f"{present}/{len(EXPECTED_AGENTS)} agents scored, {nonzero} with non-zero reliability, "
        f"{placeholders} placeholder (unscored) agents."
    )
    return _rubric(
        "reliability_scoring",
        score,
        evidence,
        {"present": present, "nonzero": nonzero, "placeholders": placeholders},
    )


@weave.op(name="eval.persistence")
def score_persistence(trace_summary: dict) -> RubricScore:
    summary = trace_summary or {}
    weave_ready = bool(summary.get("weave_project") and summary.get("weave_url"))
    try:
        redis_ready = R.ping()
    except Exception:
        redis_ready = False
    try:
        stream_len = int(R.client().xlen(f"{R.NS}:stream:decisions"))
    except Exception:
        stream_len = 0
    score = (45 if weave_ready else 0) + (35 if redis_ready else 0) + min(20, stream_len * 2)
    evidence = (
        f"weave trace metadata {'present' if weave_ready else 'missing'}, "
        f"redis {'reachable' if redis_ready else 'unreachable'}, "
        f"decision stream length {stream_len}."
    )
    return _rubric(
        "persistence",
        score,
        evidence,
        {"weave_ready": weave_ready, "redis_ready": redis_ready, "decision_stream_len": stream_len},
    )


def _trace_quality_issues(scores: list[RubricScore], recommendation: dict) -> list[TraceQualityIssue]:
    issues: list[TraceQualityIssue] = []
    node_for = {
        "context_retrieval": "intake",
        "policy_grounding": "analyst_*",
        "debate_quality": "debate_round",
        "cfo_synthesis": "cfo_synthesis",
        "reliability_scoring": "reliability_auditor",
        "persistence": "persist_decision",
    }
    for rubric_score in scores:
        if rubric_score.passed:
            continue
        gap = rubric_score.threshold - rubric_score.score
        severity = "high" if gap >= 25 else "medium" if gap >= 10 else "low"
        issues.append(
            TraceQualityIssue(
                id=_new_id("tqi"),
                node=node_for.get(rubric_score.dimension, rubric_score.dimension),
                severity=severity,
                summary=f"{rubric_score.label} scored {rubric_score.score} (threshold {rubric_score.threshold}). {rubric_score.evidence}",
                recommendation=_issue_fix(rubric_score.dimension),
            )
        )
    impact = (recommendation or {}).get("impact") or {}
    if recommendation and not (
        impact.get("scenario_runway_months") is not None
        or impact.get("delta_months") is not None
        or "note" in impact
    ):
        issues.append(
            TraceQualityIssue(
                id=_new_id("tqi"),
                node="cfo_synthesis",
                severity="high",
                summary="CFO recommendation did not carry a computed runway impact.",
                recommendation="Require compute_runway to run before synthesis is emitted; block promotion until runway is quantified.",
            )
        )
    return issues


def _issue_fix(dimension: str) -> str:
    return {
        "context_retrieval": "Ensure intake loads financials, vendor search, and policy RAG before any analyst speaks.",
        "policy_grounding": "Require each analyst to cite at least one figure and one policy/precedent from Redis.",
        "debate_quality": "Demand 3+ quantified cross-examination exchanges across distinct role pairs.",
        "cfo_synthesis": "Require a valid decision, confidence, guardrails, and a computed runway impact.",
        "reliability_scoring": "Block promotion until every council agent receives a complete, non-placeholder score.",
        "persistence": "Verify Weave is initialized and Redis Stack is reachable before accepting the run.",
    }.get(dimension, "Investigate and replay this dimension before promoting any change.")


# --------------------------------------------------------------------------- #
# Assembly (a @weave.op so the six scorers nest as child spans) + persistence
# --------------------------------------------------------------------------- #
@weave.op(name="eval.assemble_packet")
def assemble_eval_packet(run: dict) -> EvalPacket:
    """Score a completed run against the rubric and build a durable EvalPacket.

    Pure (no side effects) so the Weave trace is clean; persistence is separate.
    """
    context = run.get("context") or {}
    positions = run.get("positions") or []
    transcript = run.get("transcript") or []
    recommendation = run.get("recommendation") or {}
    reliability_scores = run.get("reliability_scores") or []
    trace_summary = run.get("trace_summary") or {}

    scores = [
        score_context_retrieval(context),
        score_policy_grounding(positions, recommendation),
        score_debate_quality(transcript),
        score_cfo_synthesis(recommendation),
        score_reliability_scoring(reliability_scores),
        score_persistence(trace_summary),
    ]
    overall = _clamp(sum(s.score * s.weight for s in scores))
    council = [int(s.get("reliability") or 0) for s in reliability_scores]
    council_average = round(sum(council) / len(council)) if council else 0
    issues = _trace_quality_issues(scores, recommendation)

    decision = str(run.get("decision") or "")
    company = (context.get("financials") or {}).get("name") or "Acme Corp"
    return EvalPacket(
        id=_new_id("eval"),
        created_at=_now(),
        created_ts=time.time(),
        source=run.get("source") or "live",
        decision=decision,
        decision_label=(decision[:90] + "…") if len(decision) > 90 else decision,
        recommendation={
            "decision": recommendation.get("decision"),
            "confidence": recommendation.get("confidence"),
            "impact": recommendation.get("impact"),
        },
        company=company,
        model=trace_summary.get("model") or run.get("model") or "",
        weave=weave_links(),
        rubric_scores=scores,
        overall_score=overall,
        reliability_scores=[
            {"agent_id": s.get("agent_id"), "reliability": s.get("reliability")}
            for s in reliability_scores
        ],
        council_average=council_average,
        trace_quality_issues=issues,
        replay_set=run.get("replay_set"),
        prompt_versions=run.get("prompt_versions") or [],
        learning_report=run.get("learning_report") or {},
    )


def persist_eval_packet(packet: EvalPacket, *, publish: bool = True) -> dict[str, Any]:
    """Persist a packet to Redis (atlas:evaluation:*) and the eval stream.

    Optionally publishes the packet to Weave so it is queryable in the W&B UI.
    Returns only non-secret metadata.
    """
    data = packet.model_dump()
    result: dict[str, Any] = {"packet_id": packet.id}
    try:
        R.set_json(f"{PACKET_PREFIX}{packet.id}", data)
        R.set_json(LATEST_PACKET, data)
        client = R.client()
        client.rpush(PACKET_INDEX, packet.id)
        client.ltrim(PACKET_INDEX, -250, -1)
        result["event_id"] = R.append_event(PACKET_STREAM, data)
        # Backward-compatible mirrors (older surfaces read these keys).
        R.set_json(f"{R.NS}:reliability:latest", data)
    except Exception as exc:
        result["redis_error"] = redact_secrets(exc)
    if publish:
        result["weave"] = publish_to_weave(packet, name=f"atlas-eval-packet-{packet.id}")
    return result


def capture_eval_packet(
    *,
    decision: str,
    context: dict,
    positions: list,
    transcript: list,
    recommendation: dict,
    reliability_scores: list,
    trace_summary: dict,
    learning_report: dict | None = None,
    source: str = "live",
    replay_set: str | None = None,
    prompt_versions: list | None = None,
    publish: bool = True,
) -> dict[str, Any]:
    """Score + persist one completed council run. Used by the reliability node."""
    packet = assemble_eval_packet(
        {
            "decision": decision,
            "context": context,
            "positions": positions,
            "transcript": transcript,
            "recommendation": recommendation,
            "reliability_scores": reliability_scores,
            "trace_summary": trace_summary,
            "learning_report": learning_report or {},
            "source": source,
            "replay_set": replay_set,
            "prompt_versions": prompt_versions or [],
        }
    )
    persisted = persist_eval_packet(packet, publish=publish)
    return {"packet": packet.model_dump(), **persisted}


# --------------------------------------------------------------------------- #
# Read API (used by REST endpoints, health, replay sets, promotion gates)
# --------------------------------------------------------------------------- #
def list_eval_packets(limit: int = 25) -> list[dict]:
    """Most-recent-first eval packets from the eval stream."""
    try:
        return R.read_events(PACKET_STREAM, count=limit)
    except Exception:
        return []


def get_eval_packet(packet_id: str) -> dict | None:
    return R.get_json(f"{PACKET_PREFIX}{packet_id}")


def latest_eval_packet() -> dict | None:
    return R.get_json(LATEST_PACKET)


def eval_summary() -> dict[str, Any]:
    """Compact, non-secret summary for health / observability surfaces."""
    packets = list_eval_packets(50)
    latest = packets[0] if packets else None
    overall_values = [int(p.get("overall_score") or 0) for p in packets]
    high_issues = 0
    for packet in packets:
        for issue in packet.get("trace_quality_issues") or []:
            if isinstance(issue, dict) and issue.get("severity") == "high":
                high_issues += 1
    return {
        "packet_count": len(packets),
        "average_overall": round(sum(overall_values) / len(overall_values)) if overall_values else 0,
        "latest_overall": int(latest.get("overall_score")) if latest else None,
        "latest_id": latest.get("id") if latest else None,
        "high_severity_issues": high_issues,
        "weave": weave_links(),
        "namespace": EVAL_NS,
        "stream": f"{R.NS}:stream:{PACKET_STREAM}",
    }
