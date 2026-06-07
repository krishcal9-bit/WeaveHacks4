"""
Financial-OS seed data for Atlas — the richer, internally consistent operating
records that turn Redis into the company's financial system of record.

This module is intentionally *additive*: ``src.data.seed`` calls
:func:`seed_financial_os` after it has written the base company/vendor/policy
docs, and this module then layers on departments, budgets, invoices, purchase
orders, customer contracts, ARR movements, security-control blockers, vendor
clauses, the machine-readable board policy, a vector knowledge corpus, and a few
canonical scenario branches — all keyed under the financial-OS namespaces that
:mod:`src.redis_models` owns (no collisions with the governance / connector
workstreams).

Internal consistency (so agents argue with numbers that reconcile):
  • Department monthly_budget sums to opex_monthly (R&D 380k / S&M 190k / G&A 83k = 653k).
  • Department headcount sums to company headcount (38).
  • ARR movements accumulate from 1.332M → 3.744M (= company ARR) over 12 months,
    with ~2.41M trailing net-new ARR → baseline burn multiple ≈ 2.0x.
  • Security-control blocked ARR ≤ late-stage pipeline; invoices age against 2026-06-06.
"""

from __future__ import annotations

from src import redis_layer as R
from src import redis_models as M
from src import redis_store as S

# --------------------------------------------------------------------------- #
# Machine-readable board policy (thresholds the scenario engine enforces)
# --------------------------------------------------------------------------- #
BOARD_POLICY: dict = {
    "min_runway_months": 9.0,
    "cfo_approval_annual": 50_000.0,
    "board_notify_annual": 150_000.0,
    "max_quarterly_netburn_growth": 0.08,
    "min_cash_buffer": 1_500_000.0,
    "max_burn_multiple": 2.0,
    "min_gross_margin": 0.70,
}

# --------------------------------------------------------------------------- #
# Departments + budgets (monthly_budget sums to opex 653k; headcount sums to 38)
# --------------------------------------------------------------------------- #
DEPARTMENTS: list[dict] = [
    {"id": "dept-eng", "name": "Engineering", "head": "VP Engineering", "cost_center": "R&D",
     "category": "rd", "headcount": 16, "monthly_budget": 290_000, "ytd_budget": 1_740_000,
     "ytd_spend": 1_712_000, "notes": "Platform, robotics autonomy, integrations."},
    {"id": "dept-product", "name": "Product & Design", "head": "Head of Product", "cost_center": "R&D",
     "category": "rd", "headcount": 5, "monthly_budget": 90_000, "ytd_budget": 540_000,
     "ytd_spend": 548_000, "notes": "Slightly over on contract design support."},
    {"id": "dept-sales", "name": "Sales", "head": "VP Sales", "cost_center": "S&M",
     "category": "sm", "headcount": 6, "monthly_budget": 100_000, "ytd_budget": 600_000,
     "ytd_spend": 615_000, "notes": "Enterprise AE ramp; over on travel."},
    {"id": "dept-marketing", "name": "Marketing", "head": "Head of Marketing", "cost_center": "S&M",
     "category": "sm", "headcount": 3, "monthly_budget": 50_000, "ytd_budget": 300_000,
     "ytd_spend": 286_000, "notes": "Under budget after pausing brand spend."},
    {"id": "dept-cs", "name": "Customer Success", "head": "Head of Customer Success", "cost_center": "S&M",
     "category": "sm", "headcount": 5, "monthly_budget": 40_000, "ytd_budget": 240_000,
     "ytd_spend": 243_000, "notes": "Enterprise onboarding backlog driving overtime."},
    {"id": "dept-ga", "name": "G&A & Operations", "head": "Chief Operating Officer", "cost_center": "G&A",
     "category": "ga", "headcount": 3, "monthly_budget": 83_000, "ytd_budget": 498_000,
     "ytd_spend": 505_000, "notes": "Finance, people, legal, facilities."},
]

