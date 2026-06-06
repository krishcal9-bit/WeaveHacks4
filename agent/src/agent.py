"""
Atlas — the multi-agent finance department debate graph.

A user poses any financial decision. A committee of role-based agents each take a
grounded position (Treasury, FP&A, Risk & Audit, Procurement), cross-examine each
other, and the CFO synthesizes a board-ready, quantified recommendation. State
streams to the frontend (CopilotKit useCoAgent) to drive the boardroom view; every
node is a @weave.op so the committee appears as named spans in Weave.

Flow:  intake → treasury → fpna → risk → procurement → debate → synthesis → persist
"""

import json
import os

import weave
from copilotkit import CopilotKitState
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from src import redis_layer as R
from src.tools import (
    compute_runway,
    get_company_financials,
    list_vendors,
    search_finance_policies,
)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.5")


def llm(temperature: float = 0.3):
    return init_chat_model(LLM_MODEL, model_provider=LLM_PROVIDER, temperature=temperature)


# --------------------------------------------------------------------------- #
# The committee — professional roles, not characters (org-chart, not mascots)
# --------------------------------------------------------------------------- #
ROSTER: dict[str, dict] = {
    "cfo": {
        "label": "Office of the CFO",
        "role": "Chief Financial Officer · Chair",
        "monogram": "CF",
        "mandate": "balancing growth, risk, and runway to make the final call",
    },
    "treasury": {
        "label": "Treasury",
        "role": "Treasury",
        "monogram": "TR",
        "mandate": "liquidity, cash position, runway, and financing risk",
    },
    "fpna": {
        "label": "FP&A",
        "role": "Financial Planning & Analysis",
        "monogram": "FP",
        "mandate": "growth, ROI, forecast, payback, and unit economics",
    },
    "risk": {
        "label": "Risk & Audit",
        "role": "Risk & Audit",
        "monogram": "RA",
        "mandate": "downside scenarios, compliance, controls, and policy adherence",
    },
    "procurement": {
        "label": "Procurement",
        "role": "Procurement",
        "monogram": "PR",
        "mandate": "vendor terms, cost efficiency, and negotiation leverage",
    },
}
ANALYSTS = ["treasury", "fpna", "risk", "procurement"]


# --------------------------------------------------------------------------- #
# Structured outputs (reliable JSON from the model)
# --------------------------------------------------------------------------- #
class Position(BaseModel):
    stance: str = Field(description="one of: support, oppose, conditional")
    headline: str = Field(description="one-line position, <= 12 words")
    argument: str = Field(description="2-4 sentences citing specific figures")
    key_points: list[str] = Field(default_factory=list, description="2-3 crisp bullets")


class Exchange(BaseModel):
    from_role: str = Field(description="the function raising the challenge")
    to_role: str = Field(description="the function being challenged")
    point: str = Field(description="a sharp, specific, quantified challenge")


class Rebuttals(BaseModel):
    exchanges: list[Exchange] = Field(default_factory=list)


class Recommendation(BaseModel):
    decision: str = Field(description="one of: APPROVE, REJECT, CONDITIONAL, DEFER")
    confidence: int = Field(ge=0, le=100)
    rationale: str = Field(description="3-5 sentences, decisive and quantified")
    key_risks: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    estimated_monthly_cost: float = Field(default=0, description="incremental recurring monthly cost; 0 if none")
    estimated_one_time_cost: float = Field(default=0, description="upfront one-time cost; 0 if none")
    estimated_added_monthly_revenue: float = Field(default=0, description="incremental monthly revenue; 0 if none")


# --------------------------------------------------------------------------- #
# Shared state (extends CopilotKitState → streams to the frontend)
# --------------------------------------------------------------------------- #
class DebateState(CopilotKitState):
    decision: str
    phase: str
    context: dict
    positions: list
    transcript: list
    recommendation: dict


