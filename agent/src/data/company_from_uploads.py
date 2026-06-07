"""
Derive the company system-of-record from the operator's UPLOADED finance data.

Atlas is upload-first: there is no seeded demo company. When the operator imports
their real finance feeds (ledger, invoices, vendor register, CRM pipeline,
headcount plan, security evidence, board policy), this module computes the
canonical company record (``atlas:company:<id>``) and the vendor index
(``atlas:vendor:*``) directly from that data — so the council, the voice agent,
the dashboard, planning, and governance all reason about the *uploaded* company,
never a fabricated one.

Strict live-only contract: every figure here is COMPUTED from uploaded records
(with provenance recorded on the record). Nothing is invented; fields that cannot
be honestly derived are omitted rather than guessed. Company *identity* (name,
founded, HQ, description) is taken only from explicit metadata the operator's own
files carry — never hallucinated.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Optional

from src import redis_layer as R
from src.env import redact_secrets
from src.integrations import store
from src.integrations.models import (
    BoardPolicyDoc,
    CrmOpportunity,
    HeadcountPlanRow,
    Invoice,
    LedgerEntry,
    SecurityEvidence,
    SourceType,
    VendorRecord,
)
from src.redis_models import COMPANY_ID, COMPANY_KEY

# Non-secret identity hints accumulated across uploaded files (company name etc.).
COMPANY_PROFILE_KEY = f"{R.NS}:company:profile"

_DATASET_MODELS = {
    SourceType.LEDGER: LedgerEntry,
    SourceType.INVOICES: Invoice,
    SourceType.VENDOR_EXPORT: VendorRecord,
    SourceType.CRM_OPPORTUNITIES: CrmOpportunity,
    SourceType.HEADCOUNT_PLAN: HeadcountPlanRow,
    SourceType.SECURITY_EVIDENCE: SecurityEvidence,
    SourceType.BOARD_POLICY: BoardPolicyDoc,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(source_type: SourceType, model) -> list:
    """Re-validate persisted dataset rows into typed records (skip stragglers)."""
    out = []
    for row in store.load_dataset(source_type.value):
        try:
            out.append(model.model_validate(row))
        except Exception:
            continue
    return out


def has_uploaded_datasets() -> bool:
    return any(store.load_dataset(st.value) for st in _DATASET_MODELS)


# --------------------------------------------------------------------------- #
# Company identity — captured only from explicit metadata in uploaded files.
# --------------------------------------------------------------------------- #
def _parse_identity_from_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    identity: dict[str, Any] = {}
    name = (
        meta.get("fictional_company")
        or meta.get("company")
        or meta.get("company_name")
        or meta.get("entity")
        or meta.get("organization")
    )
    if isinstance(name, str) and name.strip():
        identity["name"] = name.strip()

    note = meta.get("source_note") or meta.get("description")
    if isinstance(note, str) and note.strip():
        identity["description"] = note.strip()
        year = re.search(r"founded\s+(\d{4})", note, re.IGNORECASE)
        if year:
            identity["founded"] = int(year.group(1))
        # "Hawthorne CA" / "Hawthorne, CA" style HQ hint after a city name.
        hq = re.search(r"\b([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?),?\s([A-Z]{2})\b", note)
        if hq:
            identity["hq"] = f"{hq.group(1)}, {hq.group(2)}"

    for key in ("sector", "industry", "stage", "hq", "headquarters", "founded"):
        value = meta.get(key)
        if value not in (None, ""):
            identity[("hq" if key == "headquarters" else "sector" if key == "industry" else key)] = value
    return identity


def capture_company_profile(raw: bytes, *, source_name: str) -> dict[str, Any]:
    """Best-effort: pull company identity from an uploaded JSON file's metadata
    envelope and merge it into the persisted, non-secret company profile.

    Only JSON uploads carry an identity envelope; CSV uploads are ignored here.
    Never fabricates a name — if the file carries none, nothing is stored.
    """
    profile = R.get_json(COMPANY_PROFILE_KEY) or {}
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception:
        return profile
    if not isinstance(data, dict):
        return profile
    meta = data.get("metadata")
    if not isinstance(meta, dict):
        return profile

    identity = _parse_identity_from_metadata(meta)
    if not identity:
        return profile

    profile.update({k: v for k, v in identity.items() if v not in (None, "")})
    profile["updated"] = _now_iso()
    profile.setdefault("sources", [])
    if source_name and source_name not in profile["sources"]:
        profile["sources"].append(source_name)
    try:
        R.set_json(COMPANY_PROFILE_KEY, profile)
    except Exception:
        pass
    return profile


# --------------------------------------------------------------------------- #
# Financial derivation from uploaded operating data
# --------------------------------------------------------------------------- #
def _month(d: Optional[date]) -> Optional[str]:
    return d.strftime("%Y-%m") if isinstance(d, date) else None


def _ledger_financials(ledger: list[LedgerEntry]) -> dict[str, Any]:
    """Cash-basis financials computed from the general ledger.

    Revenue rows (category/account "revenue") are inflows; everything else is an
    outflow. Cash position is the cumulative net of those flows over the imported
    period (provenance makes the basis explicit — this is a ledger-derived net
    position, not a balance-sheet cash figure).
    """
    if not ledger:
        return {}
    monthly_rev: dict[str, float] = defaultdict(float)
    monthly_exp: dict[str, float] = defaultdict(float)
    for entry in ledger:
        ym = _month(entry.date)
        if ym is None:
            continue
        amount = float(entry.amount or 0.0)
        is_revenue = (entry.category or "").strip().lower() == "revenue" or "revenue" in (
            entry.account or ""
        ).lower()
        if is_revenue:
            monthly_rev[ym] += abs(amount)
        else:
            monthly_exp[ym] += abs(amount)

    months = sorted(set(monthly_rev) | set(monthly_exp))
    if not months:
        return {}
    n = len(months)
    total_rev = sum(monthly_rev.values())
    total_exp = sum(monthly_exp.values())
    avg_rev = total_rev / n
    avg_exp = total_exp / n
    net_burn = round(avg_exp - avg_rev)

    cash_history: list[dict[str, Any]] = []
    cumulative = 0.0
    for ym in months:
        net = monthly_rev[ym] - monthly_exp[ym]
        cumulative += net
        cash_history.append(
            {"month": ym, "cash": round(cumulative), "net_burn": round(monthly_exp[ym] - monthly_rev[ym])}
        )

    cash_on_hand = round(cumulative)
    out: dict[str, Any] = {
        "monthly_revenue": round(avg_rev),
        "mrr": round(avg_rev),
        "arr": round(avg_rev * 12),
        "monthly_gross_burn": round(avg_exp),
        "monthly_net_burn": net_burn,
        "cash_on_hand": cash_on_hand,
        "cash_history": cash_history[-12:],
        "ledger_period_months": n,
    }
    if net_burn > 0 and cash_on_hand > 0:
        out["runway_months"] = round(cash_on_hand / net_burn, 1)
    elif net_burn <= 0:
        out["runway_months"] = None
        out["cash_flow_positive"] = True
    return out


def _pipeline_by_stage(opps: list[CrmOpportunity]) -> list[dict[str, Any]]:
    by_stage: dict[str, dict[str, float]] = defaultdict(lambda: {"opportunities": 0, "arr": 0.0, "weighted_arr": 0.0})
    for opp in opps:
        bucket = by_stage[opp.stage or "Unstaged"]
        bucket["opportunities"] += 1
        bucket["arr"] += float(opp.arr or 0.0)
        bucket["weighted_arr"] += float(opp.weighted())
    return [
        {
            "stage": stage,
            "opportunities": int(vals["opportunities"]),
            "arr": round(vals["arr"]),
            "weighted_arr": round(vals["weighted_arr"]),
        }
        for stage, vals in sorted(by_stage.items(), key=lambda kv: kv[1]["arr"], reverse=True)
    ]


def _hiring_plan(headcount: list[HeadcountPlanRow]) -> list[dict[str, Any]]:
    by_team: dict[str, dict[str, Any]] = defaultdict(lambda: {"roles": 0, "monthly_cost": 0.0, "start_month": None})
    for row in headcount:
        bucket = by_team[row.team or "Unassigned"]
        bucket["roles"] += int(row.headcount or 0)
        bucket["monthly_cost"] += float(row.loaded_monthly_cost())
        if row.start_month and (bucket["start_month"] is None or row.start_month < bucket["start_month"]):
            bucket["start_month"] = row.start_month
    return [
        {"team": team, "roles": int(v["roles"]), "monthly_cost": round(v["monthly_cost"]), "start_month": v["start_month"]}
        for team, v in sorted(by_team.items(), key=lambda kv: kv[1]["monthly_cost"], reverse=True)
    ]


def _security_findings(security: list[SecurityEvidence]) -> tuple[list[dict[str, Any]], float]:
    findings: list[dict[str, Any]] = []
    blocked_arr = 0.0
    for control in security:
        if (control.status or "").lower() in ("satisfied", "passed", "ok", "complete"):
            continue
        findings.append(
            {
                "control_id": control.control_id,
                "title": control.title,
                "status": control.status,
                "blocks_revenue": bool(control.blocks_revenue),
                "blocked_arr": control.blocked_arr,
                "summary": control.summary,
            }
        )
        if control.blocks_revenue and control.blocked_arr:
            blocked_arr += float(control.blocked_arr)
    return findings, round(blocked_arr)


def _board_constraints(board: list[BoardPolicyDoc]) -> list[dict[str, Any]]:
    return [
        {
            "policy_id": doc.policy_id,
            "title": doc.title,
            "rule": doc.rule,
            "threshold": doc.threshold,
            "unit": doc.unit,
            "text": doc.text,
        }
        for doc in board
    ]


def derive_company_record() -> Optional[dict[str, Any]]:
    """Compute the canonical company record from uploaded datasets.

    Returns ``None`` when nothing has been uploaded (so the company stays empty
    and the UI prompts the operator to upload), otherwise a record dict grounded
    entirely in the imported data with derivation provenance attached.
    """
    ledger = _load(SourceType.LEDGER, LedgerEntry)
    invoices = _load(SourceType.INVOICES, Invoice)
    vendors = _load(SourceType.VENDOR_EXPORT, VendorRecord)
    opps = _load(SourceType.CRM_OPPORTUNITIES, CrmOpportunity)
    headcount = _load(SourceType.HEADCOUNT_PLAN, HeadcountPlanRow)
    security = _load(SourceType.SECURITY_EVIDENCE, SecurityEvidence)
    board = _load(SourceType.BOARD_POLICY, BoardPolicyDoc)

    if not any([ledger, invoices, vendors, opps, headcount, security, board]):
        return None

    profile = R.get_json(COMPANY_PROFILE_KEY) or {}
    record: dict[str, Any] = {
        "id": COMPANY_ID,
        "updated": date.today().isoformat(),
        "derived_from_uploads": True,
    }

    # Identity (only from explicit uploaded metadata).
    if profile.get("name"):
        record["name"] = profile["name"]
    for key in ("description", "sector", "stage", "hq", "founded"):
        if profile.get(key) not in (None, ""):
            record[key] = profile[key]

    # Financials from the general ledger.
    record.update(_ledger_financials(ledger))

    # Headcount (sum of the uploaded roster/plan rows).
    if headcount:
        record["headcount"] = sum(int(h.headcount or 0) for h in headcount)
        record["hiring_plan"] = _hiring_plan(headcount)

    # Pipeline / weighted ARR from CRM.
    if opps:
        pipeline = _pipeline_by_stage(opps)
        record["pipeline_by_stage"] = pipeline
        record["pipeline_weighted_arr"] = round(sum(p["weighted_arr"] for p in pipeline))

    # Vendor commitments.
    if vendors:
        record["vendor_count"] = len(vendors)
        record["annual_vendor_spend"] = round(sum(float(v.annual_cost or 0.0) for v in vendors))

    # Outstanding payables.
    if invoices:
        open_invoices = [i for i in invoices if (i.status or "").lower() not in ("paid", "closed")]
        record["accounts_payable_open"] = round(
            sum(float(i.balance_due if i.balance_due is not None else i.amount or 0.0) for i in open_invoices)
        )
        record["open_invoice_count"] = len(open_invoices)

    # Security posture + board constraints.
    if security:
        findings, blocked_arr = _security_findings(security)
        record["security_incidents"] = findings
        record["audit_findings"] = findings
        if blocked_arr:
            record["security_blocked_arr"] = blocked_arr
    if board:
        record["board_constraints"] = _board_constraints(board)

    record["provenance"] = {
        "derived_from": "uploaded_operations_data",
        "computed_at": _now_iso(),
        "data_sources": {
            "ledger": len(ledger),
            "invoices": len(invoices),
            "vendor_export": len(vendors),
            "crm_opportunities": len(opps),
            "headcount_plan": len(headcount),
            "security_evidence": len(security),
            "board_policy": len(board),
        },
        "note": (
            "All figures computed from the operator's uploaded finance feeds. Cash position is the "
            "cumulative net of ledger cash flows over the imported period (ledger-derived, not a "
            "balance-sheet figure)."
        ),
    }
    return record


# --------------------------------------------------------------------------- #
# Apply / clear
# --------------------------------------------------------------------------- #
def _apply_vendor_index(vendors: list[VendorRecord]) -> int:
    """Replace the vendor index docs with the uploaded vendor register."""
    R.delete_keys_matching(f"{R.VENDOR_PREFIX}*")
    written = 0
    for vendor in vendors:
        payload = vendor.model_dump(mode="json")
        payload.setdefault("id", vendor.vendor_id)
        R.set_json(f"{R.VENDOR_PREFIX}{vendor.vendor_id}", payload)
        written += 1
    try:
        R.ensure_vendor_index()
    except Exception:
        pass
    return written


def apply_company_from_uploads() -> dict[str, Any]:
    """Derive and persist the company record + vendor index from uploads.

    Idempotent and safe to call after every import. When nothing is uploaded it
    leaves the company empty (does not write Acme or any placeholder).
    """
    try:
        record = derive_company_record()
    except Exception as exc:
        return {"applied": False, "error": redact_secrets(exc)}
    if record is None:
        return {"applied": False, "reason": "no uploaded datasets"}

    R.set_json(COMPANY_KEY, record)
    vendors = _load(SourceType.VENDOR_EXPORT, VendorRecord)
    vendor_count = _apply_vendor_index(vendors) if vendors else 0
    return {
        "applied": True,
        "name": record.get("name"),
        "runway_months": record.get("runway_months"),
        "cash_on_hand": record.get("cash_on_hand"),
        "vendors": vendor_count,
        "data_sources": record.get("provenance", {}).get("data_sources", {}),
    }


def clear_derived_company() -> dict[str, int]:
    """Delete the upload-derived company record, vendor index docs, and profile.

    Used by demo reset so a fresh slate has NO company until the operator uploads.
    """
    deleted: dict[str, int] = {
        COMPANY_KEY: R.delete_key(COMPANY_KEY),
        COMPANY_PROFILE_KEY: R.delete_key(COMPANY_PROFILE_KEY),
    }
    deleted["vendors"] = R.delete_keys_matching(f"{R.VENDOR_PREFIX}*")
    return deleted
