"""
Approval workflow for Atlas governance.

This module turns a set of engaged controls into an **approval route** (who must
sign off, and why), persists approval requests to Redis, records decisions to an
immutable audit stream, and enforces the lifecycle of statuses
(draft → pending_approval → approved / rejected / conditionally_approved /
expired / superseded).

Integrity rule (load-bearing): the system never fabricates a human approval.
- Routing, recommendation, and auto-clear records are written with
  ``actor_type`` ``system``/``agent`` and explicit ``action`` values
  (``recommended``, ``routed``, ``auto_cleared``).
- A terminal human sign-off (``approved`` / ``conditionally_approved``) can only be
  recorded through :func:`record_decision` with ``actor_type=human`` *and* an
  explicit approver identity. ``system``/``service`` callers are refused those
  actions. There is no internal code path that creates a human decision.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from src import redis_layer as R
from src.governance_models import (
    ActorType,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStep,
    AuditEvent,
    ControlViolation,
    ExceptionRequest,
    RiskTier,
    utc_now_iso,
)

# --------------------------------------------------------------------------- #
# Canonical approval matrix (Acme Corp / Northwind board-approved)
# --------------------------------------------------------------------------- #
DEFAULT_MATRIX: dict[str, Any] = {
    "company_id": "northwind",
    "currency": "USD",
    "expiry_days": 14,
    # Commitments at or below the delegated-authority threshold need no formal
    # approval when no control is engaged — the system auto-clears them.
    "delegated_authority_max": 10_000,
    # Annualized-commitment tiers → required approver chain.
    "amount_tiers": [
        {"min": 10_000, "max": 25_000, "approvers": ["Department Head"]},
        {"min": 25_000, "max": 50_000, "approvers": ["Department Head", "Controller"]},
        {"min": 50_000, "max": 150_000, "approvers": ["Department Head", "Controller", "CFO"]},
        {"min": 150_000, "max": None, "approvers": ["Department Head", "Controller", "CFO", "Board"]},
    ],
    # Extra approvers added by risk tier.
    "risk_overrides": {
        "elevated": ["Risk & Audit"],
        "high": ["Risk & Audit", "CFO"],
    },
    # Extra approvers added by data sensitivity.
    "data_sensitivity_overrides": {
        "customer_data": ["Security Review"],
        "regulated": ["Security Review", "Legal"],
    },
    # Extra approvers added when a specific control is engaged.
    "control_approvers": {
        "CTRL-RUNWAY-FLOOR": ["CFO", "Board"],
        "CTRL-BOARD-NOTIFY": ["Board"],
        "CTRL-SPEND-CFO": ["CFO"],
        "CTRL-HEADCOUNT": ["CFO"],
        "CTRL-GROSS-MARGIN": ["CFO"],
        "CTRL-SECURITY-REVENUE": ["Risk & Audit"],
        "CTRL-DATA-SECURITY": ["Security Review"],
    },
    # Seniority ordering so the route reads in escalation order.
    "approver_rank": {
        "Department Head": 1,
        "Controller": 2,
        "Security Review": 2,
        "Legal": 3,
        "Risk & Audit": 3,
        "CFO": 4,
        "Board": 5,
    },
}

_HUMAN_SIGNOFF_ACTIONS = {"approved", "conditionally_approved"}
_ACTION_TO_STATUS: dict[str, ApprovalStatus] = {
    "approved": ApprovalStatus.APPROVED,
    "conditionally_approved": ApprovalStatus.CONDITIONAL,
    "rejected": ApprovalStatus.REJECTED,
    "expired": ApprovalStatus.EXPIRED,
    "superseded": ApprovalStatus.SUPERSEDED,
    "reopened": ApprovalStatus.PENDING,
}

# Allowed status transitions — the lifecycle guard.
_VALID_TRANSITIONS: dict[ApprovalStatus, set[ApprovalStatus]] = {
    ApprovalStatus.DRAFT: {ApprovalStatus.PENDING, ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.CONDITIONAL, ApprovalStatus.SUPERSEDED, ApprovalStatus.EXPIRED},
    ApprovalStatus.PENDING: {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.CONDITIONAL, ApprovalStatus.EXPIRED, ApprovalStatus.SUPERSEDED, ApprovalStatus.PENDING},
    ApprovalStatus.CONDITIONAL: {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED, ApprovalStatus.SUPERSEDED, ApprovalStatus.CONDITIONAL},
    ApprovalStatus.APPROVED: {ApprovalStatus.SUPERSEDED},
    ApprovalStatus.REJECTED: {ApprovalStatus.SUPERSEDED},
    ApprovalStatus.EXPIRED: {ApprovalStatus.SUPERSEDED, ApprovalStatus.PENDING},
    ApprovalStatus.SUPERSEDED: set(),
}


def can_transition(current: ApprovalStatus, target: ApprovalStatus) -> bool:
    if current == target:
        return True
    return target in _VALID_TRANSITIONS.get(current, set())


# --------------------------------------------------------------------------- #
# Matrix loading
# --------------------------------------------------------------------------- #
def load_matrix() -> dict[str, Any]:
    try:
        doc = R.get_json(R.MATRIX_KEY)
        if isinstance(doc, dict) and doc.get("amount_tiers"):
            return doc
    except Exception:
        pass
    return DEFAULT_MATRIX


# --------------------------------------------------------------------------- #
# Route construction
# --------------------------------------------------------------------------- #
def build_route(
    *,
    amount_annualized: float,
    risk_tier: RiskTier | str,
    data_sensitivity: str,
    violations: Iterable[ControlViolation],
    matrix: dict[str, Any] | None = None,
) -> list[ApprovalStep]:
    """Compose the approval route from amount tier, risk, data sensitivity, and the
    engaged controls. Each approver appears once, ordered by seniority, with the
    reasons (and policy refs) that put them on the route."""
    matrix = matrix or load_matrix()
    risk = risk_tier.value if isinstance(risk_tier, RiskTier) else str(risk_tier)
    rank: dict[str, int] = matrix.get("approver_rank", {})

    # approver_role → {reasons set, policy_refs set}
    approvers: dict[str, dict[str, set[str]]] = {}

    def add(role: str, reason: str, policy_ref: str | None = None) -> None:
        slot = approvers.setdefault(role, {"reasons": set(), "policy_refs": set()})
        slot["reasons"].add(reason)
        if policy_ref:
            slot["policy_refs"].add(policy_ref)

    # 1) Amount tier.
    if amount_annualized and amount_annualized > 0:
        for tier in matrix.get("amount_tiers", []):
            low = tier.get("min") or 0
            high = tier.get("max")
            if amount_annualized > low and (high is None or amount_annualized <= high):
                ceiling = f"<= ${high:,.0f}/yr" if high else "above $150,000/yr"
                for role in tier.get("approvers", []):
                    add(role, f"commitment tier ({ceiling})")
                break

    # 2) Risk override.
    for role in matrix.get("risk_overrides", {}).get(risk, []):
        add(role, f"{risk} risk tier")

    # 3) Data-sensitivity override.
    for role in matrix.get("data_sensitivity_overrides", {}).get(data_sensitivity, []):
        add(role, f"{data_sensitivity.replace('_', ' ')} data scope")

    # 4) Per-control approvers + control flags.
    control_approvers = matrix.get("control_approvers", {})
    for v in violations:
        for role in control_approvers.get(v.control_id, []):
            add(role, f"{v.title} ({v.control_id})", v.policy_id)
        if v.requires_board:
            add("Board", f"{v.title} ({v.control_id})", v.policy_id)
        if v.requires_security_review:
            add("Security Review", f"{v.title} ({v.control_id})", v.policy_id)

    # Order by seniority then name; assign sequence.
    ordered = sorted(approvers.items(), key=lambda kv: (rank.get(kv[0], 99), kv[0]))
    route: list[ApprovalStep] = []
    for i, (role, meta) in enumerate(ordered, start=1):
        route.append(ApprovalStep(
            sequence=i,
            approver_role=role,
            approver_type=ActorType.HUMAN,
            reason="; ".join(sorted(meta["reasons"])),
            status=ApprovalStatus.PENDING,
            policy_refs=sorted(meta["policy_refs"]),
        ))
    return route


# --------------------------------------------------------------------------- #
# Persistence + audit
# --------------------------------------------------------------------------- #
def _key(request_id: str) -> str:
    return f"{R.APPROVAL_PREFIX}{request_id}"


def append_audit(event: AuditEvent) -> str:
    """Append an immutable audit-trail entry to the Redis Stream and return its id."""
    return R.append_event(R.AUDIT_STREAM, event.model_dump(mode="json"))


def _publish(req: ApprovalRequest, event: str) -> None:
    payload = {
        "event": event,
        "kind": "governance",
        "request_id": req.id,
        "status": req.status.value,
        "title": req.title[:120],
        "at": utc_now_iso(),
    }
    try:
        R.publish("dashboard", payload)
        R.publish("governance", payload)
    except Exception:
        pass


def _save_obligations(req: ApprovalRequest) -> None:
    try:
        R.ensure_obligation_index()
    except Exception:
        pass
    for obl in req.obligations:
        obl.request_id = req.id
        try:
            R.set_json(f"{R.OBLIGATION_PREFIX}{obl.id}", obl.model_dump(mode="json"))
        except Exception:
            pass


def save_request(req: ApprovalRequest, audit_events: list[AuditEvent] | None = None, publish_event: str = "approval_created") -> ApprovalRequest:
    """Persist the request JSON + standalone obligations, append audit events, and
    notify the dashboard. Used at creation and after every mutation."""
    req.updated_at = utc_now_iso()
    try:
        R.ensure_approval_index()
    except Exception:
        pass
    R.set_json(_key(req.id), req.model_dump(mode="json"))
    _save_obligations(req)
    for ev in audit_events or []:
        append_audit(ev)
    _publish(req, publish_event)
    return req


def get_request(request_id: str, *, refresh: bool = True) -> ApprovalRequest | None:
    doc = R.get_json(_key(request_id))
    if not doc:
        return None
    req = ApprovalRequest.model_validate(doc)
    if refresh:
        req = refresh_status(req)
    return req


def list_requests(*, status: str | None = None, limit: int = 50) -> list[ApprovalRequest]:
    query = f"@status:{{{status}}}" if status else "*"
    docs: list[dict]
    try:
        docs = R.search_json_index(R.APPROVAL_INDEX, query, limit)
    except Exception:
        docs = R.list_json(R.APPROVAL_PREFIX, limit)
        if status:
            docs = [d for d in docs if d.get("status") == status]
    requests = []
    for d in docs:
        try:
            requests.append(refresh_status(ApprovalRequest.model_validate(d)))
        except Exception:
            continue
    requests.sort(key=lambda r: r.created_at, reverse=True)
    return requests[:limit]


# --------------------------------------------------------------------------- #
# Lifecycle: expiry, decisions, exceptions, supersede
# --------------------------------------------------------------------------- #
def refresh_status(req: ApprovalRequest, *, persist: bool = True) -> ApprovalRequest:
    """Lazily expire a pending request whose deadline has passed — system-generated,
    written to the audit trail (never a human action)."""
    if req.status != ApprovalStatus.PENDING or not req.expires_at:
        return req
    try:
        deadline = datetime.fromisoformat(req.expires_at)
    except ValueError:
        return req
    now = datetime.now(timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if now <= deadline:
        return req

    req.status = ApprovalStatus.EXPIRED
    decision = ApprovalDecision(
        request_id=req.id,
        actor="atlas-governance",
        actor_type=ActorType.SYSTEM,
        action="expired",
        status_after=ApprovalStatus.EXPIRED,
        rationale=f"Approval window elapsed at {req.expires_at} with sign-off still pending.",
        provenance="system",
    )
    req.decisions.append(decision)
    if persist:
        save_request(req, audit_events=[AuditEvent(
            type="status_changed",
            request_id=req.id,
            actor="atlas-governance",
            actor_type=ActorType.SYSTEM,
            summary=f"Request expired (was pending past {req.expires_at}).",
            payload={"status": "expired"},
        )], publish_event="approval_expired")
    return req


def _recompute_status_from_steps(req: ApprovalRequest) -> ApprovalStatus:
    """Derive the overall status from human step decisions."""
    human_steps = [s for s in req.route if s.approver_type == ActorType.HUMAN]
    if not human_steps:
        return req.status
    statuses = [s.status for s in human_steps]
    if ApprovalStatus.REJECTED in statuses:
        return ApprovalStatus.REJECTED
    if all(s in (ApprovalStatus.APPROVED, ApprovalStatus.CONDITIONAL) for s in statuses):
        return ApprovalStatus.CONDITIONAL if ApprovalStatus.CONDITIONAL in statuses else ApprovalStatus.APPROVED
    return ApprovalStatus.PENDING


def record_decision(
    request_id: str,
    *,
    actor: str,
    actor_type: ActorType | str,
    action: str,
    rationale: str,
    conditions: list[str] | None = None,
    step_sequence: int | None = None,
    approver_role: str | None = None,
    provenance: str = "api",
) -> ApprovalRequest:
    """Record a decision against a request and advance its lifecycle.

    Enforces the no-fake-human-approval rule: a human sign-off action requires
    ``actor_type=human`` and a non-empty identity, and ``system``/``service`` callers
    are refused those actions outright.
    """
    actor_type = ActorType(actor_type) if not isinstance(actor_type, ActorType) else actor_type
    action = action.strip().lower()
    if action not in _ACTION_TO_STATUS:
        raise ValueError(f"Unknown decision action '{action}'. Allowed: {sorted(_ACTION_TO_STATUS)}")

    # Integrity guard — the heart of the audit contract.
    if action in _HUMAN_SIGNOFF_ACTIONS:
        if actor_type != ActorType.HUMAN:
            raise PermissionError(
                f"A '{action}' sign-off must be recorded by a human approver (actor_type=human); "
                f"the system cannot approve on a person's behalf."
            )
    if actor_type == ActorType.HUMAN and not (actor or "").strip():
        raise ValueError("A human decision must carry an explicit approver identity.")

    req = get_request(request_id, refresh=True)
    if req is None:
        raise LookupError(f"Approval request {request_id} not found.")
    if req.status == ApprovalStatus.SUPERSEDED:
        raise ValueError("Cannot record a decision against a superseded request.")

    target = _ACTION_TO_STATUS[action]

    # Update the route step(s).
    decided_at = utc_now_iso()
    if step_sequence is not None or approver_role is not None:
        for step in req.route:
            if (step_sequence is not None and step.sequence == step_sequence) or (
                approver_role is not None and step.approver_role.lower() == approver_role.lower() and step.status == ApprovalStatus.PENDING
            ):
                step.status = target
                step.decided_by = actor
                step.decided_by_type = actor_type
                step.decided_at = decided_at
                step.note = rationale
                break
        overall = target if action == "reopened" else _recompute_status_from_steps(req)
    else:
        # Request-level decision: stamp all still-pending human steps.
        for step in req.route:
            if step.approver_type == ActorType.HUMAN and step.status == ApprovalStatus.PENDING:
                step.status = target
                step.decided_by = actor
                step.decided_by_type = actor_type
                step.decided_at = decided_at
                step.note = rationale
        overall = target

    if not can_transition(req.status, overall):
        raise ValueError(f"Illegal status transition {req.status.value} → {overall.value}.")

    req.status = overall
    decision = ApprovalDecision(
        request_id=req.id,
        actor=actor,
        actor_type=actor_type,
        action=action,
        status_after=overall,
        rationale=rationale,
        conditions=conditions or [],
        step_sequence=step_sequence,
        provenance=provenance,
    )
    req.decisions.append(decision)

    save_request(req, audit_events=[AuditEvent(
        type="decision_recorded",
        request_id=req.id,
        actor=actor,
        actor_type=actor_type,
        summary=f"{actor} ({actor_type.value}) recorded '{action}' → {overall.value}.",
        payload={"action": action, "status_after": overall.value, "step_sequence": step_sequence, "provenance": provenance},
    )], publish_event="approval_decision")
    return req


def request_exception(
    request_id: str,
    *,
    policy_id: str,
    control_id: str,
    justification: str,
    compensating_controls: list[str] | None = None,
    requested_by: str = "Atlas Council",
    requested_by_type: ActorType | str = ActorType.AGENT,
    expiry_days: int = 30,
) -> ApprovalRequest:
    """Attach a policy-exception request (pending board approval) to a request."""
    requested_by_type = ActorType(requested_by_type) if not isinstance(requested_by_type, ActorType) else requested_by_type
    req = get_request(request_id, refresh=True)
    if req is None:
        raise LookupError(f"Approval request {request_id} not found.")
    exc = ExceptionRequest(
        request_id=req.id,
        policy_id=policy_id,
        control_id=control_id,
        justification=justification,
        compensating_controls=compensating_controls or [],
        requested_by=requested_by,
        requested_by_type=requested_by_type,
        expires_at=(datetime.now(timezone.utc) + timedelta(days=expiry_days)).isoformat(),
    )
    req.exceptions.append(exc)
    req.decisions.append(ApprovalDecision(
        request_id=req.id,
        actor=requested_by,
        actor_type=requested_by_type,
        action="exception_requested",
        status_after=req.status,
        rationale=f"Requested exception to {control_id}: {justification}",
        provenance="system" if requested_by_type != ActorType.HUMAN else "api",
    ))
    save_request(req, audit_events=[AuditEvent(
        type="exception_requested",
        request_id=req.id,
        actor=requested_by,
        actor_type=requested_by_type,
        summary=f"Exception requested for {control_id} (pending board approval).",
        payload={"policy_id": policy_id, "control_id": control_id, "exception_id": exc.id},
    )], publish_event="approval_exception")
    return req


def supersede_request(old_id: str, new_id_value: str, reason: str) -> ApprovalRequest | None:
    """Mark a prior request superseded by a newer one (system-generated)."""
    req = get_request(old_id, refresh=False)
    if req is None or req.status == ApprovalStatus.SUPERSEDED:
        return req
    req.status = ApprovalStatus.SUPERSEDED
    req.superseded_by = new_id_value
    req.decisions.append(ApprovalDecision(
        request_id=req.id,
        actor="atlas-governance",
        actor_type=ActorType.SYSTEM,
        action="superseded",
        status_after=ApprovalStatus.SUPERSEDED,
        rationale=reason,
        provenance="system",
    ))
    return save_request(req, audit_events=[AuditEvent(
        type="superseded",
        request_id=req.id,
        actor="atlas-governance",
        actor_type=ActorType.SYSTEM,
        summary=f"Superseded by {new_id_value}: {reason}",
        payload={"superseded_by": new_id_value},
    )], publish_event="approval_superseded")


def supersede_prior_pending(title: str, new_id_value: str) -> list[str]:
    """Supersede earlier *pending* requests for the same decision title (exact match)
    so the audit trail shows one live request per topic. Returns superseded ids."""
    superseded: list[str] = []
    norm = (title or "").strip().lower()
    if not norm:
        return superseded
    for req in list_requests(status=ApprovalStatus.PENDING.value, limit=100):
        if req.id != new_id_value and req.title.strip().lower() == norm:
            supersede_request(req.id, new_id_value, "Replaced by a newer governed recommendation for the same decision.")
            superseded.append(req.id)
    return superseded