# --------------------------------------------------------------------------- #
# Customer contracts (top accounts; ARR reconciles within the cohort totals)
# --------------------------------------------------------------------------- #
CONTRACTS: list[dict] = [
    {"id": "ct-meridian", "customer": "Meridian Logistics", "segment": "Enterprise 3PL",
     "arr": 264_000, "start_date": "2024-09-01", "end_date": "2026-09-01", "term_months": 24,
     "auto_renew": True, "status": "renewing", "owner": "Enterprise AE", "expansion_arr": 36_000},
    {"id": "ct-atlas3pl", "customer": "Atlas Fulfillment", "segment": "Enterprise 3PL",
     "arr": 228_000, "start_date": "2025-01-15", "end_date": "2027-01-15", "term_months": 24,
     "auto_renew": True, "status": "active", "owner": "Enterprise AE", "expansion_arr": 24_000},
    {"id": "ct-cardinal", "customer": "Cardinal Freight", "segment": "Enterprise 3PL",
     "arr": 198_000, "start_date": "2025-03-01", "end_date": "2026-12-31", "term_months": 22,
     "auto_renew": True, "status": "active", "owner": "Enterprise AE", "expansion_arr": 30_000},
    {"id": "ct-northstar", "customer": "Northstar Distribution", "segment": "Enterprise 3PL",
     "arr": 180_000, "start_date": "2024-11-01", "end_date": "2026-11-01", "term_months": 24,
     "auto_renew": False, "status": "at_risk", "owner": "Enterprise AE",
     "expansion_arr": 0, "notes": "Implementation backlog; renewal risk."},
    {"id": "ct-vantage", "customer": "Vantage Supply Co", "segment": "Enterprise 3PL",
     "arr": 156_000, "start_date": "2025-05-01", "end_date": "2027-05-01", "term_months": 24,
     "auto_renew": True, "status": "active", "owner": "Enterprise AE", "expansion_arr": 12_000},
    {"id": "ct-brightline", "customer": "Brightline Retail", "segment": "Mid-market fulfillment",
     "arr": 84_000, "start_date": "2025-02-01", "end_date": "2026-08-01", "term_months": 18,
     "auto_renew": True, "status": "renewing", "owner": "Mid-market AE", "expansion_arr": 6_000},
    {"id": "ct-pinewood", "customer": "Pinewood Goods", "segment": "Mid-market fulfillment",
     "arr": 60_000, "start_date": "2025-06-01", "end_date": "2026-06-01", "term_months": 12,
     "auto_renew": True, "status": "active", "owner": "Mid-market AE", "expansion_arr": 0},
    {"id": "ct-harbor", "customer": "Harbor Mercantile", "segment": "Mid-market fulfillment",
     "arr": 48_000, "start_date": "2025-04-01", "end_date": "2026-10-01", "term_months": 18,
     "auto_renew": True, "status": "at_risk", "owner": "Mid-market AE",
     "expansion_arr": 0, "notes": "Support response times flagged."},
    {"id": "ct-summit", "customer": "Summit Wholesale", "segment": "Mid-market fulfillment",
     "arr": 42_000, "start_date": "2025-07-01", "end_date": "2026-07-01", "term_months": 12,
     "auto_renew": True, "status": "active", "owner": "Mid-market AE", "expansion_arr": 0},
    {"id": "ct-quickship", "customer": "QuickShip Pilot", "segment": "Pilot customers",
     "arr": 18_000, "start_date": "2026-03-01", "end_date": "2026-09-01", "term_months": 6,
     "auto_renew": False, "status": "active", "owner": "Pilot Lead",
     "expansion_arr": 0, "notes": "Paid conversion unproven."},
]

