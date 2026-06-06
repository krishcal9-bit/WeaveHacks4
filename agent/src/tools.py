"""
LangChain tools backing the finance agents — every tool is grounded in the
Redis system of record so agents argue with real Northwind numbers.
"""

import json

from langchain.tools import tool

from src import redis_layer as R

COMPANY_KEY = f"{R.NS}:company:northwind"


@tool
def get_company_financials() -> str:
    """Northwind Robotics' current financial position: cash, burn, runway,
    revenue, margins, growth, unit economics, and last raise."""
    co = R.get_json(COMPANY_KEY) or {}
    fields = [
        "name", "stage", "headcount", "cash_on_hand", "monthly_revenue",
        "monthly_gross_burn", "monthly_net_burn", "runway_months", "mrr", "arr",
        "mrr_growth_mom", "gross_margin", "logo_churn_mom", "ndr", "cac", "ltv",
        "opex_monthly", "last_raise",
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
    """List Northwind's vendor & SaaS contracts: name, category, annual cost,
    renewal date, status, and notes. Useful for procurement and cost decisions."""
    vendors = R.search_vendors("*", 50)
    keys = ["name", "category", "annual_cost", "monthly_cost", "renewal_date", "status", "notes"]
    return json.dumps([{k: v.get(k) for k in keys} for v in vendors])


@tool
def search_finance_policies(query: str) -> str:
    """Semantic search over Northwind's finance policies and past board
    decisions. Use this to ground recommendations in company policy and precedent."""
    hits = R.search_policies(query, k=4)
    return json.dumps([
        {"title": h["title"], "kind": h["kind"], "text": h["text"]} for h in hits
    ])


# Exposed to the finance agents (the CFO synthesizer gets the full set).
FINANCE_TOOLS = [
    get_company_financials,
    compute_runway,
    list_vendors,
    search_finance_policies,
]
