"""
Seed the demo company — Acme Corp — into Redis.

Loads (company financials are opt-in — derived from uploads by default):
  • company financials .......... RedisJSON  (atlas:company:northwind) [include_company]
  • vendor / SaaS contracts ..... RedisJSON  (atlas:vendor:*) + RediSearch index
  • finance policies & past
    board decisions ............. HASH + vector index  (atlas:policy:*) → RAG
  • recent decisions feed ....... Stream     (atlas:stream:decisions)
  • governance policy rules ..... RedisJSON + RediSearch (atlas:govpolicy:*) → lookup
  • approval matrix ............. RedisJSON  (atlas:approval_matrix:northwind)

Run:  uv run --directory agent python -m src.data.seed
"""

from __future__ import annotations

from src import redis_layer as R
from src.data import financial_seed as FS
from src.data.demo_scenarios import seed_demo_scenarios
from src.approvals import DEFAULT_MATRIX
from src.policies import DEFAULT_POLICY_RULES

COMPANY_KEY = f"{R.NS}:company:northwind"

# --------------------------------------------------------------------------- #
# Company financials (internally consistent: runway = cash / net burn)
# --------------------------------------------------------------------------- #
COMPANY: dict = {
    "id": "northwind",
    "name": "Acme Corp",
    "stage": "Series A",
    "sector": "Warehouse robotics and operations AI",
    "hq": "Austin, TX",
    "founded": 2022,
    "headcount": 38,
    "updated": "2026-06-15",
    # Cash & burn
    "cash_on_hand": 4_200_000,
    "monthly_revenue": 312_000,
    "cogs_monthly": 69_000,
    "opex_monthly": {"rd": 380_000, "sm": 190_000, "ga": 83_000},
    "monthly_gross_burn": 722_000,   # cogs + opex
    "monthly_net_burn": 410_000,     # gross burn − revenue
    "runway_months": 10.2,           # cash / net burn
    # SaaS metrics
    "mrr": 312_000,
    "arr": 3_744_000,
    "mrr_growth_mom": 0.09,
    "gross_margin": 0.78,
    "logo_churn_mom": 0.018,
    "ndr": 1.14,
    "cac": 18_500,
    "ltv": 142_000,
    "magic_number": 0.8,
    "last_raise": {
        "round": "Series A",
        "amount": 8_000_000,
        "date": "2025-04",
        "lead": "Cedar Ridge Ventures",
        "post_money": 34_000_000,
    },
    # 12-month cash & burn history (oldest → newest) for the runway chart
    "cash_history": [
        {"month": "2025-07", "cash": 8_620_000, "net_burn": 392_000},
        {"month": "2025-08", "cash": 8_230_000, "net_burn": 388_000},
        {"month": "2025-09", "cash": 7_840_000, "net_burn": 401_000},
        {"month": "2025-10", "cash": 7_440_000, "net_burn": 396_000},
        {"month": "2025-11", "cash": 7_050_000, "net_burn": 389_000},
        {"month": "2025-12", "cash": 6_660_000, "net_burn": 405_000},
        {"month": "2026-01", "cash": 6_270_000, "net_burn": 421_000},
        {"month": "2026-02", "cash": 5_860_000, "net_burn": 418_000},
        {"month": "2026-03", "cash": 5_440_000, "net_burn": 415_000},
        {"month": "2026-04", "cash": 5_020_000, "net_burn": 432_000},
        {"month": "2026-05", "cash": 4_610_000, "net_burn": 408_000},
        {"month": "2026-06", "cash": 4_200_000, "net_burn": 410_000},
    ],
    "cash_forecast": [
        {"month": "2026-07", "base_cash": 3_790_000, "downside_cash": 3_650_000, "net_burn": 410_000, "weighted_pipeline_arr": 380_000},
        {"month": "2026-08", "base_cash": 3_410_000, "downside_cash": 3_160_000, "net_burn": 380_000, "weighted_pipeline_arr": 510_000},
        {"month": "2026-09", "base_cash": 3_050_000, "downside_cash": 2_690_000, "net_burn": 360_000, "weighted_pipeline_arr": 720_000},
        {"month": "2026-10", "base_cash": 2_720_000, "downside_cash": 2_250_000, "net_burn": 330_000, "weighted_pipeline_arr": 910_000},
        {"month": "2026-11", "base_cash": 2_430_000, "downside_cash": 1_820_000, "net_burn": 290_000, "weighted_pipeline_arr": 1_050_000},
        {"month": "2026-12", "base_cash": 2_190_000, "downside_cash": 1_390_000, "net_burn": 240_000, "weighted_pipeline_arr": 1_290_000},
        {"month": "2027-01", "base_cash": 1_980_000, "downside_cash": 990_000, "net_burn": 210_000, "weighted_pipeline_arr": 1_410_000},
        {"month": "2027-02", "base_cash": 1_800_000, "downside_cash": 610_000, "net_burn": 180_000, "weighted_pipeline_arr": 1_520_000},
        {"month": "2027-03", "base_cash": 1_640_000, "downside_cash": 250_000, "net_burn": 160_000, "weighted_pipeline_arr": 1_630_000},
        {"month": "2027-04", "base_cash": 1_500_000, "downside_cash": -90_000, "net_burn": 140_000, "weighted_pipeline_arr": 1_740_000},
        {"month": "2027-05", "base_cash": 1_390_000, "downside_cash": -390_000, "net_burn": 110_000, "weighted_pipeline_arr": 1_870_000},
        {"month": "2027-06", "base_cash": 1_320_000, "downside_cash": -660_000, "net_burn": 70_000, "weighted_pipeline_arr": 2_020_000},
        {"month": "2027-07", "base_cash": 1_290_000, "downside_cash": -920_000, "net_burn": 30_000, "weighted_pipeline_arr": 2_140_000},
        {"month": "2027-08", "base_cash": 1_310_000, "downside_cash": -1_170_000, "net_burn": -20_000, "weighted_pipeline_arr": 2_260_000},
        {"month": "2027-09", "base_cash": 1_370_000, "downside_cash": -1_390_000, "net_burn": -60_000, "weighted_pipeline_arr": 2_390_000},
        {"month": "2027-10", "base_cash": 1_470_000, "downside_cash": -1_590_000, "net_burn": -100_000, "weighted_pipeline_arr": 2_530_000},
        {"month": "2027-11", "base_cash": 1_600_000, "downside_cash": -1_760_000, "net_burn": -130_000, "weighted_pipeline_arr": 2_680_000},
        {"month": "2027-12", "base_cash": 1_760_000, "downside_cash": -1_900_000, "net_burn": -160_000, "weighted_pipeline_arr": 2_810_000},
    ],
    "pipeline_by_stage": [
        {"stage": "Discovery", "opportunities": 21, "arr": 2_100_000, "weighted_arr": 315_000, "risk": "low conversion data quality"},
        {"stage": "Technical validation", "opportunities": 9, "arr": 1_850_000, "weighted_arr": 740_000, "risk": "security review bottlenecks"},
        {"stage": "Procurement", "opportunities": 5, "arr": 1_420_000, "weighted_arr": 994_000, "risk": "multi-year discount pressure"},
        {"stage": "Contracting", "opportunities": 3, "arr": 910_000, "weighted_arr": 774_000, "risk": "implementation capacity"},
    ],
    "customer_cohorts": [
        {"segment": "Enterprise 3PL", "customers": 14, "mrr": 176_000, "logo_churn_mom": 0.006, "ndr": 1.24, "risk": "implementation backlog"},
        {"segment": "Mid-market fulfillment", "customers": 41, "mrr": 121_000, "logo_churn_mom": 0.026, "ndr": 1.05, "risk": "support response times"},
        {"segment": "Pilot customers", "customers": 18, "mrr": 15_000, "logo_churn_mom": 0.041, "ndr": 0.88, "risk": "unclear paid conversion"},
    ],
    "hiring_plan": [
        {"team": "Engineering", "roles": 5, "monthly_cost": 95_000, "start_month": "2026-08", "dependency": "SOC 2 roadmap and customer integrations"},
        {"team": "Customer Success", "roles": 3, "monthly_cost": 42_000, "start_month": "2026-07", "dependency": "enterprise onboarding backlog"},
        {"team": "Sales", "roles": 2, "monthly_cost": 38_000, "start_month": "2026-09", "dependency": "pipeline conversion above 32%"},
    ],
    "security_incidents": [
        {"date": "2026-02-18", "severity": "medium", "summary": "Warehouse telemetry export shared with wrong customer admin", "cash_risk": 45_000, "status": "remediated"},
        {"date": "2026-04-09", "severity": "high", "summary": "Delayed SOC 2 evidence collection blocked two enterprise procurements", "cash_risk": 310_000, "status": "open control gap"},
        {"date": "2026-05-22", "severity": "medium", "summary": "Cloud cost alerting missed simulation workload spike", "cash_risk": 38_000, "status": "monitoring improved"},
    ],
    "audit_findings": [
        {"id": "AUD-17", "area": "Vendor spend", "severity": "medium", "finding": "Three contracts lack owner attestation before renewal", "due": "2026-07-15"},
        {"id": "AUD-21", "area": "Revenue forecast", "severity": "high", "finding": "Pipeline forecast overstates technical-validation conversion by 8-12 points", "due": "2026-07-31"},
        {"id": "AUD-24", "area": "AI governance", "severity": "medium", "finding": "Prompt changes need replay evidence before promotion", "due": "2026-08-10"},
        {"id": "AUD-31", "area": "Finance operations data", "severity": "high", "finding": "Connector files contain duplicate invoice IDs, vendor aliases, missing POs, and stale security evidence requiring reconciliation before board use", "due": "2026-06-30"},
    ],
    "board_constraints": [
        "Maintain at least 9 months runway unless a signed financing term sheet is in hand.",
        "Any single vendor commitment above $150K annualized requires board notification.",
        "New headcount must map to signed revenue, security compliance, or runway-positive automation.",
        "Enterprise deals blocked by SOC 2 evidence get priority over broad growth experiments.",
    ],
    "decision_outcomes": [
        {"decision_id": "dec-snowflake", "owner": "FP&A", "predicted": "7-month payback", "actual": "6.5-month payback", "outcome": "beat forecast", "calibration_score": 91},
        {"decision_id": "dec-brand", "owner": "CFO", "predicted": "CAC payback above 18 months", "actual": "market test showed 23-month payback", "outcome": "correct rejection", "calibration_score": 88},
        {"decision_id": "dec-aws", "owner": "Treasury", "predicted": "$96K annual savings", "actual": "$102K annual savings", "outcome": "beat forecast", "calibration_score": 94},
        {"decision_id": "dec-hiring", "owner": "Risk & Audit", "predicted": "preserve >10 months runway", "actual": "preserved 10.2 months runway", "outcome": "correct constraint", "calibration_score": 86},
        {"decision_id": "dec-support", "owner": "Procurement", "predicted": "outsourced tier-1 support saves $28K/mo", "actual": "quality misses increased churn risk; savings reversed", "outcome": "missed service-quality risk", "calibration_score": 58},
    ],
    "agent_reliability_baseline": {
        "treasury": 74,
        "fpna": 81,
        "risk": 69,
        "procurement": 63,
    },
    "prompt_versions": [
        {
            "agent": "cfo",
            "current": "cfo.v5-board-chair-ruling",
            "candidate": "cfo.v6-condition-dissent-chair",
            "promotion_gate": "must improve board ruling quality, condition specificity, analyst influence weighting, dissent resolution, and runway impact basis",
            "reliability_dimensions": ["board_ruling_quality", "condition_specificity", "analyst_influence_weighting", "dissent_resolution", "runway_impact_basis"],
            "gate_metric": "board_ruling_quality",
            "replay_set": "atlas-cfo-chair-replay",
        },
        {
            "agent": "treasury",
            "current": "treasury.v6-liquidity-mechanics",
            "candidate": "treasury.v7-late-cash-covenants",
            "promotion_gate": "must improve cash timing recall, runway sensitivity, payment-term grounding, working-capital precision, and financing-delay coverage",
            "reliability_dimensions": ["cash_timing_recall", "runway_sensitivity", "payment_term_grounding", "working_capital_precision", "financing_delay_coverage"],
            "gate_metric": "cash_timing_recall",
            "replay_set": "atlas-treasury-liquidity-replay",
        },
        {
            "agent": "fpna",
            "current": "fpna.v6-forecast-unit-economics",
            "candidate": "fpna.v7-forecastability-sensitivity",
            "promotion_gate": "must improve forecastability challenge, ARR bridge accuracy, scenario math quality, unit-economics grounding, and plan-vs-actual calibration",
            "reliability_dimensions": ["forecastability_challenge", "arr_bridge_accuracy", "scenario_math_quality", "unit_economics_grounding", "plan_vs_actual_calibration"],
            "gate_metric": "forecastability_challenge",
            "replay_set": "atlas-fpna-forecast-replay",
        },
        {
            "agent": "risk",
            "current": "risk.v7-controls-adversary",
            "candidate": "risk.v8-provenance-policy-adversary",
            "promotion_gate": "must improve control-gap detection, approval-route accuracy, source-provenance coverage, hidden-obligation recall, and downside evidence pressure",
            "reliability_dimensions": ["control_gap_detection", "approval_route_accuracy", "source_provenance_coverage", "hidden_obligation_recall", "downside_evidence_pressure"],
            "gate_metric": "control_gap_detection",
            "replay_set": "atlas-risk-controls-replay",
        },
        {
            "agent": "procurement",
            "current": "procurement.v5-commercial-negotiator",
            "candidate": "procurement.v6-renewal-leverage-redlines",
            "promotion_gate": "must improve supplier leverage specificity, renewal clause recall, benchmark grounding, termination/SLA redlines, and negotiation strategy quality",
            "reliability_dimensions": ["supplier_leverage_specificity", "renewal_clause_recall", "benchmark_grounding", "termination_sla_redlines", "negotiation_strategy_quality"],
            "gate_metric": "supplier_leverage_specificity",
            "replay_set": "atlas-procurement-commercial-replay",
        },
        {
            "agent": "reliability",
            "current": "reliability.v3-evaluator-scorecard",
            "candidate": "reliability.v4-scorecard-replay-directives",
            "promotion_gate": "must improve scorecard completeness, stance prohibition, trace-quality audit, replay-case generation, and prompt-directive usefulness",
            "reliability_dimensions": ["scorecard_completeness", "stance_prohibition", "trace_quality_audit", "replay_case_generation", "prompt_directive_usefulness"],
            "gate_metric": "scorecard_completeness",
            "replay_set": "atlas-reliability-evaluator-replay",
        },
    ],
}

