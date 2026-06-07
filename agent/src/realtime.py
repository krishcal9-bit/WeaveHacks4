"""
OpenAI Realtime 2 control surface for Atlas.

The browser voice path (Decision Room → WebRTC) is a first-class product surface,
not a toy. This module is the single place that:

  • Describes the **session policy** the voice agent runs under (model, voice,
    reasoning effort, turn detection, output modalities, tracing workflow, scope).
  • Builds a **live company grounding brief** from the Redis system of record
    (company identity, key financials, operator-uploaded operating data, and
    indexed documents) so the voice agent actually *knows* the company instead of
    answering "I don't have your files".
  • Defines the **voice tool surface** (company overview, knowledge search,
    submit-to-council) and exposes a grounded retrieval helper the browser calls
    mid-conversation so follow-ups stay connected to live data.
  • Reports **voice-model health** without minting a secret (config readiness,
    API-key presence, endpoint, TTL) so the UI can show whether voice is armed.
  • Mints a **short-lived ephemeral client secret** and reports its TTL
    (issued_at / expires_at / seconds_remaining) so the browser can manage
    re-minting before expiry.

Strict live-only: the secret is minted live against OpenAI; the grounding brief
and lookups read the real Redis data (never fabricated); errors are passed
through ``redact_secrets`` and we never return the standing ``OPENAI_API_KEY`` —
only the ephemeral, short-TTL client secret the WebRTC handshake requires.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from src.env import is_configured, provider_api_key_name, redact_secrets

# Reasonable defaults; the strict-live preflight requires the real values in .env.
DEFAULT_MODEL = "gpt-realtime-2"
DEFAULT_VOICE = "marin"
DEFAULT_TTL = 300
DEFAULT_REASONING = "xhigh"

PERSONA = (
    "You are Atlas Voice, the live voice interface for Atlas — an AI finance department. "
    "You sit in the AI Council Room where role agents (Treasury, FP&A, Risk & Audit, and "
    "Procurement) debate a financial decision and the CFO issues a ruling. Speak concisely and "
    "conversationally, like a sharp finance chief of staff on a call."
)


# --------------------------------------------------------------------------- #
# Live company grounding — the voice agent is connected to the SAME Redis
# system of record the dashboard and council use. This is what lets it answer
# "what is my company about?" and reference the operator's uploaded files.
# --------------------------------------------------------------------------- #
def _company_key() -> str:
    from src import redis_layer as R

    return f"{R.NS}:company:northwind"


def _fmt_usd(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        return f"${value:,.0f}"
    return None


def company_voice_brief() -> dict[str, Any]:
    """Live grounding brief for the voice agent.

    Reads the company system of record, the operator's imported operating data,
    and indexed documents straight from Redis. Safe to expose (no secrets) and
    never fabricates: if nothing is loaded it says so plainly so the agent tells
    the operator to upload their files rather than inventing a company.
    """
    brief: dict[str, Any] = {
        "company_loaded": False,
        "identity": {},
        "financials": {},
        "uploaded_sources": [],
        "uploaded_documents": {"count": 0, "categories": []},
        "warnings": [],
        "summary": "",
    }

    try:
        from src import redis_layer as R

        company = R.get_json(_company_key()) or {}
    except Exception as exc:  # Redis down / not seeded — never fabricate
        brief["warnings"].append(redact_secrets(exc))
        company = {}

    if company:
        brief["company_loaded"] = True
        brief["identity"] = {
            "name": company.get("name"),
            "sector": company.get("sector"),
            "stage": company.get("stage"),
            "hq": company.get("hq"),
            "founded": company.get("founded"),
            "headcount": company.get("headcount"),
        }
        brief["financials"] = {
            "cash_on_hand": company.get("cash_on_hand"),
            "monthly_net_burn": company.get("monthly_net_burn"),
            "runway_months": company.get("runway_months"),
            "mrr": company.get("mrr"),
            "arr": company.get("arr"),
            "mrr_growth_mom": company.get("mrr_growth_mom"),
            "gross_margin": company.get("gross_margin"),
        }

    # Operator-uploaded operating data (ledgers, invoices, vendor registers,
    # pipeline, headcount, security, board policy) — what the user just imported.
    try:
        from src.integrations import service as OPS

        statuses = OPS.connector_statuses()
        brief["uploaded_sources"] = [
            {
                "source_type": s.get("source_type"),
                "records": s.get("record_count"),
                "source_name": s.get("source_name"),
                "freshness_days": s.get("freshness_days"),
                "status": s.get("status"),
            }
            for s in statuses
            if s.get("status") in ("imported", "partial", "skipped_unchanged")
            and (s.get("record_count") or 0) > 0
        ]
    except Exception as exc:
        brief["warnings"].append(redact_secrets(exc))

    # Indexed documents available for semantic search.
    try:
        from src.documents.store import list_documents

        docs, total = list_documents(limit=100)
        categories = sorted({d.source_category for d in docs if getattr(d, "source_category", None)})
        brief["uploaded_documents"] = {"count": total, "categories": categories}
    except Exception as exc:
        brief["warnings"].append(redact_secrets(exc))

    brief["summary"] = _render_brief_summary(brief)
    return brief


def _render_brief_summary(brief: dict[str, Any]) -> str:
    identity = brief.get("identity") or {}
    sources = brief.get("uploaded_sources") or []
    documents = brief.get("uploaded_documents") or {}

    if not brief.get("company_loaded") and not sources and not documents.get("count"):
        return (
            "No company data has been loaded yet. Tell the operator to upload their company files "
            "on the Data tab before the council can run, and do not invent any figures."
        )

    parts: list[str] = []
    if identity.get("name"):
        descriptors = [d for d in (identity.get("sector"), identity.get("stage"), identity.get("hq")) if d]
        line = f"Company of record: {identity['name']}"
        if descriptors:
            line += f" — {', '.join(str(d) for d in descriptors)}"
        if identity.get("headcount"):
            line += f"; {identity['headcount']} employees"
        parts.append(line + ".")

    financials = brief.get("financials") or {}
    fbits: list[str] = []
    if (v := _fmt_usd(financials.get("cash_on_hand"))) is not None:
        fbits.append(f"cash {v}")
    if (v := _fmt_usd(financials.get("monthly_net_burn"))) is not None:
        fbits.append(f"net burn {v}/mo")
    if isinstance(financials.get("runway_months"), (int, float)):
        fbits.append(f"runway {financials['runway_months']} months")
    if (v := _fmt_usd(financials.get("arr"))) is not None:
        fbits.append(f"ARR {v}")
    if fbits:
        parts.append("Key financials: " + ", ".join(fbits) + ".")

    if sources:
        total_records = sum((s.get("records") or 0) for s in sources)
        named = ", ".join(f"{s.get('source_type')} ({s.get('records')} records)" for s in sources)
        parts.append(f"Operator-uploaded operating data ({total_records} records total): {named}.")

    if documents.get("count"):
        cats = ", ".join(documents.get("categories") or []) or "uploaded files"
        parts.append(f"{documents['count']} document(s) indexed for semantic search: {cats}.")

    return " ".join(parts)


def voice_instructions(brief: dict[str, Any] | None = None) -> str:
    """Full session instructions: persona + live company brief + tool guidance."""
    brief = brief or company_voice_brief()
    summary = brief.get("summary") or ""
    return "\n".join(
        [
            PERSONA,
            "",
            "LIVE COMPANY CONTEXT (read from the connected Redis system of record — this is the "
            "operator's own company and uploaded data, and YOU ARE CONNECTED TO IT):",
            summary,
            "",
            "You have live tools into this data. NEVER say you lack access to the company or its "
            "files — call a tool instead:",
            "- get_company_overview: company identity, key financials, and a summary of the data/files "
            "the operator has uploaded. Call this for 'what is my company about', 'what do we do', "
            "'what's our runway', 'what data do you have', and similar.",
            "- search_company_knowledge: semantic search over the operator's UPLOADED FILES plus the "
            "company's finance policies and past board decisions. Call this to answer specific "
            "questions grounded in their documents.",
            "- submit_decision_to_council: start a live council debate. Call ONLY when the operator "
            "clearly asks the council to decide something (vendor renewals, hiring, capex, security "
            "blockers, pricing, financing), not for greetings or general questions.",
            "",
            "Rules: wait for the operator to finish speaking before replying. Ground every quantified "
            "answer in a tool result — never fabricate numbers, runway, traces, or file contents. If "
            "intent is ambiguous, ask one short clarifying question. If no company data is loaded, say "
            "so plainly and point the operator to the Data tab. Keep replies short and spoken-friendly.",
        ]
    )


def voice_tools() -> list[dict[str, Any]]:
    """The function tools the realtime voice agent can call (browser executes them)."""
    return [
        {
            "type": "function",
            "name": "get_company_overview",
            "description": (
                "Get the operator's company identity, key financials, and a summary of the data and "
                "files they have uploaded. Call this whenever asked what the company is, what they do, "
                "their financial position/runway, or what data is available."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "type": "function",
            "name": "search_company_knowledge",
            "description": (
                "Semantic search over the operator's uploaded documents/files and the company's finance "
                "policies and past board decisions. Use to answer specific questions grounded in the "
                "operator's own data (e.g. a vendor's contract terms, a security finding, a board rule)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to look up, phrased as a natural-language search query.",
                    }
                },
                "required": ["query"],
            },
        },
        {
            "type": "function",
            "name": "submit_decision_to_council",
            "description": (
                "Send a finance decision question to the live Atlas AI council debate. Call only when "
                "the operator clearly asks the council to decide something."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "decision": {
                        "type": "string",
                        "description": (
                            "The decision question for Treasury, FP&A, Risk, Procurement, and the CFO — "
                            "one clear sentence, ideally ending with a question mark."
                        ),
                    }
                },
                "required": ["decision"],
            },
        },
    ]


def voice_lookup(query: str, *, k: int = 6) -> dict[str, Any]:
    """Grounded retrieval for the voice agent: live semantic search over the
    operator's uploaded document chunks and the company's finance policies and
    board-decision precedent. Live embeddings; never fabricates results.
    """
    query = (query or "").strip()
    out: dict[str, Any] = {"query": query, "documents": [], "policies": [], "notes": []}
    if not query:
        out["notes"].append("Empty query.")
        return out

    capped = min(max(int(k or 6), 1), 8)

    try:
        from src.documents.store import search_document_chunks

        hits = search_document_chunks(query, k=capped)
        out["documents"] = [
            {
                "filename": h.get("filename"),
                "source_category": h.get("source_category"),
                "kind": h.get("kind"),
                "vendor": h.get("vendor"),
                "text": h.get("text"),
                "score": h.get("score"),
            }
            for h in hits
        ]
    except Exception as exc:
        out["notes"].append(redact_secrets(exc))

    try:
        from src import redis_layer as R

        policies = R.search_policies(query, k=4)
        out["policies"] = [
            {
                "title": p.get("title"),
                "kind": p.get("kind"),
                "text": p.get("text"),
                "score": p.get("score"),
            }
            for p in policies
        ]
    except Exception as exc:
        out["notes"].append(redact_secrets(exc))

    if not out["documents"] and not out["policies"]:
        out["notes"].append("No matching uploaded documents or finance policies found for this query.")
    return out


def realtime_config() -> dict[str, Any]:
    """Resolved Realtime configuration from the environment."""
    return {
        "model": os.getenv("OPENAI_REALTIME_MODEL", DEFAULT_MODEL),
        "voice": os.getenv("OPENAI_REALTIME_VOICE", DEFAULT_VOICE),
        "reasoning_effort": os.getenv("OPENAI_REALTIME_REASONING_EFFORT", DEFAULT_REASONING),
        "ttl_seconds": _ttl_seconds(),
        "turn_detection": {
            "type": "semantic_vad",
            "eagerness": os.getenv("OPENAI_REALTIME_EAGERNESS", "low"),
            "create_response": True,
            "interrupt_response": False,
        },
        "output_modalities": ["audio"],
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
        "instructions_summary": (
            "Grounded in the live company record + uploaded files; can look up company "
            "overview/knowledge and submit decisions to the council; never fabricates numbers."
        ),
        "tools": [t["name"] for t in voice_tools()],
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
        "capabilities": ["webrtc_session_secret", "semantic_vad", "audio", "ephemeral_secret_ttl"],
        "policy": session_policy(),
        "checks": checks,
    }


def _get_attr(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _session_payload(config: dict[str, Any], instructions: str, tools: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "expires_after": {"anchor": "created_at", "seconds": config["ttl_seconds"]},
        "session": {
            "type": "realtime",
            "model": config["model"],
            "instructions": instructions,
            "tools": tools,
            "tool_choice": "auto",
            "audio": {
                "input": {
                    "turn_detection": config["turn_detection"],
                    "transcription": {"model": "whisper-1"},
                    "noise_reduction": {"type": "far_field"},
                },
                "output": {"voice": config["voice"]},
            },
            "tracing": {"workflow_name": config["workflow_name"]},
        },
    }


async def _mint_session_http(
    config: dict[str, Any], instructions: str, tools: list[dict[str, Any]]
) -> dict[str, Any]:
    api_key_name = provider_api_key_name()
    api_key = os.getenv(api_key_name)
    if not api_key:
        raise RuntimeError(f"{api_key_name} is not configured")

    async with httpx.AsyncClient(timeout=15) as http:
        response = await http.post(
            "https://api.openai.com/v1/realtime/client_secrets",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=_session_payload(config, instructions, tools),
        )
    if response.status_code >= 400:
        raise RuntimeError(response.text)
    return response.json()


async def mint_session() -> dict[str, Any]:
    """Mint a live, short-TTL OpenAI Realtime client secret with TTL reporting.

    The minted session is grounded in the live company brief (so the voice agent
    knows the operator's company and uploaded files) and carries the voice tool
    surface. Raises ``RuntimeError`` on a live OpenAI failure (the caller maps it
    to a 502 with a redacted detail). Never returns the standing API key.
    """
    config = realtime_config()
    brief = company_voice_brief()
    instructions = voice_instructions(brief)
    tools = voice_tools()
    issued_at = int(time.time())

    try:
        secret = await _mint_session_http(config, instructions, tools)
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
        # Grounding + tool surface the browser session config uses verbatim.
        "instructions": instructions,
        "tools": tools,
        "company_brief": brief,
        "client_secret": client_secret,
    }
