"""
Atlas AG-UI command protocol — the contract that turns CopilotKit/AG-UI from a
one-way streaming bridge into a command-and-control channel for the finance
council.

This module owns the *protocol*, not the execution (that lives in
``council_commands.py``):

  • the canonical set of command types an operator may issue mid-debate,
  • the eight new ``DebateState`` keys the commands surface through, kept in one
    place so ``agent.py`` (STREAM_STATE_KEYS) and ``frontend/src/lib/types.ts``
    can be mirrored without drift,
  • a small, exception-safe Redis-backed store for the live command state
    (``atlas:command_state:<room>``) plus a ``merge_command_state`` helper the
    LangGraph nodes fold into every AG-UI emit so commands stream live,
  • validation/normalization primitives and an append-only command event log on
    Redis Streams (``atlas:stream:commands``),
  • a version-tolerant ``copilotkit_emit_state`` shim mirroring the fallback
    approach already used in ``agent.py``.

Every Redis touch here is best-effort: a command-state read or write must never
take down a live debate, so failures degrade to the empty default instead of
raising.
"""

from __future__ import annotations

import inspect
import time
import uuid
from typing import Any

from src import redis_layer as R

# --------------------------------------------------------------------------- #
# Rooms — this demo runs a single council room over the seeded company, but we
# scope command state by room so a future multi-company build stays compatible.
# --------------------------------------------------------------------------- #
DEFAULT_ROOM = "northwind"


def command_state_key(room: str = DEFAULT_ROOM) -> str:
    return f"{R.NS}:command_state:{room or DEFAULT_ROOM}"


# --------------------------------------------------------------------------- #
# The eight DebateState keys the command layer adds. Single source of truth:
#   • agent.py extends STREAM_STATE_KEYS with COMMAND_STATE_KEYS
#   • frontend/src/lib/types.ts mirrors the same names
# --------------------------------------------------------------------------- #
COMMAND_STATE_KEYS: tuple[str, ...] = (
    "command_queue",
    "active_command",
    "pinned_evidence",
    "requested_scenario",
    "agent_focus",
    "phase_controls",
    "export_status",
    "command_audit_log",
)

# How many audit entries / pins to retain in the streamed state (keep it light).
_AUDIT_KEEP = 24
_PIN_KEEP = 24
_QUEUE_KEEP = 24

# --------------------------------------------------------------------------- #
# Command vocabulary. Each entry documents the operator workflow and the
# council role(s) it may target. ``targets_agent`` drives validation.
# --------------------------------------------------------------------------- #
COMMAND_TYPES: dict[str, dict[str, Any]] = {
    "clarify": {
        "label": "Ask an agent to clarify",
        "targets_agent": True,
        "needs_context": True,
        "summary": "Request a grounded clarification from a specific council role.",
    },
    "route_question": {
        "label": "Route a question to a role",
        "targets_agent": True,
        "needs_context": True,
        "summary": "Direct an operator question to a specific council role.",
    },
    "challenge_claim": {
        "label": "Challenge a claim",
        "targets_agent": True,
        "needs_context": True,
        "summary": "Force a role to defend or revise a specific claim with figures.",
    },
    "scenario_fork": {
        "label": "Request a scenario fork",
        "targets_agent": False,
        "needs_context": True,
        "summary": "Project runway under a what-if cost/revenue scenario.",
    },
    "compare_options": {
        "label": "Compare options",
        "targets_agent": False,
        "needs_context": True,
        "summary": "Compare two or more scenarios side by side on runway impact.",
    },
    "pin_evidence": {
        "label": "Pin evidence",
        "targets_agent": False,
        "needs_context": True,
        "summary": "Pin a policy, vendor, or financial fact to the board record.",
    },
    "pause_phase": {
        "label": "Pause the council",
        "targets_agent": False,
        "needs_context": False,
        "summary": "Cooperatively hold the council at the next node boundary.",
    },
    "resume_phase": {
        "label": "Resume the council",
        "targets_agent": False,
        "needs_context": False,
        "summary": "Release a cooperative pause and let the council continue.",
    },
    "export_memo": {
        "label": "Export board memo",
        "targets_agent": False,
        "needs_context": False,
        "summary": "Assemble the board-ready memo from the completed decision.",
    },
}

COMMAND_STATUSES = ("queued", "accepted", "executed", "rejected", "failed")

# Council roles a command may target (mirrors agent.ROSTER ids).
KNOWN_AGENTS = ("cfo", "treasury", "fpna", "risk", "procurement", "reliability")

