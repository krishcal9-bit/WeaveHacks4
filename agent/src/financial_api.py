"""
Financial-OS REST routes — scenarios, operating collections, knowledge RAG, and
the Redis key/index map. Exposed as a sub-router (``financial_router``) that
``src.api`` mounts onto its ``/api`` router, so the hot ``api.py`` route
definitions stay untouched.

Read-only except ``POST /scenarios`` (which forks the live company state). Every
number is served from the Redis system of record via the scenario engine and
``redis_store`` — no fabricated data.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src import redis_models as M
from src import redis_store as S
from src import scenario_engine as E

# No prefix — src.api mounts this under its own "/api" router.
financial_router = APIRouter(tags=["financial-os"])


class ScenarioRequest(BaseModel):
    name: str
    changes: list[M.ScenarioChange] = []
    description: str = ""
    tags: list[str] = []


# --- Scenarios (static paths declared before the dynamic /{id}) ------------- #
@financial_router.get("/scenarios")
def list_scenarios(limit: int = 50) -> list:
    return E.list_scenarios(limit=limit)


@financial_router.get("/scenarios/compare")
def compare_scenarios(ids: str = "") -> dict:
    scenario_ids = [s.strip() for s in ids.split(",") if s.strip()]
    return E.compare_scenarios(scenario_ids)


@financial_router.get("/scenarios/search")
def search_scenarios(q: str = "*", tag: str = "", limit: int = 25) -> list:
    filters = {"tags": tag} if tag else None
    return E.search_scenarios(q or "*", filters=filters, limit=limit)


@financial_router.get("/scenarios/{scenario_id}")
def get_scenario(scenario_id: str) -> dict:
    doc = E.get_scenario(scenario_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario_id}")
    return doc


@financial_router.post("/scenarios")
def create_scenario(req: ScenarioRequest) -> dict:
    """Fork the live company state into a new what-if branch (persisted to Redis)."""
    scenario = E.create_scenario(req.name, req.changes, description=req.description, tags=req.tags)
    return scenario.model_dump()


@financial_router.delete("/scenarios/{scenario_id}")
def delete_scenario(scenario_id: str) -> dict:
    removed = E.delete_scenario(scenario_id)
    return {"deleted": bool(removed), "id": scenario_id}


# --- Operating collections -------------------------------------------------- #
@financial_router.get("/departments")
def departments() -> list:
    return S.scan_collection(M.DEPARTMENT_PREFIX)


@financial_router.get("/invoices")
def invoices(status: str = "") -> list:
    filters = {"status": status} if status else None
    return S.search_index(M.INVOICE_INDEX, "*", filters=filters, sort_by="days_overdue", ascending=False, limit=100)


@financial_router.get("/purchase-orders")
def purchase_orders(status: str = "") -> list:
    filters = {"status": status} if status else None
    return S.search_index(M.PO_INDEX, "*", filters=filters, sort_by="amount", ascending=False, limit=100)


@financial_router.get("/contracts")
def contracts(segment: str = "") -> list:
    filters = {"segment": segment} if segment else None
    return S.search_index(M.CONTRACT_INDEX, "*", filters=filters, sort_by="arr", ascending=False, limit=100)


@financial_router.get("/arr-movements")
def arr_movements() -> list:
    return sorted(S.scan_collection(M.ARR_PREFIX), key=lambda m: m.get("month", ""))


# --- Vector knowledge RAG + Redis map --------------------------------------- #
@financial_router.get("/knowledge/search")
def knowledge_search(q: str, kind: str = "", k: int = 5) -> list:
    kinds = [kind] if kind else None
    return S.search_knowledge(q, k=k, kinds=kinds)


@financial_router.get("/redis-map")
def redis_map() -> dict:
    """The full financial-OS Redis key/index/stream map plus live counts —
    powers the 'Redis as system of record' view and the preflight handoff."""
    return S.redis_overview()
