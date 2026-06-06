"""
Governance smoke checks for Atlas — live, no mocks.

Verifies the governance lifecycle end-to-end against the real Redis Stack:
  1. in-process: govern a sample recommendation, read back the persisted approval
     request and its immutable audit stream, and assert no decision is falsely
     attributed to a human;
  2. REST (FastAPI TestClient): list policies, create an approval request, read it
     and its audit trail, prove the system is *refused* a human sign-off, then
     record a genuine operator-supplied human approval; list obligations.

Every created record is tagged source="governance_smoke" and cleaned up at the end
(the append-only audit stream is intentionally left intact).

Run:  uv run --directory agent python -m src.governance_smoke
Exit code 0 on success, 1 on any failed assertion.
"""

from __future__ import annotations

import sys
import traceback

from src import approvals as APV
from src import governance as GOV
from src import redis_layer as R
from src.data.seed import seed_governance
from src.governance_models import ActorType

SMOKE_SOURCE = "governance_smoke"

SAMPLE_INPROCESS = {
    "decision": "[SMOKE] Renew the $180k/yr Datadog contract as-is, or renegotiate it down?",
    "recommendation": {
        "decision": "CONDITIONAL",
        "confidence": 62,
        "rationale": "Renegotiate with usage caps before the renewal window.",
        "key_risks": ["usage overage", "switching cost"],
        "conditions": ["cap usage", "file 45-day notice"],
        "impact": {
            "current_runway_months": 10.2,
            "scenario_runway_months": 10.0,
            "delta_months": -0.2,
            "scenario": {"extra_monthly_spend": 15000, "one_time_cost": 0, "added_monthly_revenue": 0},
        },
    },
}

SAMPLE_REST = {
    "decision": "[SMOKE] Hire 5 engineers next quarter (~$95k/mo)?",
    "estimated_monthly_cost": 95000,
    "department": "Engineering",
    "decision_outcome": "APPROVE",
}

_failures: list[str] = []
_passes: list[str] = []


def check(condition: bool, label: str) -> None:
    if condition:
        _passes.append(label)
        print(f"  PASS  {label}")
    else:
        _failures.append(label)
        print(f"  FAIL  {label}")


def cleanup() -> int:
    """Delete approval requests (and their standalone obligations) created by the
    smoke run. Matches both the in-process source tag and the REST path (which the
    create endpoint tags source="api"), identified by the "[SMOKE]" title prefix."""
    removed = 0
    for key in R.keys(R.APPROVAL_PREFIX + "*"):
        doc = R.get_json(key)
        if doc and (doc.get("source") == SMOKE_SOURCE or str(doc.get("title", "")).startswith("[SMOKE]")):
            rid = doc.get("id")
            R.delete_key(key)
            removed += 1
            for okey in R.keys(R.OBLIGATION_PREFIX + "*"):
                odoc = R.get_json(okey)
                if odoc and odoc.get("request_id") == rid:
                    R.delete_key(okey)
    return removed


def smoke_inprocess() -> None:
    print("\n[1/2] In-process governance lifecycle")
    context = GOV.load_context()
    req = GOV.govern_recommendation(
        SAMPLE_INPROCESS["decision"],
        SAMPLE_INPROCESS["recommendation"],
        context,
        source=SMOKE_SOURCE,
        created_by="Atlas Council",
        created_by_type=ActorType.AGENT,
    )
    print(f"  created approval {req.id} · status={req.status.value} · {len(req.route)}-step route · {len(req.violations)} control(s)")

    stored = R.get_json(f"{R.APPROVAL_PREFIX}{req.id}")
    check(stored is not None, "approval request persisted to RedisJSON")
    check(stored.get("status") in {"pending_approval", "conditionally_approved", "approved", "rejected", "draft"}, "status is a valid lifecycle state")
    check(req.status.value == "pending_approval", "Datadog renewal routes to pending_approval (human sign-off required)")
    check(len(stored.get("route", [])) >= 1, "approval route has at least one approver step")
    check(len(stored.get("obligations", [])) >= 1, "post-decision obligations generated")
    check(len(stored.get("monitoring", [])) >= 1, "monitoring triggers generated")

    # INTEGRITY: nothing persisted as a human decision, no step pre-approved.
    human_decisions = [d for d in stored.get("decisions", []) if d.get("actor_type") == "human"]
    check(len(human_decisions) == 0, "no decision is falsely attributed to a human")
    approved_steps = [s for s in stored.get("route", []) if s.get("status") != "pending_approval"]
    check(len(approved_steps) == 0, "no approval step is pre-marked as decided")
    actions = {(d.get("actor_type"), d.get("action")) for d in stored.get("decisions", [])}
    check(("agent", "recommended") in actions, "council 'recommended' decision recorded (agent)")
    check(("system", "routed") in actions, "system 'routed' decision recorded (system)")

    # Audit stream is the immutable system of record.
    audit = [e for e in R.read_events(R.AUDIT_STREAM, count=100) if e.get("request_id") == req.id]
    check(len(audit) >= 1, f"audit stream has events for the request ({len(audit)} found)")
    check(any(e.get("type") == "request_created" for e in audit), "audit stream contains a request_created event")
    check(all(e.get("actor_type") in {"system", "agent", "service", "human"} for e in audit), "every audit event carries a valid actor_type")


