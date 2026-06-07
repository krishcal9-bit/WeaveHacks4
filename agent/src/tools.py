"""
LangChain tools backing the finance agents — every tool is grounded in the
Redis system of record so agents argue with real Acme Corp numbers.
"""

import json

from langchain.tools import tool

from src import redis_layer as R

COMPANY_KEY = f"{R.NS}:company:northwind"


@tool
def get_company_financials() -> str:
    """Acme Corp's financial and operating position: cash, burn, runway,
    revenue, margins, growth, unit economics, forecasts, cohorts, pipeline,
    hiring plan, incidents, audit findings, board constraints, and prior
    decision outcomes."""
    co = R.get_json(COMPANY_KEY) or {}
    fields = [
        "name", "stage", "headcount", "cash_on_hand", "monthly_revenue",
        "monthly_gross_burn", "monthly_net_burn", "runway_months", "mrr", "arr",
        "mrr_growth_mom", "gross_margin", "logo_churn_mom", "ndr", "cac", "ltv",
        "opex_monthly", "last_raise", "cash_history", "cash_forecast", "pipeline_by_stage",
        "customer_cohorts", "hiring_plan", "security_incidents", "audit_findings",
        "board_constraints", "decision_outcomes", "prompt_versions",
    ]
    return json.dumps({k: co.get(k) for k in fields if k in co})


@tool
def compute_runway(
    extra_monthly_spend: float = 0.0,
    one_time_cost: float = 0.0,
    added_monthly_revenue: float = 0.0,
) -> str:
    """Project cash runway under a what-if scenario.

    extra_monthly_spend: incremental recurring monthly cost (e.g. new hires, a new contract).
    one_time_cost: upfront one-time cost.
    added_monthly_revenue: incremental monthly revenue the decision is expected to generate.

    Returns current vs. scenario runway in months so the impact is quantified.
    """
    co = R.get_json(COMPANY_KEY) or {}
    base_cash = co.get("cash_on_hand", 0)
    base_burn = co.get("monthly_net_burn", 0)
    current = co.get("runway_months")
    cash = base_cash - one_time_cost
    net_burn = base_burn + extra_monthly_spend - added_monthly_revenue
    if net_burn <= 0:
        return json.dumps({
            "current_runway_months": current,
            "scenario_runway_months": None,
            "note": "Cash-flow positive under this scenario (net burn <= 0).",
        })
    new_runway = round(cash / net_burn, 1)
    return json.dumps({
        "current_runway_months": current,
        "current_cash": base_cash,
        "current_net_burn": base_burn,
        "scenario": {
            "extra_monthly_spend": extra_monthly_spend,
            "one_time_cost": one_time_cost,
            "added_monthly_revenue": added_monthly_revenue,
        },
        "scenario_runway_months": new_runway,
        "delta_months": round(new_runway - current, 1) if current is not None else None,
    })


@tool
def list_vendors() -> str:
    """List Acme Corp's vendor & SaaS contracts: name, category, annual cost,
    renewal date, billing terms, contract clauses, switching cost, and notes.
    Useful for procurement and cost decisions."""
    vendors = R.search_vendors("*", 50)
    keys = [
        "name", "category", "annual_cost", "monthly_cost", "renewal_date", "status",
        "owner", "owner_history", "contract_aliases", "billing_frequency", "billing_terms",
        "tiered_pricing", "termination_notice_days", "notice_window_days", "termination_penalty",
        "auto_renew", "board_approved", "board_approval_id", "sla_uptime_pct", "sla_credits",
        "security_clause", "data_processing_addendum", "switching_cost", "data_sensitivity",
        "clauses", "notes",
    ]
    return json.dumps([{k: v.get(k) for k in keys} for v in vendors])


