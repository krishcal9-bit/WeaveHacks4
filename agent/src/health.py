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

    status["ready"] = all(check["ready"] for check in status["checks"])
    if status["ready"]:
        status["detail"] = "Redis Stack is reachable with RedisJSON and RediSearch."
    if not status["ready"]:
        status["detail"] = "Redis is reachable, but Redis Stack modules are missing."
    return status


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
    api_key = provider_api_key_name()
    ready = bool(provider and model and os.getenv(api_key))
    detail = f"{provider}:{model}" if provider and model else "LLM_PROVIDER or LLM_MODEL missing"
    return {
        "id": "openai" if provider_normalized == "openai" else "llm",
        "label": "OpenAI" if provider_normalized == "openai" else "Live LLM",
        "ready": ready,
        "detail": detail,
        "provider": provider,
        "model": model,
        "api_key": api_key,
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


def require_live_ready() -> None:
    health = sponsor_health()
    if not health["ready"]:
        raise RuntimeError("Atlas strict-live preflight failed: " + "; ".join(health["blockers"]))