def smoke_rest() -> None:
    print("\n[2/2] REST API governance lifecycle (FastAPI TestClient)")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Policies + RediSearch lookup
    r = client.get("/api/policies")
    check(r.status_code == 200 and len(r.json()) >= 7, "GET /api/policies returns the board policy rules")
    r = client.get("/api/policies/search", params={"q": "@category:{runway}"})
    runway = r.status_code == 200 and any(p.get("control_id") == "CTRL-RUNWAY-FLOOR" for p in r.json())
    check(runway, "GET /api/policies/search finds the runway-floor control by category")

    # Create a governed approval request
    r = client.post("/api/approvals", json=SAMPLE_REST)
    check(r.status_code == 201, f"POST /api/approvals creates a request (got {r.status_code})")
    if r.status_code != 201:
        return
    body = r.json()
    rid = body["id"]
    print(f"  created approval {rid} · status={body['status']} · blocked={body.get('blocked')}")
    check(body["status"] == "pending_approval", "engineer hire routes to pending_approval")
    check(body.get("blocked") is True, "runway-floor breach marks the request blocked (needs board exception)")
    check(body.get("human_approvals_pending") is True, "human approvals are pending (nothing auto-approved)")

    # Read it back + audit trail
    r = client.get(f"/api/approvals/{rid}")
    check(r.status_code == 200 and r.json()["id"] == rid, "GET /api/approvals/{id} returns the request")
    r = client.get("/api/audit", params={"request_id": rid})
    check(r.status_code == 200 and len(r.json()) >= 1, "GET /api/audit returns the request's audit events")

    # INTEGRITY GUARD: the system must not be able to approve.
    r = client.post(f"/api/approvals/{rid}/decisions", json={
        "actor": "atlas-governance", "actor_type": "system", "action": "approved", "rationale": "auto",
    })
    check(r.status_code == 403, f"system is REFUSED an 'approved' sign-off (got {r.status_code})")

    # INTEGRITY GUARD: a human approval needs an identity.
    r = client.post(f"/api/approvals/{rid}/decisions", json={
        "actor": "", "actor_type": "human", "action": "approved", "rationale": "x",
    })
    check(r.status_code == 400, f"empty human identity is REFUSED (got {r.status_code})")

    # A genuine operator-supplied human approval IS accepted (one step).
    r = client.post(f"/api/approvals/{rid}/decisions", json={
        "actor": "smoke-test-operator (CFO)", "actor_type": "human", "action": "approved",
        "rationale": "Smoke-test human sign-off on the CFO step.", "approver_role": "CFO",
    })
    accepted = r.status_code == 200
    check(accepted, f"a real human approver CAN record an 'approved' decision (got {r.status_code})")
    if accepted:
        updated = r.json()
        human = [d for d in updated["decisions"] if d.get("actor_type") == "human"]
        check(len(human) == 1, "exactly one human decision recorded, attributed to the operator")
        check(updated["status"] == "pending_approval", "request stays pending while other human steps remain unsigned")

    # Obligations endpoint
    r = client.get("/api/obligations")
    check(r.status_code == 200 and isinstance(r.json(), list), "GET /api/obligations returns the obligation list")


def main() -> int:
    print("=" * 72)
    print("Atlas governance smoke checks (live Redis, no mocks)")
    print("=" * 72)
    if not R.ping():
        print(f"FAIL: Redis not reachable at {R.REDIS_URL}. Start Redis Stack and seed first.")
        return 1
    # Ensure the governance namespace exists (idempotent).
    seed_governance(verbose=False)

    try:
        smoke_inprocess()
        smoke_rest()
    except Exception:
        print("\nUNEXPECTED ERROR during smoke checks:")
        traceback.print_exc()
        _failures.append("unexpected exception")
    finally:
        removed = cleanup()
        print(f"\nCleanup: removed {removed} smoke approval request(s) (audit stream left intact).")

    print("\n" + "-" * 72)
    print(f"RESULT: {len(_passes)} passed, {len(_failures)} failed")
    if _failures:
        print("FAILURES:")
        for f in _failures:
            print(f"  - {f}")
        print("\nGOVERNANCE SMOKE FAILED")
        return 1
    print("\nGOVERNANCE SMOKE PASSED — approvals are pending or system-generated, never falsely human-approved.")
    return 0


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    sys.exit(main())