# --------------------------------------------------------------------------- #
# Vendors / SaaS contracts
# --------------------------------------------------------------------------- #
VENDORS: list[dict] = [
    {"id": "aws", "name": "Amazon Web Services", "category": "infrastructure",
     "annual_cost": 336_000, "monthly_cost": 28_000, "renewal_date": "2026-12-01",
     "status": "active", "owner": "Engineering / Platform", "termination_notice_days": 90,
     "notice_window_days": 90, "auto_renew": True, "board_approved": True, "board_approval_id": "BRD-2026-01-AWS",
     "billing_frequency": "monthly", "billing_terms": "monthly in arrears against reserved-capacity commitment plus overage accruals",
     "contract_aliases": ["AWS", "Amazon AWS", "Amazon Web Svcs", "Amazon Web Services"],
     "tiered_pricing": [
         {"tier": "reserved compute", "annual_commit": 336_000, "discount_pct": 22},
         {"tier": "on-demand overage", "price_multiplier": 1.0},
     ],
     "owner_history": [
         {"owner": "Infrastructure", "from": "2023-01-01", "to": "2026-02-28"},
         {"owner": "Engineering / Platform", "from": "2026-03-01", "reason": "Platform team owns reserved-capacity commitments."},
     ],
     "termination_penalty": 84_000, "sla_uptime_pct": 99.9,
     "sla_credits": "Service credits vary by service and require support-case claim.",
     "security_clause": "Enterprise agreement includes DPA, encryption, and subprocessor notification.",
     "data_processing_addendum": True,
     "notes": "Committed-use discount in place; ~22% of gross burn."},
    {"id": "datadog", "name": "Datadog", "category": "observability",
     "annual_cost": 180_000, "monthly_cost": 15_000, "renewal_date": "2026-08-01",
     "status": "up_for_renewal", "owner": "Engineering", "termination_notice_days": 45,
     "notice_window_days": 45, "auto_renew": True, "board_approved": True, "board_approval_id": "BRD-2025-11-DDOG",
     "billing_frequency": "annual", "billing_terms": "annual committed tier with monthly overage true-up invoices",
     "contract_aliases": ["Datadog", "DataDog Inc", "Data Dog", "DDOG Observability"],
     "tiered_pricing": [
         {"tier": "committed hosts", "minimum_hosts": 0, "maximum_hosts": 300, "annual_price": 180_000},
         {"tier": "burst hosts", "minimum_hosts": 301, "unit": "host_month", "price": 74},
     ],
     "owner_history": [
         {"owner": "Engineering", "from": "2024-08-01", "to": "2026-05-10"},
         {"owner": "Platform Ops", "from": "2026-05-11", "reason": "Observability budget moved to Platform Ops."},
     ],
     "termination_penalty": 30_000, "sla_uptime_pct": 99.8,
     "sla_credits": "Credits require 30-day claim and are capped at 10% of monthly service fees.",
     "security_clause": "SOC 2 Type II current; production telemetry allowed, customer PII prohibited.",
     "data_processing_addendum": True,
     "switching_cost": 70_000, "data_sensitivity": "production telemetry",
     "notes": "Usage-based; trending ~40% over committed tier. Renewal in 8 weeks."},
    {"id": "snowflake", "name": "Snowflake", "category": "data",
     "annual_cost": 108_000, "monthly_cost": 9_000, "renewal_date": "2027-01-15",
     "status": "active", "owner": "FP&A", "termination_notice_days": 60,
     "notice_window_days": 60, "auto_renew": False, "board_approved": True, "board_approval_id": "BRD-2025-10-SNOW",
     "billing_frequency": "monthly", "billing_terms": "monthly usage invoice against committed warehouse budget",
     "contract_aliases": ["Snowflake", "Snowflake Computing", "SNOW data warehouse"],
     "tiered_pricing": [
         {"tier": "committed credits", "annual_commit": 108_000, "credit_discount_pct": 18},
         {"tier": "overage credits", "price_multiplier": 1.0},
     ],
     "owner_history": [
         {"owner": "Data", "from": "2025-01-01", "to": "2025-12-31"},
         {"owner": "FP&A", "from": "2026-01-01", "reason": "Forecasting team owns warehouse budget guardrails."},
     ],
     "termination_penalty": 0, "sla_uptime_pct": 99.9,
     "sla_credits": "Standard service-credit remedy; no unused-credit refund.",
     "security_clause": "DPA signed; customer revenue and telemetry data allowed.",
     "data_processing_addendum": True,
     "switching_cost": 125_000, "data_sensitivity": "customer revenue and telemetry",
     "notes": "Migrated Q4 2025; cut data costs ~22%."},
    {"id": "salesforce", "name": "Salesforce", "category": "crm",
     "annual_cost": 74_400, "monthly_cost": 6_200, "renewal_date": "2026-09-30",
     "status": "active", "owner": "Revenue", "termination_notice_days": 30,
     "notice_window_days": 30, "auto_renew": False, "board_approved": True, "board_approval_id": "CFO-2025-09-SFDC",
     "billing_frequency": "annual", "billing_terms": "annual prepaid seat bundle; monthly AP rows are allocations only",
     "contract_aliases": ["Salesforce", "Sales Force", "SFCI Sales Cloud"],
     "tiered_pricing": [
         {"tier": "sales cloud seats", "seats": 32, "annual_price": 74_400},
         {"tier": "incremental seats", "unit": "seat_year", "price": 2_400},
     ],
     "owner_history": [
         {"owner": "Revenue", "from": "2022-06-01", "to": "2026-05-01"},
         {"owner": "RevOps", "from": "2026-05-02", "reason": "CRM owner changed during sales-ops reorg."},
     ],
     "termination_penalty": 18_600, "sla_uptime_pct": 99.9,
     "sla_credits": "Standard service credits; credits do not offset unused seats.",
     "security_clause": "DPA signed; customer contact data permitted, opportunity notes excluded from sandbox refresh.",
     "data_processing_addendum": True,
     "switching_cost": 48_000, "data_sensitivity": "pipeline and customer contacts",
     "notes": "32 seats; ~9 underused."},
    {"id": "rippling", "name": "Rippling", "category": "hr_payroll",
     "annual_cost": 45_600, "monthly_cost": 3_800, "renewal_date": "2026-11-01",
     "status": "active", "owner": "People Ops", "termination_notice_days": 30,
     "notice_window_days": 30, "auto_renew": True, "board_approved": True,
     "billing_frequency": "monthly", "billing_terms": "monthly per-employee billing",
     "contract_aliases": ["Rippling", "Rippling HRIS"],
     "tiered_pricing": [{"tier": "base employees", "unit": "employee_month", "price": 16}],
     "termination_penalty": 0, "sla_uptime_pct": 99.5,
     "sla_credits": "Service credits only.",
     "security_clause": "DPA signed for employee PII.",
     "data_processing_addendum": True,
     "notes": "HRIS + payroll + IT."},
    {"id": "gong", "name": "Gong", "category": "sales",
     "annual_cost": 28_800, "monthly_cost": 2_400, "renewal_date": "2026-10-15",
     "status": "active", "owner": "Sales Ops", "termination_notice_days": 30,
     "notice_window_days": 30, "auto_renew": True, "board_approved": True,
     "billing_frequency": "annual", "billing_terms": "annual seat bundle billed as monthly AP accrual",
     "contract_aliases": ["Gong", "Gong.io"],
     "tiered_pricing": [{"tier": "sales seats", "seats": 24, "annual_price": 28_800}],
     "termination_penalty": 7_200, "sla_uptime_pct": 99.5,
     "sla_credits": "Credits capped at one month.",
     "security_clause": "Call recordings require retention-policy review.",
     "data_processing_addendum": True,
     "notes": "Sales call intelligence."},
    {"id": "github", "name": "GitHub Enterprise", "category": "engineering",
     "annual_cost": 22_800, "monthly_cost": 1_900, "renewal_date": "2026-10-01",
     "status": "active", "owner": "Engineering", "termination_notice_days": 30,
     "notice_window_days": 30, "auto_renew": True, "board_approved": True,
     "billing_frequency": "monthly", "billing_terms": "monthly seat true-up",
     "contract_aliases": ["GitHub", "GitHub Enterprise", "GH Enterprise"],
     "tiered_pricing": [{"tier": "enterprise seats", "unit": "seat_month", "price": 19}],
     "termination_penalty": 0, "sla_uptime_pct": 99.9,
     "sla_credits": "Enterprise SLA credits via support case.",
     "security_clause": "SOC reports available; code repository data covered by DPA.",
     "data_processing_addendum": True,
     "notes": "Includes Copilot seats."},
    {"id": "figma", "name": "Figma", "category": "design",
     "annual_cost": 14_400, "monthly_cost": 1_200, "renewal_date": "2026-12-20",
     "status": "active", "owner": "Design", "termination_notice_days": 30,
     "notice_window_days": 30, "auto_renew": True, "board_approved": True,
     "billing_frequency": "annual", "billing_terms": "annual creator-seat bundle",
     "contract_aliases": ["Figma", "FigJam"],
     "tiered_pricing": [{"tier": "creator seats", "seats": 10, "annual_price": 14_400}],
     "termination_penalty": 3_600, "sla_uptime_pct": 99.0,
     "sla_credits": "Service credits only.",
     "security_clause": "DPA not attached in procurement export; design files may include customer screenshots.",
     "data_processing_addendum": False,
     "notes": "Design + FigJam."},
]

