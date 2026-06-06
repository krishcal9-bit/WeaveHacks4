"""
OpenAI Realtime 2 control surface for Atlas.

The browser voice path (Decision Room → WebRTC) is a first-class product surface,
not a toy. This module is the single place that:

  • Describes the **session policy** the voice agent runs under (model, voice,
    reasoning effort, turn detection, output modalities, tracing workflow, scope).
  • Reports **voice-model health** without minting a secret (config readiness,
    API-key presence, endpoint, TTL) so the UI can show whether voice is armed.
  • Mints a **short-lived ephemeral client secret** and reports its TTL
    (issued_at / expires_at / seconds_remaining) so the browser can manage
    re-minting before expiry.

Strict live-only: the secret is minted live against OpenAI; errors are passed
through ``redact_secrets`` and we never return the standing ``OPENAI_API_KEY`` —
only the ephemeral, short-TTL client secret the WebRTC handshake requires.
"""

from __future__ import annotations

import os
import time
from typing import Any

from openai import AsyncOpenAI

from src.env import is_configured, provider_api_key_name, redact_secrets

# Reasonable defaults; the strict-live preflight requires the real values in .env.
DEFAULT_MODEL = "gpt-realtime-2"
DEFAULT_VOICE = "marin"
DEFAULT_TTL = 300
DEFAULT_REASONING = "xhigh"

INSTRUCTIONS = (
    "You are the live voice interface for Atlas, an AI finance department. You facilitate the AI "
    "Council Room: Treasury, FP&A, Risk & Audit, and Procurement debate a decision and the CFO rules. "
    "You can speak to vendor renewals, hiring plans, capital allocation, security blockers, pricing "
    "changes, and financing scenarios. Keep answers concise and quantified, route detailed finance "
    "questions to the named council agents, and never fabricate sponsor health, traces, runway, or "
    "Redis data — defer to the live council run for numbers."
)


def realtime_config() -> dict[str, Any]:
    """Resolved Realtime configuration from the environment."""
    return {
        "model": os.getenv("OPENAI_REALTIME_MODEL", DEFAULT_MODEL),
        "voice": os.getenv("OPENAI_REALTIME_VOICE", DEFAULT_VOICE),
        "reasoning_effort": os.getenv("OPENAI_REALTIME_REASONING_EFFORT", DEFAULT_REASONING),
        "ttl_seconds": _ttl_seconds(),
        "turn_detection": {
            "type": "semantic_vad",
            "eagerness": os.getenv("OPENAI_REALTIME_EAGERNESS", "medium"),
            "interrupt_response": True,
        },
        "output_modalities": ["audio", "text"],
        "endpoint": "v1/realtime",
        "transport": "webrtc",
        "workflow_name": "atlas_realtime_council",
    }


def _ttl_seconds() -> int:
    try:
        return int(os.getenv("OPENAI_REALTIME_SECRET_TTL", str(DEFAULT_TTL)))
    except ValueError:
        return DEFAULT_TTL


def session_policy() -> dict[str, Any]:
    """The policy metadata the minted session enforces (safe to expose)."""
    config = realtime_config()
    return {
        "model": config["model"],
        "voice": config["voice"],
        "reasoning_effort": config["reasoning_effort"],
        "output_modalities": config["output_modalities"],
        "turn_detection": config["turn_detection"],
        "secret_ttl_seconds": config["ttl_seconds"],
        "secret_anchor": "created_at",
        "transport": config["transport"],
        "endpoint": config["endpoint"],
        "tracing": {"workflow_name": config["workflow_name"]},
        "scope": "atlas-finance-council-voice",
        "instructions_summary": "Facilitates the AI Council Room; defers numbers to live runs.",
    }


def realtime_health() -> dict[str, Any]:
    """Voice-model readiness without minting a secret (for /api/realtime/health)."""
    config = realtime_config()
    api_key = provider_api_key_name()
    api_key_ready = is_configured(api_key)
    checks = [
        {"label": "Realtime model", "ready": config["model"] == DEFAULT_MODEL, "detail": config["model"]},
        {"label": "Realtime reasoning", "ready": config["reasoning_effort"] == "xhigh", "detail": config["reasoning_effort"]},
        {"label": "Realtime voice", "ready": bool(config["voice"]), "detail": config["voice"] or "missing"},
        {"label": api_key, "ready": api_key_ready, "detail": "Configured" if api_key_ready else "Missing"},
        {"label": "Secret TTL", "ready": 0 < config["ttl_seconds"] <= 600, "detail": f"{config['ttl_seconds']}s ephemeral"},
    ]
    ready = all(check["ready"] for check in checks)
    return {
        "id": "openai_realtime",
        "label": "OpenAI Realtime 2",
        "ready": ready,
        "detail": (
            f"{config['model']} · {config['voice']} · {config['reasoning_effort']} reasoning · {config['ttl_seconds']}s secret"
            if ready
            else "Realtime voice configuration incomplete"
        ),
        "model": config["model"],
        "voice": config["voice"],
        "reasoning_effort": config["reasoning_effort"],
        "endpoint": config["endpoint"],
        "transport": config["transport"],
        "ttl_seconds": config["ttl_seconds"],
        "api_key_configured": api_key_ready,
        "capabilities": ["webrtc_session_secret", "semantic_vad", "audio+text", "ephemeral_secret_ttl"],
        "policy": session_policy(),
        "checks": checks,
    }


def _get_attr(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


async def mint_session() -> dict[str, Any]:
    """Mint a live, short-TTL OpenAI Realtime client secret with TTL reporting.

    Raises ``RuntimeError`` on a live OpenAI failure (the caller maps it to a 502
    with a redacted detail). Never returns the standing API key.
    """
    config = realtime_config()
    issued_at = int(time.time())
    client = AsyncOpenAI()

    try:
        secret = await client.realtime.client_secrets.create(
            expires_after={"anchor": "created_at", "seconds": config["ttl_seconds"]},
            session={
                "type": "realtime",
                "model": config["model"],
                "instructions": INSTRUCTIONS,
                "output_modalities": config["output_modalities"],
                "audio": {
                    "input": {"turn_detection": config["turn_detection"]},
                    "output": {"voice": config["voice"]},
                },
                "tracing": {"workflow_name": config["workflow_name"]},
            },
        )
    except Exception as exc:  # live failure — surface a redacted reason
        raise RuntimeError(redact_secrets(exc)) from exc

    expires_at = _get_attr(secret, "expires_at")
    client_secret = _get_attr(secret, "value")
    seconds_remaining: int | None = None
    if isinstance(expires_at, (int, float)):
        seconds_remaining = max(0, int(expires_at) - issued_at)

    return {
        "ready": True,
        "model": config["model"],
        "voice": config["voice"],
        "reasoning_effort": config["reasoning_effort"],
        "endpoint": config["endpoint"],
        "transport": config["transport"],
        "issued_at": issued_at,
        "expires_at": expires_at,
        "ttl_seconds": config["ttl_seconds"],
        "seconds_remaining": seconds_remaining,
        "policy": session_policy(),
        "health": {key: value for key, value in realtime_health().items() if key not in {"policy", "checks"}},
        "client_secret": client_secret,
    }
