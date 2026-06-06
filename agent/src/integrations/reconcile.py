"""
Reconciliation workflows: compare imported operations data against the seeded
company system of record and emit explainable, deterministic discrepancies.

Each workflow is a pure function over typed records (no Redis I/O, no model
calls) so it is easy to reason about and test. A workflow whose required source
has not been imported returns an ``insufficient_data`` summary with explicit
blockers and an informational ``MISSING_SOURCE`` discrepancy — it never assumes a
clean pass on absent data.

Workflows
---------
1. invoices → vendors            (unmatched / shadow spend)
2. contract terms → spend        (annualised over/underspend vs contract value)
3. CRM pipeline → forecast        (weighted ARR vs the company forecast assumption)
4. headcount → hiring plan       (count / cost drift, unplanned teams)
5. policy & board constraints    (vendor commitment notification, renewal review)
6. security → revenue priority   (controls blocking signed/late-stage revenue)
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Any, Optional

from src.integrations.models import (
    BoardPolicyDoc,
    CrmOpportunity,
    Discrepancy,
    DiscrepancyKind,
    DiscrepancySeverity,
    HeadcountPlanRow,
    Invoice,
    SecurityEvidence,
    SourceType,
    VendorRecord,
    WorkflowSummary,
)

# Tolerances (fraction) before a variance is flagged. Tuned so small noise does
# not raise discrepancies but material drift does.
CONTRACT_SPEND_TOLERANCE = 0.10
CRM_FORECAST_TOLERANCE = 0.10
HEADCOUNT_COST_TOLERANCE = 0.05

# Default policy thresholds, used when a board_policy connector does not supply a
# machine-checkable rule. These mirror the seeded Acme board constraints/policies.
DEFAULT_VENDOR_BOARD_NOTIFICATION = 150_000.0   # annualised single-vendor commitment
DEFAULT_VENDOR_REVIEW_VALUE = 100_000.0         # contract value requiring review
DEFAULT_VENDOR_REVIEW_DAYS = 60                  # days before renewal to review
DEFAULT_MIN_RUNWAY_MONTHS = 9.0


def _disc_id(kind: DiscrepancyKind, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:10]
    return f"{kind.value}-{digest}"


def _norm(name: Optional[str]) -> str:
    return " ".join((name or "").lower().split())


def _as_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _missing(workflow: str, source_type: SourceType, *also: str) -> tuple[WorkflowSummary, list[Discrepancy]]:
    needed = ", ".join([source_type.value, *also])
    blocker = f"{workflow}: requires imported source(s): {needed}"
    disc = Discrepancy(
        id=_disc_id(DiscrepancyKind.MISSING_SOURCE, workflow),
        kind=DiscrepancyKind.MISSING_SOURCE,
        severity=DiscrepancySeverity.INFO,
        title=f"{workflow} not run — source not configured",
        detail=f"No data imported for: {needed}. Configure the connector to enable this check.",
        sources=[source_type.value, *also],
        recommended_action=f"Import {needed} (set the connector env var or load the demo fixture).",
        confidence=100,
    )
    return WorkflowSummary(workflow=workflow, status="insufficient_data", blockers=[blocker], detail=blocker), [disc]


# --------------------------------------------------------------------------- #
# Contract baseline: seeded vendors overlaid with any imported vendor export
# --------------------------------------------------------------------------- #
def _build_contracts(
    vendors_seed: list[dict[str, Any]],
    vendor_export: list[VendorRecord],
) -> dict[str, dict[str, Any]]:
    """Authoritative contract terms keyed by vendor_id, with a name index."""
    contracts: dict[str, dict[str, Any]] = {}
    for v in vendors_seed:
        vid = v.get("id") or _norm(v.get("name"))
        contracts[vid] = {
            "vendor_id": vid,
            "name": v.get("name"),
            "annual_cost": float(v.get("annual_cost") or 0),
            "renewal_date": _as_date(v.get("renewal_date")),
            "status": v.get("status"),
            "source": "seed",
            "board_approved": True,  # seeded vendors are existing, board-known commitments
        }
    for rec in vendor_export:
        contracts[rec.vendor_id] = {
            "vendor_id": rec.vendor_id,
            "name": rec.name,
            "annual_cost": float(rec.annual_cost or 0),
            "renewal_date": rec.renewal_date,
            "status": rec.status,
            "source": "vendor_export",
            "board_approved": rec.board_approved,
            "is_new": rec.vendor_id not in {(v.get("id") or _norm(v.get("name"))) for v in vendors_seed},
        }
    return contracts


def _contract_name_index(contracts: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {_norm(c["name"]): vid for vid, c in contracts.items() if c.get("name")}


# --------------------------------------------------------------------------- #
# 1) Invoices → vendors
# --------------------------------------------------------------------------- #
def reconcile_invoices_to_vendors(
    invoices: list[Invoice],
    contracts: dict[str, dict[str, Any]],
) -> tuple[WorkflowSummary, list[Discrepancy]]:
    workflow = "invoices_to_vendors"
    if not invoices:
        return _missing(workflow, SourceType.INVOICES)
    name_index = _contract_name_index(contracts)
    discrepancies: list[Discrepancy] = []
    matched = 0
    for inv in invoices:
        vid = inv.vendor_id if inv.vendor_id in contracts else name_index.get(_norm(inv.vendor_name))
        if vid:
            matched += 1
            continue
        discrepancies.append(
            Discrepancy(
                id=_disc_id(DiscrepancyKind.UNMATCHED_INVOICE, inv.invoice_id),
                kind=DiscrepancyKind.UNMATCHED_INVOICE,
                severity=DiscrepancySeverity.MEDIUM,
                title=f"Invoice {inv.invoice_id} has no vendor on file",
                detail=(
                    f"Invoice from '{inv.vendor_name}' for {inv.amount:,.0f} {inv.currency} "
                    f"does not match any contracted vendor — possible unapproved/shadow spend."
                ),
                sources=[SourceType.INVOICES.value, SourceType.VENDOR_EXPORT.value],
                observed={"invoice_id": inv.invoice_id, "vendor_name": inv.vendor_name, "amount": inv.amount},
                recommended_action="Confirm the vendor exists in procurement and the spend is authorised.",
                confidence=90,
                references={"invoice_id": inv.invoice_id},
            )
        )
    status = "discrepancies" if discrepancies else "ok"
    detail = f"{matched}/{len(invoices)} invoices matched to a contracted vendor."
    return WorkflowSummary(workflow=workflow, status=status, checked=len(invoices), discrepancy_count=len(discrepancies), detail=detail), discrepancies


# --------------------------------------------------------------------------- #
# 2) Contract terms → spend
# --------------------------------------------------------------------------- #
def reconcile_contract_terms_to_spend(
    invoices: list[Invoice],
    contracts: dict[str, dict[str, Any]],
) -> tuple[WorkflowSummary, list[Discrepancy]]:
    workflow = "contract_terms_to_spend"
    if not invoices:
        return _missing(workflow, SourceType.INVOICES)
    name_index = _contract_name_index(contracts)

    spend: dict[str, float] = {}
    months: dict[str, set[str]] = {}
    for inv in invoices:
        vid = inv.vendor_id if inv.vendor_id in contracts else name_index.get(_norm(inv.vendor_name))
        if not vid:
            continue  # unmatched invoices are handled by workflow 1
        spend[vid] = spend.get(vid, 0.0) + (inv.amount or 0.0)
        period = inv.period or (inv.issue_date.strftime("%Y-%m") if inv.issue_date else None)
        if period:
            months.setdefault(vid, set()).add(period)

    discrepancies: list[Discrepancy] = []
    checked = 0
    for vid, total in spend.items():
        contract = contracts.get(vid) or {}
        annual_cost = float(contract.get("annual_cost") or 0)
        if annual_cost <= 0:
            continue
        checked += 1
        observed_months = len(months.get(vid)) or 1
        annualised = total / observed_months * 12
        ratio = annualised / annual_cost
        if ratio > 1 + CONTRACT_SPEND_TOLERANCE:
            severity = DiscrepancySeverity.HIGH if ratio > 1.25 else DiscrepancySeverity.MEDIUM
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.CONTRACT_OVERSPEND, vid),
                    kind=DiscrepancyKind.CONTRACT_OVERSPEND,
                    severity=severity,
                    title=f"{contract.get('name', vid)} spend is running {ratio - 1:.0%} over contract",
                    detail=(
                        f"Annualised invoice run-rate {annualised:,.0f} vs contracted {annual_cost:,.0f} "
                        f"(observed over {observed_months} month(s))."
                    ),
                    sources=[SourceType.INVOICES.value, SourceType.VENDOR_EXPORT.value],
                    expected=annual_cost,
                    observed=round(annualised, 2),
                    delta=round(annualised - annual_cost, 2),
                    recommended_action="Review usage tier / renegotiate; confirm budget impact on burn.",
                    confidence=80,
                    references={"vendor_id": vid},
                )
            )
        elif ratio < 1 - CONTRACT_SPEND_TOLERANCE:
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.CONTRACT_UNDERSPEND, vid),
                    kind=DiscrepancyKind.CONTRACT_UNDERSPEND,
                    severity=DiscrepancySeverity.LOW,
                    title=f"{contract.get('name', vid)} spend is {1 - ratio:.0%} under contract",
                    detail=(
                        f"Annualised invoice run-rate {annualised:,.0f} vs contracted {annual_cost:,.0f} — "
                        f"possible unused commitment or savings opportunity."
                    ),
                    sources=[SourceType.INVOICES.value, SourceType.VENDOR_EXPORT.value],
                    expected=annual_cost,
                    observed=round(annualised, 2),
                    delta=round(annualised - annual_cost, 2),
                    recommended_action="Right-size the commitment at renewal or reallocate budget.",
                    confidence=70,
                    references={"vendor_id": vid},
                )
            )
    status = "discrepancies" if discrepancies else "ok"
    detail = f"Compared annualised spend to contract value for {checked} vendor(s)."
    return WorkflowSummary(workflow=workflow, status=status, checked=checked, discrepancy_count=len(discrepancies), detail=detail), discrepancies


# --------------------------------------------------------------------------- #
# 3) CRM pipeline → forecast
# --------------------------------------------------------------------------- #
def reconcile_crm_to_forecast(
    opportunities: list[CrmOpportunity],
    company: dict[str, Any],
) -> tuple[WorkflowSummary, list[Discrepancy]]:
    workflow = "crm_to_forecast"
    if not opportunities:
        return _missing(workflow, SourceType.CRM_OPPORTUNITIES)
    pipeline = company.get("pipeline_by_stage") or []
    if not pipeline:
        blocker = f"{workflow}: company.pipeline_by_stage is missing from the system of record."
        return WorkflowSummary(workflow=workflow, status="insufficient_data", blockers=[blocker], detail=blocker), []

    crm_weighted = sum(o.weighted() for o in opportunities)
    forecast_weighted = sum(float(s.get("weighted_arr") or 0) for s in pipeline)
    discrepancies: list[Discrepancy] = []
    if forecast_weighted > 0:
        variance = (crm_weighted - forecast_weighted) / forecast_weighted
        if abs(variance) > CRM_FORECAST_TOLERANCE:
            severity = DiscrepancySeverity.MEDIUM if abs(variance) > 0.20 else DiscrepancySeverity.LOW
            direction = "above" if variance > 0 else "below"
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.CRM_FORECAST_VARIANCE, "weighted"),
                    kind=DiscrepancyKind.CRM_FORECAST_VARIANCE,
                    severity=severity,
                    title=f"CRM weighted pipeline is {abs(variance):.0%} {direction} the forecast assumption",
                    detail=(
                        f"Live CRM weighted ARR {crm_weighted:,.0f} vs forecast assumption {forecast_weighted:,.0f} "
                        f"across {len(opportunities)} opportunities."
                    ),
                    sources=[SourceType.CRM_OPPORTUNITIES.value, "company.pipeline_by_stage"],
                    expected=round(forecast_weighted, 2),
                    observed=round(crm_weighted, 2),
                    delta=round(crm_weighted - forecast_weighted, 2),
                    recommended_action="Reconcile forecast assumptions with live pipeline; check stage conversion calibration.",
                    confidence=75,
                )
            )
    status = "discrepancies" if discrepancies else "ok"
    detail = f"CRM weighted ARR {crm_weighted:,.0f} vs forecast {forecast_weighted:,.0f}."
    return WorkflowSummary(workflow=workflow, status=status, checked=len(opportunities), discrepancy_count=len(discrepancies), detail=detail), discrepancies


# --------------------------------------------------------------------------- #
# 4) Headcount → hiring plan
# --------------------------------------------------------------------------- #
def reconcile_headcount_to_plan(
    headcount: list[HeadcountPlanRow],
    company: dict[str, Any],
) -> tuple[WorkflowSummary, list[Discrepancy]]:
    workflow = "headcount_to_plan"
    if not headcount:
        return _missing(workflow, SourceType.HEADCOUNT_PLAN)
    plan = company.get("hiring_plan") or []
    if not plan:
        blocker = f"{workflow}: company.hiring_plan is missing from the system of record."
        return WorkflowSummary(workflow=workflow, status="insufficient_data", blockers=[blocker], detail=blocker), []

    plan_by_team = {
        _norm(p.get("team")): {"roles": int(p.get("roles") or 0), "monthly_cost": float(p.get("monthly_cost") or 0)}
        for p in plan
    }
    # Aggregate imported rows by team.
    actual: dict[str, dict[str, float]] = {}
    for row in headcount:
        bucket = actual.setdefault(_norm(row.team), {"headcount": 0.0, "monthly_cost": 0.0, "label": row.team})
        bucket["headcount"] += row.headcount
        bucket["monthly_cost"] += row.monthly_cost or 0.0

    discrepancies: list[Discrepancy] = []
    for team_key, agg in actual.items():
        planned = plan_by_team.get(team_key)
        if planned is None:
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.UNPLANNED_HEADCOUNT, team_key),
                    kind=DiscrepancyKind.UNPLANNED_HEADCOUNT,
                    severity=DiscrepancySeverity.HIGH,
                    title=f"Unplanned headcount: {agg['label']}",
                    detail=(
                        f"{int(agg['headcount'])} role(s) at {agg['monthly_cost']:,.0f}/mo for '{agg['label']}' "
                        f"are not in the approved hiring plan."
                    ),
                    sources=[SourceType.HEADCOUNT_PLAN.value, "company.hiring_plan"],
                    observed={"team": agg["label"], "headcount": int(agg["headcount"]), "monthly_cost": agg["monthly_cost"]},
                    recommended_action="Tie new roles to signed revenue / security / runway-positive automation per board policy, or freeze.",
                    confidence=85,
                )
            )
            continue
        if agg["headcount"] > planned["roles"]:
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.HEADCOUNT_DRIFT, team_key, "count"),
                    kind=DiscrepancyKind.HEADCOUNT_DRIFT,
                    severity=DiscrepancySeverity.MEDIUM,
                    title=f"{agg['label']} headcount exceeds plan",
                    detail=f"{int(agg['headcount'])} role(s) vs planned {planned['roles']}.",
                    sources=[SourceType.HEADCOUNT_PLAN.value, "company.hiring_plan"],
                    expected=planned["roles"],
                    observed=int(agg["headcount"]),
                    delta=agg["headcount"] - planned["roles"],
                    recommended_action="Confirm incremental roles are funded and within burn discipline.",
                    confidence=85,
                )
            )
        if planned["monthly_cost"] > 0 and agg["monthly_cost"] > planned["monthly_cost"] * (1 + HEADCOUNT_COST_TOLERANCE):
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.HEADCOUNT_DRIFT, team_key, "cost"),
                    kind=DiscrepancyKind.HEADCOUNT_DRIFT,
                    severity=DiscrepancySeverity.MEDIUM,
                    title=f"{agg['label']} monthly cost exceeds plan",
                    detail=f"{agg['monthly_cost']:,.0f}/mo vs planned {planned['monthly_cost']:,.0f}/mo.",
                    sources=[SourceType.HEADCOUNT_PLAN.value, "company.hiring_plan"],
                    expected=planned["monthly_cost"],
                    observed=agg["monthly_cost"],
                    delta=round(agg["monthly_cost"] - planned["monthly_cost"], 2),
                    recommended_action="Reconcile compensation assumptions with the funded plan and net-burn guardrail.",
                    confidence=80,
                )
            )
    status = "discrepancies" if discrepancies else "ok"
    detail = f"Compared {len(actual)} team(s) of imported headcount to the funded plan."
    return WorkflowSummary(workflow=workflow, status=status, checked=len(actual), discrepancy_count=len(discrepancies), detail=detail), discrepancies


# --------------------------------------------------------------------------- #
# Policy threshold resolution (board_policy connector → defaults)
# --------------------------------------------------------------------------- #
def _resolve_threshold(board_policies: list[BoardPolicyDoc], rule: str, default: float) -> float:
    for doc in board_policies:
        if doc.rule == rule and doc.threshold is not None:
            return float(doc.threshold)
    return default


# --------------------------------------------------------------------------- #
# 5) Policy & board constraints (vendor commitment notification, renewal review)
# --------------------------------------------------------------------------- #
def reconcile_policy_and_board(
    contracts: dict[str, dict[str, Any]],
    board_policies: list[BoardPolicyDoc],
    as_of: date,
) -> tuple[WorkflowSummary, list[Discrepancy]]:
    workflow = "policy_and_board_constraints"
    notify_threshold = _resolve_threshold(board_policies, "vendor_commitment_board_notification", DEFAULT_VENDOR_BOARD_NOTIFICATION)
    review_value = _resolve_threshold(board_policies, "vendor_competitive_review_value", DEFAULT_VENDOR_REVIEW_VALUE)
    review_days = int(_resolve_threshold(board_policies, "vendor_competitive_review_days", DEFAULT_VENDOR_REVIEW_DAYS))

    discrepancies: list[Discrepancy] = []
    checked = 0
    for vid, c in contracts.items():
        checked += 1
        annual_cost = float(c.get("annual_cost") or 0)
        # Board notification: only flag *new* commitments (from the live vendor export)
        # that breach the threshold without recorded board approval — seeded vendors are
        # existing, board-known commitments and are not re-flagged.
        if c.get("source") == "vendor_export" and c.get("is_new") and annual_cost >= notify_threshold and c.get("board_approved") is not True:
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.BOARD_CONSTRAINT_VIOLATION, vid, "notify"),
                    kind=DiscrepancyKind.BOARD_CONSTRAINT_VIOLATION,
                    severity=DiscrepancySeverity.HIGH,
                    title=f"New {c.get('name', vid)} commitment needs board notification",
                    detail=(
                        f"New vendor commitment of {annual_cost:,.0f}/yr is at/above the "
                        f"{notify_threshold:,.0f} board-notification threshold and is not marked board-approved."
                    ),
                    sources=[SourceType.VENDOR_EXPORT.value, SourceType.BOARD_POLICY.value],
                    expected=f"<= {notify_threshold:,.0f}/yr or board-approved",
                    observed=annual_cost,
                    delta=round(annual_cost - notify_threshold, 2),
                    recommended_action="Notify the board before signing; record approval in the vendor record.",
                    confidence=90,
                    references={"vendor_id": vid},
                )
            )
        # Competitive review window: high-value contracts within N days of renewal.
        renewal = c.get("renewal_date")
        renewal = renewal if isinstance(renewal, date) else _as_date(renewal)
        if annual_cost > review_value and renewal is not None:
            days = (renewal - as_of).days
            if 0 <= days <= review_days:
                discrepancies.append(
                    Discrepancy(
                        id=_disc_id(DiscrepancyKind.POLICY_VIOLATION, vid, "review"),
                        kind=DiscrepancyKind.POLICY_VIOLATION,
                        severity=DiscrepancySeverity.MEDIUM,
                        title=f"{c.get('name', vid)} renewal needs competitive review",
                        detail=(
                            f"Contract at {annual_cost:,.0f}/yr renews in {days} day(s) ({renewal.isoformat()}); "
                            f"policy requires competitive review at least {review_days} days before renewal."
                        ),
                        sources=[SourceType.VENDOR_EXPORT.value, SourceType.BOARD_POLICY.value],
                        expected=f"review >= {review_days} days before renewal",
                        observed=f"{days} days to renewal",
                        recommended_action="Open a competitive review / renegotiation before the renewal date.",
                        confidence=80,
                        references={"vendor_id": vid, "renewal_date": renewal.isoformat()},
                    )
                )
    status = "discrepancies" if discrepancies else "ok"
    detail = f"Checked {checked} contract(s) against board-notification and renewal-review policy."
    return WorkflowSummary(workflow=workflow, status=status, checked=checked, discrepancy_count=len(discrepancies), detail=detail), discrepancies


# --------------------------------------------------------------------------- #
# 6) Security → revenue priority
# --------------------------------------------------------------------------- #
def reconcile_security_revenue(
    evidence: list[SecurityEvidence],
) -> tuple[WorkflowSummary, list[Discrepancy]]:
    workflow = "security_revenue_priority"
    if not evidence:
        return _missing(workflow, SourceType.SECURITY_EVIDENCE)
    open_states = {"gap", "not_started", "in_progress"}
    discrepancies: list[Discrepancy] = []
    for ev in evidence:
        if ev.blocks_revenue and ev.status.lower() in open_states:
            blocked = ev.blocked_arr or 0
            severity = DiscrepancySeverity.CRITICAL if blocked >= 250_000 else DiscrepancySeverity.HIGH
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.SECURITY_REVENUE_BLOCKER, ev.control_id),
                    kind=DiscrepancyKind.SECURITY_REVENUE_BLOCKER,
                    severity=severity,
                    title=f"Security control {ev.control_id} is blocking revenue",
                    detail=(
                        f"{ev.framework} control '{ev.title}' is '{ev.status}' and blocks "
                        f"{blocked:,.0f} of revenue. Policy prioritises unblocking controls when runway is tight."
                    ),
                    sources=[SourceType.SECURITY_EVIDENCE.value, "company.board_constraints"],
                    observed={"control_id": ev.control_id, "status": ev.status, "blocked_arr": blocked},
                    recommended_action="Prioritise remediation evidence to unblock signed/late-stage enterprise revenue.",
                    confidence=85,
                    references={"control_id": ev.control_id},
                )
            )
    status = "discrepancies" if discrepancies else "ok"
    detail = f"Checked {len(evidence)} control(s) for revenue-blocking gaps."
    return WorkflowSummary(workflow=workflow, status=status, checked=len(evidence), discrepancy_count=len(discrepancies), detail=detail), discrepancies


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_workflows(
    *,
    invoices: list[Invoice],
    vendor_export: list[VendorRecord],
    opportunities: list[CrmOpportunity],
    headcount: list[HeadcountPlanRow],
    security: list[SecurityEvidence],
    board_policies: list[BoardPolicyDoc],
    company: dict[str, Any],
    vendors_seed: list[dict[str, Any]],
    as_of: Optional[date] = None,
) -> tuple[list[WorkflowSummary], list[Discrepancy]]:
    """Run every workflow, returning per-workflow summaries and all discrepancies."""
    as_of = as_of or _as_date(company.get("updated")) or datetime.now(timezone.utc).date()
    contracts = _build_contracts(vendors_seed, vendor_export)

    summaries: list[WorkflowSummary] = []
    discrepancies: list[Discrepancy] = []
    for summary, discs in (
        reconcile_invoices_to_vendors(invoices, contracts),
        reconcile_contract_terms_to_spend(invoices, contracts),
        reconcile_crm_to_forecast(opportunities, company),
        reconcile_headcount_to_plan(headcount, company),
        reconcile_policy_and_board(contracts, board_policies, as_of),
        reconcile_security_revenue(security),
    ):
        summaries.append(summary)
        discrepancies.extend(discs)
    return summaries, discrepancies