# --------------------------------------------------------------------------- #
# Finance policies & past board decisions (semantic RAG corpus)
# --------------------------------------------------------------------------- #
POLICIES: list[dict] = [
    {"id": "pol-spend", "kind": "policy", "title": "Spend approval thresholds",
     "text": "Policy ID pol-spend maps to governance controls gov-spend-cfo and gov-board-notify. "
             "Any single financial commitment over $50,000 per year requires the approval route "
             "Department Head -> Controller -> CFO, with a CFO approval memo and signed contract or PO retained. "
             "Commitments over $150,000 per year require board notification before signing, a board memo, "
             "delivery timestamp, and audit-trail evidence. Exceptions require CFO-documented delegated authority."},
    {"id": "pol-runway", "kind": "policy", "title": "Runway guardrail",
     "text": "Policy ID pol-runway maps to governance control gov-runway-floor. Maintain at least "
             "9 months of cash runway at all times. Any decision that would reduce runway below 9 months "
             "requires CFO and Board approval, a signed financing term sheet or board-approved runway exception, "
             "before/after runway forecast, and a 30-day Treasury runway re-check. If cash arrives late, "
             "the exception must name contingency spend cuts and financing close ownership."},
    {"id": "pol-vendor", "kind": "policy", "title": "Vendor renewal review",
     "text": "Policy ID pol-vendor maps to board policies BP-2 and BP-3. Vendor contracts over "
             "$100,000 per year must be competitively reviewed and renegotiated at least 60 days before "
             "renewal or auto-renewal notice deadlines. Required evidence includes contract metadata, "
             "renewal date, termination notice, benchmark or alternative quote, procurement notes, and prior renewal outcome. "
             "Missed notice windows require CFO escalation and an audit note on lost leverage."},
    {"id": "pol-hiring", "kind": "policy", "title": "Headcount & burn discipline",
     "text": "Policy ID pol-hiring maps to governance control gov-headcount and board policy BP-5. "
             "Net-new headcount must keep monthly net-burn growth under 8% unless the role is directly tied "
             "to committed revenue, security compliance, or runway-positive automation. Required evidence includes "
             "approved headcount plan row, start date, fully loaded cost, department mapping, and business linkage. "
             "Partially approved roles, contractors, or unplanned backfills require CFO exception."},
    {"id": "pol-cash", "kind": "policy", "title": "Cash management",
     "text": "Policy ID pol-cash is the Treasury liquidity policy. Keep a minimum operating cash buffer "
             "of $1.5M, stress cash receipt delays, payment terms, renewal prepayments, payroll timing, "
             "working-capital swings, and financing close delays. Cash above 12 months of runway may be placed "
             "in short-term Treasuries only after covenant-style runway and operating-buffer checks are documented."},
    {"id": "pol-ai-promotion", "kind": "policy", "title": "AI council promotion gate",
     "text": "Policy ID pol-ai-promotion requires that no agent prompt, model, or policy change be "
             "promoted unless a W&B Weave replay evaluation beats the incumbent on reliability, policy compliance, "
             "debate value, trace quality, and calibration without regressing evidence grounding. Required evidence "
             "includes replay cases, prompt-improvement directives, and gate results by role."},
    {"id": "pol-security-blockers", "kind": "policy", "title": "Security-blocked revenue priority",
     "text": "Policy ID pol-security-blockers maps to governance control gov-security-revenue. Controls "
             "that unblock signed or late-stage enterprise revenue take priority over broad growth spend when "
             "runway is under 12 months. Required evidence includes blocked ARR, security evidence freshness, "
             "Risk & Audit sign-off, and a remediation checkpoint before funding broad growth spend."},
    {"id": "pol-data-security", "kind": "policy", "title": "Customer and regulated data review",
     "text": "Policy ID pol-data-security maps to governance control gov-data-security and board policy BP-6. "
             "Any vendor or workflow processing customer or regulated data requires Security Review, Legal review "
             "when regulated data is in scope, signed DPA, data-flow owner, and fresh SOC 2 or equivalent security evidence "
             "before go-live. No regulated-data exception is allowed without Legal and Security Review approval."},
    {"id": "pol-forecast-calibration", "kind": "policy", "title": "Post-decision forecast calibration",
     "text": "Policy ID pol-forecast-calibration maps to governance control gov-forecast-calibration and board policy BP-7. "
             "Every material council decision must compare predicted cash, ARR, margin, and control outcomes against actuals "
             "within 60 days, record a calibration score, preserve source provenance, and generate replay directives for misses."},
    {"id": "dec-snowflake", "kind": "decision", "title": "Approved Snowflake migration (Q4 2025)",
     "text": "Approved migrating the data warehouse to Snowflake at $108K/yr, cutting data costs "
             "~22% with a projected 7-month payback."},
    {"id": "dec-brand", "kind": "decision", "title": "Declined brand campaign (Q1 2026)",
     "text": "Declined a $300K brand marketing campaign because projected CAC payback exceeded the "
             "18-month threshold and it did not improve net revenue retention."},
    {"id": "dec-aws", "kind": "decision", "title": "Renegotiated AWS commitment (Q1 2026)",
     "text": "Moved AWS to a committed-use plan, saving roughly $96K per year versus on-demand."},
    {"id": "dec-hiring", "kind": "decision", "title": "Paused senior hires (Q2 2026)",
     "text": "Paused two senior backend hires to keep runway above 10 months amid slower top-of-funnel."},
    {"id": "dec-support", "kind": "decision", "title": "Reversed outsourced tier-1 support (Q2 2026)",
     "text": "Reversed an outsourced support decision after response-quality misses increased churn risk "
             "in mid-market fulfillment accounts despite short-term cost savings."},
]