# --------------------------------------------------------------------------- #
# Invoices (accounts receivable; aged against 2026-06-06)
# --------------------------------------------------------------------------- #
INVOICES: list[dict] = [
    {"id": "inv-2026-041", "customer": "Meridian Logistics", "segment": "Enterprise 3PL",
     "contract_id": "ct-meridian", "amount": 22_000, "issued": "2026-05-01", "due": "2026-05-31",
     "status": "paid", "days_overdue": 0},
    {"id": "inv-2026-052", "customer": "Meridian Logistics", "segment": "Enterprise 3PL",
     "contract_id": "ct-meridian", "amount": 22_000, "issued": "2026-06-01", "due": "2026-06-30",
     "status": "outstanding", "days_overdue": 0},
    {"id": "inv-2026-047", "customer": "Atlas Fulfillment", "segment": "Enterprise 3PL",
     "contract_id": "ct-atlas3pl", "amount": 19_000, "issued": "2026-05-15", "due": "2026-06-14",
     "status": "outstanding", "days_overdue": 0},
    {"id": "inv-2026-039", "customer": "Cardinal Freight", "segment": "Enterprise 3PL",
     "contract_id": "ct-cardinal", "amount": 16_500, "issued": "2026-04-20", "due": "2026-05-20",
     "status": "overdue", "days_overdue": 17},
    {"id": "inv-2026-050", "customer": "Northstar Distribution", "segment": "Enterprise 3PL",
     "contract_id": "ct-northstar", "amount": 15_000, "issued": "2026-06-01", "due": "2026-07-01",
     "status": "outstanding", "days_overdue": 0},
    {"id": "inv-2026-045", "customer": "Vantage Supply Co", "segment": "Enterprise 3PL",
     "contract_id": "ct-vantage", "amount": 13_000, "issued": "2026-05-05", "due": "2026-06-04",
     "status": "paid", "days_overdue": 0},
    {"id": "inv-2026-033", "customer": "Brightline Retail", "segment": "Mid-market fulfillment",
     "contract_id": "ct-brightline", "amount": 7_000, "issued": "2026-04-10", "due": "2026-05-10",
     "status": "overdue", "days_overdue": 27},
    {"id": "inv-2026-048", "customer": "Pinewood Goods", "segment": "Mid-market fulfillment",
     "contract_id": "ct-pinewood", "amount": 5_000, "issued": "2026-05-20", "due": "2026-06-19",
     "status": "outstanding", "days_overdue": 0},
    {"id": "inv-2026-051", "customer": "Harbor Mercantile", "segment": "Mid-market fulfillment",
     "contract_id": "ct-harbor", "amount": 4_000, "issued": "2026-06-02", "due": "2026-07-02",
     "status": "outstanding", "days_overdue": 0},
    {"id": "inv-2026-053", "customer": "QuickShip Pilot", "segment": "Pilot customers",
     "contract_id": "ct-quickship", "amount": 1_500, "issued": "2026-06-05", "due": "2026-07-05",
     "status": "outstanding", "days_overdue": 0},
]

# --------------------------------------------------------------------------- #
# Purchase orders (against vendors + departments; approval_status vs. thresholds)
# --------------------------------------------------------------------------- #
PURCHASE_ORDERS: list[dict] = [
    {"id": "po-1039", "vendor_id": "snowflake", "description": "Snowflake committed-use renewal",
     "amount": 108_000, "department": "dept-ga", "status": "approved", "approval_status": "approved",
     "created": "2026-01-10"},
    {"id": "po-1042", "vendor_id": "aws", "description": "AWS reserved-capacity expansion",
     "amount": 84_000, "department": "dept-eng", "status": "approved", "approval_status": "approved",
     "created": "2026-02-02"},
    {"id": "po-1048", "vendor_id": "salesforce", "description": "Salesforce seat true-up",
     "amount": 24_000, "department": "dept-sales", "status": "approved", "approval_status": "not_required",
     "created": "2026-04-18"},
    {"id": "po-1051", "vendor_id": "datadog", "description": "Datadog annual renewal (40% over tier)",
     "amount": 180_000, "department": "dept-eng", "status": "open", "approval_status": "pending",
     "created": "2026-05-28"},
    {"id": "po-1055", "vendor_id": "github", "description": "GitHub Enterprise + Copilot seats",
     "amount": 22_800, "department": "dept-eng", "status": "approved", "approval_status": "not_required",
     "created": "2026-03-30"},
    {"id": "po-1057", "vendor_id": "rippling", "description": "Rippling HRIS + payroll renewal",
     "amount": 45_600, "department": "dept-ga", "status": "open", "approval_status": "not_required",
     "created": "2026-05-12"},
    {"id": "po-1060", "vendor_id": "gong", "description": "Gong renewal",
     "amount": 28_800, "department": "dept-sales", "status": "draft", "approval_status": "not_required",
     "created": "2026-06-01"},
]

