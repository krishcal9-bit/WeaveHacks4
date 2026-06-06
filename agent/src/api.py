"""
Read-only data API for the dashboard (separate from the AG-UI agent at "/").
Serves the seeded Acme Corp data straight from Redis so the executive dashboard
and department views can render without running a debate.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, Response, UploadFile
from openai import AsyncOpenAI

from src.env import redact_secrets
from src.health import (
    evaluation_health,
    observability_health,
    require_live_ready,
    sponsor_health,
)
from src import agui_commands as AGUI
from src import council_commands
from src import realtime as RT
from src import promotion_gates as PG
from src import redis_layer as R
from src import replay_sets as RS
from src import weave_eval as WE
from src.integrations import service as OPS

router = APIRouter(prefix="/api")

# Mount the financial-OS sub-router (scenarios, operating collections, knowledge
# RAG, redis map) without touching this module's hot route definitions.
from src.financial_api import financial_router  # noqa: E402

router.include_router(financial_router)

COMPANY_KEY = f"{R.NS}:company:northwind"


@router.get("/company")
def company() -> dict:
    return R.get_json(COMPANY_KEY) or {}


@router.get("/vendors")
def vendors() -> list:
    return R.search_vendors("*", 50)


@router.get("/decisions")
def decisions(limit: int = 25) -> list:
    return R.read_events("decisions", count=limit)


@router.get("/roster")
def roster() -> list:
    from src.agent import ROSTER

    return [{"id": key, **meta} for key, meta in ROSTER.items()]


@router.get("/health")
def health(response: Response) -> dict:
    payload = sponsor_health()
    if not payload["ready"]:
        response.status_code = 503
    return payload


@router.get("/observability")
def observability(response: Response, limit: int = 15) -> dict:
    health_payload = sponsor_health()
    observability_payload = observability_health()
    ready = bool(health_payload["ready"] and observability_payload["ready"])
    if not ready:
        response.status_code = 503
    recent_decisions = R.read_events("decisions", count=limit) if health_payload["ready"] else []
    return {
        "ready": ready,
        "mode": "strict-live",
        "sponsor_health": health_payload["sponsors"],
        "blockers": health_payload["blockers"],
        "observability": observability_payload,
        "weave": observability_payload["weave"],
        "realtime": RT.realtime_health(),
        "redis_activity": [
            {
                "label": "Decision stream",
                "detail": f"{len(recent_decisions)} recent events",
                "kind": "stream",
            },
            {
                "label": "System of record",
                "detail": "RedisJSON company and vendor records",
                "kind": "json",
            },
            {
                "label": "Vector memory",
                "detail": "RediSearch policy and precedent RAG index",
                "kind": "search",
            },
        ],
        "events": [
            {
                "id": "health",
                "sponsor": "Atlas",
                "label": "Strict-live preflight",
                "detail": "Ready" if health_payload["ready"] else "Blocked",
                "tone": "positive" if health_payload["ready"] else "risk",
            }
        ],
    }


@router.get("/command/state")
def command_state(room: str = AGUI.DEFAULT_ROOM) -> dict:
    """Current AG-UI command-and-control state (for initial load / polling).

    Read-only; mirrors the eight command keys that also stream through the
    LangGraph DebateState while a debate is running.
    """
    return {"room": room, "state": AGUI.load_command_state(room)}


@router.get("/command/types")
def command_types() -> dict:
    """The command vocabulary the operator panel and CopilotKit actions expose."""
    return {"types": AGUI.COMMAND_TYPES, "agents": list(AGUI.KNOWN_AGENTS)}


@router.post("/command")
async def command(response: Response, payload: dict = Body(...)) -> dict:
    """Dispatch a single operator command to the council command engine.

    Validation, strict-live gating, execution against the Redis-backed finance
    tools / live model, Redis Stream recording, and command-state persistence
    all happen server-side in council_commands.dispatch_command. Rejections and
    failures are returned in the envelope (HTTP 200 with status != "executed")
    so the UI can explain them; only unexpected errors surface as 5xx.
    """
    room = (payload.get("room") if isinstance(payload, dict) else None) or AGUI.DEFAULT_ROOM
    try:
        result = await council_commands.dispatch_command(payload, room=room)
    except Exception as exc:  # dispatch is defensive, but never 500 silently
        response.status_code = 500
        raise HTTPException(status_code=500, detail=redact_secrets(exc)) from exc
    if result.get("status") == "rejected" and result.get("reason") == "not_live":
        response.status_code = 503
    return result


@router.post("/realtime/session")
async def realtime_session(response: Response) -> dict:
    """Mint an ephemeral OpenAI Realtime 2 session for browser voice council chat.

    Robust control surface (see src/realtime.py): returns session-policy metadata,
    short-lived secret TTL reporting (issued_at / expires_at / seconds_remaining),
    and voice-model health. Every error passes through redact_secrets, and the
    standing OPENAI_API_KEY is never returned — only the ephemeral client secret
    the WebRTC handshake requires.
    """
    try:
        require_live_ready()
    except Exception as exc:
        response.status_code = 503
        raise HTTPException(status_code=503, detail=redact_secrets(exc)) from exc
    try:
        return await RT.mint_session()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=redact_secrets(exc)) from exc


@router.get("/realtime/health")
def realtime_health_endpoint(response: Response) -> dict:
    """Voice-model readiness without minting a secret: model, voice, reasoning
    effort, endpoint, secret TTL, session policy, and API-key presence."""
    payload = RT.realtime_health()
    if not payload.get("ready"):
        response.status_code = 503
    return payload


# --------------------------------------------------------------------------- #
# W&B Weave evaluation / replay / promotion operating system
# --------------------------------------------------------------------------- #
@router.get("/evals")
def evals(limit: int = 25, id: str | None = None) -> dict:
    """List recent eval packets (or one packet by ?id=)."""
    if id:
        packet = WE.get_eval_packet(id)
        if not packet:
            raise HTTPException(status_code=404, detail=f"Unknown eval packet: {id}")
        return {"packet": packet}
    return {"summary": WE.eval_summary(), "packets": WE.list_eval_packets(limit)}


@router.get("/evals/replay-sets")
def replay_sets_list(slug: str | None = None) -> dict:
    """List replay sets (or one full replay set by ?slug=, including its cases)."""
    if slug:
        record = RS.get_replay_set(slug)
        if not record:
            raise HTTPException(status_code=404, detail=f"Unknown replay set: {slug}")
        return record
    return {"summary": RS.replay_summary(), "replay_sets": RS.list_replay_sets()}


@router.post("/evals/replay-sets")
def create_replay_set(
    response: Response,
    name: str = RS.DEFAULT_REPLAY_SET,
    description: str = "",
    limit: int = 25,
    include_live: bool = True,
    include_history: bool = True,
) -> dict:
    """Build a replay set from prior board decisions and publish it as a live weave.Dataset."""
    try:
        require_live_ready()
    except Exception as exc:
        response.status_code = 503
        raise HTTPException(status_code=503, detail=redact_secrets(exc)) from exc
    try:
        record = RS.create_replay_set(
            name,
            description=description,
            limit=limit,
            include_live=include_live,
            include_history=include_history,
            publish=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=redact_secrets(exc)) from exc
    return {
        key: record.get(key)
        for key in ("name", "slug", "description", "created_at", "case_count", "history_cases", "live_cases", "weave")
    }


@router.get("/evals/promotions")
def promotions(limit: int = 25) -> dict:
    """List promotion candidates, recorded gate decisions, and the enforced gates."""
    return {
        "summary": PG.promotion_status_summary(),
        "candidates": PG.list_candidates(),
        "promotions": PG.list_promotions(limit),
        "enforced_gates": PG.summarize_gates(),
    }


@router.post("/evals/promotions")
async def promotions_action(
    response: Response,
    action: str,
    candidate: str,
    status: str | None = None,
    replay_set: str | None = None,
    note: str = "",
    max_cases: int = PG.REPLAY_MAX_CASES,
) -> dict:
    """Replay a candidate (live), block it, or mark it approved/blocked/needs_review.

    action=replay  → live W&B Weave replay vs incumbent over the replay set → GateDecision
    action=block   → record a BLOCKED gate (no replay evidence yet)
    action=mark    → human override (requires status=approved|blocked|needs_review)
    """
    try:
        require_live_ready()
    except Exception as exc:
        response.status_code = 503
        raise HTTPException(status_code=503, detail=redact_secrets(exc)) from exc
    try:
        if action == "replay":
            return await PG.run_promotion_replay(candidate, replay_set=replay_set, max_cases=max_cases, publish=True)
        if action == "block":
            return PG.block_unproven_candidate(candidate, replay_set=replay_set, publish=True)
        if action == "mark":
            if not status:
                raise HTTPException(status_code=422, detail="mark requires status=approved|blocked|needs_review")
            return PG.mark_candidate(candidate, status, note=note, publish=True)
        raise HTTPException(status_code=422, detail="action must be one of: replay, block, mark")
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=redact_secrets(exc)) from exc


@router.get("/observability/evals")
def observability_evals(response: Response, limit: int = 15) -> dict:
    """Eval-subsystem observability: Weave readiness, counts, recent packets + gate decisions."""
    payload = evaluation_health()
    if not payload.get("ready"):
        response.status_code = 503
    return {
        "ready": payload.get("ready"),
        "mode": "strict-live",
        "weave": payload.get("weave"),
        "evals": payload.get("evals"),
        "replay_sets": payload.get("replay_sets"),
        "promotions": payload.get("promotions"),
        "recent_packets": WE.list_eval_packets(limit),
        "recent_promotions": PG.list_promotions(limit),
        "enforced_gates": PG.summarize_gates(),
        "blockers": payload.get("blockers", []),
    }


# --------------------------------------------------------------------------- #
# Finance-operations connectors: ingestion, source inventory, reconciliation
# --------------------------------------------------------------------------- #
# Read-only inventories never fabricate: unconfigured connectors report their
# blockers, and reconciliation drilldowns reflect only what has been imported.
@router.get("/connectors")
def connectors() -> dict:
    """Connector configuration + import state (env-driven; optional for the core demo)."""
    return {
        "mode": "strict-live",
        "connectors": OPS.connector_statuses(),
        "confidence": OPS.import_confidence().model_dump(mode="json"),
    }


@router.post("/connectors/import/{connector_id}")
async def connector_import(response: Response, connector_id: str, file: UploadFile = File(...)) -> dict:
    """Import one uploaded connector file, then immediately reconcile."""
    try:
        raw = await file.read()
        result = OPS.import_uploaded_file(
            connector_id,
            source_name=file.filename or connector_id,
            raw=raw,
        )
        report = OPS.run_reconciliation()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        response.status_code = 503
        raise HTTPException(status_code=503, detail=redact_secrets(exc)) from exc
    return {
        "import_result": result.model_dump(mode="json"),
        "connectors": OPS.connector_statuses(),
        "confidence": OPS.import_confidence().model_dump(mode="json"),
        "reconciliation": report.model_dump(mode="json"),
    }


@router.get("/sources")
def sources() -> dict:
    """Inventory of imported sources with provenance (paths redacted)."""
    inventory = OPS.source_inventory()
    return {"count": len(inventory), "sources": inventory}


@router.get("/sources/{connector_id}")
def source_detail(connector_id: str, sample: int = 10) -> dict:
    """Provenance + a sample of records for one connector."""
    detail = OPS.get_source(connector_id, sample=sample)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"No imported source for connector: {connector_id}")
    return detail


@router.get("/reconciliation")
def reconciliation() -> dict:
    """Latest reconciliation report (summary, workflows, discrepancies)."""
    report = OPS.reconciliation_summary()
    if not report:
        return {"status": "not_run", "detail": "No reconciliation has been run yet.", "discrepancies": []}
    return report


@router.post("/reconciliation/run")
def reconciliation_run(response: Response) -> dict:
    """Recompute reconciliation from already-imported Redis data (no file I/O).

    Independent of the LLM/Weave stack — needs only Redis — so it is not gated on
    full sponsor readiness. Redis/IO failures surface as 503 with redacted detail.
    """
    try:
        report = OPS.run_reconciliation()
    except Exception as exc:
        response.status_code = 503
        raise HTTPException(status_code=503, detail=redact_secrets(exc)) from exc
    return report.model_dump(mode="json")


@router.get("/reconciliation/discrepancies")
def discrepancies(severity: str | None = None, kind: str | None = None) -> dict:
    """Outstanding mismatches, optionally filtered by ?severity= and/or ?kind=."""
    items = OPS.list_discrepancies(severity=severity, kind=kind)
    return {"count": len(items), "discrepancies": items}


@router.get("/reconciliation/discrepancies/{discrepancy_id}")
def discrepancy_detail(discrepancy_id: str) -> dict:
    """Drilldown into a single discrepancy by id."""
    disc = OPS.get_discrepancy(discrepancy_id)
    if disc is None:
        raise HTTPException(status_code=404, detail=f"Unknown discrepancy: {discrepancy_id}")
    return disc


@router.post("/demo/reset")
def demo_reset(response: Response) -> dict:
    """Clear uploaded connector, reconciliation, and command-panel state only."""
    try:
        payload = OPS.reset_demo_state()
        payload["deleted"][AGUI.command_state_key()] = R.delete_key(AGUI.command_state_key())
        payload["command_state"] = AGUI.default_command_state()
        return payload
    except Exception as exc:
        response.status_code = 503
        raise HTTPException(status_code=503, detail=redact_secrets(exc)) from exc


# --------------------------------------------------------------------------- #
# Strategic planning digital twin — plans, stress tests, playbook portfolios,
# sensitivity, and CFO board narratives (src/planning.py, src/playbooks.py,
# src/stress_tests.py). All figures are computed deterministically; only the
# board narrative calls OpenAI, and only after the math is fixed. The compute
# endpoints read from Redis like /company; the narrative endpoint enforces
# strict-live readiness because it is the one model-backed artifact.
# --------------------------------------------------------------------------- #
from pydantic import BaseModel  # noqa: E402


class PlanRequest(BaseModel):
    horizon_months: int = 12
    playbook: str | None = None
    decision: str | None = None
    title: str | None = None
    assumptions_overrides: dict[str, float] | None = None


class StressRequest(BaseModel):
    trials: int = 400
    seed: int = 42
    horizon_months: int | None = None


class CompareRequest(BaseModel):
    decision: str
    playbooks: list[str] | None = None
    horizon_months: int = 12


@router.get("/playbooks")
def playbooks_catalog() -> list:
    """The finance playbook library (extend runway, unblock enterprise via
    security spend, renegotiate vendors, hire against revenue, financing bridge,
    growth→efficiency, recover pipeline)."""
    from src import playbooks as PB

    return PB.catalog()


@router.get("/plans")
def plans(limit: int = 25) -> list:
    """Most-recent persisted strategic plans, as compact cards."""
    from src import planning as PL

    return PL.list_plans(limit=limit)


@router.post("/plans")
def create_plan(req: PlanRequest | None = Body(default=None)) -> dict:
    """Build and persist a strategic plan. Body is optional: defaults to a
    12-month base operating plan. Supply ``playbook`` to instantiate one, or
    ``decision`` to auto-select the most relevant playbook."""
    from src import planning as PL
    from src import playbooks as PB

    req = req or PlanRequest()
    company = PL.load_company()
    horizon = max(1, min(36, int(req.horizon_months or 12)))
    if req.playbook and req.playbook in PB.PLAYBOOKS:
        plan = PB.build_playbook_plan(company, req.playbook, horizon_months=horizon)
    elif req.decision:
        plan = PL.plan_from_decision(req.decision, horizon=horizon, persist=False)
    else:
        plan = PL.build_plan(
            company,
            title=req.title or f"{horizon}-month base operating plan",
            horizon_months=horizon,
            assumptions_overrides=req.assumptions_overrides,
        )
    PL.save_plan(plan)
    return plan.model_dump()


@router.get("/plans/{plan_id}")
def plan_detail(plan_id: str) -> dict:
    """Full persisted plan: assumptions, steps, month-by-month projection,
    milestones, capital plan, policy blockers, provenance, and calc metadata."""
    from src import planning as PL

    doc = PL.get_plan(plan_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Unknown plan: {plan_id}")
    return doc


@router.post("/plans/{plan_id}/stress")
def stress_plan(plan_id: str, req: StressRequest | None = Body(default=None)) -> dict:
    """Run a Monte Carlo-style stress test on a persisted plan (deterministic for
    a fixed seed). Reports percentile bands and guardrail-breach probabilities."""
    from src import planning as PL
    from src import stress_tests as ST

    doc = PL.get_plan(plan_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Unknown plan: {plan_id}")
    req = req or StressRequest()
    steps, overrides = ST.steps_from_plan_doc(doc)
    horizon = int(req.horizon_months or doc.get("horizon_months") or 12)
    st = ST.run_stress_test(
        PL.load_company(),
        name=f"{doc.get('title')} stress test",
        horizon_months=horizon,
        trials=req.trials,
        seed=req.seed,
        steps=steps,
        base_overrides=overrides,
    )
    return st.model_dump()


@router.get("/sensitivity")
def sensitivity(variable: str | None = None, horizon_months: int = 12, output_metric: str = "min_cash") -> dict:
    """One-variable sensitivity sweep (?variable=churn|conversion|gross_margin|
    hiring_start|vendor_savings|financing_close_month) or the full ranked suite
    when no variable is given."""
    from src import planning as PL
    from src import stress_tests as ST

    company = PL.load_company()
    horizon = max(1, min(36, int(horizon_months)))
    if variable:
        return ST.run_sensitivity(company, variable, horizon_months=horizon, output_metric=output_metric).model_dump()
    return ST.sensitivity_suite(company, horizon_months=horizon, output_metric=output_metric)


@router.post("/playbooks/compare")
def compare_playbooks(req: CompareRequest) -> dict:
    """Compare playbooks for one decision and recommend a portfolio (a sequenced
    set of strategies), not a binary approve/reject. Deterministic scoring."""
    from src import planning as PL
    from src import playbooks as PB

    company = PL.load_company()
    horizon = max(1, min(36, int(req.horizon_months or 12)))
    portfolio, _plans = PB.compare_playbooks(company, req.playbooks or [], req.decision, horizon_months=horizon)
    return portfolio.model_dump()


@router.get("/plans/{plan_id}/narrative")
def plan_narrative(plan_id: str, response: Response, refresh: bool = False) -> dict:
    """CFO/board strategic narrative for a plan. Prose is model-generated by
    OpenAI from the already-computed figures (cited in ``deterministic_basis``);
    cached in Redis. This is the only model-backed planning endpoint, so it
    enforces strict-live readiness."""
    from src import planning as PL
    from src.health import require_live_ready

    doc = PL.get_plan(plan_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Unknown plan: {plan_id}")
    cache_key = f"{PL.PLAN_PREFIX}{plan_id}:narrative"
    if not refresh:
        cached = R.get_json(cache_key)
        if cached:
            return cached
    try:
        require_live_ready()
    except Exception as exc:
        response.status_code = 503
        raise HTTPException(status_code=503, detail=redact_secrets(exc)) from exc
    plan = PL.StrategicPlan(**doc)
    payload = PL.generate_board_narrative(plan).model_dump()
    try:
        R.set_json(cache_key, payload)
    except Exception:
        pass
    return payload


# --------------------------------------------------------------------------- #
# Governance — policies, approvals, obligations, and the immutable audit log.
# Reads come straight from Redis (RedisJSON + RediSearch + the audit Stream).
# Writes are deterministic and live; the decision endpoint enforces the
# no-fake-human-approval contract (see src/approvals.record_decision).
# --------------------------------------------------------------------------- #
class ApprovalCreateBody(BaseModel):
    decision: str
    estimated_monthly_cost: float = 0.0
    estimated_one_time_cost: float = 0.0
    added_monthly_revenue: float = 0.0
    department: str | None = None
    data_sensitivity: str | None = None
    decision_outcome: str | None = None  # APPROVE | CONDITIONAL | REJECT | DEFER
    recommendation: dict | None = None


class DecisionBody(BaseModel):
    actor: str
    actor_type: str  # human | system | service | agent
    action: str  # approved | conditionally_approved | rejected | expired | superseded | reopened
    rationale: str
    conditions: list[str] | None = None
    step_sequence: int | None = None
    approver_role: str | None = None


class ExceptionBody(BaseModel):
    policy_id: str
    control_id: str
    justification: str
    compensating_controls: list[str] | None = None
    requested_by: str = "Atlas Council"
    requested_by_type: str = "agent"


def _require_redis() -> None:
    if not R.ping():
        raise HTTPException(status_code=503, detail="Redis is not reachable; governance store unavailable.")


@router.get("/policies")
def governance_policies() -> list:
    """Acme Corp's structured board/finance policy rules (the controls the
    governance engine enforces), read from RedisJSON."""
    from src.policies import load_policy_rules

    return [r.model_dump(mode="json") for r in load_policy_rules()]


@router.get("/policies/search")
def governance_policies_search(q: str = "*", limit: int = 25) -> list:
    """RediSearch lookup over policy rules. Examples:
    ``q=@category:{vendor_spend}``, ``q=@amount_threshold:[150000 +inf]``, or free text."""
    _require_redis()
    return R.search_govpolicies(q or "*", limit=limit)


@router.get("/approvals")
def approvals_list(status: str | None = None, limit: int = 50) -> list:
    """Governed approval requests, newest first; optional ?status= filter
    (draft|pending_approval|approved|rejected|conditionally_approved|expired|superseded)."""
    _require_redis()
    from src import approvals as APV

    return [r.model_dump(mode="json") for r in APV.list_requests(status=status, limit=limit)]


@router.get("/approvals/{request_id}")
def approval_detail(request_id: str) -> dict:
    """A single approval request with its route, engaged controls, recorded
    decisions, exceptions, obligations, and monitoring triggers."""
    _require_redis()
    from src import approvals as APV
    from src import governance as GOV

    req = APV.get_request(request_id)
    if req is None:
        raise HTTPException(status_code=404, detail=f"Unknown approval request: {request_id}")
    return GOV.governance_state(req)


@router.post("/approvals")
def approval_create(body: ApprovalCreateBody, response: Response) -> dict:
    """Create a governed approval request from a decision (live; persists to Redis
    and the audit stream). The request is recorded as pending_approval or
    system-cleared — never as approved by a human."""
    _require_redis()
    from src import governance as GOV

    rec = body.recommendation or {
        "decision": (body.decision_outcome or "APPROVE"),
        "confidence": 70,
        "rationale": f"Operator-submitted decision via API: {body.decision}",
        "key_risks": [],
        "conditions": [],
    }
    try:
        req = GOV.govern_recommendation(
            body.decision,
            rec,
            monthly_cost=body.estimated_monthly_cost,
            one_time_cost=body.estimated_one_time_cost,
            added_monthly_revenue=body.added_monthly_revenue,
            department=body.department,
            data_sensitivity=body.data_sensitivity,
            created_by="API",
            created_by_type="service",
            source="api",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        response.status_code = 503
        raise HTTPException(status_code=503, detail=redact_secrets(exc)) from exc
    response.status_code = 201
    return GOV.governance_state(req)


@router.post("/approvals/{request_id}/decisions")
def approval_decision(request_id: str, body: DecisionBody) -> dict:
    """Record a decision against an approval request. A human sign-off
    (approved/conditionally_approved) requires actor_type=human and an explicit
    approver identity — the system is refused those actions."""
    _require_redis()
    from src import approvals as APV
    from src import governance as GOV

    try:
        req = APV.record_decision(
            request_id,
            actor=body.actor,
            actor_type=body.actor_type,
            action=body.action,
            rationale=body.rationale,
            conditions=body.conditions,
            step_sequence=body.step_sequence,
            approver_role=body.approver_role,
            provenance="api",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return GOV.governance_state(req)


@router.post("/approvals/{request_id}/exceptions")
def approval_exception(request_id: str, body: ExceptionBody) -> dict:
    """Attach a policy-exception request (pending board approval) to an approval."""
    _require_redis()
    from src import approvals as APV
    from src import governance as GOV

    try:
        req = APV.request_exception(
            request_id,
            policy_id=body.policy_id,
            control_id=body.control_id,
            justification=body.justification,
            compensating_controls=body.compensating_controls,
            requested_by=body.requested_by,
            requested_by_type=body.requested_by_type,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return GOV.governance_state(req)


@router.get("/obligations")
def obligations_list(status: str | None = None, kind: str | None = None, limit: int = 100) -> list:
    """Post-decision obligations (board notice, renewal windows, SOC 2 evidence,
    revenue milestones, forecast calibration, follow-ups), soonest due first.
    Overdue open items are surfaced as status=overdue."""
    _require_redis()
    from datetime import date

    try:
        docs = R.search_json_index(R.OBLIGATION_INDEX, "*", limit)
    except Exception:
        docs = R.list_json(R.OBLIGATION_PREFIX, limit)
    today = date.today().isoformat()
    out = []
    for d in docs:
        if d.get("status") == "open" and d.get("due_date") and d["due_date"] < today:
            d["status"] = "overdue"
        if status and d.get("status") != status:
            continue
        if kind and d.get("kind") != kind:
            continue
        out.append(d)
    out.sort(key=lambda d: (d.get("due_date") or "9999-12-31"))
    return out[:limit]


@router.get("/audit")
def audit_log(limit: int = 50, request_id: str | None = None) -> list:
    """The immutable governance audit trail (Redis Stream atlas:stream:audit),
    newest first; optional ?request_id= filter."""
    _require_redis()
    events = R.read_events(R.AUDIT_STREAM, count=max(limit, limit if not request_id else limit * 5))
    if request_id:
        events = [e for e in events if e.get("request_id") == request_id]
    return events[:limit]


@router.get("/approval-matrix")
def approval_matrix() -> dict:
    """The board-approved approval matrix (thresholds → approver chains)."""
    _require_redis()
    from src import approvals as APV

    return APV.load_matrix()