@tool
def search_finance_policies(query: str) -> str:
    """Semantic search over Acme Corp's finance policies and past board
    decisions. Use this to ground recommendations in company policy and precedent.
    Returns stable policy_id/source_id values so Risk and CFO can cite concrete
    board-policy references, not generic policy language."""
    hits = R.search_policies(query, k=4)
    return json.dumps([
        {
            "policy_id": h.get("policy_id") or h.get("source_id"),
            "source_id": h.get("source_id") or h.get("policy_id"),
            "title": h["title"],
            "kind": h["kind"],
            "text": h["text"],
            "score": h.get("score"),
        }
        for h in hits
    ])


@tool
def search_uploaded_documents(
    query: str,
    source_categories: str = "",
    kinds: str = "",
    connector_id: str = "",
    vendor: str = "",
    min_confidence: float = 0.5,
    max_freshness_days: int = 120,
    k: int = 6,
) -> str:
    """Semantic search over uploaded document chunks with source-aware filters.

    source_categories and kinds are comma-separated filter lists. Returns capped,
    ranked excerpts — never the full document corpus.
    """
    from src.documents.models import DocumentRetrievalFilter
    from src.documents.store import search_document_chunks

    filters = DocumentRetrievalFilter(
        source_categories=[item.strip() for item in source_categories.split(",") if item.strip()],
        kinds=[item.strip() for item in kinds.split(",") if item.strip()],
        connector_id=connector_id or None,
        vendor=vendor or None,
        min_confidence=min_confidence,
        max_freshness_days=max_freshness_days,
    )
    hits = search_document_chunks(query, filters=filters, k=min(max(k, 1), 8))
    return json.dumps(hits)


# --------------------------------------------------------------------------- #
# Governance — read-only previews so agents can reason about controls, approvals,
# evidence, and obligations *before* the CFO rules. These never create or approve
# anything; they run the same deterministic engine the governance node uses.
# --------------------------------------------------------------------------- #
def _govern_preview(
    decision: str,
    estimated_monthly_cost: float,
    estimated_one_time_cost: float,
    added_monthly_revenue: float,
    department: str,
    data_sensitivity: str,
):
    """Run the governance pipeline in preview mode (no persistence) for a decision."""
    from src import governance as G
    from src.governance_models import DataSensitivity

    impact = json.loads(compute_runway.invoke({
        "extra_monthly_spend": float(estimated_monthly_cost or 0),
        "one_time_cost": float(estimated_one_time_cost or 0),
        "added_monthly_revenue": float(added_monthly_revenue or 0),
    }))
    rec = {
        "decision": "APPROVE", "confidence": 70,
        "rationale": "Pre-decision governance preview.",
        "key_risks": [], "conditions": [], "impact": impact,
    }
    sensitivity = None
    if data_sensitivity:
        try:
            sensitivity = DataSensitivity(data_sensitivity).value
        except ValueError:
            sensitivity = None
    return G.preview_governance(
        decision, rec,
        monthly_cost=float(estimated_monthly_cost or 0),
        one_time_cost=float(estimated_one_time_cost or 0),
        added_monthly_revenue=float(added_monthly_revenue or 0),
        department=department or None,
        data_sensitivity=sensitivity,
    )


@tool
def required_approvals(
    decision: str,
    estimated_monthly_cost: float = 0.0,
    estimated_one_time_cost: float = 0.0,
    added_monthly_revenue: float = 0.0,
    department: str = "",
    data_sensitivity: str = "",
) -> str:
    """What approvals are required for a decision under Acme Corp's approval matrix.

    Returns the full approval route (which roles must sign off and why), the
    committed amount, inferred risk tier and data sensitivity, the runway impact,
    and the status the decision would land in (pending_approval vs. system-cleared).
    data_sensitivity: optional one of internal|confidential|customer_data|regulated.
    Preview only — creates and approves nothing."""
    req = _govern_preview(decision, estimated_monthly_cost, estimated_one_time_cost, added_monthly_revenue, department, data_sensitivity)
    return json.dumps({
        "decision": decision,
        "amount_annualized": req.amount_annualized,
        "department": req.department,
        "risk_tier": req.risk_tier.value,
        "data_sensitivity": req.data_sensitivity.value,
        "runway_after_months": req.runway_after_months,
        "resulting_status": req.status.value,
        "human_approval_required": req.human_approvals_pending(),
        "approval_route": [
            {"sequence": s.sequence, "approver": s.approver_role, "reason": s.reason, "policy_refs": s.policy_refs}
            for s in req.route
        ],
        "note": "Preview only — no approval request is created and nothing is approved.",
    }, default=str)


