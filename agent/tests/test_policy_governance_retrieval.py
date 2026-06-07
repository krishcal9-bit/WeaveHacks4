"""
Operational policy/governance retrieval checks.

These tests intentionally hit Redis/RediSearch for the structured governance
rules and policy vector RAG paths. They keep the vector embedding deterministic
so the test proves Atlas' Redis plumbing without depending on an external model
call.
"""

from __future__ import annotations

import json

from src import redis_layer as R
from src.data.seed import seed_governance
from src.integrations import connectors as C
from src.integrations.models import SourceType
from src.tools import check_controls, obligations_if_approved, required_approvals, search_finance_policies


def _parse_board_policy_fixture():
    spec = C.CONNECTORS["board_policy"]
    path = C.fixture_path(spec)
    raw = path.read_bytes()
    fmt = C.detect_format(path)
    records, issues, duplicate_count = C.parse_records(spec, raw, fmt)
    assert not issues
    assert duplicate_count == 0
    return {record.policy_id: record for record in records}


def test_board_policy_fixture_carries_operational_governance_fields() -> None:
    policies = _parse_board_policy_fixture()

    assert set(policies) >= {"BP-1", "BP-4", "BP-6", "BP-7"}
    assert policies["BP-1"].control_id == "CTRL-BOARD-NOTIFY"
    assert policies["BP-1"].approval_route == ["CFO", "Board"]
    assert policies["BP-1"].notice_period_days == 7
    assert "Board notification memo" in policies["BP-1"].required_evidence
    assert policies["BP-4"].threshold == 9
    assert policies["BP-4"].exception_process
    assert "customer_data" in policies["BP-6"].data_sensitivity
    assert policies["BP-6"].obligations[0]["kind"] == "data_access_review"
    assert policies["BP-7"].control_id == "CTRL-FORECAST-CALIBRATION"


def test_structured_governance_policy_search_returns_routes_and_policy_ids() -> None:
    assert R.ping(), "Redis must be running for governance RediSearch retrieval"
    seed_governance(verbose=False)

    rows = R.search_govpolicies("@category:{vendor_spend}", limit=10)
    spend = next(row for row in rows if row["id"] == "gov-spend-cfo")

    assert spend["control_id"] == "CTRL-SPEND-CFO"
    assert spend["approval_route"] == ["Department Head", "Controller", "CFO"]
    assert "CFO approval memo" in spend["evidence_required"]
    assert spend["audit_requirements"]
    assert spend["obligations"][0]["kind"] == "approval_audit"


def test_governance_tools_surface_policy_refs_evidence_and_obligations() -> None:
    assert R.ping(), "Redis must be running for governance tool retrieval"
    seed_governance(verbose=False)

    payload = {
        "decision": "Approve a $180K customer-data vendor expansion for enterprise security evidence",
        "estimated_monthly_cost": 15_000,
        "estimated_one_time_cost": 0,
        "added_monthly_revenue": 0,
        "department": "Engineering",
        "data_sensitivity": "customer_data",
    }
    controls = json.loads(check_controls.invoke(payload))
    policy_ids = {item["policy_id"] for item in controls["controls_engaged"]}

    assert {"gov-spend-cfo", "gov-board-notify", "gov-data-security"} <= policy_ids
    assert all(item["evidence_required"] for item in controls["controls_engaged"])

    approvals = json.loads(required_approvals.invoke(payload))
    route_refs = {ref for step in approvals["approval_route"] for ref in step["policy_refs"]}
    assert {"gov-spend-cfo", "gov-board-notify", "gov-data-security"} <= route_refs

    obligations = json.loads(obligations_if_approved.invoke(payload))
    obligation_refs = {item["source_policy"] for item in obligations["obligations"]}
    assert {"gov-data-security", "gov-forecast-calibration"} <= obligation_refs


def test_vector_policy_rag_returns_stable_policy_id_and_source_id(monkeypatch) -> None:
    assert R.ping(), "Redis must be running for vector policy RAG retrieval"
    R.ensure_policy_index()

    policy_id = "pol-test-governance-route"
    vector = [0.0] * R.EMBED_DIM
    vector[0] = 1.0

    def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        return [vector for _ in texts]

    monkeypatch.setattr(R, "embed_texts", fake_embed_texts)
    R.upsert_policy(
        policy_id,
        text=(
            "Policy ID pol-test-governance-route requires CFO and Board approval, "
            "13-day notice period, board memo evidence, audit trail retention, and "
            "a runway exception obligation."
        ),
        kind="policy",
        title="Test governance route policy",
        embedding=vector,
        source_id=policy_id,
    )
    try:
        hits = json.loads(search_finance_policies.invoke({
            "query": "pol-test-governance-route CFO Board approval route notice period audit obligation"
        }))
        match = next(hit for hit in hits if hit["policy_id"] == policy_id)
        assert match["source_id"] == policy_id
        assert match["kind"] == SourceType.BOARD_POLICY.value or match["kind"] == "policy"
        assert "13-day notice" in match["text"]
    finally:
        R.delete_key(f"{R.POLICY_PREFIX}{policy_id}")