# --------------------------------------------------------------------------- #
# Security-control blockers (blocked_arr ≤ late-stage pipeline)
# --------------------------------------------------------------------------- #
SECURITY_CONTROLS: list[dict] = [
    {"id": "ctl-soc2-evidence", "control": "SOC 2 Type II evidence collection", "framework": "SOC 2",
     "severity": "high", "blocked_arr": 310_000, "status": "open control gap", "owner": "Security Lead",
     "due": "2026-07-31"},
    {"id": "ctl-data-residency", "control": "EU data-residency controls", "framework": "ISO 27001",
     "severity": "medium", "blocked_arr": 180_000, "status": "in progress", "owner": "Platform Eng",
     "due": "2026-09-15"},
    {"id": "ctl-pentest", "control": "Annual penetration-test remediation", "framework": "SOC 2",
     "severity": "medium", "blocked_arr": 90_000, "status": "scheduled", "owner": "Security Lead",
     "due": "2026-08-20"},
    {"id": "ctl-access-review", "control": "Quarterly access-review automation", "framework": "SOC 2",
     "severity": "low", "blocked_arr": 0, "status": "monitoring", "owner": "IT", "due": "2026-07-10"},
]

# --------------------------------------------------------------------------- #
# Vendor clauses (merged into the vendor docs; drives renewal/procurement RAG)
# --------------------------------------------------------------------------- #
VENDOR_CLAUSES: dict[str, dict] = {
    "aws": {"auto_renew": True, "price_increase_cap_pct": 0.05, "termination_notice_days": 90,
            "liability_cap": 500_000, "data_processing_addendum": True, "sla_uptime_pct": 99.9,
            "renewal_uplift_pct": 0.0, "billing_frequency": "monthly",
            "billing_terms": "monthly in arrears against reserved-capacity commitment plus overage accruals",
            "contract_aliases": ["AWS", "Amazon AWS", "Amazon Web Svcs", "Amazon Web Services"],
            "tiered_pricing": [{"tier": "reserved compute", "annual_commit": 336_000, "discount_pct": 22}],
            "termination_penalty": 84_000, "sla_credits": "Service credits require support-case claim.",
            "security_clause": "Enterprise agreement includes DPA, encryption, and subprocessor notification.",
            "owner_history": [{"owner": "Infrastructure", "to": "2026-02-28"}, {"owner": "Engineering / Platform", "from": "2026-03-01"}]},
    "datadog": {"auto_renew": True, "price_increase_cap_pct": 0.12, "termination_notice_days": 45,
                "liability_cap": 180_000, "data_processing_addendum": True, "sla_uptime_pct": 99.8,
                "renewal_uplift_pct": 0.40, "billing_frequency": "annual",
                "billing_terms": "annual committed tier with monthly overage true-up invoices",
                "contract_aliases": ["Datadog", "DataDog Inc", "Data Dog", "DDOG Observability"],
                "tiered_pricing": [{"tier": "committed hosts", "annual_price": 180_000}, {"tier": "burst hosts", "price": 74, "unit": "host_month"}],
                "termination_penalty": 30_000, "sla_credits": "Credits require 30-day claim and are capped at 10% of monthly fees.",
                "security_clause": "SOC 2 Type II current; production telemetry allowed, customer PII prohibited.",
                "owner_history": [{"owner": "Engineering", "to": "2026-05-10"}, {"owner": "Platform Ops", "from": "2026-05-11"}]},
    "snowflake": {"auto_renew": False, "price_increase_cap_pct": 0.08, "termination_notice_days": 60,
                  "liability_cap": 250_000, "data_processing_addendum": True, "sla_uptime_pct": 99.9,
                  "renewal_uplift_pct": 0.0, "billing_frequency": "monthly",
                  "billing_terms": "monthly usage invoice against committed warehouse budget",
                  "contract_aliases": ["Snowflake", "Snowflake Computing", "SNOW data warehouse"],
                  "tiered_pricing": [{"tier": "committed credits", "annual_commit": 108_000, "credit_discount_pct": 18}],
                  "termination_penalty": 0, "sla_credits": "Standard service-credit remedy; no unused-credit refund.",
                  "security_clause": "DPA signed; customer revenue and telemetry data allowed.",
                  "owner_history": [{"owner": "Data", "to": "2025-12-31"}, {"owner": "FP&A", "from": "2026-01-01"}]},
    "salesforce": {"auto_renew": True, "price_increase_cap_pct": 0.07, "termination_notice_days": 30,
                   "liability_cap": 100_000, "data_processing_addendum": True, "sla_uptime_pct": 99.9,
                   "renewal_uplift_pct": 0.07, "billing_frequency": "annual",
                   "billing_terms": "annual prepaid seat bundle; AP export may show monthly allocation rows",
                   "contract_aliases": ["Salesforce", "Sales Force", "SFCI Sales Cloud"],
                   "tiered_pricing": [{"tier": "sales cloud seats", "seats": 32, "annual_price": 74_400}],
                   "termination_penalty": 18_600, "sla_credits": "Standard service credits; credits do not offset unused seats.",
                   "security_clause": "DPA signed; customer contact data permitted, opportunity notes excluded from sandbox refresh.",
                   "owner_history": [{"owner": "Revenue", "to": "2026-05-01"}, {"owner": "RevOps", "from": "2026-05-02"}]},
    "rippling": {"auto_renew": True, "termination_notice_days": 30, "billing_frequency": "monthly",
                 "contract_aliases": ["Rippling", "Rippling HRIS"], "termination_penalty": 0,
                 "data_processing_addendum": True, "security_clause": "DPA signed for employee PII."},
    "gong": {"auto_renew": True, "termination_notice_days": 30, "billing_frequency": "annual",
             "contract_aliases": ["Gong", "Gong.io"], "termination_penalty": 7_200,
             "data_processing_addendum": True, "security_clause": "Call recordings require retention-policy review."},
    "github": {"auto_renew": True, "termination_notice_days": 30, "billing_frequency": "monthly",
               "contract_aliases": ["GitHub", "GitHub Enterprise", "GH Enterprise"], "termination_penalty": 0,
               "data_processing_addendum": True, "security_clause": "SOC reports available; code repository data covered by DPA."},
    "figma": {"auto_renew": True, "termination_notice_days": 30, "billing_frequency": "annual",
              "contract_aliases": ["Figma", "FigJam"], "termination_penalty": 3_600,
              "data_processing_addendum": False, "security_clause": "DPA not attached; design files may include customer screenshots."},
}

