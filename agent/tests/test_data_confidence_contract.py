from __future__ import annotations

import json

from src import redis_layer as R
from src.data.seed import seed
from src.integrations import service as OPS
from src.openai_council import (
    ROLE_DIRECTIVES,
    _PROMPT_TEMPLATES,
    fpna_evidence_preferences,
    procurement_evidence_preferences,
    risk_evidence_preferences,
    treasury_evidence_preferences,
)
from src.tools import get_operations_data_confidence


def _seed_messy_operations() -> None:
    assert R.ping(), "Redis must be running for operations confidence smoke coverage"
    seed(verbose=False)
    OPS.reset_demo_state()
    OPS.run_import(demo=True)


def test_import_confidence_scores_messy_sources_and_reconciliation() -> None:
    _seed_messy_operations()
    report = OPS.run_reconciliation()
    confidence = report.confidence

    assert confidence.sources_imported == confidence.sources_total
    assert confidence.score < 95
    assert confidence.validation_failure_count >= 1
    assert confidence.duplicate_count >= 1
    assert confidence.reconciliation_discrepancy_count >= 10
    assert confidence.average_source_age_days is not None
    assert confidence.oldest_source_age_days is not None
    assert "duplicate" in " ".join(confidence.confidence_reasons).lower()
    assert "reconciliation" in " ".join(confidence.confidence_reasons).lower()
    assert confidence.components["duplicate_factor"] < 1
    assert confidence.components["reconciliation_factor"] < 1
    assert any(
        source.source_type.value == "invoices"
        and source.rejected_count >= 1
        and source.duplicate_count >= 1
        and source.score < 100
        for source in confidence.source_confidence
    )
    assert any(
        source.reconciliation_status == "needs_review"
        and "reconciliation needs review" in source.reasons
        for source in confidence.source_confidence
    )

    tool_payload = json.loads(get_operations_data_confidence.invoke({}))
    assert tool_payload["score"] == confidence.score
    assert tool_payload["validation_failure_count"] >= 1
    assert tool_payload["duplicate_count"] >= 1
    assert tool_payload["reconciliation_discrepancy_count"] >= 10
    assert tool_payload["source_confidence"]


def test_operations_context_exposes_confidence_and_freshness_to_council_evidence() -> None:
    _seed_messy_operations()
    OPS.run_reconciliation()
    snapshot = OPS.operations_context_snapshot()

    assert snapshot is not None
    assert snapshot["confidence"]["score"] < 95
    assert snapshot["confidence"]["confidence_reasons"]
    assert snapshot["confidence"]["duplicate_count"] >= 1
    assert snapshot["confidence"]["reconciliation_discrepancy_count"] >= 10
    assert all("confidence_score" in source for source in snapshot["sources"])
    assert all("freshness_days" in source for source in snapshot["sources"])
    assert any(source["confidence_reasons"] for source in snapshot["sources"])


def test_agent_prompts_and_role_evidence_plans_require_confidence_freshness() -> None:
    analyst_template = _PROMPT_TEMPLATES["treasury"].lower()
    cfo_template = _PROMPT_TEMPLATES["cfo"].lower()
    directives = " ".join(ROLE_DIRECTIVES.values()).lower()

    assert "source confidence/freshness" in analyst_template
    assert "validation failures/duplicates" in analyst_template
    assert "required facts are missing" in analyst_template
    assert "confidence score, freshness age" in cfo_template
    assert "ruling conditions" in cfo_template
    assert "confidence" in directives

    preference_sets = [
        treasury_evidence_preferences(),
        fpna_evidence_preferences(),
        risk_evidence_preferences(),
        procurement_evidence_preferences(),
    ]
    for prefs in preference_sets:
        assert "get_operations_data_confidence" in prefs["tools"]
        assert "list_operations_sources" in prefs["tools"]