@tool
def check_controls(
    decision: str,
    estimated_monthly_cost: float = 0.0,
    estimated_one_time_cost: float = 0.0,
    added_monthly_revenue: float = 0.0,
    department: str = "",
    data_sensitivity: str = "",
) -> str:
    """Which governance controls a decision engages or violates (runway floor,
    vendor/CFO spend thresholds, board notification, headcount burn discipline,
    gross-margin floor, security-blocked revenue priority, data-sensitivity review).

    Each finding is quantified (observed vs. policy limit) and flags whether it is
    blocking (requires a board-approved exception). Preview only."""
    req = _govern_preview(decision, estimated_monthly_cost, estimated_one_time_cost, added_monthly_revenue, department, data_sensitivity)
    controls = [
        {
            "control_id": v.control_id,
            "policy_id": v.policy_id,
            "title": v.title,
            "severity": v.severity.value,
            "message": v.message,
            "blocking": v.blocking,
            "requires_exception": v.requires_exception,
            "evidence_required": v.evidence_required,
            "remediation": v.remediation,
        }
        for v in req.violations
    ]
    return json.dumps({
        "controls_engaged": controls,
        "blocking_count": sum(1 for v in req.violations if v.blocking),
        "note": "No governance controls are engaged by this decision as specified." if not controls else "Controls engaged; see route for required approvals.",
    }, default=str)


@tool
def missing_evidence(
    decision: str,
    estimated_monthly_cost: float = 0.0,
    estimated_one_time_cost: float = 0.0,
    added_monthly_revenue: float = 0.0,
    department: str = "",
    data_sensitivity: str = "",
) -> str:
    """What evidence is required, already present, and still missing for a decision
    to pass governance (e.g. CFO/board memos, security review sign-off, signed DPA,
    financing term sheet, headcount-to-revenue mapping). Preview only."""
    req = _govern_preview(decision, estimated_monthly_cost, estimated_one_time_cost, added_monthly_revenue, department, data_sensitivity)
    return json.dumps({
        "evidence_required": req.evidence_required,
        "evidence_present": req.evidence_present,
        "evidence_missing": req.evidence_missing,
        "note": "Missing items must be supplied by an owner before the decision can be approved.",
    }, default=str)


@tool
def obligations_if_approved(
    decision: str,
    estimated_monthly_cost: float = 0.0,
    estimated_one_time_cost: float = 0.0,
    added_monthly_revenue: float = 0.0,
    department: str = "",
    data_sensitivity: str = "",
) -> str:
    """What obligations and monitoring follow if a decision is approved: board
    notification windows, vendor renewal/termination notice deadlines, SOC 2
    evidence deadlines, revenue milestones, forecast-calibration checkpoints, and
    follow-up reviews — each with an owner and a due date. Preview only."""
    req = _govern_preview(decision, estimated_monthly_cost, estimated_one_time_cost, added_monthly_revenue, department, data_sensitivity)
    return json.dumps({
        "obligations": [
            {
                "kind": o.kind,
                "title": o.title,
                "owner_role": o.owner_role,
                "due_date": o.due_date,
                "source_policy": o.source_policy,
                "evidence_required": o.evidence_required,
            }
            for o in req.obligations
        ],
        "monitoring_triggers": [
            {"kind": m.kind, "label": m.label, "trigger_date": m.trigger_date, "condition": m.condition, "target": m.target}
            for m in req.monitoring
        ],
        "note": "These obligations are scheduled only if the decision is approved.",
    }, default=str)


