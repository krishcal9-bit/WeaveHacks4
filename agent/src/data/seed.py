"""
Seed the demo company — Northwind Robotics — into Redis.

Loads:
  • company financials .......... RedisJSON  (atlas:company:northwind)
  • vendor / SaaS contracts ..... RedisJSON  (atlas:vendor:*) + RediSearch index
  • finance policies & past
    board decisions ............. HASH + vector index  (atlas:policy:*) → RAG
  • recent decisions feed ....... Stream     (atlas:stream:decisions)

Run:  uv run --directory agent python -m src.data.seed
"""

from __future__ import annotations

from src import redis_layer as R

COMPANY_KEY = f"{R.NS}:company:northwind"

# --------------------------------------------------------------------------- #
# Company financials (internally consistent: runway = cash / net burn)
# --------------------------------------------------------------------------- #
COMPANY: dict = {
    "id": "northwind",
    "name": "Northwind Robotics",
    "stage": "Series A",
    "sector": "Warehouse robotics — vertical SaaS",
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
     "status": "up_for_renewal", "notes": "Usage-based; trending ~40% over committed tier. Renewal in 8 weeks."},
    {"id": "snowflake", "name": "Snowflake", "category": "data",
     "annual_cost": 108_000, "monthly_cost": 9_000, "renewal_date": "2027-01-15",
     "status": "active", "notes": "Migrated Q4 2025; cut data costs ~22%."},
    {"id": "salesforce", "name": "Salesforce", "category": "crm",
     "annual_cost": 74_400, "monthly_cost": 6_200, "renewal_date": "2026-09-30",
     "status": "active", "notes": "32 seats; ~9 underused."},
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
]


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

    summary = {
        "company": COMPANY["name"],
        "vendors": len(VENDORS),
        "policies": len(POLICIES),
        "runway_months": COMPANY["runway_months"],
    }
    if verbose:
        print("[seed] loaded:", summary)
    return summary


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    seed()
