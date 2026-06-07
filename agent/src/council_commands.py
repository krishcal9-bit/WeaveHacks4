"""
Atlas council command dispatcher — the server-side command-and-control engine.

The frontend command panel (and the CopilotKit actions the LLM can call) post a
structured command to ``POST /api/command``; this module validates it, refuses
unsafe or missing-context requests, executes it **live** against the same
Redis-backed finance tools and the same ``gpt-5.5`` reasoning model the council
uses, records the outcome to ``atlas:stream:commands``, and folds the result
into the Redis command-state document that the LangGraph nodes stream back over
AG-UI.

Design rules honored here:
  • No mocked council turns, no hard-coded responses — clarify/challenge/route
    are real ``@weave.op`` model calls grounded in Redis financials.
  • Strict-live gating: execution is refused (with a clear, non-pretending
    message) whenever ``require_live_ready()`` fails.
  • Scenario/evidence work goes through narrow adapters that prefer an external
    helper module if a parallel worker ships one, and otherwise fall back to the
    in-repo Redis tools. If a capability is genuinely absent the command is
    *rejected* with an explanation rather than faked.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import weave
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from src import agui_commands as C
from src import redis_layer as R
from src.env import redact_secrets
from src.health import require_live_ready
from src.tools import (
    compute_runway,
    get_company_financials,
    list_vendors,
    search_finance_policies,
)

DEBATE_SNAPSHOT_KEY = f"{R.NS}:debate:latest"
MEMO_KEY_PREFIX = f"{R.NS}:memo:"


# --------------------------------------------------------------------------- #
# Structured model output for the conversational commands
# --------------------------------------------------------------------------- #
class CommandReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str = Field(description="one-line answer, <= 14 words")
    response: str = Field(description="2-4 sentences, quantified, citing specific figures")
    key_points: list[str] = Field(description="0-3 crisp supporting bullets")
    revised_stance: str = Field(
        description="for challenges only: support / oppose / conditional / unchanged",
    )


# --------------------------------------------------------------------------- #
# Tool plumbing — run the Redis-backed LangChain tools without emitting AG-UI
# tool events (mirrors agent._tool_body so we don't import the graph module).
# --------------------------------------------------------------------------- #
def _tool_body(tool_obj, *args, **kwargs) -> str:
    func = getattr(tool_obj, "func", None)
    if func is not None:
        return func(*args, **kwargs)
    payload = kwargs if kwargs else (args[0] if args else {})
    return tool_obj.invoke(payload)


def _load_financials() -> dict[str, Any]:
    try:
        return json.loads(_tool_body(get_company_financials))
    except Exception as exc:
        print(f"[council_commands] financials load warning: {exc}")
        return {}


def _company_name(financials: dict[str, Any] | None = None) -> str:
    return (financials or {}).get("name") or "the company"


def _rejection(reason: str, message: str) -> dict[str, Any]:
    return {"status": "rejected", "reason": reason, "message": message, "result": {}, "state_patch": {}}


def _failure(message: str) -> dict[str, Any]:
    return {"status": "failed", "reason": "execution_error", "message": message, "result": {}, "state_patch": {}}


# --------------------------------------------------------------------------- #
# Narrow adapters for scenario + evidence capabilities.
#
# Prefer an external helper module if one exists (a parallel worker may add
# ``src.scenario_helpers`` / ``src.evidence_helpers``); otherwise fall back to
# the in-repo Redis tools. Returning ``None`` lets a handler emit a safe no-op
# rejection that names the missing capability instead of pretending success.
# --------------------------------------------------------------------------- #
def _scenario_adapter() -> Callable[..., str] | None:
    try:
        from src import scenario_helpers  # type: ignore

        fn = getattr(scenario_helpers, "fork_scenario", None)
        if callable(fn):
            return fn
    except Exception:
        pass
    # In-repo capability: the runway projection tool is always available.
    if compute_runway is not None:
        return lambda **kw: _tool_body(compute_runway, **kw)
    return None


def _evidence_adapter() -> dict[str, Callable[..., Any]] | None:
    """Resolve evidence-pinning helpers. Returns a map of kind -> resolver."""
    external: dict[str, Callable[..., Any]] = {}
    try:
        from src import evidence_helpers  # type: ignore

        for kind in ("policy", "vendor", "financial"):
            fn = getattr(evidence_helpers, f"resolve_{kind}", None)
            if callable(fn):
                external[kind] = fn
    except Exception:
        external = {}

    resolvers: dict[str, Callable[..., Any]] = {
        "policy": external.get("policy", _resolve_policy_evidence),
        "vendor": external.get("vendor", _resolve_vendor_evidence),
        "financial": external.get("financial", _resolve_financial_evidence),
        "custom": _resolve_custom_evidence,
    }
    return resolvers or None


def _resolve_policy_evidence(query: str, **_: Any) -> list[dict[str, Any]]:
    hits = json.loads(_tool_body(search_finance_policies, query=query or "finance policy"))
    out = []
    for hit in hits[:3]:
        out.append(
            {
                "kind": "policy",
                "title": hit.get("title") or "Policy",
                "detail": (hit.get("text") or "")[:400],
                "source": "Redis vector RAG · atlas:idx:policies",
            }
        )
    return out


def _resolve_vendor_evidence(query: str, ref: str | None = None, **_: Any) -> list[dict[str, Any]]:
    vendors = json.loads(_tool_body(list_vendors))
    needle = (ref or query or "").strip().lower()
    matches = [v for v in vendors if needle in str(v.get("name", "")).lower()] if needle else vendors
    out = []
    for vendor in (matches or vendors)[:3]:
        cost = vendor.get("annual_cost") or vendor.get("monthly_cost")
        out.append(
            {
                "kind": "vendor",
                "title": vendor.get("name") or "Vendor",
                "detail": f"{vendor.get('category', 'vendor')} · cost {cost} · renews {vendor.get('renewal_date', 'n/a')} · {vendor.get('status', '')}".strip(),
                "source": "RediSearch · atlas:idx:vendors",
            }
        )
    return out


def _resolve_financial_evidence(query: str, ref: str | None = None, **_: Any) -> list[dict[str, Any]]:
    financials = _load_financials()
    field = (ref or query or "").strip()
    candidates = [field] if field else ["runway_months", "cash_on_hand", "monthly_net_burn"]
    out = []
    for key in candidates:
        if key in financials:
            out.append(
                {
                    "kind": "financial",
                    "title": key.replace("_", " ").title(),
                    "detail": f"{key} = {financials.get(key)}",
                    "source": "RedisJSON · atlas:company:northwind",
                }
            )
    if not out and financials:
        out.append(
            {
                "kind": "financial",
                "title": "Company snapshot",
                "detail": f"{_company_name(financials)} · runway {financials.get('runway_months')}m · cash {financials.get('cash_on_hand')}",
                "source": "RedisJSON · atlas:company:northwind",
            }
        )
    return out


def _resolve_custom_evidence(query: str, note: str | None = None, **_: Any) -> list[dict[str, Any]]:
    text = (note or query or "").strip()
    if not text:
        return []
    return [
        {
            "kind": "custom",
            "title": text[:60],
            "detail": text,
            "source": "operator note",
        }
    ]


# --------------------------------------------------------------------------- #
# Conversational command handlers (clarify / route_question / challenge /
# defend_position / rerun_role)
# --------------------------------------------------------------------------- #
def _persona(agent_id: str) -> dict[str, Any]:
    from src.agent import ROSTER  # lazy import; agent module owns the roster

    return ROSTER.get(agent_id, {"label": agent_id, "role": agent_id, "mandate": "finance analysis"})


def _context_decision(command: dict[str, Any]) -> str:
    ctx = command.get("payload", {}).get("context") or {}
    return str(ctx.get("decision") or "").strip()


def role_command_metadata(agent_id: str, command_type: str) -> dict[str, str]:
    """Role-specific command metadata streamed to the UI and used in prompts."""
    profile = C.role_command_profile(agent_id)
    return {
        "role_lens": str(profile.get("command_lens") or ""),
        "role_instruction": C.role_command_instruction(agent_id, command_type),
        "evidence_priorities": str(profile.get("evidence_priorities") or ""),
        "avoid": str(profile.get("avoid") or ""),
    }


def build_role_command_system(agent_id: str, command_type: str, persona: dict[str, Any], financials: dict[str, Any]) -> str:
    """Build the system prompt for a role-targeted operator command."""
    meta = role_command_metadata(agent_id, command_type)
    return (
        f"You are {persona['label']} at {_company_name(financials)}, responding to an operator command "
        f"during a live investment-committee debate. Your standing mandate is {persona['mandate']}.\n"
        f"ROLE-SPECIFIC COMMAND MANDATE: {meta['role_instruction']}\n"
        f"ROLE LENS: {meta['role_lens']}\n"
        f"EVIDENCE TO PREFER: {meta['evidence_priorities']}\n"
        f"AVOID ROLE DRIFT: {meta['avoid']}\n"
        "Use the response and key_points fields to make the role lens visible. Cite specific figures from "
        "the company context or say which exact role-specific evidence is missing. Stay concise, quantified, "
        "and never mention being an AI."
    )


async def _run_reply(agent_id: str, system_text: str, human_text: str, temperature: float) -> CommandReply:
    from src.agent import llm  # lazy import to reuse the council's model config

    model = llm(temperature).with_structured_output(CommandReply)
    return await model.ainvoke([SystemMessage(content=system_text), HumanMessage(content=human_text)])


def _prior_position_text(command: dict[str, Any]) -> str:
    position = (command.get("payload", {}).get("context") or {}).get("position") or {}
    if not position:
        return "No prior position from this role is on record yet."
    return json.dumps(
        {
            "stance": position.get("stance"),
            "headline": position.get("headline"),
            "argument": position.get("argument"),
            "key_points": position.get("key_points"),
        }
    )


def _focus_patch(
    *,
    command_type: str,
    agent_id: str,
    persona: dict[str, Any],
    mode: str,
    prompt: str,
    reply: CommandReply,
    revised_stance: str | None = None,
) -> dict[str, Any]:
    meta = role_command_metadata(agent_id, command_type)
    return {
        "agent": agent_id,
        "label": persona["label"],
        "mode": mode,
        "question": prompt,
        "headline": reply.headline,
        "response": reply.response,
        "key_points": reply.key_points,
        "revised_stance": revised_stance or reply.revised_stance or "unchanged",
        **meta,
        "at": C.now_label(),
    }


def _result_payload(
    *,
    command_type: str,
    agent_id: str,
    persona: dict[str, Any],
    kind: str,
    prompt_key: str,
    prompt: str,
    reply: CommandReply,
    revised_stance: str | None = None,
) -> dict[str, Any]:
    meta = role_command_metadata(agent_id, command_type)
    return {
        "agent": agent_id,
        "label": persona["label"],
        prompt_key: prompt,
        "headline": reply.headline,
        "response": reply.response,
        "key_points": reply.key_points,
        "revised_stance": revised_stance or reply.revised_stance or "unchanged",
        "kind": kind,
        **meta,
    }


@weave.op(name="council_command_clarify")
async def _handle_clarify(command: dict[str, Any], *, route: bool = False) -> dict[str, Any]:
    agent_id = command.get("agent")
    persona = _persona(agent_id)
    decision = _context_decision(command)
    if not decision:
        return _rejection(
            "missing_context",
            "No decision is under review. Submit a decision to the council before asking a role to clarify.",
        )
    question = str(command.get("payload", {}).get("question") or "").strip()
    if not question:
        return _rejection("missing_input", "Provide a question for the role to address.")

    financials = _load_financials()
    command_type = "route_question" if route else "clarify"
    system = build_role_command_system(agent_id, command_type, persona, financials)
    human = (
        f"DECISION UNDER REVIEW:\n{decision}\n\n"
        f"YOUR PRIOR POSITION:\n{_prior_position_text(command)}\n\n"
        f"COMPANY CONTEXT:\n{json.dumps(financials)}\n\n"
        f"OPERATOR COMMAND TYPE:\n{command_type}\n\n"
        f"OPERATOR QUESTION:\n{question}"
    )
    try:
        reply = await _run_reply(agent_id, system, human, 0.35)
    except Exception as exc:
        return _failure(f"Model call failed: {redact_secrets(exc)}")

    ctype_label = "Routed question" if route else "Clarification"
    mode = "route" if route else "clarify"
    result_payload = _result_payload(
        command_type=command_type,
        agent_id=agent_id,
        persona=persona,
        kind=mode,
        prompt_key="question",
        prompt=question,
        reply=reply,
    )
    return {
        "status": "executed",
        "reason": None,
        "message": f"{ctype_label} delivered by {persona['label']} using its {result_payload['role_lens']} lens.",
        "result": result_payload,
        "state_patch": {
            "agent_focus": _focus_patch(
                command_type=command_type,
                agent_id=agent_id,
                persona=persona,
                mode=mode,
                prompt=question,
                reply=reply,
            )
        },
    }


@weave.op(name="council_command_route_question")
async def _handle_route_question(command: dict[str, Any]) -> dict[str, Any]:
    return await _handle_clarify(command, route=True)


@weave.op(name="council_command_challenge")
async def _handle_challenge(command: dict[str, Any]) -> dict[str, Any]:
    agent_id = command.get("agent")
    persona = _persona(agent_id)
    decision = _context_decision(command)
    if not decision:
        return _rejection(
            "missing_context",
            "No decision is under review. The council must have spoken before a claim can be challenged.",
        )
    point = str(command.get("payload", {}).get("point") or command.get("payload", {}).get("claim") or "").strip()
    if not point:
        return _rejection("missing_input", "Provide the claim or challenge for the role to defend.")

    financials = _load_financials()
    command_type = "challenge_claim"
    system = build_role_command_system(agent_id, command_type, persona, financials)
    human = (
        f"DECISION UNDER REVIEW:\n{decision}\n\n"
        f"YOUR PRIOR POSITION:\n{_prior_position_text(command)}\n\n"
        f"COMPANY CONTEXT:\n{json.dumps(financials)}\n\n"
        "CHALLENGE CONTRACT:\nEither defend the claim with role-specific figures or concede and revise. "
        "Set revised_stance to support/oppose/conditional, or 'unchanged' if your stance holds.\n\n"
        f"OPERATOR CHALLENGE:\n{point}"
    )
    try:
        reply = await _run_reply(agent_id, system, human, 0.45)
    except Exception as exc:
        return _failure(f"Model call failed: {redact_secrets(exc)}")

    revised = reply.revised_stance or "unchanged"
    result_payload = _result_payload(
        command_type=command_type,
        agent_id=agent_id,
        persona=persona,
        kind="challenge",
        prompt_key="challenge",
        prompt=point,
        reply=reply,
        revised_stance=revised,
    )
    return {
        "status": "executed",
        "reason": None,
        "message": f"{persona['label']} responded through its {result_payload['role_lens']} lens.",
        "result": result_payload,
        "state_patch": {
            "agent_focus": _focus_patch(
                command_type=command_type,
                agent_id=agent_id,
                persona=persona,
                mode="challenge",
                prompt=point,
                reply=reply,
                revised_stance=revised,
            )
        },
    }


@weave.op(name="council_command_defend")
async def _handle_defend(command: dict[str, Any]) -> dict[str, Any]:
    agent_id = command.get("agent")
    persona = _persona(agent_id)
    decision = _context_decision(command)
    if not decision:
        return _rejection(
            "missing_context",
            "No decision is under review. The council must have spoken before a role can defend its position.",
        )
    focus = str(command.get("payload", {}).get("point") or command.get("payload", {}).get("question") or "").strip()
    focus = focus or "Defend your current role position."

    financials = _load_financials()
    command_type = "defend_position"
    system = build_role_command_system(agent_id, command_type, persona, financials)
    human = (
        f"DECISION UNDER REVIEW:\n{decision}\n\n"
        f"YOUR PRIOR POSITION:\n{_prior_position_text(command)}\n\n"
        f"COMPANY CONTEXT:\n{json.dumps(financials)}\n\n"
        "DEFENSE CONTRACT:\nDefend only the part of the position that belongs to your mandate. "
        "If the prior position lacks role-specific evidence, say so and name the missing evidence.\n\n"
        f"OPERATOR DEFENSE REQUEST:\n{focus}"
    )
    try:
        reply = await _run_reply(agent_id, system, human, 0.35)
    except Exception as exc:
        return _failure(f"Model call failed: {redact_secrets(exc)}")

    result_payload = _result_payload(
        command_type=command_type,
        agent_id=agent_id,
        persona=persona,
        kind="defend",
        prompt_key="question",
        prompt=focus,
        reply=reply,
    )
    return {
        "status": "executed",
        "reason": None,
        "message": f"{persona['label']} defended its position through its {result_payload['role_lens']} lens.",
        "result": result_payload,
        "state_patch": {
            "agent_focus": _focus_patch(
                command_type=command_type,
                agent_id=agent_id,
                persona=persona,
                mode="defend",
                prompt=focus,
                reply=reply,
            )
        },
    }


@weave.op(name="council_command_rerun_role")
async def _handle_rerun_role(command: dict[str, Any]) -> dict[str, Any]:
    agent_id = command.get("agent")
    persona = _persona(agent_id)
    decision = _context_decision(command)
    if not decision:
        return _rejection(
            "missing_context",
            "No decision is under review. Submit a decision before rerunning a role analysis.",
        )
    reason = str(command.get("payload", {}).get("reason") or command.get("payload", {}).get("question") or "").strip()
    reason = reason or "Rerun this role analysis from scratch."

    financials = _load_financials()
    command_type = "rerun_role"
    system = build_role_command_system(agent_id, command_type, persona, financials)
    human = (
        f"DECISION UNDER REVIEW:\n{decision}\n\n"
        f"PREVIOUS POSITION TO IGNORE IF NEEDED:\n{_prior_position_text(command)}\n\n"
        f"COMPANY CONTEXT:\n{json.dumps(financials)}\n\n"
        "RERUN CONTRACT:\nRe-analyze from scratch under your unique role mandate. Do not summarize the old answer. "
        "Return a role-specific stance in revised_stance and cite the numbers or missing evidence that changed the view.\n\n"
        f"OPERATOR RERUN REASON:\n{reason}"
    )
    try:
        reply = await _run_reply(agent_id, system, human, 0.4)
    except Exception as exc:
        return _failure(f"Model call failed: {redact_secrets(exc)}")

    revised = reply.revised_stance or "unchanged"
    result_payload = _result_payload(
        command_type=command_type,
        agent_id=agent_id,
        persona=persona,
        kind="rerun",
        prompt_key="question",
        prompt=reason,
        reply=reply,
        revised_stance=revised,
    )
    return {
        "status": "executed",
        "reason": None,
        "message": f"{persona['label']} reran analysis through its {result_payload['role_lens']} lens.",
        "result": result_payload,
        "state_patch": {
            "agent_focus": _focus_patch(
                command_type=command_type,
                agent_id=agent_id,
                persona=persona,
                mode="rerun",
                prompt=reason,
                reply=reply,
                revised_stance=revised,
            )
        },
    }


# --------------------------------------------------------------------------- #
# Scenario commands (scenario_fork / compare_options)
# --------------------------------------------------------------------------- #
def _scenario_params(payload: dict[str, Any]) -> dict[str, float]:
    def num(key: str) -> float:
        try:
            return float(payload.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    return {
        "extra_monthly_spend": num("extra_monthly_spend"),
        "one_time_cost": num("one_time_cost"),
        "added_monthly_revenue": num("added_monthly_revenue"),
    }


def _run_scenario(adapter: Callable[..., str], params: dict[str, float]) -> dict[str, Any]:
    return json.loads(adapter(**params))


@weave.op(name="council_command_scenario_fork")
async def _handle_scenario_fork(command: dict[str, Any]) -> dict[str, Any]:
    adapter = _scenario_adapter()
    if adapter is None:
        return _rejection(
            "capability_unavailable",
            "No scenario/runway capability is wired in (expected compute_runway or scenario_helpers.fork_scenario).",
        )
    if not _load_financials():
        return _rejection("missing_context", "Company financials are unavailable in Redis; cannot project a scenario.")

    payload = command.get("payload", {})
    params = _scenario_params(payload)
    if all(value == 0 for value in params.values()):
        return _rejection(
            "missing_input",
            "Provide at least one of extra monthly spend, one-time cost, or added monthly revenue.",
        )
    label = str(payload.get("label") or "Operator scenario").strip()
    try:
        impact = _run_scenario(adapter, params)
    except Exception as exc:
        return _failure(f"Scenario projection failed: {redact_secrets(exc)}")

    scenario = {
        "id": command.get("id"),
        "mode": "single",
        "label": label,
        "params": params,
        "impact": impact,
        "at": C.now_label(),
    }
    return {
        "status": "executed",
        "reason": None,
        "message": f"Scenario '{label}' projected against the live cash record.",
        "result": scenario,
        "state_patch": {"requested_scenario": scenario},
    }


@weave.op(name="council_command_compare_options")
async def _handle_compare(command: dict[str, Any]) -> dict[str, Any]:
    adapter = _scenario_adapter()
    if adapter is None:
        return _rejection(
            "capability_unavailable",
            "No scenario/runway capability is wired in (expected compute_runway or scenario_helpers.fork_scenario).",
        )
    if not _load_financials():
        return _rejection("missing_context", "Company financials are unavailable in Redis; cannot compare options.")

    options = command.get("payload", {}).get("options") or []
    if not isinstance(options, list) or len(options) < 2:
        return _rejection("missing_input", "Provide at least two options to compare.")
    if len(options) > 4:
        options = options[:4]

    compared = []
    try:
        for index, option in enumerate(options):
            params = _scenario_params(option if isinstance(option, dict) else {})
            impact = _run_scenario(adapter, params)
            compared.append(
                {
                    "label": str((option or {}).get("label") or f"Option {chr(65 + index)}"),
                    "params": params,
                    "impact": impact,
                }
            )
    except Exception as exc:
        return _failure(f"Comparison projection failed: {redact_secrets(exc)}")

    scenario = {
        "id": command.get("id"),
        "mode": "compare",
        "options": compared,
        "at": C.now_label(),
    }
    return {
        "status": "executed",
        "reason": None,
        "message": f"Compared {len(compared)} options on live runway impact.",
        "result": scenario,
        "state_patch": {"requested_scenario": scenario},
    }


# --------------------------------------------------------------------------- #
# Evidence command (pin_evidence)
# --------------------------------------------------------------------------- #
@weave.op(name="council_command_pin_evidence")
async def _handle_pin_evidence(command: dict[str, Any]) -> dict[str, Any]:
    resolvers = _evidence_adapter()
    if resolvers is None:
        return _rejection("capability_unavailable", "No evidence-resolution capability is wired in.")

    payload = command.get("payload", {})
    kind = str(payload.get("kind") or "policy").strip().lower()
    resolver = resolvers.get(kind)
    if resolver is None:
        return _rejection(
            "capability_unavailable",
            f"Evidence kind '{kind}' is not supported. Supported: {', '.join(sorted(resolvers))}.",
        )

    query = str(payload.get("query") or "").strip()
    note = payload.get("note")
    ref = payload.get("ref")
    if kind == "custom" and not (note or query):
        return _rejection("missing_input", "Provide a note to pin custom evidence.")
    if kind in ("policy",) and not query:
        return _rejection("missing_input", "Provide a search query to pin a policy or precedent.")

    try:
        resolved = resolver(query=query, ref=ref, note=note)
    except Exception as exc:
        return _failure(f"Evidence resolution failed: {redact_secrets(exc)}")

    if not resolved:
        return _rejection("not_found", f"No {kind} evidence matched the request; nothing pinned.")

    pins = []
    for item in resolved:
        pins.append(
            {
                "id": C.new_command_id(),
                "kind": item.get("kind", kind),
                "title": item.get("title"),
                "detail": item.get("detail"),
                "source": item.get("source"),
                "at": C.now_label(),
            }
        )
    return {
        "status": "executed",
        "reason": None,
        "message": f"Pinned {len(pins)} {kind} evidence item(s) to the board record.",
        "result": {"pinned": pins},
        "state_patch": {"pinned_evidence": pins},
    }


# --------------------------------------------------------------------------- #
# Phase controls (pause_phase / resume_phase)
# --------------------------------------------------------------------------- #
def _handle_phase(command: dict[str, Any], *, paused: bool) -> dict[str, Any]:
    payload = command.get("payload", {})
    phase = payload.get("phase")
    reason = payload.get("reason")
    controls = {
        "paused": paused,
        "phase": phase,
        "reason": reason,
        "updated_at": C.now_label(),
    }
    verb = "Pause" if paused else "Resume"
    detail = f"at the {phase} boundary" if phase else "at the next node boundary"
    return {
        "status": "executed",
        "reason": None,
        "message": f"{verb} requested {detail}. The council honors it cooperatively between nodes.",
        "result": controls,
        "state_patch": {"phase_controls": controls},
    }


# --------------------------------------------------------------------------- #
# Board memo export (export_memo)
# --------------------------------------------------------------------------- #
def _load_debate_snapshot(command: dict[str, Any]) -> dict[str, Any]:
    """Prefer the Redis-persisted debate snapshot; fall back to a payload one."""
    try:
        snapshot = R.get_json(DEBATE_SNAPSHOT_KEY)
        if isinstance(snapshot, dict) and snapshot.get("recommendation"):
            return snapshot
    except Exception as exc:
        print(f"[council_commands] debate snapshot load warning: {exc}")
    payload_snapshot = command.get("payload", {}).get("snapshot")
    return payload_snapshot if isinstance(payload_snapshot, dict) else {}


def _format_memo(snapshot: dict[str, Any], pinned: list[dict[str, Any]]) -> str:
    rec = snapshot.get("recommendation") or {}
    decision = snapshot.get("decision") or "(decision)"
    impact = rec.get("impact") or {}
    lines: list[str] = []
    lines.append(f"# Board Memo — {snapshot.get('company') or 'Acme Corp'}")
    lines.append("")
    lines.append(f"**Decision under review:** {decision}")
    lines.append("")
    lines.append(f"## Recommendation: {rec.get('decision', 'N/A')} ({rec.get('confidence', '--')}% confidence)")
    if rec.get("rationale"):
        lines.append("")
        lines.append(rec["rationale"])
    if impact:
        lines.append("")
        lines.append("### Runway impact")
        lines.append(
            f"- Current runway: {impact.get('current_runway_months', 'n/a')} months"
        )
        lines.append(
            f"- Scenario runway: {impact.get('scenario_runway_months', 'n/a')} months"
        )
        if impact.get("delta_months") is not None:
            lines.append(f"- Delta: {impact.get('delta_months')} months")
    positions = snapshot.get("positions") or []
    if positions:
        lines.append("")
        lines.append("### Committee positions")
        for pos in positions:
            lines.append(
                f"- **{pos.get('label') or pos.get('role') or pos.get('agent')}** "
                f"({pos.get('stance', 'n/a')}): {pos.get('headline', '')}"
            )
    risks = rec.get("key_risks") or []
    if risks:
        lines.append("")
        lines.append("### Key risks")
        lines.extend(f"- {risk}" for risk in risks)
    conditions = rec.get("conditions") or []
    if conditions:
        lines.append("")
        lines.append("### Conditions")
        lines.extend(f"- {cond}" for cond in conditions)
    if pinned:
        lines.append("")
        lines.append("### Pinned evidence")
        for pin in pinned:
            lines.append(f"- **{pin.get('title')}** — {pin.get('detail')} ({pin.get('source')})")
    learning = snapshot.get("learning_report") or {}
    if learning.get("summary"):
        lines.append("")
        lines.append("### Reliability summary")
        lines.append(learning["summary"])
    lines.append("")
    lines.append(f"_Generated {C.now_label()} · Atlas finance council._")
    return "\n".join(lines)


@weave.op(name="council_command_export_memo")
async def _handle_export(command: dict[str, Any], *, pinned: list[dict[str, Any]]) -> dict[str, Any]:
    snapshot = _load_debate_snapshot(command)
    if not snapshot.get("recommendation"):
        return _rejection(
            "missing_context",
            "No completed decision is available to export. Run a debate to a CFO ruling first.",
        )
    memo = _format_memo(snapshot, pinned)
    memo_id = command.get("id")
    generated_at = C.now_label()
    try:
        R.set_json(
            f"{MEMO_KEY_PREFIX}{memo_id}",
            {"id": memo_id, "memo": memo, "generated_at": generated_at, "decision": snapshot.get("decision")},
        )
        stream_id = R.append_event(
            "memos",
            {"memo_id": memo_id, "decision": (snapshot.get("decision") or "")[:140], "generated_at": generated_at},
        )
    except Exception as exc:
        print(f"[council_commands] memo persist warning: {exc}")
        stream_id = None

    export_status = {
        "ready": True,
        "id": memo_id,
        "format": "markdown",
        "generated_at": generated_at,
        "title": f"Board memo · {snapshot.get('decision', 'decision')[:60]}",
        "memo": memo,
        "stream_id": stream_id,
    }
    return {
        "status": "executed",
        "reason": None,
        "message": "Board memo assembled from the completed decision.",
        "result": export_status,
        "state_patch": {"export_status": export_status},
    }


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #
async def dispatch_command(raw_command: dict[str, Any], *, room: str = C.DEFAULT_ROOM) -> dict[str, Any]:
    """Validate, execute, record, and persist a single operator command.

    Always returns a structured envelope:
        {status, reason, message, result, command, state, stream_id}
    Rejections and failures are returned (not raised) so the UI can explain them.
    """
    command = C.normalize_command(raw_command)
    command["source"] = raw_command.get("source") or "operator"

    ok, error = C.validate_command(command)
    if not ok:
        result = _rejection("invalid_command", error or "Invalid command.")
        return _finalize(command, result, room)

    # Strict-live gating — the same contract that locks decision submission.
    try:
        require_live_ready()
    except Exception as exc:
        result = _rejection(
            "not_live",
            f"Command refused: Atlas strict-live preflight is not green. {redact_secrets(exc)}",
        )
        return _finalize(command, result, room)

    ctype = command["type"]
    try:
        if ctype == "clarify":
            result = await _handle_clarify(command)
        elif ctype == "route_question":
            result = await _handle_route_question(command)
        elif ctype == "challenge_claim":
            result = await _handle_challenge(command)
        elif ctype == "defend_position":
            result = await _handle_defend(command)
        elif ctype == "rerun_role":
            result = await _handle_rerun_role(command)
        elif ctype == "scenario_fork":
            result = await _handle_scenario_fork(command)
        elif ctype == "compare_options":
            result = await _handle_compare(command)
        elif ctype == "pin_evidence":
            result = await _handle_pin_evidence(command)
        elif ctype == "pause_phase":
            result = _handle_phase(command, paused=True)
        elif ctype == "resume_phase":
            result = _handle_phase(command, paused=False)
        elif ctype == "export_memo":
            current = C.load_command_state(room)
            result = await _handle_export(command, pinned=current.get("pinned_evidence", []))
        else:  # pragma: no cover — guarded by validate_command
            result = _rejection("invalid_command", f"Unsupported command type '{ctype}'.")
    except Exception as exc:
        result = _failure(f"Unhandled command error: {redact_secrets(exc)}")

    return _finalize(command, result, room)


def _finalize(command: dict[str, Any], result: dict[str, Any], room: str) -> dict[str, Any]:
    stream_id = C.record_command_event(command, result, room)
    state = C.load_command_state(room)
    next_state = C.apply_result_to_state(state, command, result, stream_id)
    C.save_command_state(next_state, room)
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "message": result.get("message"),
        "result": result.get("result", {}),
        "command": {
            "id": command.get("id"),
            "type": command.get("type"),
            "agent": command.get("agent"),
        },
        "stream_id": stream_id,
        "state": next_state,
        "room": room,
    }
