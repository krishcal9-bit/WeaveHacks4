"""
Financial-OS LangChain tools — scenario branches, vector knowledge RAG, and the
operating collections (departments, invoices, purchase orders, contracts, ARR).

Every tool is grounded in the Redis system of record (via :mod:`src.scenario_engine`
and :mod:`src.redis_store`) so the council reasons over real, reconciled numbers.
These are registered into ``src.tools.ALL_TOOLS`` so they join the council's tool
surface without touching the hot ``tools.py`` definitions.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool

from src import redis_models as M
from src import redis_store as S
from src import scenario_engine as E


def _trim(doc: dict, fields: tuple[str, ...]) -> dict:
    return {f: doc.get(f) for f in fields if f in doc}


# --------------------------------------------------------------------------- #
# Scenario tools
# --------------------------------------------------------------------------- #
@tool
def run_scenario(name: str, changes_json: str, description: str = "") -> str:
    """Fork Acme Corp's live financials into a named what-if scenario and compute
    its board-grade impact (runway, burn multiple, gross margin, CAC payback) plus
    any board-constraint violations. The branch is saved to Redis so it is
    searchable and comparable.

    changes_json: a JSON array of changes. Each change has a "type" and the
    fields that type needs. Supported types and fields:
      • hire: team, roles, monthly_cost
      • vendor_renegotiation: vendor_id, new_annual_cost  (or pct)
      • revenue_slip: pct  (or amount)
      • churn_shock: segment, pct  (or amount)
      • compliance_blocker: control, blocked_arr
      • financing: financing_type (equity|debt|grant), amount
      • capex: one_time
      • opex_change: monthly_cost (signed)
    Example: '[{"type":"hire","team":"Engineering","roles":5,"monthly_cost":95000}]'
    """
    try:
        raw = json.loads(changes_json) if changes_json.strip() else []
        if isinstance(raw, dict):
            raw = [raw]
        changes = E.coerce_changes(raw)
    except Exception as exc:
        return json.dumps({"error": f"could not parse changes_json: {exc}"})

    scenario = E.create_scenario(name, changes, description=description)
    return json.dumps({
        "id": scenario.id,
        "name": scenario.name,
        "summary": scenario.summary,
        "baseline": scenario.baseline.model_dump(),
        "projected": scenario.projected.model_dump(),
        "deltas": scenario.deltas,
        "violations": [v.model_dump() for v in scenario.violations],
    }, default=str)


@tool
def list_scenarios(limit: int = 15) -> str:
    """List saved what-if scenario branches with their headline metrics and
    board-constraint violation counts, newest first."""
    rows = E.list_scenarios(limit=limit)
    out = []
    for d in rows:
        out.append({
            "id": d.get("id"),
            "name": d.get("name"),
            "summary": d.get("summary"),
            "runway_months": (d.get("projected") or {}).get("runway_months"),
            "burn_multiple": (d.get("projected") or {}).get("burn_multiple"),
            "violation_count": d.get("violation_count"),
            "tags": d.get("tags"),
        })
    return json.dumps(out, default=str)


@tool
def compare_scenarios(scenario_ids: str) -> str:
    """Compare saved scenarios side-by-side against the live baseline on the
    board metrics (runway, net burn, gross margin, burn multiple, CAC payback,
    cash, ARR). scenario_ids: comma-separated scenario ids."""
    ids = [s.strip() for s in scenario_ids.split(",") if s.strip()]
    return json.dumps(E.compare_scenarios(ids), default=str)


@tool
def search_scenarios(query: str = "*", tag: str = "") -> str:
    """Search saved scenario branches by free text (name/summary) and optional
    tag (e.g. financing, hiring, procurement, churn, downside)."""
    filters = {"tags": tag} if tag else None
    rows = E.search_scenarios(query or "*", filters=filters, limit=20)
    return json.dumps([_trim(d, ("id", "name", "summary", "violation_count", "tags")) for d in rows], default=str)


# --------------------------------------------------------------------------- #
# Vector knowledge RAG (policies + decisions + vendor clauses + audit findings)
# --------------------------------------------------------------------------- #
@tool
def search_finance_knowledge(query: str, kind: str = "") -> str:
    """Semantic search over Acme Corp's full finance knowledge corpus: policies,
    past board decisions, vendor contract clauses, and audit findings. Optionally
    restrict by kind: policy | decision | vendor_clause | audit_finding. Returns
    ranked hits with similarity score and metadata for grounding recommendations."""
    kinds = [kind] if kind else None
    hits = S.search_knowledge(query, k=5, kinds=kinds)
    return json.dumps([
        {"title": h["title"], "kind": h["kind"], "source_id": h["source_id"],
         "severity": h["severity"], "score": h["score"], "text": h["text"]}
        for h in hits
    ], default=str)


# --------------------------------------------------------------------------- #
# Operating collections (read-only, structured)
# --------------------------------------------------------------------------- #
@tool
def list_departments() -> str:
    """List Acme Corp's departments with owner, headcount, monthly budget, and
    YTD budget vs. spend — useful for cost-center and burn analysis."""
    rows = S.scan_collection(M.DEPARTMENT_PREFIX)
    return json.dumps([
        _trim(d, ("id", "name", "head", "cost_center", "headcount", "monthly_budget", "ytd_budget", "ytd_spend"))
        for d in rows
    ], default=str)


@tool
def list_invoices(status: str = "") -> str:
    """List accounts-receivable invoices (customer, amount, due date, status,
    days overdue). Optionally filter by status: paid | outstanding | overdue."""
    filters = {"status": status} if status else None
    rows = S.search_index(M.INVOICE_INDEX, "*", filters=filters, sort_by="days_overdue", ascending=False, limit=50)
    return json.dumps([
        _trim(d, ("id", "customer", "segment", "amount", "due", "status", "days_overdue"))
        for d in rows
    ], default=str)


@tool
def list_purchase_orders(status: str = "") -> str:
    """List purchase orders against vendors (amount, department, status, approval
    status). Optionally filter by status: draft | open | approved | received."""
    filters = {"status": status} if status else None
    rows = S.search_index(M.PO_INDEX, "*", filters=filters, sort_by="amount", ascending=False, limit=50)
    return json.dumps([
        _trim(d, ("id", "vendor_id", "description", "amount", "department", "status", "approval_status"))
        for d in rows
    ], default=str)


@tool
def list_customer_contracts(segment: str = "") -> str:
    """List top customer contracts (ARR, term, renewal, status). Optionally filter
    by segment: 'Enterprise 3PL' | 'Mid-market fulfillment' | 'Pilot customers'."""
    filters = {"segment": segment} if segment else None
    rows = S.search_index(M.CONTRACT_INDEX, "*", filters=filters, sort_by="arr", ascending=False, limit=50)
    return json.dumps([
        _trim(d, ("id", "customer", "segment", "arr", "end_date", "auto_renew", "status", "owner"))
        for d in rows
    ], default=str)


@tool
def list_arr_movements() -> str:
    """Acme Corp's monthly ARR movements (new, expansion, contraction, churned,
    net-new, ending ARR) — the bookings bridge behind growth and burn multiple."""
    rows = sorted(S.scan_collection(M.ARR_PREFIX), key=lambda m: m.get("month", ""))
    return json.dumps(rows, default=str)


# Financial-OS tool surface (registered into src.tools.ALL_TOOLS).
SCENARIO_TOOLS = [run_scenario, list_scenarios, compare_scenarios, search_scenarios]
KNOWLEDGE_TOOLS = [search_finance_knowledge]
COLLECTION_TOOLS = [list_departments, list_invoices, list_purchase_orders, list_customer_contracts, list_arr_movements]
FINANCIAL_OS_TOOLS = [*SCENARIO_TOOLS, *KNOWLEDGE_TOOLS, *COLLECTION_TOOLS]
