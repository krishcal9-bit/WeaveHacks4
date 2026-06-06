"""
Seed the demo company — Acme Corp — into Redis.

Loads:
  • company financials .......... RedisJSON  (atlas:company:northwind)
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
    "updated": "2026-06-01",
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
    "prompt_versions": [
        {"agent": "treasury", "current": "treasury.v3", "candidate": "treasury.v4-liquidity-stress", "promotion_gate": "must beat v3 on runway-risk recall by 5%"},
        {"agent": "fpna", "current": "fpna.v3", "candidate": "fpna.v4-cohort-calibration", "promotion_gate": "must reduce forecast overconfidence on replay set"},
        {"agent": "risk", "current": "risk.v4", "candidate": "risk.v5-control-evidence", "promotion_gate": "must catch all high-severity audit blockers"},
        {"agent": "procurement", "current": "procurement.v2", "candidate": "procurement.v3-renewal-redlines", "promotion_gate": "must improve renewal leverage scoring"},
    ],
}

# --------------------------------------------------------------------------- #
# Vendors / SaaS contracts
# --------------------------------------------------------------------------- #
VENDORS: list[dict] = [
    {"id": "aws", "name": "Amazon Web Services", "category": "infrastructure",
     "annual_cost": 336_000, "monthly_cost": 28_000, "renewal_date": "2026-12-01",
     "status": "active", "notes": "Committed-use discount in place; ~22% of gross burn."},
    {"id": "datadog", "name": "Datadog", "category": "observability",
     "annual_cost": 180_000, "monthly_cost": 15_000, "renewal_date": "2026-08-01",
     "status": "up_for_renewal", "owner": "Engineering", "termination_notice_days": 45,
     "switching_cost": 70_000, "data_sensitivity": "production telemetry",
     "notes": "Usage-based; trending ~40% over committed tier. Renewal in 8 weeks."},
    {"id": "snowflake", "name": "Snowflake", "category": "data",
     "annual_cost": 108_000, "monthly_cost": 9_000, "renewal_date": "2027-01-15",
     "status": "active", "owner": "FP&A", "termination_notice_days": 60,
     "switching_cost": 125_000, "data_sensitivity": "customer revenue and telemetry",
     "notes": "Migrated Q4 2025; cut data costs ~22%."},
    {"id": "salesforce", "name": "Salesforce", "category": "crm",
     "annual_cost": 74_400, "monthly_cost": 6_200, "renewal_date": "2026-09-30",
     "status": "active", "owner": "Revenue", "termination_notice_days": 30,
     "switching_cost": 48_000, "data_sensitivity": "pipeline and customer contacts",
     "notes": "32 seats; ~9 underused."},
    {"id": "rippling", "name": "Rippling", "category": "hr_payroll",
     "annual_cost": 45_600, "monthly_cost": 3_800, "renewal_date": "2026-11-01",
     "status": "active", "notes": "HRIS + payroll + IT."},
    {"id": "gong", "name": "Gong", "category": "sales",
     "annual_cost": 28_800, "monthly_cost": 2_400, "renewal_date": "2026-10-15",
     "status": "active", "notes": "Sales call intelligence."},
    {"id": "github", "name": "GitHub Enterprise", "category": "engineering",
     "annual_cost": 22_800, "monthly_cost": 1_900, "renewal_date": "2026-10-01",
     "status": "active", "notes": "Includes Copilot seats."},
    {"id": "figma", "name": "Figma", "category": "design",
     "annual_cost": 14_400, "monthly_cost": 1_200, "renewal_date": "2026-12-20",
     "status": "active", "notes": "Design + FigJam."},
]

# --------------------------------------------------------------------------- #
# Finance policies & past board decisions (semantic RAG corpus)
# --------------------------------------------------------------------------- #
POLICIES: list[dict] = [
    {"id": "pol-spend", "kind": "policy", "title": "Spend approval thresholds",
     "text": "Any single financial commitment over $50,000 per year requires CFO approval. "
             "Commitments over $150,000 per year require board notification before signing."},
    {"id": "pol-runway", "kind": "policy", "title": "Runway guardrail",
     "text": "Maintain at least 9 months of cash runway at all times. Any decision that would "
             "reduce runway below 9 months must be accompanied by an explicit financing plan."},
    {"id": "pol-vendor", "kind": "policy", "title": "Vendor renewal review",
     "text": "Vendor contracts over $100,000 per year must be competitively reviewed and "
             "renegotiated at least 60 days before their renewal date."},
    {"id": "pol-hiring", "kind": "policy", "title": "Headcount & burn discipline",
     "text": "Net-new headcount must keep quarterly net-burn growth under 8% unless the role is "
             "directly tied to committed revenue."},
    {"id": "pol-cash", "kind": "policy", "title": "Cash management",
     "text": "Keep a minimum operating cash buffer of $1.5M. Cash above 12 months of runway may "
             "be placed in short-term Treasuries."},
    {"id": "pol-ai-promotion", "kind": "policy", "title": "AI council promotion gate",
     "text": "No agent prompt, model, or policy change may be promoted unless a W&B Weave replay "
             "evaluation beats the incumbent on reliability, policy compliance, and calibration without "
             "regressing evidence grounding."},
    {"id": "pol-security-blockers", "kind": "policy", "title": "Security-blocked revenue priority",
     "text": "Controls that unblock signed or late-stage enterprise revenue take priority over broad "
             "growth spend when runway is under 12 months."},
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


def seed(verbose: bool = True) -> dict:
    """Idempotently load the demo company into Redis."""
    if not R.ping():
        raise RuntimeError(f"Redis not reachable at {R.REDIS_URL}")

    # 1) Company financials (JSON system of record)
    R.set_json(COMPANY_KEY, COMPANY)

    # 2) Vendors (JSON) + search index
    for v in VENDORS:
        R.set_json(f"{R.VENDOR_PREFIX}{v['id']}", v)
    R.ensure_vendor_index()

    # 3) Policies & decisions (HASH + vector index) for semantic RAG
    R.ensure_policy_index()
    embeddings = R.embed_texts([f"{p['title']}. {p['text']}" for p in POLICIES])
    for p, emb in zip(POLICIES, embeddings):
        R.upsert_policy(p["id"], text=p["text"], kind=p["kind"], title=p["title"], embedding=emb)

    # 4) Seed the recent-decisions stream from historical board decisions
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

    # 7) Seed the financial-OS layer (departments, invoices, POs, contracts, ARR
    #    movements, vendor clauses, knowledge corpus, scenarios) — non-fatal; the
    #    live preflight independently asserts the seeded counts.
    financial_os: dict = {}
    try:
        financial_os = FS.seed_financial_os(COMPANY, VENDORS, POLICIES, verbose=verbose)
    except Exception as exc:
        from src.env import redact_secrets

        print(f"[seed] financial-OS seeding warning: {redact_secrets(exc)}")

    summary = {
        "company": COMPANY["name"],
        "vendors": len(VENDORS),
        "policies": len(POLICIES),
        "runway_months": COMPANY["runway_months"],
        "evaluation": evaluation,
        "governance": governance,
        "financial_os": financial_os,
    }
    if verbose:
        print("[seed] loaded:", summary)
    return summary


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    seed()
