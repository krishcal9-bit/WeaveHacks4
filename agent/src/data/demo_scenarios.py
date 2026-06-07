"""
Scenario-specific messy data packs for Atlas demos.

These are not a replacement for uploaded connector files. They are seeded,
inspectable RedisJSON examples that let the operator choose a realistic decision
and show which disorganized source-system rows Atlas would have to reconcile.
Each pack spans at least three source categories and maps to a stable
scenario-engine branch.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src import redis_layer as R

DEMO_SCENARIO_PREFIX = f"{R.NS}:demo_scenario:"


DEMO_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "datadog-renewal",
        "branch_id": "demo-datadog-renewal",
        "title": "Datadog renewal",
        "decision_type": "vendor_renewal",
        "decision_prompt": (
            "Should Atlas renew Datadog for one year at a messy $228K implied annual run-rate, "
            "or force a renegotiation before the auto-renew notice window closes?"
        ),
        "description": "Observability renewal with alias drift, duplicate invoices, card charges, and board-notice exposure.",
        "scenario_changes": [
            {"type": "vendor_renegotiation", "vendor_id": "datadog", "new_annual_cost": 228_000, "label": "Renew Datadog at $228K/yr"},
        ],
        "tags": ["vendor_renewal", "procurement", "messy_sources"],
        "expected_council_focus": [
            "Procurement should isolate auto-renew and benchmark leverage.",
            "Treasury should test annual-prepay and late-cash timing.",
            "Risk should cite board-notice, invoice mismatch, and approval evidence.",
        ],
        "sources": [
            {
                "source_type": "vendor_export",
                "source_system": "ContractVault",
                "messy_fields": ["DataDog Inc vs DDOG Observability aliases", "owner changed Engineering -> Platform Ops", "auto-renew notice date conflicts"],
                "records": [
                    {"vendor": "DataDog Inc", "annual_commit": "$180,000", "renewal": "8/1/26", "auto_renew": "Y", "notice_days": "45", "owner": "Platform Ops"},
                    {"vendor": "DDOG Observability", "annual_commit": "228000 run-rate incl overage", "renewal": "2026-08-01", "owner": "Engineering"},
                ],
            },
            {
                "source_type": "invoices",
                "source_system": "PayablesDesk",
                "messy_fields": ["duplicate invoice id", "partial payment", "amount exceeds contract cadence"],
                "records": [
                    {"invoice_id": "INV-DD-8821", "vendor": "Data Dog", "amount": "$19,200", "status": "partial", "po": ""},
                    {"invoice_id": "INV-DD-8821", "vendor": "Datadog", "amount": "$18,950", "status": "open", "po": "PO-1051"},
                ],
            },
            {
                "source_type": "ledger",
                "source_system": "CloudLedger",
                "messy_fields": ["card descriptor instead of vendor", "refund netted against software spend"],
                "records": [
                    {"txn_id": "CARD-8821", "description": "CARD 4242 DDOG*OBSERVABILITY", "amount": "-19200.00", "category": ""},
                    {"txn_id": "BANK-8822", "description": "DATADOG CREDIT MEMO HOST ADJ", "amount": "1250.00", "category": "software"},
                ],
            },
            {
                "source_type": "board_policy",
                "source_system": "BoardPortal",
                "messy_fields": ["threshold applies to annualized overage not original contract only"],
                "records": [{"policy_id": "BP-1", "threshold": "$150K", "route": "CFO -> Board", "evidence": "board memo"}],
            },
        ],
    },
    {
        "id": "security-blocker",
        "branch_id": "demo-security-blocker",
        "title": "Security blocker",
        "decision_type": "security_blocker",
        "decision_prompt": (
            "Should Atlas fund the TrustVault security remediation package now to unblock enterprise customer-data access, "
            "despite stale SOC 2 evidence and incomplete DPA records?"
        ),
        "description": "Revenue blocker where security evidence, CRM upside, policy, and invoices disagree.",
        "scenario_changes": [
            {"type": "opex_change", "monthly_cost": 28_000, "label": "Security remediation retainer"},
            {"type": "compliance_blocker", "control": "SOC 2 CC6.1 / CC7.2", "blocked_arr": 610_000, "label": "$610K ARR remains blocked until controls clear"},
        ],
        "tags": ["security", "risk", "customer_data"],
        "expected_council_focus": [
            "Risk should condition on BP-6, DPA, source freshness, and security sign-off.",
            "FP&A should haircut blocked ARR by stage and stale-close risk.",
            "Treasury should compare remediation cash timing against late customer receipts.",
        ],
        "sources": [
            {
                "source_type": "security_evidence",
                "source_system": "TrustVault",
                "messy_fields": ["stale evidence date", "missing DPA", "control owner changed"],
                "records": [
                    {"control_id": "CC6.1", "status": "open", "evidence_date": "2026-01-15", "blocked_arr": 310000, "owner": "Security"},
                    {"control_id": "CC7.2", "status": "needs review", "evidence_date": "", "blocked_arr": 300000, "owner": "Platform"},
                ],
            },
            {
                "source_type": "crm_opportunities",
                "source_system": "PipelineHub",
                "messy_fields": ["probability override", "slipped close date", "duplicate account alias"],
                "records": [
                    {"opportunity_id": "OPP-SEC-14", "account": "Meridian Logistics", "arr": 310000, "probability": "85% override", "close_date": "slipped to 2026-09-30"},
                    {"opportunity_id": "OPP-SEC-15", "account": "Meridian Log.", "arr": 300000, "probability": "0.65", "stage_age_days": 92},
                ],
            },
            {
                "source_type": "invoices",
                "source_system": "PayablesDesk",
                "messy_fields": ["missing PO", "line description mixes retainer and audit work"],
                "records": [{"invoice_id": "TV-610", "vendor": "TrustVault Security", "amount": "$28,000", "due_date": "", "po": "", "line": "SOC2 retainer + DPA review"}],
            },
            {
                "source_type": "board_policy",
                "source_system": "BoardPortal",
                "messy_fields": ["data-sensitivity route requires Security Review and Legal"],
                "records": [{"policy_id": "BP-6", "data_sensitivity": "customer_data", "approval_route": ["Security Review", "Legal"]}],
            },
        ],
    },
    {
        "id": "hiring-plan",
        "branch_id": "demo-hiring-plan",
        "title": "Hiring plan",
        "decision_type": "hiring_plan",
        "decision_prompt": (
            "Should Atlas pull forward the Q3 hiring plan of seven roles when recruiting starts, contractor approvals, "
            "and pipeline coverage do not reconcile?"
        ),
        "description": "Headcount acceleration with department drift, partial approvals, contractor leakage, and forecast uncertainty.",
        "scenario_changes": [
            {"type": "hire", "team": "Engineering", "roles": 4, "monthly_cost": 74_000, "start_month": "2026-08", "label": "Pull forward 4 engineering roles"},
            {"type": "hire", "team": "Sales", "roles": 3, "monthly_cost": 58_000, "start_month": "2026-08", "label": "Pull forward 3 GTM roles"},
        ],
        "tags": ["hiring", "fpna", "treasury"],
        "expected_council_focus": [
            "FP&A should challenge whether pipeline supports headcount timing.",
            "Treasury should quantify start-month cash burn and payroll timing.",
            "Risk should challenge partial approvals and department mapping drift.",
        ],
        "sources": [
            {
                "source_type": "headcount_plan",
                "source_system": "PeopleRoster",
                "messy_fields": ["planned vs open vs contractor rows", "partial approvals", "department names drift"],
                "records": [
                    {"role_id": "HC-Q3-ENG", "team": "Eng", "roles": 4, "approval": "partial", "start": "08/15/26", "loaded_cost": "$74,000/mo"},
                    {"role_id": "HC-Q3-GTM", "team": "Sales Ops / Revenue", "roles": 3, "approval": "pending CFO", "start": "Sept-ish", "loaded_cost": "$58k"},
                ],
            },
            {
                "source_type": "ledger",
                "source_system": "CloudLedger",
                "messy_fields": ["payroll summary only", "contractor charges buried in card spend"],
                "records": [
                    {"txn_id": "PAY-0901", "description": "PAYROLL SUMMARY AUG PREVIEW", "amount": "-174500"},
                    {"txn_id": "CARD-LAB-77", "description": "Prototype Lab Contractor", "amount": "-12500", "category": ""},
                ],
            },
            {
                "source_type": "crm_opportunities",
                "source_system": "PipelineHub",
                "messy_fields": ["stale opportunities", "probability overrides", "owner changes"],
                "records": [
                    {"opportunity_id": "OPP-HC-1", "arr": 420000, "probability": "sales override 80%", "last_activity_days": 46},
                    {"opportunity_id": "OPP-HC-2", "arr": 260000, "probability": "", "owner": "AE moved territories"},
                ],
            },
            {
                "source_type": "board_policy",
                "source_system": "BoardPortal",
                "messy_fields": ["8% burn-growth cap requires fully loaded cost evidence"],
                "records": [{"policy_id": "BP-5", "threshold": "8% net burn growth", "required_evidence": "approved headcount row + start date"}],
            },
        ],
    },
    {
        "id": "bridge-financing",
        "branch_id": "demo-bridge-financing",
        "title": "Bridge financing",
        "decision_type": "financing_scenario",
        "decision_prompt": (
            "Should Atlas accept a $3M bridge with late-close risk and fees, or defer spend until customer cash clears?"
        ),
        "description": "Financing decision where treasury timing, AR collections, covenants, and board exceptions are out of sync.",
        "scenario_changes": [
            {"type": "financing", "financing_type": "debt", "amount": 3_000_000, "label": "$3M debt bridge"},
            {"type": "opex_change", "monthly_cost": 32_000, "label": "Bridge interest and legal fees"},
        ],
        "tags": ["financing", "runway", "treasury"],
        "expected_council_focus": [
            "Treasury should ask what happens if financing or receivables arrive late.",
            "Risk should cite runway exception and covenant-style evidence.",
            "CFO should turn unresolved timing into conditions.",
        ],
        "sources": [
            {
                "source_type": "ledger",
                "source_system": "CloudLedger",
                "messy_fields": ["bank fees netted separately", "incoming wires not matched to invoices"],
                "records": [
                    {"txn_id": "BANK-BRIDGE-1", "description": "BRIDGE TERM SHEET FEE", "amount": "-42500"},
                    {"txn_id": "BANK-WIRE-88", "description": "UNAPPLIED CUSTOMER WIRE", "amount": "85000"},
                ],
            },
            {
                "source_type": "invoices",
                "source_system": "PayablesDesk",
                "messy_fields": ["large receivables due after financing close", "partial customer payment"],
                "records": [
                    {"invoice_id": "AR-920", "customer": "Cardinal Freight", "amount": "$116,000", "due": "2026-08-31", "status": "partial"},
                    {"invoice_id": "AR-921", "customer": "Northstar Distribution", "amount": "$92,000", "due": "2026-09-15", "status": "open"},
                ],
            },
            {
                "source_type": "financing_scenarios",
                "source_system": "BoardModel",
                "messy_fields": ["close date entered as text", "fees excluded from one tab", "cash covenant not linked"],
                "records": [{"scenario": "Bridge v7 final FINAL", "amount": "$3M", "close": "late Aug?", "cash_covenant": "$1.5M min", "fees": "not in model"}],
            },
            {
                "source_type": "board_policy",
                "source_system": "BoardPortal",
                "messy_fields": ["runway exception requires signed term sheet or board exception"],
                "records": [{"policy_id": "BP-4", "threshold": "9 months runway", "exception": "signed financing term sheet"}],
            },
        ],
    },
    {
        "id": "vendor-consolidation",
        "branch_id": "demo-vendor-consolidation",
        "title": "Vendor consolidation",
        "decision_type": "vendor_renewal",
        "decision_prompt": (
            "Should Atlas consolidate Figma, Gong, and sales enablement tools into one vendor bundle when invoices, owners, "
            "and termination clauses disagree?"
        ),
        "description": "Commercial consolidation with aliases, split invoices, owner changes, and termination penalties.",
        "scenario_changes": [
            {"type": "opex_change", "monthly_cost": -18_000, "label": "Consolidation savings"},
            {"type": "capex", "one_time": 45_000, "label": "Migration and enablement cost"},
        ],
        "tags": ["procurement", "vendor_consolidation", "savings"],
        "expected_council_focus": [
            "Procurement should identify termination penalties and bundle leverage.",
            "Treasury should separate one-time migration cash from recurring savings.",
            "Risk should check owner attestations and SLA/security clauses.",
        ],
        "sources": [
            {
                "source_type": "vendor_export",
                "source_system": "ContractVault",
                "messy_fields": ["contract aliases", "owners changed", "mixed annual/monthly billing"],
                "records": [
                    {"vendor": "Gong.io", "annual_cost": "$28,800", "owner": "Sales Ops", "termination_penalty": "$7,200"},
                    {"vendor": "Figma / FigJam", "annual_cost": "$14,400", "owner": "Design", "dpa": "missing"},
                ],
            },
            {
                "source_type": "invoices",
                "source_system": "PayablesDesk",
                "messy_fields": ["split invoice lines", "vendor names do not match contract export"],
                "records": [
                    {"invoice_id": "INV-CONS-1A", "vendor": "Gong", "amount": "$2,400", "line": "sales call intelligence"},
                    {"invoice_id": "INV-CONS-1B", "vendor": "FigJam", "amount": "$1,200", "line": "design + workshops"},
                ],
            },
            {
                "source_type": "ledger",
                "source_system": "CloudLedger",
                "messy_fields": ["card charges split across departments", "uncategorized software spend"],
                "records": [
                    {"txn_id": "CARD-CONS-1", "description": "GONG.IO*SAAS", "amount": "-2400", "department": "Sales"},
                    {"txn_id": "CARD-CONS-2", "description": "FIGMA*", "amount": "-1200", "department": "Eng?"},
                ],
            },
            {
                "source_type": "purchase_orders",
                "source_system": "SpendDesk",
                "messy_fields": ["prior PO says not required despite annualized threshold"],
                "records": [{"po": "PO-CONS-77", "vendor": "Unified Enablement Suite", "amount": "$96,000", "approval": "not_required"}],
            },
        ],
    },
    {
        "id": "pricing-change",
        "branch_id": "demo-pricing-change",
        "title": "Pricing change",
        "decision_type": "pricing_change",
        "decision_prompt": (
            "Should Atlas raise mid-market pricing by 8% when renewal data, invoice credits, and pipeline probabilities are messy?"
        ),
        "description": "Pricing decision with renewal ARR at risk, credit memos, duplicate accounts, and forecastability questions.",
        "scenario_changes": [
            {"type": "opex_change", "monthly_cost": 14_000, "added_monthly_revenue": 38_000, "label": "Pricing rollout and expected uplift"},
            {"type": "churn_shock", "segment": "Mid-market fulfillment", "pct": 0.07, "label": "7% mid-market churn sensitivity"},
        ],
        "tags": ["pricing", "fpna", "pipeline"],
        "expected_council_focus": [
            "FP&A should challenge whether the uplift is forecastable after churn and credits.",
            "Treasury should test cash timing from renewal invoices.",
            "Risk should ask for customer-notice and source-provenance evidence.",
        ],
        "sources": [
            {
                "source_type": "crm_opportunities",
                "source_system": "PipelineHub",
                "messy_fields": ["renewal vs expansion mixed", "probability overrides", "duplicate account aliases"],
                "records": [
                    {"opportunity_id": "OPP-PRICE-1", "account": "Brightline Retail", "type": "renewal+uplift", "arr": 84000, "probability": "manual 75%"},
                    {"opportunity_id": "OPP-PRICE-2", "account": "Brightline Rtl", "type": "expansion", "arr": 18000, "probability": "0.55"},
                ],
            },
            {
                "source_type": "invoices",
                "source_system": "PayablesDesk",
                "messy_fields": ["credit memos", "partial payments", "line item has old pricing"],
                "records": [
                    {"invoice_id": "INV-PRICE-9", "customer": "Brightline Retail", "amount": "$7,000", "status": "partial", "line": "old plan"},
                    {"invoice_id": "CM-PRICE-9", "customer": "Brightline Retail", "amount": "($850)", "status": "credit"},
                ],
            },
            {
                "source_type": "customer_contracts",
                "source_system": "RevOps Sheet",
                "messy_fields": ["notice period missing", "auto-renew flag conflicts with CRM"],
                "records": [{"customer": "Brightline Retail", "renewal": "2026-08-01", "auto_renew": "yes in CRM / no in contract", "notice_days": ""}],
            },
            {
                "source_type": "ledger",
                "source_system": "CloudLedger",
                "messy_fields": ["refunds obscure realized price"],
                "records": [{"txn_id": "BANK-PRICE-1", "description": "BRIGHTLINE CREDIT ADJ", "amount": "-850"}],
            },
        ],
    },
    {
        "id": "pipeline-shortfall",
        "branch_id": "demo-pipeline-shortfall",
        "title": "Pipeline shortfall",
        "decision_type": "capital_allocation",
        "decision_prompt": (
            "Should Atlas cut discretionary spend now because the Q3 pipeline shortfall looks real after cleaning CRM, AR, and ledger signals?"
        ),
        "description": "Forecast miss with stale opportunities, late invoices, customer credits, and hiring commitments still in the plan.",
        "scenario_changes": [
            {"type": "revenue_slip", "amount": 62_000, "label": "$62K monthly revenue slip"},
            {"type": "opex_change", "monthly_cost": -36_000, "label": "Discretionary spend cut"},
        ],
        "tags": ["pipeline", "forecast", "downside"],
        "expected_council_focus": [
            "FP&A should haircut stale pipeline and explain forecastability.",
            "Treasury should ask how late cash changes runway if spend is not cut.",
            "CFO should resolve dissent between growth investment and runway protection.",
        ],
        "sources": [
            {
                "source_type": "crm_opportunities",
                "source_system": "PipelineHub",
                "messy_fields": ["slipped close dates", "stage aging", "missing probabilities", "duplicate accounts"],
                "records": [
                    {"opportunity_id": "OPP-SHORT-1", "account": "Summit Supply Co", "arr": 210000, "probability": "", "stage_age_days": 118},
                    {"opportunity_id": "OPP-SHORT-2", "account": "Cardinal Fulfilment", "arr": 198000, "probability": "override 90%", "close_date": "slipped twice"},
                ],
            },
            {
                "source_type": "invoices",
                "source_system": "PayablesDesk",
                "messy_fields": ["overdue AR", "missing due date", "partial collection"],
                "records": [
                    {"invoice_id": "AR-SHORT-1", "customer": "Cardinal Freight", "amount": "$16,500", "status": "overdue", "days_overdue": 31},
                    {"invoice_id": "AR-SHORT-2", "customer": "Summit Wholesale", "amount": "$42,000", "due": "", "status": "open"},
                ],
            },
            {
                "source_type": "ledger",
                "source_system": "CloudLedger",
                "messy_fields": ["refunds and unapplied wires obscure recognized revenue"],
                "records": [
                    {"txn_id": "BANK-SHORT-1", "description": "UNAPPLIED CARDINAL WIRE", "amount": "8000"},
                    {"txn_id": "BANK-SHORT-2", "description": "CUSTOMER REFUND SUMMIT", "amount": "-4200"},
                ],
            },
            {
                "source_type": "headcount_plan",
                "source_system": "PeopleRoster",
                "messy_fields": ["open GTM roles still planned despite coverage gap"],
                "records": [{"role_id": "HC-SHORT-1", "role": "Demand Gen Manager", "approval": "partial", "start": "2026-08-01", "loaded_cost": "$18,500"}],
            },
        ],
    },
]


def _with_counts(pack: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(pack)
    for source in item["sources"]:
        source["record_count"] = len(source.get("records") or [])
    item["source_count"] = len(item["sources"])
    item["source_types"] = sorted({source["source_type"] for source in item["sources"]})
    item["messy_input_count"] = sum(len(source.get("messy_fields") or []) for source in item["sources"])
    return item


def demo_scenarios() -> list[dict[str, Any]]:
    return [_with_counts(pack) for pack in DEMO_SCENARIOS]


def get_demo_scenario(scenario_id: str) -> dict[str, Any] | None:
    for pack in demo_scenarios():
        if pack["id"] == scenario_id or pack["branch_id"] == scenario_id:
            return pack
    return None


def scenario_branch_specs() -> list[tuple[str, str, list[dict[str, Any]], str, list[str]]]:
    return [
        (
            pack["branch_id"],
            pack["title"],
            deepcopy(pack["scenario_changes"]),
            pack["description"],
            list(pack["tags"]),
        )
        for pack in DEMO_SCENARIOS
    ]


def seed_demo_scenarios(verbose: bool = True) -> dict[str, Any]:
    """Persist the selector catalog to RedisJSON for the live demo UI."""
    items = demo_scenarios()
    for pack in items:
        R.set_json(f"{DEMO_SCENARIO_PREFIX}{pack['id']}", pack)
    summary = {"scenario_packs": len(items), "prefix": DEMO_SCENARIO_PREFIX}
    if verbose:
        print("[seed] demo scenarios:", summary)
    return summary


def list_seeded_demo_scenarios() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for key in sorted(R.keys(f"{DEMO_SCENARIO_PREFIX}*")):
            doc = R.get_json(key)
            if isinstance(doc, dict):
                rows.append(doc)
    except Exception:
        rows = []
    return rows or demo_scenarios()
