"""
Atlas — replay sets for the W&B Weave evaluation operating system.

A **replay set** is a curated collection of past board decisions (seeded history
plus live council runs) that a candidate prompt/model version is replayed against
so its reliability can be compared to the incumbent on identical inputs.

Replay sets are:

- built from the ``atlas:stream:decisions`` log (historical + live),
- persisted as non-secret metadata under ``atlas:evaluation:replay:*`` in Redis,
- published as a **live** ``weave.Dataset`` so the exact eval inputs are versioned
  and queryable in the W&B UI (the Dataset ref/URL is captured, redacted).

No fabricated cases: every ReplayCase is derived from a real Redis decision event
and grounded in the current company system-of-record snapshot.
"""

from __future__ import annotations

import re
import time
from typing import Any

import weave
from pydantic import BaseModel, Field

from src import redis_layer as R
from src.env import redact_secrets
from src.weave_eval import EVAL_NS, _new_id, _now, publish_to_weave, weave_links

COMPANY_KEY = f"{R.NS}:company:northwind"
REPLAY_PREFIX = f"{EVAL_NS}:replay:"
REPLAY_INDEX = f"{EVAL_NS}:replay_index"  # Redis SET of replay-set slugs

DEFAULT_REPLAY_SET = "Board Decision Replay Set"
DEFAULT_SLUG = "board-decision-replay-set"

_CONTEXT_FIELDS = (
    "name", "stage", "cash_on_hand", "monthly_revenue", "monthly_net_burn",
    "runway_months", "mrr", "arr", "mrr_growth_mom", "gross_margin", "logo_churn_mom",
    "ndr", "cac", "ltv", "board_constraints", "audit_findings", "decision_outcomes",
    "pipeline_by_stage", "customer_cohorts", "security_incidents", "prompt_versions",
)


class ReplayCase(BaseModel):
    """One grounded decision used as a replay/eval input."""

    id: str
    source: str = Field(description="history | live | seed")
    decision: str
    expected_decision: str | None = None
    expected_confidence: int | None = None
    baseline_reliability: int | None = None
    tags: list[str] = Field(default_factory=list)
    origin_event_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


# --------------------------------------------------------------------------- #
# Derivation helpers
# --------------------------------------------------------------------------- #
def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "replay-set"


def _company_context_snapshot() -> dict[str, Any]:
    co = R.get_json(COMPANY_KEY) or {}
    return {"financials": {k: co.get(k) for k in _CONTEXT_FIELDS if k in co}}


def _infer_expected(text: str) -> str | None:
    lowered = (text or "").lower()
    if any(word in lowered for word in ("approved", "renegotiated", "migrated", "moved")):
        return "APPROVE"
    if any(word in lowered for word in ("declined", "reversed", "rejected")):
        return "REJECT"
    if any(word in lowered for word in ("paused", "deferred", "defer")):
        return "DEFER"
    if "conditional" in lowered:
        return "CONDITIONAL"
    return None


def _tags_for(text: str) -> list[str]:
    lowered = (text or "").lower()
    tags: list[str] = []
    rules = {
        "vendor": ("vendor", "renew", "contract", "datadog", "snowflake", "aws", "salesforce"),
        "hiring": ("hire", "headcount", "engineer", "salary", "comp"),
        "security": ("security", "soc 2", "soc2", "incident", "control", "audit"),
        "marketing": ("brand", "campaign", "marketing", "cac"),
        "infrastructure": ("aws", "cloud", "infra", "compute"),
    }
    for tag, needles in rules.items():
        if any(needle in lowered for needle in needles):
            tags.append(tag)
    return tags or ["general"]


def _avg_reliability(scores: Any) -> int | None:
    if not isinstance(scores, list) or not scores:
        return None
    values = [s.get("reliability", 0) for s in scores if isinstance(s, dict)]
    return round(sum(values) / len(values)) if values else None


def build_replay_cases(
    limit: int = 25,
    *,
    include_live: bool = True,
    include_history: bool = True,
) -> list[ReplayCase]:
    """Derive replay cases from the decision stream, grounded in the current snapshot."""
    try:
        events = R.read_events("decisions", count=limit)
    except Exception:
        events = []
    snapshot = _company_context_snapshot()
    cases: list[ReplayCase] = []
    for event in events:
        source = event.get("source") or "history"
        is_history = source == "history"
        if is_history and not include_history:
            continue
        if not is_history and not include_live:
            continue
        decision_text = event.get("title") or event.get("summary") or ""
        if not decision_text:
            continue
        cases.append(
            ReplayCase(
                id=_new_id("case"),
                source="history" if is_history else "live",
                decision=decision_text,
                expected_decision=event.get("decision") or _infer_expected(decision_text),
                expected_confidence=event.get("confidence"),
                baseline_reliability=_avg_reliability(event.get("reliability_scores")),
                tags=_tags_for(decision_text),
                origin_event_id=event.get("_id"),
                context=snapshot,
                created_at=_now(),
            )
        )
    return cases