def _ensure_weave() -> bool:
    """Best-effort Weave init so seeded replay sets publish as live weave.Datasets."""
    import os

    if not (os.getenv("WANDB_API_KEY") and os.getenv("WANDB_PROJECT")):
        return False
    try:
        import weave

        from src.env import redact_secrets  # noqa: F401 (ensures redaction module is importable)

        entity = os.getenv("WANDB_ENTITY")
        project = os.getenv("WANDB_PROJECT")
        weave.init(f"{entity}/{project}" if entity else project)
        # Mark health status so weave_status()/weave_links() reflect reality outside main.py.
        from src.health import set_weave_status

        set_weave_status(initialized=True, error=None)
        return True
    except Exception as exc:  # publishing simply degrades to Redis-only metadata
        from src.env import redact_secrets

        print(f"[seed] Weave init skipped (replay set stays Redis-only): {redact_secrets(exc)}")
        return False


def seed_evaluation(verbose: bool = True) -> dict:
    """Seed the W&B Weave eval subsystem: promotion candidates + default replay set.

    Idempotent. Unproven candidates are blocked by default so the promotion gates
    visibly hold the line until a live replay proves an improvement.
    """
    from src import promotion_gates as PG
    from src import replay_sets as RS

    publish = _ensure_weave()
    candidates = PG.upsert_candidates_from_prompt_versions()
    replay = RS.ensure_default_replay_set(publish=publish)

    blocked = 0
    for candidate in candidates:
        if candidate.get("status") == "proposed" and not candidate.get("last_gate_id"):
            try:
                PG.block_unproven_candidate(candidate["id"], publish=publish)
                blocked += 1
            except Exception as exc:
                from src.env import redact_secrets

                print(f"[seed] gate seed warning: {redact_secrets(exc)}")

    summary = {
        "promotion_candidates": len(candidates),
        "blocked_unproven": blocked,
        "replay_set": replay.get("slug"),
        "replay_cases": replay.get("case_count"),
        "weave_dataset_published": bool((replay.get("weave") or {}).get("published")),
    }
    if verbose:
        print("[seed] evaluation:", summary)
    return summary


