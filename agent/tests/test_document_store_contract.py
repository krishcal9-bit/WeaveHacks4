from __future__ import annotations

from src import redis_layer as R
from src.documents.models import DocumentRetrievalFilter
from src.documents.store import delete_document, get_document, list_documents, search_document_chunks
from tests.document_test_helpers import reset_document_store, seed_document


def test_document_store_crud_and_list() -> None:
    assert R.ping(), "Redis must be running for document store coverage"
    reset_document_store()
    meta = seed_document(
        filename="datadog-contract.pdf",
        kind="pdf",
        source_category="vendor_contract",
        text="Datadog renewal auto-renew 45-day notice $180K annual",
        vendor="Datadog",
    )
    loaded = get_document(meta.doc_id)
    assert loaded is not None
    assert loaded.source_category == "vendor_contract"
    docs, total = list_documents(source_category="vendor_contract")
    assert total >= 1
    assert any(doc.doc_id == meta.doc_id for doc in docs)
    assert delete_document(meta.doc_id)
    assert get_document(meta.doc_id) is None
