"""
Read-only data API for the dashboard (separate from the AG-UI agent at "/").
Serves the seeded Acme Corp data straight from Redis so the executive dashboard
and department views can render without running a debate.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from openai import AsyncOpenAI

from src.env import redact_secrets
from src.health import observability_health, require_live_ready, sponsor_health
from src import redis_layer as R

router = APIRouter(prefix="/api")

COMPANY_KEY = f"{R.NS}:company:northwind"


@router.get("/company")
def company() -> dict:
    return R.get_json(COMPANY_KEY) or {}


@router.get("/vendors")
def vendors() -> list:
    return R.search_vendors("*", 50)


@router.get("/decisions")
def decisions(limit: int = 25) -> list:
    return R.read_events("decisions", count=limit)


@router.get("/roster")
def roster() -> list:
    from src.agent import ROSTER

    return [{"id": key, **meta} for key, meta in ROSTER.items()]


@router.get("/health")
def health(response: Response) -> dict:
    payload = sponsor_health()
    if not payload["ready"]:
        response.status_code = 503
    return payload


@router.get("/observability")
def observability(response: Response, limit: int = 15) -> dict:
    health_payload = sponsor_health()
    observability_payload = observability_health()
    ready = bool(health_payload["ready"] and observability_payload["ready"])
    if not ready:
        response.status_code = 503
    recent_decisions = R.read_events("decisions", count=limit) if health_payload["ready"] else []
    return {
        "ready": ready,
        "mode": "strict-live",
        "sponsor_health": health_payload["sponsors"],
        "blockers": health_payload["blockers"],
        "observability": observability_payload,
        "weave": observability_payload["weave"],
        "redis_activity": [
            {
                "label": "Decision stream",
                "detail": f"{len(recent_decisions)} recent events",
                "kind": "stream",
            },
            {
                "label": "System of record",
                "detail": "RedisJSON company and vendor records",
                "kind": "json",
            },
            {
                "label": "Vector memory",
                "detail": "RediSearch policy and precedent RAG index",
                "kind": "search",
            },
        ],
        "events": [
            {
                "id": "health",
                "sponsor": "Atlas",
                "label": "Strict-live preflight",
                "detail": "Ready" if health_payload["ready"] else "Blocked",
                "tone": "positive" if health_payload["ready"] else "risk",
            }
        ],
    }


@router.post("/realtime/session")
async def realtime_session(response: Response) -> dict:
    """Mint an ephemeral OpenAI Realtime 2 session for browser voice council chat."""
    try:
        require_live_ready()
    except Exception as exc:
        response.status_code = 503
        raise HTTPException(status_code=503, detail=redact_secrets(exc)) from exc

    model = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2")
    voice = os.getenv("OPENAI_REALTIME_VOICE", "marin")
    ttl = int(os.getenv("OPENAI_REALTIME_SECRET_TTL", "300"))
    reasoning_effort = os.getenv("OPENAI_REALTIME_REASONING_EFFORT", "xhigh")
    client = AsyncOpenAI()

    try:
        secret = await client.realtime.client_secrets.create(
            expires_after={"anchor": "created_at", "seconds": ttl},
            session={
                "type": "realtime",
                "model": model,
                "instructions": (
                    "You are the live voice interface for Atlas Finance OS. Converse as the AI "
                    "Council Room facilitator. Keep answers concise, route finance questions to "
                    "the named council agents, and never fabricate sponsor health or traces."
                ),
                "output_modalities": ["audio", "text"],
                "audio": {
                    "input": {
                        "turn_detection": {
                            "type": "semantic_vad",
                            "eagerness": "medium",
                            "interrupt_response": True,
                        }
                    },
                    "output": {"voice": voice},
                },
                "tracing": {"workflow_name": "atlas_realtime_council"},
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=redact_secrets(exc)) from exc

    return {
        "ready": True,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "voice": voice,
        "expires_at": _get_attr(secret, "expires_at"),
        "client_secret": _get_attr(secret, "value"),
    }


def _get_attr(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)