# --------------------------------------------------------------------------- #
# ARR movements (accumulate 1.332M → 3.744M; trailing net-new ≈ 2.41M)
# --------------------------------------------------------------------------- #
_ARR_START = 1_332_000
_NET_NEW_BY_MONTH = [
    ("2025-07", 150_000), ("2025-08", 165_000), ("2025-09", 172_000), ("2025-10", 180_000),
    ("2025-11", 188_000), ("2025-12", 196_000), ("2026-01", 205_000), ("2026-02", 212_000),
    ("2026-03", 220_000), ("2026-04", 228_000), ("2026-05", 238_000), ("2026-06", 258_000),
]


def build_arr_movements() -> list[dict]:
    """Decompose each month's net-new ARR into new/expansion/contraction/churn so
    the series reconciles to company ARR (3.744M) and NDR (~1.14)."""
    movements: list[dict] = []
    ending = _ARR_START
    for month, net_new in _NET_NEW_BY_MONTH:
        churned = round(0.018 * ending)
        contraction = round(0.004 * ending)
        expansion = round(0.030 * ending)
        new_arr = net_new + churned + contraction - expansion
        ending += net_new
        movements.append({
            "month": month,
            "new_arr": new_arr,
            "expansion_arr": expansion,
            "contraction_arr": contraction,
            "churned_arr": churned,
            "net_new_arr": net_new,
            "ending_arr": ending,
        })
    return movements


# --------------------------------------------------------------------------- #
# Knowledge corpus (vector RAG): policies, decisions, vendor clauses, audit findings
# --------------------------------------------------------------------------- #
_POLICY_META: dict[str, tuple[str, str]] = {
    # id → (category, severity)
    "pol-spend": ("approvals", "high"),
    "pol-runway": ("liquidity", "high"),
    "pol-vendor": ("procurement", "medium"),
    "pol-hiring": ("headcount", "medium"),
    "pol-cash": ("liquidity", "medium"),
    "pol-ai-promotion": ("governance", "medium"),
    "pol-security-blockers": ("security", "high"),
    "pol-data-security": ("data_governance", "high"),
    "pol-forecast-calibration": ("forecast_governance", "medium"),
}


