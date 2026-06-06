"""
Read-only data API for the dashboard (separate from the AG-UI agent at "/").
Serves the seeded Northwind data straight from Redis so the executive dashboard
and department views can render without running a debate.
"""

from fastapi import APIRouter, Response

from src.health import observability_health, sponsor_health
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
