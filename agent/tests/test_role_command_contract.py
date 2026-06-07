"""
Deterministic checks for role-targeted operator commands.

No OpenAI or Redis calls: these tests lock the contract that clarify, challenge,
defend, and rerun commands use each role's mandate instead of generic finance
instructions.
"""

from __future__ import annotations

from src import agui_commands as C
from src.council_commands import (
    CommandReply,
    _focus_patch,
    _result_payload,
    build_role_command_system,
    role_command_metadata,
)


TARGETED_COMMANDS = ("clarify", "route_question", "challenge_claim", "defend_position", "rerun_role")


def _persona(label: str = "Treasury") -> dict:
    return {"label": label, "mandate": "role-specific finance mandate"}


def _reply() -> CommandReply:
    return CommandReply(
        headline="Role-specific command answered",
        response="The answer cites the role mandate and named evidence.",
        key_points=["role evidence", "mandate boundary"],
        revised_stance="unchanged",
    )


def test_targeted_command_types_validate_role_aliases() -> None:
    for command_type in TARGETED_COMMANDS:
        assert C.COMMAND_TYPES[command_type]["targets_agent"] is True
        command = C.normalize_command({"type": command_type, "agent": "FP&A", "payload": {}})
        ok, error = C.validate_command(command)

        assert ok is True
        assert error is None
        assert command["agent"] == "fpna"

    bad = C.normalize_command({"type": "rerun_role", "agent": "generic analyst", "payload": {}})
    ok, error = C.validate_command(bad)
    assert ok is False
    assert "Unknown council role" in str(error)


def test_role_command_profiles_have_unique_lenses_and_action_instructions() -> None:
    lenses = [C.role_command_profile(role)["command_lens"] for role in C.KNOWN_AGENTS]
    rerun_instructions = [C.role_command_instruction(role, "rerun_role") for role in C.KNOWN_AGENTS]

    assert len(set(lenses)) == len(C.KNOWN_AGENTS)
    assert len(set(rerun_instructions)) == len(C.KNOWN_AGENTS)
    assert "cash runway" in C.role_command_profile("treasury")["command_lens"]
    assert "forecastability" in C.role_command_profile("fpna")["command_lens"]
    assert "policy violations" in C.role_command_profile("risk")["command_lens"]
    assert "supplier leverage" in C.role_command_profile("procurement")["command_lens"]


def test_role_command_system_prompt_changes_by_target_role() -> None:
    financials = {"name": "Northwind Robotics", "runway_months": 10.2}
    treasury = build_role_command_system("treasury", "challenge_claim", _persona("Treasury"), financials)
    fpna = build_role_command_system("fpna", "challenge_claim", _persona("FP&A"), financials)
    risk = build_role_command_system("risk", "challenge_claim", _persona("Risk & Audit"), financials)
    procurement = build_role_command_system("procurement", "challenge_claim", _persona("Procurement"), financials)

    assert treasury != fpna != risk != procurement
    assert "late-cash" in treasury and "cash timing" in treasury
    assert "forecast assumptions" in fpna and "CAC/payback" in fpna
    assert "policy compliance" in risk and "source provenance" in risk
    assert "auto-renewal" in procurement and "supplier leverage" in procurement


def test_streamed_focus_payload_carries_role_mandate() -> None:
    reply = _reply()
    focus = _focus_patch(
        command_type="rerun_role",
        agent_id="procurement",
        persona=_persona("Procurement"),
        mode="rerun",
        prompt="Rerun vendor terms.",
        reply=reply,
    )
    result = _result_payload(
        command_type="rerun_role",
        agent_id="procurement",
        persona=_persona("Procurement"),
        kind="rerun",
        prompt_key="question",
        prompt="Rerun vendor terms.",
        reply=reply,
    )

    assert focus["mode"] == "rerun"
    assert "vendor and commercial negotiation" in focus["role_lens"]
    assert "Rerun Procurement" in focus["role_instruction"]
    assert "contract metadata" in focus["evidence_priorities"]
    assert result["role_lens"] == focus["role_lens"]
    assert result["role_instruction"] == focus["role_instruction"]


def test_agui_active_command_promotes_role_metadata() -> None:
    result_payload = {
        "agent": "treasury",
        "label": "Treasury",
        **role_command_metadata("treasury", "clarify"),
    }
    state = C.apply_result_to_state(
        C.default_command_state(),
        {"id": "cmd-test", "type": "clarify", "agent": "treasury", "payload": {"question": "cash late?"}},
        {
            "status": "executed",
            "reason": None,
            "message": "Clarification delivered by Treasury using its liquidity lens.",
            "result": result_payload,
            "state_patch": {
                "agent_focus": {
                    "agent": "treasury",
                    "label": "Treasury",
                    "mode": "clarify",
                    "question": "cash late?",
                    **role_command_metadata("treasury", "clarify"),
                }
            },
        },
        "1-0",
    )

    assert "liquidity mechanics" in state["active_command"]["role_lens"]
    assert "cash forecast" in state["active_command"]["evidence_priorities"]
    assert "liquidity mechanics" in state["agent_focus"]["role_lens"]
    assert state["command_audit_log"][-1]["agent"] == "treasury"


ALL_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"  ok {test.__name__}")
    print(f"\n{len(ALL_TESTS)}/{len(ALL_TESTS)} role command contract checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
