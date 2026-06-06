"""
Read-only data API for the dashboard (separate from the AG-UI agent at "/").
Serves the seeded Northwind data straight from Redis so the executive dashboard
and department views can render without running a debate.
"""

from fastapi import APIRouter

from src import redis_layer as R
from src.agent import ROSTER

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
    return [{"id": key, **meta} for key, meta in ROSTER.items()]
