"""
orchestration/llm_io.py — shared structured-output call + telemetry helpers.

Reuses the configured reasoning model via ``src.agent.llm`` (lazy import) and
returns ``(parsed, telemetry)`` for every model call — token counts, latency, and
any parsing error. Never returns a faked response: on failure ``parsed`` is None
and the error is recorded in telemetry, so callers degrade honestly.
"""

import json
import time

from langchain_core.messages import HumanMessage, SystemMessage

# Rough informational USD rates per 1M tokens (input, output). Cost is telemetry
# for the eval/observability surfaces, never load-bearing for a decision.
_RATES = {
    "gpt-5": (1.25, 10.0),
    "gpt-4o": (2.5, 10.0),
    "o1": (15.0, 60.0),
    "o3": (10.0, 40.0),
    "default": (1.25, 10.0),
}


async def structured_call(system: str, user: str, schema, *, temperature: float = 0.2, config=None):
    """One structured-output OpenAI call. Returns (parsed_or_None, telemetry)."""
    from src.agent import llm  # lazy reuse of the configured reasoning model

    chat = llm(temperature=temperature).with_structured_output(schema, include_raw=True)
    t0 = time.time()
    try:
        res = await chat.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=user)], config=config
        )
    except Exception as exc:
        from src.env import redact_secrets

        return None, {"error": redact_secrets(exc), "latency_ms": int((time.time() - t0) * 1000)}
    latency_ms = int((time.time() - t0) * 1000)
    usage = getattr(res.get("raw"), "usage_metadata", None) or {}
    telemetry = {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "latency_ms": latency_ms,
        "error": str(res.get("parsing_error")) if res.get("parsing_error") else None,
    }
    return res.get("parsed"), telemetry


def merge_telemetry(acc: dict | None, tel: dict | None) -> dict:
    """Accumulate token/latency/call telemetry across many model calls."""
    out = dict(acc or {})
    tel = tel or {}
    for key in ("input_tokens", "output_tokens", "total_tokens", "latency_ms"):
        if tel.get(key) is not None:
            out[key] = (out.get(key) or 0) + tel[key]
    out["calls"] = (out.get("calls") or 0) + 1
    if tel.get("error"):
        out.setdefault("errors", []).append(tel["error"])
    return out


def estimate_cost(model: str, input_tokens: int | None, output_tokens: int | None) -> float:
    key = next((k for k in _RATES if k != "default" and k in (model or "").lower()), "default")
    cost_in, cost_out = _RATES[key]
    return round((input_tokens or 0) / 1e6 * cost_in + (output_tokens or 0) / 1e6 * cost_out, 6)


def context_digest(context: dict | None, company: str, stage: str, precedents=None) -> str:
    context = context or {}
    fin = context.get("financials") or {}
    vendors = context.get("vendors") or []
    policies = context.get("policies") or []
    parts = [f"Company: {company} ({stage})"]
    if fin:
        parts.append("Financials (live system of record): " + json.dumps(fin, default=str)[:1400])
    parts.append(f"Vendors on file: {len(vendors)}")
    if policies:
        titles = "; ".join(str(p.get("title") or p.get("kind") or "")[:60] for p in policies[:5])
        parts.append(f"Relevant policies/precedents: {titles}")
    if precedents:
        prec = "; ".join(
            f"{(p.get('decision') or '')[:50]} -> {p.get('recommendation') or '?'}" for p in precedents[:3]
        )
        parts.append(f"Recalled past decisions (episodic memory): {prec}")
    return "\n".join(parts)