_AGENT_ALIASES = {
    "office_of_the_cfo": "cfo",
    "chief_financial_officer": "cfo",
    "chair": "cfo",
    "financial_planning_and_analysis": "fpna",
    "fpa": "fpna",
    "fp&a": "fpna",
    "risk_audit": "risk",
    "risk_and_audit": "risk",
    "risk_&_audit": "risk",
    "reliability_auditor": "reliability",
    "reliability_and_learning": "reliability",
}


def normalize_agent_id(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("&", "and").replace("-", "_").replace(" ", "_")
    return _AGENT_ALIASES.get(normalized, normalized)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def now_label() -> str:
    return time.strftime("%H:%M:%S")


def new_command_id() -> str:
    return f"cmd-{uuid.uuid4().hex[:12]}"


def default_command_state() -> dict[str, Any]:
    """A fresh, empty command-state document (all eight streamed keys present)."""
    return {
        "command_queue": [],
        "active_command": {},
        "pinned_evidence": [],
        "requested_scenario": {},
        "agent_focus": {},
        "phase_controls": {"paused": False, "phase": None, "updated_at": None, "reason": None},
        "export_status": {"ready": False},
        "command_audit_log": [],
    }


def _coerce_state(raw: Any) -> dict[str, Any]:
    """Merge a loaded doc onto the default so every streamed key always exists."""
    base = default_command_state()
    if isinstance(raw, dict):
        for key in COMMAND_STATE_KEYS:
            if key in raw and raw[key] is not None:
                base[key] = raw[key]
    return base


# --------------------------------------------------------------------------- #
# Redis-backed command-state store (best-effort; never raises into the graph)
# --------------------------------------------------------------------------- #
def load_command_state(room: str = DEFAULT_ROOM) -> dict[str, Any]:
    try:
        return _coerce_state(R.get_json(command_state_key(room)))
    except Exception as exc:  # a Redis hiccup must not break a live debate
        print(f"[agui_commands] load_command_state degraded: {exc}")
        return default_command_state()


def save_command_state(state: dict[str, Any], room: str = DEFAULT_ROOM) -> None:
    try:
        R.set_json(command_state_key(room), _coerce_state(state))
    except Exception as exc:
        print(f"[agui_commands] save_command_state warning: {exc}")


def reset_command_state(room: str = DEFAULT_ROOM) -> dict[str, Any]:
    """Clear command state for a fresh debate run."""
    fresh = default_command_state()
    save_command_state(fresh, room)
    return fresh


def merge_command_state(patch: dict[str, Any], room: str = DEFAULT_ROOM) -> dict[str, Any]:
    """Fold the live command-state from Redis into a graph patch/return dict.

    The LangGraph nodes call this on every ``_emit_patch`` and node return so
    that commands an operator issues mid-debate stream straight back through the
    same AG-UI ``useCoAgent`` channel without bypassing the agent.
    """
    state = load_command_state(room)
    merged = dict(patch)
    for key in COMMAND_STATE_KEYS:
        merged[key] = state.get(key)
    return merged


def is_paused(room: str = DEFAULT_ROOM) -> bool:
    return bool(load_command_state(room).get("phase_controls", {}).get("paused"))


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def normalize_command(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce an inbound command into a canonical shape with an id + timestamp."""
    command = dict(raw or {})
    command["type"] = str(command.get("type") or "").strip().lower()
    command["id"] = command.get("id") or new_command_id()
    command.setdefault("payload", {})
    if not isinstance(command["payload"], dict):
        command["payload"] = {"value": command["payload"]}
    if command.get("agent"):
        command["agent"] = normalize_agent_id(command.get("agent"))
    command["created_at"] = command.get("created_at") or now_label()
    return command


def validate_command(command: dict[str, Any]) -> tuple[bool, str | None]:
    """Shape validation. Deep per-command checks live in the dispatcher."""
    ctype = command.get("type")
    if not ctype:
        return False, "Command is missing a 'type'."
    spec = COMMAND_TYPES.get(ctype)
    if not spec:
        return False, f"Unknown command type '{ctype}'. Known: {', '.join(sorted(COMMAND_TYPES))}."
    if spec["targets_agent"]:
        agent = normalize_agent_id(command.get("agent"))
        if not agent:
            return False, f"Command '{ctype}' requires a target 'agent'."
        if agent not in KNOWN_AGENTS:
            return False, f"Unknown council role '{agent}'. Known roles: {', '.join(KNOWN_AGENTS)}."
    return True, None


# --------------------------------------------------------------------------- #
# Command event log — append-only Redis Stream + dashboard pub/sub
# --------------------------------------------------------------------------- #
def record_command_event(command: dict[str, Any], result: dict[str, Any], room: str = DEFAULT_ROOM) -> str | None:
    """Append a command outcome to ``atlas:stream:commands`` (best-effort)."""
    try:
        stream_id = R.append_event(
            "commands",
            {
                "room": room,
                "command_id": command.get("id"),
                "type": command.get("type"),
                "agent": command.get("agent"),
                "status": result.get("status"),
                "reason": result.get("reason"),
                "message": result.get("message"),
                "payload": command.get("payload", {}),
                "at": now_label(),
                "source": command.get("source") or "operator",
            },
        )
        R.publish(
            "dashboard",
            {
                "event": "command",
                "type": command.get("type"),
                "status": result.get("status"),
                "agent": command.get("agent"),
            },
        )
        return stream_id
    except Exception as exc:
        print(f"[agui_commands] record_command_event warning: {exc}")
        return None


def audit_entry(command: dict[str, Any], result: dict[str, Any], stream_id: str | None) -> dict[str, Any]:
    return {
        "id": command.get("id"),
        "type": command.get("type"),
        "agent": command.get("agent"),
        "status": result.get("status"),
        "reason": result.get("reason"),
        "summary": result.get("message"),
        "at": now_label(),
        "stream_id": stream_id,
        "source": command.get("source") or "operator",
    }


def apply_result_to_state(
    state: dict[str, Any],
    command: dict[str, Any],
    result: dict[str, Any],
    stream_id: str | None,
) -> dict[str, Any]:
    """Fold a dispatched command's outcome into a command-state document.

    Mutations are intentionally additive and bounded so the streamed state stays
    small. Specific state slices (pins, scenario, focus, phase, export) are set
    by the dispatcher via ``result['state_patch']``; this just records the
    active command + audit trail and applies that patch.
    """
    next_state = _coerce_state(state)

    active = {
        "id": command.get("id"),
        "type": command.get("type"),
        "agent": command.get("agent"),
        "status": result.get("status"),
        "reason": result.get("reason"),
        "message": result.get("message"),
        "payload": command.get("payload", {}),
        "result": result.get("result", {}),
        "at": now_label(),
        "stream_id": stream_id,
    }
    next_state["active_command"] = active

    next_state["command_audit_log"] = [
        *next_state.get("command_audit_log", []),
        audit_entry(command, result, stream_id),
    ][-_AUDIT_KEEP:]

    patch = result.get("state_patch") or {}
    for key, value in patch.items():
        if key not in COMMAND_STATE_KEYS:
            continue
        if key == "pinned_evidence":
            next_state["pinned_evidence"] = [*next_state.get("pinned_evidence", []), *value][-_PIN_KEEP:]
        elif key == "command_queue":
            next_state["command_queue"] = list(value)[-_QUEUE_KEEP:]
        else:
            next_state[key] = value

    return next_state


# --------------------------------------------------------------------------- #
# CopilotKit state-emit compatibility shim (mirrors agent._emit fallbacks)
# --------------------------------------------------------------------------- #
try:
    from copilotkit.langgraph import copilotkit_emit_state as _copilotkit_emit_state
except Exception:
    try:
        from copilotkit import copilotkit_emit_state as _copilotkit_emit_state
    except Exception:  # CopilotKit Python package versions expose different helpers.
        _copilotkit_emit_state = None


def emit_helper_available() -> bool:
    return _copilotkit_emit_state is not None


async def emit_state_compat(config: Any, state: dict[str, Any]) -> None:
    """Version-tolerant ``copilotkit_emit_state`` call.

    Tries the ``(config, state)`` signature first, then ``(state)``, then gives
    up silently — identical defensive posture to ``agent._emit`` so callers can
    share a single contract regardless of the installed CopilotKit helper shape.
    """
    if _copilotkit_emit_state is None:
        return
    try:
        result = _copilotkit_emit_state(config, state)
    except TypeError as exc:
        try:
            result = _copilotkit_emit_state(state)
        except Exception:
            print(f"[agui_commands] state emit skipped: {exc}")
            return
    except Exception as exc:
        print(f"[agui_commands] state emit skipped: {exc}")
        return
    try:
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        print(f"[agui_commands] state emit await skipped: {exc}")
