"""Strict live-readiness checks for Atlas sponsor infrastructure."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.env import (
    is_configured,
    load_env,
    provider_api_key_name,
    redact_secrets,
    repo_root,
    required_env_keys,
    safe_url,
)

load_env()

_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh"}
_TEXT_VERBOSITIES = {"low", "medium", "high"}

_weave_status: dict[str, Any] = {
    "configured": bool(os.getenv("WANDB_API_KEY") and os.getenv("WANDB_PROJECT")),
    "initialized": False,
    "project": os.getenv("WANDB_PROJECT"),
    "entity": os.getenv("WANDB_ENTITY"),
    "error": "Weave has not been initialized yet.",
}
_copilotkit_status: dict[str, Any] = {
    "id": "copilotkit",
    "label": "CopilotKit / AG-UI",
    "ready": False,
    "mounted": False,
    "path": None,
    "agent": None,
    "detail": "FastAPI AG-UI endpoint has not been mounted.",
}


def set_weave_status(*, initialized: bool, error: str | None = None) -> None:
    _weave_status.update(
        {
            "configured": bool(os.getenv("WANDB_API_KEY") and os.getenv("WANDB_PROJECT")),
            "initialized": initialized,
            "project": os.getenv("WANDB_PROJECT"),
            "entity": os.getenv("WANDB_ENTITY"),
            "error": redact_secrets(error) if error else None,
        }
    )


def weave_status() -> dict[str, Any]:
    project = _weave_status.get("project")
    entity = _weave_status.get("entity")
    url = f"https://wandb.ai/{entity}/{project}/weave" if entity and project else None
    sandbox_id = os.getenv("WANDB_SANDBOX_ID") or os.getenv("WANDB_SANDBOX_NAME")
    sandbox_url = os.getenv("WANDB_SANDBOX_URL")
    sandbox = {
        "configured": bool(sandbox_id or sandbox_url),
        "id": sandbox_id,
        "url": safe_url(sandbox_url),
        "detail": (
            "W&B Serverless Sandbox linked for isolated agent execution."
            if sandbox_id or sandbox_url
            else "No W&B Serverless Sandbox env linked; Weave tracing is still live."
        ),
    }
    return {**_weave_status, "url": url, "sandbox": sandbox}


def mark_copilotkit_mounted(*, path: str, agent_name: str) -> None:
    _copilotkit_status.update(
        {
            "ready": True,
            "mounted": True,
            "path": path,
            "agent": agent_name,
            "detail": f"FastAPI LangGraph endpoint mounted at {path}",
        }
    )


def copilotkit_status() -> dict[str, Any]:
    return dict(_copilotkit_status)


def _env_status() -> list[dict[str, Any]]:
    return [
        {
            "id": key.lower(),
            "label": key,
            "ready": is_configured(key),
            "detail": "Configured" if is_configured(key) else "Missing",
        }
        for key in required_env_keys()
    ]


def _redis_status() -> dict[str, Any]:
    from src import redis_layer as R

    redis_url = os.getenv("REDIS_URL")
    status: dict[str, Any] = {
        "id": "redis",
        "label": "Redis Stack",
        "ready": False,
        "detail": "Redis URL configured" if redis_url else "REDIS_URL missing",
        "url": safe_url(redis_url),
        "checks": [],
        "modules": {},
        "indices": {},
        "streams": {},
    }
    if not redis_url:
        status["checks"].append({"label": "PING", "ready": False, "detail": "REDIS_URL missing"})
        return status

    try:
        client = R.client()
        client.ping()
        status["checks"].append({"label": "PING", "ready": True, "detail": "Connected"})
    except Exception as exc:
        status["checks"].append({"label": "PING", "ready": False, "detail": redact_secrets(exc)})
        status["detail"] = "Redis is not reachable."
        return status

    try:
        client.execute_command("JSON.GET", "__atlas:health:missing__", "$")
        status["checks"].append({"label": "RedisJSON", "ready": True, "detail": "JSON commands available"})
    except Exception as exc:
        status["checks"].append({"label": "RedisJSON", "ready": False, "detail": redact_secrets(exc)})

    try:
        client.execute_command("FT._LIST")
        status["checks"].append({"label": "RediSearch", "ready": True, "detail": "Search commands available"})
    except Exception as exc:
        status["checks"].append({"label": "RediSearch", "ready": False, "detail": redact_secrets(exc)})

    try:
        modules = _redis_modules(client)
        status["modules"] = modules
        has_json = any("json" in name or "rejson" in name for name in modules)
        has_search = any("search" in name for name in modules)
        status["checks"].append(
            {
                "label": "Redis Stack modules",
                "ready": bool(has_json and has_search),
                "detail": ", ".join(f"{name} {version}" for name, version in modules.items()) or "No modules reported",
            }
        )
    except Exception as exc:
        status["checks"].append({"label": "Redis Stack modules", "ready": False, "detail": redact_secrets(exc)})

    for index_name, label in ((R.VENDOR_INDEX, "Vendor search index"), (R.POLICY_INDEX, "Policy vector index")):
        try:
            info = _redis_index_info(client, index_name)
            num_docs = info.get("num_docs") or info.get("num_records") or "0"
            ready = int(float(str(num_docs))) > 0
            status["indices"][index_name] = info
            status["checks"].append(
                {
                    "label": label,
                    "ready": ready,
                    "detail": f"{index_name} contains {num_docs} indexed docs",
                }
            )
        except Exception as exc:
            status["checks"].append({"label": label, "ready": False, "detail": redact_secrets(exc)})

    try:
        stream_key = f"{R.NS}:stream:decisions"
        stream_len = int(client.xlen(stream_key))
        stream_info = _redis_stream_info(client, stream_key)
        status["streams"][stream_key] = stream_info
        status["checks"].append(
            {
                "label": "Decision stream",
                "ready": stream_len > 0,
                "detail": f"{stream_key} length {stream_len}, last id {stream_info.get('last-generated-id') or 'none'}",
            }
        )
    except Exception as exc:
        status["checks"].append({"label": "Decision stream", "ready": False, "detail": redact_secrets(exc)})

    status["ready"] = all(check["ready"] for check in status["checks"])
    if status["ready"]:
        status["detail"] = "Redis Stack is live with JSON, Search, vector RAG, and Streams."
    if not status["ready"]:
        status["detail"] = "Redis is reachable, but one or more Stack/index/stream checks failed."
    return status


def _redis_modules(client: Any) -> dict[str, str]:
    rows = client.execute_command("MODULE", "LIST")
    modules: dict[str, str] = {}
    for row in rows or []:
        parsed = _pairs_to_dict(row)
        name = str(parsed.get("name") or parsed.get(b"name") or "").lower()
        version = str(parsed.get("ver") or parsed.get(b"ver") or "unknown")
        if name:
            modules[name] = version
    return modules


def _redis_index_info(client: Any, index: str) -> dict[str, Any]:
    return _pairs_to_dict(client.execute_command("FT.INFO", index))


def _redis_stream_info(client: Any, key: str) -> dict[str, Any]:
    return _pairs_to_dict(client.execute_command("XINFO", "STREAM", key))


def _pairs_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    if not isinstance(value, (list, tuple)):
        return {}
    parsed: dict[str, Any] = {}
    iterator = iter(value)
    for key in iterator:
        try:
            item = next(iterator)
        except StopIteration:
            break
        parsed[str(key)] = item
    return parsed


def _cursor_status() -> dict[str, Any]:
    rules_dir = repo_root() / ".cursor" / "rules"
    rule_files = _rule_files(rules_dir)
    ready = rules_dir.is_dir() and bool(rule_files)
    detail = (
        f".cursor/rules contains {len(rule_files)} rule file(s)."
        if ready
        else "Missing .cursor/rules with at least one rule file."
    )
    return {
        "id": "cursor",
        "label": "Cursor",
        "ready": ready,
        "detail": detail,
        "path": str(rules_dir.relative_to(repo_root())),
        "rules": [str(path.relative_to(repo_root())) for path in rule_files],
    }


def _rule_files(rules_dir: Path) -> list[Path]:
    if not rules_dir.is_dir():
        return []
    return sorted(path for path in rules_dir.rglob("*") if path.is_file())


def _llm_status() -> dict[str, Any]:
    provider = os.getenv("LLM_PROVIDER")
    provider_normalized = (provider or "").lower()
    model = os.getenv("LLM_MODEL")
    reasoning_effort = os.getenv("LLM_REASONING_EFFORT")
    verbosity = os.getenv("LLM_TEXT_VERBOSITY")
    realtime_model = os.getenv("OPENAI_REALTIME_MODEL")
    realtime_reasoning_effort = os.getenv("OPENAI_REALTIME_REASONING_EFFORT")
    realtime_voice = os.getenv("OPENAI_REALTIME_VOICE")
    api_key = provider_api_key_name()
    checks = [
        {
            "label": "Provider",
            "ready": provider_normalized == "openai",
            "detail": provider or "LLM_PROVIDER missing",
        },
        {
            "label": "Reasoning model",
            "ready": bool(model and model.startswith("gpt-5.5")),
            "detail": model or "LLM_MODEL missing",
        },
        {
            "label": "Reasoning effort",
            "ready": reasoning_effort == "xhigh",
            "detail": reasoning_effort or "LLM_REASONING_EFFORT missing",
        },
        {
            "label": "Text verbosity",
            "ready": bool(verbosity in _TEXT_VERBOSITIES),
            "detail": verbosity or "LLM_TEXT_VERBOSITY missing",
        },
        {
            "label": "Realtime model",
            "ready": realtime_model == "gpt-realtime-2",
            "detail": realtime_model or "OPENAI_REALTIME_MODEL missing",
        },
        {
            "label": "Realtime reasoning",
            "ready": realtime_reasoning_effort == "xhigh",
            "detail": realtime_reasoning_effort or "OPENAI_REALTIME_REASONING_EFFORT missing",
        },
        {
            "label": "Realtime voice",
            "ready": bool(realtime_voice),
            "detail": realtime_voice or "OPENAI_REALTIME_VOICE missing",
        },
        {
            "label": api_key,
            "ready": bool(os.getenv(api_key)),
            "detail": "Configured" if os.getenv(api_key) else "Missing",
        },
    ]
    ready = all(check["ready"] for check in checks)
    detail = (
        f"{provider}:{model} · {reasoning_effort} reasoning · {realtime_model} voice"
        if provider and model
        else "OpenAI model configuration missing"
    )
    return {
        "id": "openai" if provider_normalized == "openai" else "llm",
        "label": "OpenAI" if provider_normalized == "openai" else "Live LLM",
        "ready": ready,
        "detail": detail,
        "provider": provider,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "verbosity": verbosity,
        "realtime": {
            "model": realtime_model,
            "reasoning_effort": realtime_reasoning_effort,
            "voice": realtime_voice,
            "endpoint": "v1/realtime",
        },
        "capabilities": [
            "structured_outputs",
            "function_calling",
            "reasoning_xhigh",
            "realtime_voice",
            "webrtc_session_secret",
        ],
        "api_key": api_key,
        "checks": checks,
    }


def sponsor_health() -> dict[str, Any]:
    env_checks = _env_status()
    weave = weave_status()
    redis = _redis_status()
    llm = _llm_status()
    copilotkit = copilotkit_status()
    cursor = _cursor_status()

    sponsors = [
        llm,
        {
            "id": "weave",
            "label": "W&B Weave",
            "ready": bool(weave["configured"] and weave["initialized"]),
            "detail": (
                f"Initialized for project {weave['project']}"
                if weave["initialized"]
                else weave["error"] or "Weave tracing is not initialized."
            ),
            "project": weave["project"],
            "url": weave["url"],
            "error": weave["error"],
            "sandbox": weave.get("sandbox"),
        },
        redis,
        copilotkit,
        cursor,
    ]
    blockers = [
        f"{item['label']}: {item.get('detail') or item.get('error')}"
        for item in [*env_checks, *sponsors]
        if not item["ready"]
    ]
    return {
        "ready": len(blockers) == 0,
        "mode": "strict-live",
        "blockers": blockers,
        "env": env_checks,
        "sponsors": sponsors,
        "weave": weave,
        "observability": observability_health(),
    }


def observability_health() -> dict[str, Any]:
    weave = weave_status()
    ready = bool(weave["configured"] and weave["initialized"])
    return {
        "ready": ready,
        "mode": "strict-live",
        "weave": weave,
        "blockers": [] if ready else ["W&B Weave: tracing is not initialized."],
    }


def evaluation_health() -> dict[str, Any]:
    """Live readiness + non-secret counts for the W&B Weave eval/replay/promotion subsystem.

    Kept out of the hot-path :func:`sponsor_health` so the 15s health poll stays
    cheap; the eval modules are imported lazily to avoid an import cycle.
    """
    weave = weave_status()
    ready = bool(weave["configured"] and weave["initialized"])
    payload: dict[str, Any] = {
        "ready": ready,
        "mode": "strict-live",
        "weave": weave,
        "blockers": [] if ready else ["W&B Weave: tracing is not initialized."],
    }
    try:
        from src import promotion_gates as PG
        from src import replay_sets as RS
        from src import weave_eval as WE

        payload["evals"] = WE.eval_summary()
        payload["replay_sets"] = RS.replay_summary()
        payload["promotions"] = PG.promotion_status_summary()
    except Exception as exc:
        payload["error"] = redact_secrets(exc)
    return payload


def require_live_ready() -> None:
    health = sponsor_health()
    if not health["ready"]:
        raise RuntimeError("Atlas strict-live preflight failed: " + "; ".join(health["blockers"]))