def seed_governance(verbose: bool = True) -> dict:
    """Seed Acme Corp's governance layer: structured board policy rules (RedisJSON +
    RediSearch lookup index) and the board-approved approval matrix. Idempotent and
    deterministic — no embeddings or external calls required. Never seeds approval
    requests or audit evidence; those are created live by the governance engine."""
    # Structured board / finance policy rules → RedisJSON + RediSearch index.
    for rule in DEFAULT_POLICY_RULES:
        R.set_json(f"{R.GOVPOLICY_PREFIX}{rule.id}", rule.model_dump(mode="json"))
    R.ensure_govpolicy_index()

    # Board-approved approval matrix (thresholds → approver chains).
    R.set_json(R.MATRIX_KEY, DEFAULT_MATRIX)

    # Indices that back the approvals/obligations REST + monitoring views.
    R.ensure_approval_index()
    R.ensure_obligation_index()

    summary = {
        "policy_rules": len(DEFAULT_POLICY_RULES),
        "approval_tiers": len(DEFAULT_MATRIX.get("amount_tiers", [])),
        "matrix_key": R.MATRIX_KEY,
    }
    if verbose:
        print("[seed] governance loaded:", summary)
    return summary


def seed_company() -> dict:
    """Persist the bundled demo company financials (atlas:company:northwind).

    Opt-in only. Atlas runs upload-driven: the council derives its system of
    record from the operator's uploaded operations data (see
    ``src.integrations.derive_company``), so the default seed and the demo-reset
    no longer create this record. Tests, evals, and the bundled "load the sample
    company" workflow can still call this to seed the Northwind baseline.
    """
    R.set_json(COMPANY_KEY, COMPANY)
    return COMPANY


