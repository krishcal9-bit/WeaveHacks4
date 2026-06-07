from __future__ import annotations

from src import redis_layer as R
from src.documents.models import DocumentRetrievalFilter
from src.documents.store import search_document_chunks
from src.openai_council import enforce_role_specific_evidence_plan, gather_role_evidence
from src.structured_models import DecisionPlan, DecisionType, RoleEvidencePlan
from tests.document_test_helpers import reset_document_store, seed_document


def _seed_scenario_corpus() -> None:
    reset_document_store()
    seed_document(
        filename="datadog-msa-renewal.pdf",
        kind="pdf",
        source_category="vendor_contract",
        text="Datadog master services agreement renewal notice window 45 days auto-renew",
        vendor="Datadog",
    )
    seed_document(
        filename="datadog-invoice-june.pdf",
        kind="pdf",
        source_category="invoice",
        text="Datadog invoice $15,000 due 2026-06-30 renewal billing cadence",
        vendor="Datadog",
    )
    seed_document(
        filename="procurement-renewal-notes.txt",
        kind="txt",
        source_category="procurement_note",
        text="Procurement notes: consolidate observability vendors and renegotiate Datadog renewal",
    )
    seed_document(
        filename="headcount-plan-q3.csv",
        kind="csv",
        source_category="headcount_sheet",
        text="role,department,start_date,fully_loaded_cost\nSenior AE,Sales,2026-08-01,18500",
    )
    seed_document(
        filename="board-headcount-approval.docx",
        kind="docx",
        source_category="board_approval",
        text="Board approved 12 incremental sales hires subject to plan-vs-actual review",
    )
    seed_document(
        filename="security-evidence-open-finding.json",
        kind="json",
        source_category="security_evidence",
        text='{"finding":"SOC2 CC6.1 gap","severity":"high","blocker":true}',
    )
    seed_document(
        filename="financing-bridge-memo.pdf",
        kind="pdf",
        source_category="financing_memo",
        text="Bridge financing memo $5M runway extension covenant package board review",
    )


def test_source_aware_rag_filters_vendor_renewal_chunks() -> None:
    assert R.ping(), "Redis must be running for source-aware RAG coverage"
    _seed_scenario_corpus()
    filters = DocumentRetrievalFilter(
        source_categories=["vendor_contract", "invoice", "procurement_note"],
        vendor="Datadog",
    )
    hits = search_document_chunks("Datadog renewal contract invoice procurement", filters=filters, k=6)
    categories = {hit["source_category"] for hit in hits}
    assert categories.issubset({"vendor_contract", "invoice", "procurement_note"})
    assert "headcount_sheet" not in categories
    assert "security_evidence" not in categories
    assert len(hits) <= 6


def test_source_aware_rag_filters_hiring_plan_chunks() -> None:
    assert R.ping(), "Redis must be running for source-aware RAG coverage"
    _seed_scenario_corpus()
    filters = DocumentRetrievalFilter(source_categories=["headcount_sheet", "board_approval"])
    hits = search_document_chunks("headcount hiring board approval", filters=filters, k=6)
    categories = {hit["source_category"] for hit in hits}
    assert categories.issubset({"headcount_sheet", "board_approval"})
    assert "vendor_contract" not in categories
    assert len(hits) <= 6


def test_source_aware_rag_filters_security_blocker_chunks() -> None:
    assert R.ping(), "Redis must be running for source-aware RAG coverage"
    _seed_scenario_corpus()
    filters = DocumentRetrievalFilter(source_categories=["security_evidence", "policy_doc"])
    hits = search_document_chunks("security blocker SOC2 audit finding", filters=filters, k=4)
    assert hits
    assert all(hit["source_category"] in {"security_evidence", "policy_doc"} for hit in hits)
    assert all(hit["source_category"] != "vendor_contract" for hit in hits)


def test_source_aware_rag_filters_financing_scenario_chunks() -> None:
    assert R.ping(), "Redis must be running for source-aware RAG coverage"
    _seed_scenario_corpus()
    filters = DocumentRetrievalFilter(source_categories=["financing_memo", "board_approval"])
    hits = search_document_chunks("bridge financing runway covenant", filters=filters, k=4)
    assert hits
    assert all(hit["source_category"] in {"financing_memo", "board_approval"} for hit in hits)


def test_planner_attaches_document_routes_for_renewal_and_hiring() -> None:
    renewal_plan = enforce_role_specific_evidence_plan(
        DecisionPlan(
            decision_type=DecisionType.vendor_renewal,
            title="Datadog renewal",
            summary="Renew Datadog contract",
            entities=["Datadog", "$180K"],
            required_facts=[],
            assumptions=[],
            follow_up_questions=[],
            role_plans=[
                RoleEvidencePlan(
                    role="procurement",
                    tools=["list_vendors"],
                    policy_queries=["vendor renewal policy"],
                    focus_slices=["vendors"],
                    prior_decisions=[],
                    rationale="Commercial review.",
                )
            ],
            decision_specific_focus=["renewal cost"],
        )
    )
    procurement = next(plan for plan in renewal_plan.role_plans if plan.role == "procurement")
    assert procurement.document_source_categories
    assert "vendor_contract" in procurement.document_source_categories
    assert "search_uploaded_documents" in procurement.tools

    hiring_plan = enforce_role_specific_evidence_plan(
        DecisionPlan(
            decision_type=DecisionType.hiring_plan,
            title="Sales hiring plan",
            summary="Approve incremental sales hires",
            entities=["Sales", "12 hires"],
            required_facts=[],
            assumptions=[],
            follow_up_questions=[],
            role_plans=[
                RoleEvidencePlan(
                    role="fpna",
                    tools=["get_company_financials"],
                    policy_queries=["hiring plan policy"],
                    focus_slices=["hiring_plan"],
                    prior_decisions=[],
                    rationale="Forecast impact.",
                )
            ],
            decision_specific_focus=["fully loaded cost"],
        )
    )
    fpna = next(plan for plan in hiring_plan.role_plans if plan.role == "fpna")
    assert "headcount_sheet" in fpna.document_source_categories


def test_gather_role_evidence_caps_uploaded_document_hits() -> None:
    assert R.ping(), "Redis must be running for council document retrieval coverage"
    _seed_scenario_corpus()
    role_plan = RoleEvidencePlan(
        role="procurement",
        tools=["search_uploaded_documents"],
        policy_queries=[],
        document_queries=["Datadog renewal contract invoice procurement"],
        document_source_categories=["vendor_contract", "invoice", "procurement_note"],
        document_kinds=["pdf", "txt"],
        document_rationale="Renewal needs contract, invoice, and procurement notes.",
        focus_slices=["vendors"],
        prior_decisions=[],
        rationale="Commercial evidence.",
    )
    bundle = gather_role_evidence(
        role_plan,
        {"financials": {}, "vendors": []},
        decision="Should we renew Datadog?",
        decision_type="vendor_renewal",
        entities=["Datadog"],
    )
    uploaded = bundle.evidence.get("uploaded_documents") or []
    assert uploaded
    assert len(uploaded) <= 8
    assert bundle.evidence.get("document_citations")