# --------------------------------------------------------------------------- #
# Finance-operations connectors — reconciled facts from imported live data.
# These return honest "not imported / not configured" payloads when no operations
# feed has been ingested, so agents never invent connector data.
# --------------------------------------------------------------------------- #
@tool
def list_operations_sources() -> str:
    """Inventory of imported finance-operations feeds (ledgers, invoices, vendor
    exports, CRM pipeline, headcount, security evidence, board policy) with their
    provenance: origin, record counts, source freshness, import status, and
    parser-derived quality signals such as ledger normalization, invoice
    messiness, CRM pipeline quality, or headcount-plan quality. Each source also
    carries confidence_score, confidence_reasons, freshness_days, and missing
    required facts. Use this to know which real operating data is available and
    how trustworthy it is."""
    from src.integrations import service as OPS

    statuses = OPS.connector_statuses()
    imported = [s for s in statuses if s.get("status") in ("imported", "partial", "skipped_unchanged") and (s.get("record_count") or 0) > 0]
    return json.dumps({
        "configured_connectors": [s["source_type"] for s in statuses if s.get("configured")],
        "imported_sources": [
            {
                "source_type": s["source_type"],
                "origin": s.get("origin"),
                "records": s.get("record_count"),
                "status": s.get("status"),
                "source_timestamp": s.get("source_timestamp"),
                "workbook_name": s.get("workbook_name"),
                "workbook_sheet": s.get("workbook_sheet"),
                "header_row_number": s.get("header_row_number"),
                "hidden_column_count": s.get("hidden_column_count", 0),
                "extra_column_count": s.get("extra_column_count", 0),
                "freshness_days": s.get("freshness_days"),
                "reconciliation_status": s.get("reconciliation_status"),
                "confidence_score": s.get("confidence_score"),
                "confidence_reasons": s.get("confidence_reasons") or [],
                "required_facts_missing": s.get("required_facts_missing") or [],
                "normalization_summary": s.get("normalization_summary") or {},
                "messiness_summary": s.get("messiness_summary") or {},
                "pipeline_quality_summary": s.get("pipeline_quality_summary") or {},
                "headcount_quality_summary": s.get("headcount_quality_summary") or {},
            }
            for s in imported
        ],
        "confidence": OPS.import_confidence().model_dump(mode="json"),
        "note": "Empty imported_sources means no live operations data has been ingested; do not assume figures.",
    }, default=str)


@tool
def get_reconciliation_summary() -> str:
    """Summary of the latest reconciliation of imported operations data against
    the company system of record: per-workflow status (invoices vs vendors, contract
    terms vs spend, CRM vs forecast, headcount vs plan, policy/board constraints,
    security revenue blockers), discrepancy counts by severity, blockers, and confidence."""
    from src.integrations import service as OPS

    report = OPS.reconciliation_summary()
    if not report:
        return json.dumps({"status": "not_run", "note": "No reconciliation has been run; no reconciled facts available."})
    return json.dumps({
        "status": report.get("status"),
        "generated_at": report.get("generated_at"),
        "counts_by_severity": report.get("counts_by_severity"),
        "workflows": [
            {"workflow": w.get("workflow"), "status": w.get("status"), "checked": w.get("checked"), "discrepancy_count": w.get("discrepancy_count")}
            for w in (report.get("workflows") or [])
        ],
        "blockers": report.get("blockers", []),
        "confidence": report.get("confidence"),
    }, default=str)


@tool
def list_open_discrepancies(severity: str = "") -> str:
    """Outstanding reconciliation mismatches (e.g. contract overspend, unmatched
    invoices, contract-vs-invoice mismatch, renewal urgency, missing board
    approvals, headcount drift, board-constraint violations, revenue-blocking
    security gaps). Optionally filter by severity: info|low|medium|high|critical.
    Each item is explainable with expected vs observed values and a recommended action."""
    from src.integrations import service as OPS

    items = OPS.list_discrepancies(severity=severity or None)
    return json.dumps([
        {
            "id": d.get("id"),
            "kind": d.get("kind"),
            "severity": d.get("severity"),
            "title": d.get("title"),
            "detail": d.get("detail"),
            "expected": d.get("expected"),
            "observed": d.get("observed"),
            "recommended_action": d.get("recommended_action"),
            "confidence": d.get("confidence"),
        }
        for d in items
    ], default=str)


