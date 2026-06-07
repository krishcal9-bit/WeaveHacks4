from __future__ import annotations

from src.documents.models import ParseJobStatus
from src.documents.pipeline import create_parse_job, run_parse_pipeline
from src.documents.store import get_parse_job
from src import redis_layer as R


def test_parse_pipeline_indexes_csv_document() -> None:
    assert R.ping(), "Redis must be running for parse pipeline coverage"
    raw = b"role,department,start_date\nAE,Sales,2026-08-01\n"
    job = create_parse_job(filename="headcount-plan.csv", connector_id="headcount_plan")
    meta = run_parse_pipeline(
        job.job_id,
        raw,
        filename="headcount-plan.csv",
        connector_id="headcount_plan",
        reconcile=False,
    )
    finished = get_parse_job(job.job_id)
    assert finished is not None
    assert finished.status in {ParseJobStatus.READY, ParseJobStatus.NEEDS_REVIEW}
    assert meta.chunk_count >= 1
    assert meta.source_category == "headcount_sheet"