def seed(verbose: bool = True, *, include_company: bool = False) -> dict:
    """Idempotently load the demo scaffolding into Redis.

    The company *financials* record is intentionally NOT written by default —
    Atlas derives it from uploaded operations data so the council debates the
    operator's own numbers. Everything else (vendors, finance-policy RAG,
    governance matrix, eval/replay subsystem, financial-OS reference data) is
    company-agnostic scaffolding the council/tooling depend on, so it still
    seeds. Pass ``include_company=True`` to also seed the bundled Northwind
    financials (used by tests and the bundled-demo workflow).
    """
    if not R.ping():
        raise RuntimeError(f"Redis not reachable at {R.REDIS_URL}")

    # 1) Company financials (JSON system of record) — opt-in; otherwise derived
    #    from uploads at import time (src.integrations.service.apply_company_derivation).
    if include_company:
        seed_company()

    # 1b) Rolling council reliability priors for influence weighting (idempotent).
    try:
        from src.council_influence import seed_historical_reliability

        seed_historical_reliability(COMPANY["id"], COMPANY.get("agent_reliability_baseline"))
    except Exception as exc:
        from src.env import redact_secrets

        print(f"[seed] reliability history warning: {redact_secrets(exc)}")

    # 1c) Blank W&B Weave self-improvement overlay for the five-agent council
    # (CFO + four sub-agents). Idempotent — only written if absent.
    try:
        from src.self_improvement import seed_agent_improvement_state

        seed_agent_improvement_state(COMPANY["id"])
    except Exception as exc:
        from src.env import redact_secrets

        print(f"[seed] self-improvement overlay warning: {redact_secrets(exc)}")

    # 2) Vendors (JSON) + search index. These are company-agnostic finance
    #    scaffolding (generic SaaS contracts) that the live-readiness gate requires
    #    a populated vendor index for; on upload they are REPLACED by the operator's
    #    own vendor register (service.apply_company_derivation → _apply_uploaded_vendors).
    for v in VENDORS:
        R.set_json(f"{R.VENDOR_PREFIX}{v['id']}", v)
    R.ensure_vendor_index()

    # 3) Finance-policy + precedent RAG (HASH + vector index). Generic finance
    #    governance norms (not company identity) the council grounds in; the
    #    live-readiness gate requires this vector index to be populated.
    R.ensure_policy_index()
    # Idempotent: the policy RAG is not in the demo-reset clear set, so skip the
    # (network) re-embedding when it is already fully populated — this is the main
    # cost the reset reseed was paying every time.
    if len(R.keys(f"{R.POLICY_PREFIX}*")) < len(POLICIES):
        embeddings = R.embed_texts([f"{p['title']}. {p['text']}" for p in POLICIES])
        for p, emb in zip(POLICIES, embeddings):
            R.upsert_policy(p["id"], text=p["text"], kind=p["kind"], title=p["title"], embedding=emb, source_id=p["id"])

    # 4) Seed the recent-decisions stream from generic finance board precedents
    #    (also required non-empty by the live-readiness gate).
    if not R.read_events("decisions", count=1):
        for d in [p for p in POLICIES if p["kind"] == "decision"]:
            R.append_event("decisions", {"title": d["title"], "summary": d["text"], "source": "history"})

    # 5) Seed the W&B Weave eval/replay/promotion subsystem (non-fatal).
    evaluation: dict = {}
    try:
        evaluation = seed_evaluation(verbose=verbose)
    except Exception as exc:
        from src.env import redact_secrets

        print(f"[seed] evaluation seeding warning: {redact_secrets(exc)}")

    # 6) Seed the governance layer (board policy rules + approval matrix + indices).
    governance = seed_governance(verbose=verbose)

    # 7) Seed the bundled financial-OS layer (departments, invoices, POs, contracts,
    #    ARR movements, vendor clauses, knowledge corpus, scenarios) — opt-in demo
    #    data; upload-first leaves these collections to be filled from real uploads.
    financial_os: dict = {}
    demo_scenarios: dict = {}
    if include_company:
        try:
            financial_os = FS.seed_financial_os(COMPANY, VENDORS, POLICIES, verbose=verbose)
        except Exception as exc:
            from src.env import redact_secrets

            print(f"[seed] financial-OS seeding warning: {redact_secrets(exc)}")

        # 8) Seed scenario-specific messy decision examples for the demo selector.
        demo_scenarios = seed_demo_scenarios(verbose=verbose)

    # 9) Seed the orchestration namespace (atlas:orch:*) ONLY when the engine is
    #    enabled, so the core demo seed stays unchanged with ATLAS_ORCHESTRATOR off.
    orchestration: dict = {}
    import os as _os

    if _os.getenv("ATLAS_ORCHESTRATOR", "").strip().lower() in ("1", "true", "yes", "on"):
        try:
            from src.orchestration.seed import seed_orchestration

            orchestration = seed_orchestration()
        except Exception as exc:
            from src.env import redact_secrets

            print(f"[seed] orchestration seeding warning: {redact_secrets(exc)}")

    summary = {
        "company": COMPANY["name"] if include_company else None,
        "company_seeded": include_company,
        "vendors": len(VENDORS),
        "policies": len(POLICIES),
        "runway_months": COMPANY["runway_months"] if include_company else None,
        "evaluation": evaluation,
        "governance": governance,
        "financial_os": financial_os,
        "demo_scenarios": demo_scenarios,
        "orchestration": orchestration,
    }
    if verbose:
        print("[seed] loaded:", summary)
    return summary


if __name__ == "__main__":
    import os
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    # Upload-driven by default; pass --with-company (or ATLAS_SEED_COMPANY=1) to
    # also seed the bundled Northwind financials baseline.
    include_company = "--with-company" in sys.argv or os.getenv("ATLAS_SEED_COMPANY", "").strip().lower() in ("1", "true", "yes", "on")
    seed(include_company=include_company)