@tool
def get_operations_data_confidence() -> str:
    """Confidence in the imported operations picture: connector coverage, row
    validation failures, duplicate keys, source freshness/age, reconciliation
    discrepancies, missing required facts, source-level scores, and an overall
    0-100 score. Use this to weight how much to rely on reconciled operations facts
    and explicitly mention confidence/freshness when the data is imperfect."""
    from src.integrations import service as OPS

    return json.dumps(OPS.import_confidence().model_dump(mode="json"), default=str)


# --------------------------------------------------------------------------- #
# Strategic planning tools — deterministic finance "digital twin".
# These wrap the calculation engine in src/planning.py, src/playbooks.py and
# src/stress_tests.py. Every number returned is computed from the Redis system of
# record; no LLM is involved in producing the figures (the council/CFO narrates
# them afterwards). Each tool persists its artifacts to Redis with provenance.
# --------------------------------------------------------------------------- #
@tool
def list_finance_playbooks() -> str:
    """List the available finance playbooks (extend runway, unblock enterprise
    revenue via security spend, renegotiate vendors, hire against signed revenue,
    prepare a financing bridge, shift from growth to efficiency, recover from
    pipeline slippage). Returns each playbook's id, label, and what it does."""
    from src import playbooks as PB

    return json.dumps(PB.catalog())


@tool
def build_strategic_plan(horizon_months: int = 12, playbook: str = "") -> str:
    """Build and persist a multi-month strategic operating plan for Acme Corp,
    projecting cash, burn, runway, ARR, churn, hiring ramps, vendor savings, and
    financing month by month from the real company record.

    horizon_months: how many months to project (1-36).
    playbook: optional playbook id (see list_finance_playbooks); empty = base
        operating plan that executes the current hiring plan as-is.

    Returns the plan id, computed summary (ending cash, min cash, lowest runway,
    ending ARR, cash-flow-positive month), milestones, capital plan, and any
    policy/compliance blockers. Deterministic."""
    from src import planning as PL
    from src import playbooks as PB

    company = PL.load_company()
    horizon = max(1, min(36, int(horizon_months)))
    if playbook and playbook in PB.PLAYBOOKS:
        plan = PB.build_playbook_plan(company, playbook, horizon_months=horizon)
    else:
        plan = PL.build_plan(company, title=f"{horizon}-month base operating plan", horizon_months=horizon)
    PL.save_plan(plan)
    return json.dumps(
        {
            "plan_id": plan.id,
            "title": plan.title,
            "playbook": plan.playbook_label or plan.playbook_id,
            "horizon_months": plan.horizon_months,
            "summary": plan.summary,
            "assumptions": [a.model_dump() for a in plan.assumptions],
            "capital_plan": plan.capital_plan.model_dump(),
            "milestones": [m.model_dump() for m in plan.milestones],
            "policy_blockers": plan.policy_blockers,
            "risks": plan.risks,
            "monitoring_triggers": plan.monitoring_triggers,
            "provenance": plan.provenance,
        },
        default=str,
    )


@tool
def compare_finance_playbooks(decision: str, playbooks: str = "", horizon_months: int = 12) -> str:
    """Compare multiple finance playbooks for one decision and recommend a
    portfolio (a sequenced set of strategies), not just approve/reject.

    decision: the financial decision or situation under review.
    playbooks: optional comma-separated playbook ids to compare; empty = all.
    horizon_months: projection horizon (1-36).

    Returns a deterministic scorecard for each playbook (runway safety, liquidity,
    growth, compliance, efficiency, dilution), an overall ranking, the recommended
    portfolio with weights/roles, and the key trade-offs."""
    from src import playbooks as PB

    ids = [p.strip() for p in (playbooks or "").split(",") if p.strip()]
    horizon = max(1, min(36, int(horizon_months)))
    portfolio, _plans = PB.compare_playbooks(PB.PL.load_company(), ids, decision, horizon_months=horizon)
    return json.dumps(portfolio.model_dump(), default=str)