def dataset_row(case: ReplayCase) -> dict[str, Any]:
    """Flatten a case into a Weave Dataset row (the exact eval input)."""
    return {
        "case_id": case.id,
        "decision": case.decision,
        "expected_decision": case.expected_decision,
        "expected_confidence": case.expected_confidence,
        "tags": case.tags,
        "context": case.context,
    }


def _publish_dataset(name: str, cases: list[ReplayCase]) -> dict[str, Any]:
    if not cases:
        return {"published": False, "error": "Replay set has no cases to publish."}
    rows = [dataset_row(case) for case in cases]
    try:
        dataset = weave.Dataset(name=re.sub(r"[^a-zA-Z0-9_]+", "_", _slug(name)), rows=rows)
    except Exception as exc:
        return {"published": False, "error": redact_secrets(exc)}
    return publish_to_weave(dataset, name=f"atlas-replay-{_slug(name)}")


# --------------------------------------------------------------------------- #
# Persistence + read API
# --------------------------------------------------------------------------- #
def create_replay_set(
    name: str = DEFAULT_REPLAY_SET,
    *,
    description: str = "",
    limit: int = 25,
    include_live: bool = True,
    include_history: bool = True,
    publish: bool = True,
) -> dict[str, Any]:
    """Build, persist (atlas:evaluation:replay:*), and publish a replay set as a weave.Dataset."""
    cases = build_replay_cases(limit, include_live=include_live, include_history=include_history)
    slug = _slug(name)
    record: dict[str, Any] = {
        "name": name,
        "slug": slug,
        "description": description or f"Replay set built from {len(cases)} prior board decisions.",
        "created_at": _now(),
        "created_ts": time.time(),
        "case_count": len(cases),
        "history_cases": sum(1 for c in cases if c.source == "history"),
        "live_cases": sum(1 for c in cases if c.source == "live"),
        "cases": [c.model_dump() for c in cases],
        "weave": _publish_dataset(name, cases) if publish else {"published": False, "skipped": True},
    }
    R.set_json(f"{REPLAY_PREFIX}{slug}", record)
    try:
        R.client().sadd(REPLAY_INDEX, slug)
    except Exception:
        pass
    return record


def list_replay_sets() -> list[dict[str, Any]]:
    """Replay-set summaries (without the full case payloads)."""
    try:
        slugs = sorted(R.client().smembers(REPLAY_INDEX))
    except Exception:
        slugs = []
    out: list[dict[str, Any]] = []
    for slug in slugs:
        record = R.get_json(f"{REPLAY_PREFIX}{slug}")
        if not record:
            continue
        out.append(
            {
                key: record.get(key)
                for key in (
                    "name", "slug", "description", "created_at", "case_count",
                    "history_cases", "live_cases", "weave",
                )
            }
        )
    return out


def get_replay_set(name_or_slug: str) -> dict | None:
    """Full replay-set record (including cases)."""
    return R.get_json(f"{REPLAY_PREFIX}{_slug(name_or_slug)}")


def ensure_default_replay_set(publish: bool = True) -> dict[str, Any]:
    """Create the default replay set if it does not exist yet (safe to call repeatedly)."""
    existing = get_replay_set(DEFAULT_SLUG)
    if existing and existing.get("case_count"):
        return existing
    return create_replay_set(
        DEFAULT_REPLAY_SET,
        description="Default replay set across seeded board precedents and live council runs.",
        publish=publish,
    )


def replay_summary() -> dict[str, Any]:
    """Compact, non-secret summary for health / observability surfaces."""
    sets = list_replay_sets()
    total_cases = sum(int(s.get("case_count") or 0) for s in sets)
    return {
        "replay_set_count": len(sets),
        "total_cases": total_cases,
        "default": DEFAULT_SLUG if get_replay_set(DEFAULT_SLUG) else None,
        "sets": [{"slug": s.get("slug"), "name": s.get("name"), "case_count": s.get("case_count")} for s in sets],
        "weave": weave_links(),
    }
