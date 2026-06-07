"""
orchestration/registry.py — the dynamic seat registry.

The Conductor names seats by role id; this module resolves each id to a concrete
persona (label / role / monogram / mandate / system prompt) the debate engine uses
to run that seat. Two sources:

  * the BASE committee — reused from ``src.agent.ROSTER`` (cfo, treasury, fpna,
    risk, procurement) via a lazy import (with an offline fallback mirror), so the
    base personas never diverge from the live demo;
  * on-demand SPECIALISTS — tax, legal, hedging, mna — seated only when the
    Conductor decides the decision needs them.

Every persona exposes a ``system_prompt`` so base and specialist seats run through
one uniform position-taking path in the debate engine.
"""

from __future__ import annotations

# Offline mirror of the base committee, used only if ``src.agent`` can't be
# imported (e.g. unit tests). Kept in sync with ROSTER in src/agent.py.
_BASE_FALLBACK: dict[str, dict] = {
    "cfo": {"label": "Office of the CFO", "role": "Chief Financial Officer · Chair", "monogram": "CF",
            "mandate": "balancing growth, risk, and runway to make the final call"},
    "treasury": {"label": "Treasury", "role": "Treasury", "monogram": "TR",
                 "mandate": "liquidity, cash position, runway, and financing risk"},
    "fpna": {"label": "FP&A", "role": "Financial Planning & Analysis", "monogram": "FP",
             "mandate": "growth, ROI, forecast, payback, and unit economics"},
    "risk": {"label": "Risk & Audit", "role": "Risk & Audit", "monogram": "RA",
             "mandate": "downside scenarios, compliance, controls, and policy adherence"},
    "procurement": {"label": "Procurement", "role": "Procurement", "monogram": "PR",
                    "mandate": "vendor terms, cost efficiency, and negotiation leverage"},
}

# On-demand specialists the Conductor can seat for decisions that need them.
SPECIALISTS: dict[str, dict] = {
    "tax": {
        "label": "Tax & Structuring",
        "role": "Tax Counsel",
        "monogram": "TX",
        "mandate": "cross-border tax exposure, entity structure, transfer pricing, and equity-comp tax",
        "applies_to": ["acquisition", "fundraising", "expansion", "equity_comp", "restructuring"],
        "system_prompt": (
            "You are Tax Counsel on a finance committee. Assess the decision's tax and structuring "
            "implications ONLY: jurisdiction/entity structure, withholding and transfer-pricing exposure, "
            "treatment of cash vs equity consideration, and equity-comp tax. Quantify exposure against the "
            "live figures where possible and flag where outside tax advice is required. Be specific, not generic."
        ),
    },
    "legal": {
        "label": "Legal & Contracts",
        "role": "Legal Counsel",
        "monogram": "LG",
        "mandate": "contracts, IP ownership, liabilities, reps & warranties, regulatory and litigation risk",
        "applies_to": ["acquisition", "vendor_contract", "partnership", "licensing", "compliance"],
        "system_prompt": (
            "You are Legal Counsel on a finance committee. Assess legal risk ONLY: contract terms, IP "
            "ownership and assignment, indemnities, reps & warranties, change-of-control, regulatory and "
            "litigation exposure. Identify the deal-breakers and the conditions that must be met before "
            "approval. Tie risks to dollar impact where you can. Be specific."
        ),
    },
    "hedging": {
        "label": "Markets & Hedging",
        "role": "Treasury Markets",
        "monogram": "HG",
        "mandate": "FX, interest-rate, and commodity exposure and the hedging program",
        "applies_to": ["acquisition", "fundraising", "expansion", "fx_exposure", "debt"],
        "system_prompt": (
            "You are the Markets & Hedging desk on a finance committee. Assess market-risk exposure ONLY: "
            "FX exposure on non-USD consideration or revenue, interest-rate and refinancing risk, and what "
            "hedges (forwards/options/swaps) would cost and cover. Quantify the exposure and the hedge cost "
            "against the live figures. Be specific."
        ),
    },
    "mna": {
        "label": "Corporate Development",
        "role": "M&A / Corp Dev",
        "monogram": "MA",
        "mandate": "valuation, deal structure, synergies, integration risk, and dilution",
        "applies_to": ["acquisition", "divestiture", "fundraising", "merger"],
        "system_prompt": (
            "You are Corporate Development (M&A) on a finance committee. Assess the transaction ONLY: "
            "valuation vs comparables, deal structure (cash/equity/earnout), expected synergies and their "
            "realization risk, dilution, and post-close integration risk. Recommend structure changes that "
            "de-risk the deal. Quantify against the live figures. Be specific."
        ),
    },
}

ALL_ROLES = list(_BASE_FALLBACK.keys()) + list(SPECIALISTS.keys())


def base_roster() -> dict[str, dict]:
    """The base committee, reused from the live demo's ROSTER (lazy import)."""
    try:
        from src.agent import ROSTER  # lazy: never drag src.agent into offline import

        return dict(ROSTER)
    except Exception:
        return {k: dict(v) for k, v in _BASE_FALLBACK.items()}


def all_specialists() -> dict[str, dict]:
    return {k: dict(v) for k, v in SPECIALISTS.items()}


def _base_system_prompt(role: str, persona: dict) -> str:
    return (
        f"You are {persona.get('label', role)} ({persona.get('role', role)}) on a finance committee. "
        f"Your mandate is {persona.get('mandate', role)}. Take a clear, decisive position on the decision, "
        "grounded in and citing the company's live figures. Be specific and quantified, not generic."
    )


def seat_persona(role: str) -> dict | None:
    """Resolve a seat id to a uniform persona dict (base or specialist)."""
    role = (role or "").lower().strip()
    base = base_roster()
    if role in base:
        persona = dict(base[role])
        persona.setdefault("system_prompt", _base_system_prompt(role, persona))
        persona["id"] = role
        persona["is_specialist"] = False
        return persona
    if role in SPECIALISTS:
        persona = dict(SPECIALISTS[role])
        persona["id"] = role
        persona["is_specialist"] = True
        return persona
    return None


def resolve_seats(roles) -> list[dict]:
    """Resolve an ordered, de-duplicated list of seat ids to personas."""
    out: list[dict] = []
    seen: set[str] = set()
    for raw in roles or []:
        role = (raw or "").lower().strip()
        if not role or role in seen:
            continue
        seen.add(role)
        persona = seat_persona(role)
        if persona:
            out.append(persona)
    return out


def suggest_specialists(decision_type: str) -> list[str]:
    """Heuristic helper (used as a hint, not a hard rule): which specialists tend
    to apply to a decision type. The Conductor still decides who is actually seated."""
    decision_type = (decision_type or "").lower()
    return [sid for sid, spec in SPECIALISTS.items() if decision_type in (spec.get("applies_to") or [])]