@tool
def run_plan_stress_test(playbook: str = "", horizon_months: int = 12, trials: int = 400) -> str:
    """Run a Monte Carlo-style stress test over the uncertain operating dials
    (churn, pipeline conversion, growth, gross margin) and report the probability
    of breaching the runway and cash guardrails.

    playbook: optional playbook id whose plan should be stress-tested; empty =
        the base operating plan (the current hiring plan as-is).
    horizon_months: projection horizon (1-36).
    trials: number of Monte Carlo trials (deterministic for a fixed internal seed).

    Returns percentile bands (p5/p50/p95) for ending cash, min cash, and runway,
    plus probability of runway breach / insolvency and the expected breach month."""
    from src import planning as PL
    from src import playbooks as PB
    from src import stress_tests as ST

    company = PL.load_company()
    horizon = max(1, min(36, int(horizon_months)))
    trials = max(50, min(2000, int(trials)))
    steps = None
    overrides = None
    name = "Base operating plan stress test"
    if playbook and playbook in PB.PLAYBOOKS:
        plan = PB.build_playbook_plan(company, playbook, horizon_months=horizon)
        steps = plan.steps
        overrides = {a.key: a.value for a in plan.assumptions if a.source in ("playbook", "override")}
        name = f"{plan.playbook_label} stress test"
    st = ST.run_stress_test(
        company, name=name, horizon_months=horizon, trials=trials, steps=steps, base_overrides=overrides
    )
    return json.dumps(st.model_dump(), default=str)


@tool
def run_plan_sensitivity(variable: str = "", horizon_months: int = 12) -> str:
    """Run sensitivity analysis on a strategic plan: vary one lever and measure the
    impact on minimum cash and runway.

    variable: one of churn, conversion, gross_margin, hiring_start, vendor_savings,
        financing_close_month. Empty = run the full suite and rank the most
        sensitive lever.
    horizon_months: projection horizon (1-36).

    Returns, per swept value, the resulting min cash / lowest runway / ending ARR,
    plus a near-base elasticity and the output swing. Deterministic."""
    from src import planning as PL
    from src import stress_tests as ST

    company = PL.load_company()
    horizon = max(1, min(36, int(horizon_months)))
    if variable:
        result = ST.run_sensitivity(company, variable, horizon_months=horizon)
        return json.dumps(result.model_dump(), default=str)
    return json.dumps(ST.sensitivity_suite(company, horizon_months=horizon), default=str)


# Exposed to the finance agents (the CFO synthesizer gets the full set).
FINANCE_TOOLS = [
    get_company_financials,
    compute_runway,
    list_vendors,
    search_finance_policies,
    search_uploaded_documents,
]

# Strategic-planning digital-twin tools (deterministic; persisted with provenance).
PLANNING_TOOLS = [
    list_finance_playbooks,
    build_strategic_plan,
    compare_finance_playbooks,
    run_plan_stress_test,
    run_plan_sensitivity,
]

# Finance-operations connector tools (reconciled facts from imported live data).
OPERATIONS_TOOLS = [
    list_operations_sources,
    get_reconciliation_summary,
    list_open_discrepancies,
    get_operations_data_confidence,
]

# Governance tools (read-only previews of controls, approvals, evidence, obligations).
GOVERNANCE_TOOLS = [
    required_approvals,
    check_controls,
    missing_evidence,
    obligations_if_approved,
]

# Financial-OS tools (scenario branches, vector knowledge RAG, operating
# collections) — defined in src.scenario_tools to keep this module's hot
# definitions untouched.
from src.scenario_tools import FINANCIAL_OS_TOOLS

# Full tool surface available to the council.
ALL_TOOLS = [*FINANCE_TOOLS, *PLANNING_TOOLS, *OPERATIONS_TOOLS, *GOVERNANCE_TOOLS, *FINANCIAL_OS_TOOLS]