def build_knowledge_docs(policies: list[dict], company: dict) -> list[dict]:
    """Assemble the knowledge corpus from finance policies & decisions (passed
    from the base seed), vendor clauses, and the company's audit findings."""
    docs: list[dict] = []

    for p in policies:
        category, severity = _POLICY_META.get(p["id"], ("precedent", ""))
        docs.append({
            "id": p["id"],
            "kind": p["kind"],
            "title": p["title"],
            "text": p["text"],
            "source_id": p["id"],
            "category": category if p["kind"] == "policy" else "precedent",
            "severity": severity if p["kind"] == "policy" else "",
            "effective_date": "",
            "tags": [p["kind"], category],
        })

    clause_titles = {
        "datadog": "Datadog renewal & price-escalation clause",
        "aws": "AWS committed-use & liability clause",
        "snowflake": "Snowflake termination-notice clause",
        "salesforce": "Salesforce renewal-uplift clause",
    }
    for vendor_id, title in clause_titles.items():
        clause = VENDOR_CLAUSES.get(vendor_id, {})
        bits = []
        if clause.get("auto_renew") is not None:
            bits.append(f"auto-renew {'on' if clause['auto_renew'] else 'off'}")
        if clause.get("termination_notice_days"):
            bits.append(f"{clause['termination_notice_days']}-day termination notice")
        if clause.get("renewal_uplift_pct"):
            bits.append(f"renewal uplift {clause['renewal_uplift_pct']:.0%}")
        if clause.get("price_increase_cap_pct"):
            bits.append(f"price-increase cap {clause['price_increase_cap_pct']:.0%}")
        if clause.get("billing_frequency"):
            bits.append(f"{clause['billing_frequency']} billing")
        if clause.get("billing_terms"):
            bits.append(f"billing terms: {clause['billing_terms']}")
        if clause.get("contract_aliases"):
            bits.append("aliases " + ", ".join(clause["contract_aliases"]))
        if clause.get("liability_cap"):
            bits.append(f"liability cap ${clause['liability_cap']:,.0f}")
        if clause.get("termination_penalty"):
            bits.append(f"termination penalty ${clause['termination_penalty']:,.0f}")
        if clause.get("sla_uptime_pct"):
            bits.append(f"SLA {clause['sla_uptime_pct']}% uptime")
        if clause.get("sla_credits"):
            bits.append(f"SLA credits: {clause['sla_credits']}")
        if clause.get("security_clause"):
            bits.append(f"security clause: {clause['security_clause']}")
        if clause.get("tiered_pricing"):
            bits.append(f"{len(clause['tiered_pricing'])} pricing tier(s)")
        if clause.get("owner_history"):
            bits.append(f"{len(clause['owner_history'])} owner-history entry(s)")
        docs.append({
            "id": f"clause-{vendor_id}",
            "kind": "vendor_clause",
            "title": title,
            "text": f"{title}: " + "; ".join(bits) + ".",
            "source_id": vendor_id,
            "category": "vendor",
            "severity": "high" if vendor_id == "datadog" else "low",
            "effective_date": "",
            "tags": ["vendor_clause", vendor_id],
        })

    for finding in company.get("audit_findings") or []:
        docs.append({
            "id": f"aud-{finding['id']}",
            "kind": "audit_finding",
            "title": f"Audit finding {finding['id']}: {finding['area']}",
            "text": finding["finding"],
            "source_id": finding["id"],
            "category": finding["area"],
            "severity": finding.get("severity", "medium"),
            "effective_date": finding.get("due", ""),
            "tags": ["audit_finding", finding["area"]],
        })

    return docs


# --------------------------------------------------------------------------- #
# Canonical scenario branches (fixed ids → idempotent on reseed)
# --------------------------------------------------------------------------- #
def seed_scenarios() -> list[str]:
    from src import scenario_engine as E
    from src.data import demo_scenarios as DS

    legacy_specs = [
        ("demo-bridge-round", "Bridge financing round",
         [{"type": "financing", "financing_type": "equity", "amount": 5_000_000, "label": "$5M equity bridge"}],
         "Raise a $5M equity bridge to extend runway through the SOC 2 unlock.", ["financing", "runway"]),
        ("demo-aggressive-hiring", "Aggressive go-to-market hiring",
         [
             {"type": "hire", "team": "Engineering", "roles": 5, "monthly_cost": 95_000, "label": "Hire 5 engineers"},
             {"type": "hire", "team": "Sales", "roles": 2, "monthly_cost": 38_000, "label": "Hire 2 AEs"},
         ],
         "Pull forward the H2 hiring plan before pipeline conversion is proven.", ["hiring", "risk"]),
        ("demo-datadog-renegotiation", "Datadog renewal renegotiation",
         [{"type": "vendor_renegotiation", "vendor_id": "datadog", "new_annual_cost": 120_000,
           "label": "Cap Datadog at $120K/yr"}],
         "Renegotiate the Datadog renewal down from $180K to $120K/yr.", ["procurement", "savings"]),
        ("demo-enterprise-churn", "Enterprise churn shock",
         [{"type": "churn_shock", "segment": "Enterprise 3PL", "pct": 0.20, "label": "Lose 20% of enterprise MRR"}],
         "Stress test losing 20% of enterprise MRR if implementation backlog isn't cleared.", ["churn", "downside"]),
    ]
    specs = [*DS.scenario_branch_specs(), *legacy_specs]
    ids: list[str] = []
    for scenario_id, name, changes, description, tags in specs:
        try:
            E.create_scenario(name, changes, description=description, tags=tags, scenario_id=scenario_id)
            ids.append(scenario_id)
        except Exception as exc:  # a single scenario hiccup must not fail the seed
            print(f"[financial_seed] scenario '{scenario_id}' skipped: {exc}")
    return ids