def _extract_decision(messages: list) -> str:
    """Pull the most recent human message as the decision under review."""
    for m in reversed(messages or []):
        role = getattr(m, "type", None) or (m.get("role") if isinstance(m, dict) else None)
        if role in ("human", "user"):
            content = getattr(m, "content", None)
            if content is None and isinstance(m, dict):
                content = m.get("content", "")
            return content if isinstance(content, str) else str(content)
    return ""


def _turn(role_key: str, **extra) -> dict:
    p = ROSTER[role_key]
    return {"agent": role_key, "label": p["label"], "role": p["role"], "monogram": p["monogram"], **extra}


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #
@weave.op(name="intake")
async def intake_node(state: DebateState, config: RunnableConfig) -> dict:
    decision = _extract_decision(state.get("messages", []))
    context = {
        "financials": json.loads(get_company_financials.invoke({})),
        "vendors": json.loads(list_vendors.invoke({})),
        "policies": json.loads(search_finance_policies.invoke({"query": decision or "financial decision"})),
    }
    framing = _turn(
        "cfo",
        type="framing",
        headline="Convening the committee",
        argument=f"The committee will evaluate: “{decision}”. Treasury, FP&A, Risk & Audit, and Procurement will each weigh in before I rule.",
        key_points=[],
    )
    return {
        "decision": decision,
        "context": context,
        "phase": "analysis",
        "positions": [],
        "transcript": [framing],
        "recommendation": {},
    }


def make_analyst_node(role_key: str):
    persona = ROSTER[role_key]

    @weave.op(name=f"analyst_{role_key}")
    async def node(state: DebateState, config: RunnableConfig) -> dict:
        model = llm(0.4).with_structured_output(Position)
        system = SystemMessage(
            content=(
                f"You are {persona['label']} at Northwind Robotics (Series A), a member of its "
                f"investment committee. Your mandate is {persona['mandate']}. Evaluate the decision "
                f"strictly from your function's perspective. Cite specific figures from the company "
                f"context. Take a clear stance (support / oppose / conditional) and defend it crisply. "
                f"Speak like a senior finance executive in a boardroom — precise, quantified, no fluff. "
                f"Never mention being an AI or a model."
            )
        )
        human = HumanMessage(
            content=(
                f"DECISION UNDER REVIEW:\n{state['decision']}\n\n"
                f"COMPANY CONTEXT (Northwind Robotics):\n{json.dumps(state['context'])}\n\n"
                f"Give your position."
            )
        )
        pos: Position = await model.ainvoke([system, human], config)
        entry = _turn(
            role_key,
            type="position",
            stance=pos.stance,
            headline=pos.headline,
            argument=pos.argument,
            key_points=pos.key_points,
        )
        return {
            "positions": state.get("positions", []) + [entry],
            "transcript": state.get("transcript", []) + [entry],
            "phase": "analysis",
        }

    return node


@weave.op(name="debate_round")
async def debate_node(state: DebateState, config: RunnableConfig) -> dict:
    positions = state.get("positions", [])
    model = llm(0.55).with_structured_output(Rebuttals)
    system = SystemMessage(
        content=(
            "You are moderating an investment-committee debate at Northwind Robotics. Given each "
            "function's position, produce 3-4 sharp cross-examination exchanges where members "
            "challenge each other's reasoning with specific numbers and trade-offs. Keep it "
            "professional, substantive, and concrete — like a real boardroom, not small talk."
        )
    )
    slim = [{"role": p["role"], "stance": p.get("stance"), "headline": p.get("headline"), "key_points": p.get("key_points")} for p in positions]
    human = HumanMessage(content=f"DECISION:\n{state['decision']}\n\nPOSITIONS:\n{json.dumps(slim)}")
    reb: Rebuttals = await model.ainvoke([system, human], config)
    turns = [
        {"agent": "debate", "type": "rebuttal", "from_role": e.from_role, "to_role": e.to_role, "point": e.point}
        for e in reb.exchanges
    ]
    return {"transcript": state.get("transcript", []) + turns, "phase": "debate"}


