"""
Deterministic checks for role-specific prompt-version promotion metadata.

No OpenAI or Redis calls: these tests lock the active prompt provenance used by
the W&B promotion gate UI and self-improvement loop.
"""

from __future__ import annotations

from src.openai_council import COUNCIL_PROMPT_ROLES, ROLE_PROMOTION_PROFILES, prompt_versions_payload
from src.structured_models import PromptVersion


def _stale_seed_context() -> dict:
    return {
        "financials": {
            "prompt_versions": [
                {
                    "agent": role,
                    "current": f"{role}.v0-stale",
                    "candidate": "shared.v0-stale-candidate",
                    "candidate_prompt_hash": "stalehash",
                    "promotion_gate": "shared stale gate",
                    "reliability_dimensions": ["shared_stale_dimension"],
                    "gate_metric": "shared_stale_dimension",
                    "replay_set": "atlas-stale-replay",
                }
                for role in COUNCIL_PROMPT_ROLES
            ]
        }
    }


def _payload() -> list[dict]:
    return prompt_versions_payload(_stale_seed_context())


def test_each_role_has_active_prompt_version_metadata() -> None:
    payload = _payload()
    roles = [item["agent"] for item in payload]

    assert tuple(roles) == COUNCIL_PROMPT_ROLES

    for item in payload:
        role = item["agent"]
        profile = ROLE_PROMOTION_PROFILES[role]

        assert item["role"] == role
        assert item["current"] == item["version"]
        assert item["current"].startswith(f"{role}.")
        assert item["prompt_hash"] == item["active_prompt_hash"]
        assert len(item["prompt_hash"]) == 12
        assert item["candidate"] == profile["candidate"]
        assert item["candidate"].startswith(f"{role}.")
        assert len(item["candidate_prompt_hash"]) == 12
        assert item["promotion_gate"] == profile["promotion_gate"]
        assert item["reliability_dimensions"] == profile["reliability_dimensions"]
        assert item["gate_metric"] == profile["gate_metric"]
        assert item["gate_metric"] in item["reliability_dimensions"]
        assert item["replay_set"] == profile["replay_set"]


def test_active_prompt_hashes_are_unique_per_role() -> None:
    payload = _payload()
    hashes = [item["active_prompt_hash"] for item in payload]

    assert len(set(hashes)) == len(COUNCIL_PROMPT_ROLES)


def test_promotion_gates_and_candidate_fields_are_not_shared() -> None:
    payload = _payload()

    assert len({item["promotion_gate"] for item in payload}) == len(COUNCIL_PROMPT_ROLES)
    assert len({item["candidate"] for item in payload}) == len(COUNCIL_PROMPT_ROLES)
    assert len({item["candidate_prompt_hash"] for item in payload}) == len(COUNCIL_PROMPT_ROLES)
    assert len({tuple(item["reliability_dimensions"]) for item in payload}) == len(COUNCIL_PROMPT_ROLES)
    assert "shared stale gate" not in {item["promotion_gate"] for item in payload}
    assert "shared.v0-stale-candidate" not in {item["candidate"] for item in payload}


def test_prompt_version_schema_accepts_role_specific_fields() -> None:
    for item in _payload():
        version = PromptVersion(**item)

        assert version.agent == version.role
        assert version.active_prompt_hash == version.prompt_hash
        assert version.candidate_prompt_hash
        assert version.reliability_dimensions
        assert version.gate_metric in version.reliability_dimensions


ALL_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"  ok {test.__name__}")
    print(f"\n{len(ALL_TESTS)}/{len(ALL_TESTS)} prompt-version contract checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