# --------------------------------------------------------------------------- #
# Entry point (called from src.data.seed.seed)
# --------------------------------------------------------------------------- #
def seed_financial_os(company: dict, vendors: list[dict], policies: list[dict], *, verbose: bool = True) -> dict:
    """Layer the financial-OS records onto the already-seeded base data.

    Idempotent: rebuilds indexes via migrations, upserts every collection,
    embeds the knowledge corpus, and (re)creates the canonical scenarios.
    """
    migration = S.run_migrations()

    # 1) Augment the company system-of-record with machine-readable policy + controls.
    #    Only persist when a company record already exists (seeded opt-in or
    #    derived from uploads) — the upload-driven default never re-creates it here.
    persisted = R.get_json(M.COMPANY_KEY)
    co = dict(persisted) if persisted else dict(company)
    co["board_policy"] = BOARD_POLICY
    co["security_controls"] = SECURITY_CONTROLS
    if persisted is not None:
        R.set_json(M.COMPANY_KEY, co)

    # 2) Merge clauses + an indexable auto_renew flag into each vendor doc.
    for vendor_id, clause in VENDOR_CLAUSES.items():
        doc = R.get_json(M.vendor_key(vendor_id))
        if not isinstance(doc, dict):
            continue
        doc["clauses"] = clause
        doc["auto_renew"] = "yes" if clause.get("auto_renew") else "no"
        for key in (
            "billing_frequency",
            "billing_terms",
            "contract_aliases",
            "tiered_pricing",
            "termination_penalty",
            "sla_uptime_pct",
            "sla_credits",
            "security_clause",
            "data_processing_addendum",
            "owner_history",
            "termination_notice_days",
        ):
            if key in clause:
                doc[key] = clause[key]
        if clause.get("termination_notice_days") and not doc.get("notice_window_days"):
            doc["notice_window_days"] = clause["termination_notice_days"]
        R.set_json(M.vendor_key(vendor_id), doc)

    # 3) Store the JSON collections (idempotent upserts → auto-indexed).
    counts = {
        "departments": S.store_many(M.DEPARTMENT_PREFIX, DEPARTMENTS),
        "invoices": S.store_many(M.INVOICE_PREFIX, INVOICES),
        "purchase_orders": S.store_many(M.PO_PREFIX, PURCHASE_ORDERS),
        "contracts": S.store_many(M.CONTRACT_PREFIX, CONTRACTS),
        "arr_movements": S.store_many(M.ARR_PREFIX, build_arr_movements(), id_field="month"),
    }

    # 4) Vector knowledge corpus (policies, decisions, vendor clauses, audit findings).
    knowledge_docs = build_knowledge_docs(policies, co)
    counts["knowledge"] = S.seed_knowledge(knowledge_docs)

    # 5) Canonical scenario branches (searchable + comparable demo content).
    scenario_ids = seed_scenarios()
    counts["scenarios"] = len(scenario_ids)

    manifest = {
        "schema_version": M.SCHEMA_VERSION,
        "migration": migration,
        "counts": counts,
        "scenarios": scenario_ids,
        "indexes": [spec.name for spec in M.ALL_INDEX_SPECS],
        "streams": list(M.ALL_STREAMS),
    }
    R.set_json(M.SEED_MANIFEST_KEY, manifest)
    if verbose:
        print("[financial_seed] loaded:", counts, "scenarios:", scenario_ids)
    return manifest