@weave.op(name="cfo_synthesis")
async def synthesis_node(state: DebateState, config: RunnableConfig) -> dict:
    model = llm(0.3).with_structured_output(Recommendation)
    positions = state.get("positions", [])
    debate_turns = [t for t in state.get("transcript", []) if t.get("type") == "rebuttal"]
    system = SystemMessage(
        content=(
            "You are the Chief Financial Officer of Northwind Robotics, chairing the investment "
            "committee. You have heard each function's position and the cross-examination. Weigh "
            "them, resolve the disagreements, and issue a final, board-ready decision. Be decisive "
            "and quantified. Also estimate the decision's incremental monthly cost, one-time cost, "
            "and added monthly revenue (numbers only, 0 if none) so runway impact can be computed."
        )
    )
    human = HumanMessage(
        content=(
            f"DECISION:\n{state['decision']}\n\n"
            f"COMPANY CONTEXT:\n{json.dumps(state['context'])}\n\n"
            f"POSITIONS:\n{json.dumps([{'role': p['role'], 'stance': p.get('stance'), 'headline': p.get('headline'), 'argument': p.get('argument')} for p in positions])}\n\n"
            f"CROSS-EXAMINATION:\n{json.dumps([{'from': t['from_role'], 'to': t['to_role'], 'point': t['point']} for t in debate_turns])}"
        )
    )
    rec: Recommendation = await model.ainvoke([system, human], config)

    # Precise runway impact, computed (not hallucinated) from the CFO's estimates.
    impact = json.loads(
        compute_runway.invoke({
            "extra_monthly_spend": rec.estimated_monthly_cost,
            "one_time_cost": rec.estimated_one_time_cost,
            "added_monthly_revenue": rec.estimated_added_monthly_revenue,
        })
    )
    recommendation = {
        "decision": rec.decision,
        "confidence": rec.confidence,
        "rationale": rec.rationale,
        "key_risks": rec.key_risks,
        "conditions": rec.conditions,
        "impact": impact,
    }
    closing = _turn(
        "cfo",
        type="decision",
        headline=f"{rec.decision} · {rec.confidence}% confidence",
        argument=rec.rationale,
        key_points=rec.conditions or rec.key_risks,
    )
    summary = (
        f"**Recommendation: {rec.decision}** ({rec.confidence}% confidence)\n\n{rec.rationale}"
    )
    return {
        "recommendation": recommendation,
        "transcript": state.get("transcript", []) + [closing],
        "phase": "synthesis",
        "messages": [AIMessage(content=summary)],
    }


@weave.op(name="persist_decision")
async def persist_node(state: DebateState, config: RunnableConfig) -> dict:
    rec = state.get("recommendation", {})
    try:
        R.append_event("decisions", {
            "title": (state.get("decision") or "")[:140],
            "summary": (rec.get("rationale") or "")[:400],
            "decision": rec.get("decision"),
            "confidence": rec.get("confidence"),
            "source": "debate",
        })
        R.publish("dashboard", {"event": "decision", "decision": rec.get("decision")})
    except Exception as exc:  # persistence must not fail the run
        print(f"[persist] warning: {exc}")
    return {"phase": "done"}


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #
workflow = StateGraph(DebateState)
workflow.add_node("intake", intake_node)
for _a in ANALYSTS:
    workflow.add_node(_a, make_analyst_node(_a))
workflow.add_node("debate", debate_node)
workflow.add_node("synthesis", synthesis_node)
workflow.add_node("persist", persist_node)

workflow.add_edge(START, "intake")
workflow.add_edge("intake", "treasury")
workflow.add_edge("treasury", "fpna")
workflow.add_edge("fpna", "risk")
workflow.add_edge("risk", "procurement")
workflow.add_edge("procurement", "debate")
workflow.add_edge("debate", "synthesis")
workflow.add_edge("synthesis", "persist")
workflow.add_edge("persist", END)

checkpointer = MemorySaver()
graph = workflow.compile(checkpointer=checkpointer)
