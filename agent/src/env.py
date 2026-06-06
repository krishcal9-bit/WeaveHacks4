"""Environment loading and safe environment metadata for Atlas.

Secrets live in the repository root ``.env``. The agent may run from either the
repo root or ``agent/``, so this module loads both locations while keeping the
root file authoritative and never exposing secret values in health responses.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

_BASE_REQUIRED_ENV = (
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_REASONING_EFFORT",
    "LLM_TEXT_VERBOSITY",
    "OPENAI_REALTIME_MODEL",
    "OPENAI_REALTIME_REASONING_EFFORT",
    "OPENAI_REALTIME_VOICE",
    "REDIS_URL",
    "WANDB_API_KEY",
    "WANDB_PROJECT",
)
_PROVIDER_API_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS", "CREDENTIAL", "AUTH")


@dataclass(frozen=True)
class EnvFileStatus:
    path: Path
    exists: bool
    loaded: bool


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def agent_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def env_files() -> tuple[Path, ...]:
    return (repo_root() / ".env", agent_dir() / ".env")


def load_env() -> tuple[EnvFileStatus, ...]:
    statuses: list[EnvFileStatus] = []
    for path in env_files():
        exists = path.exists()
        loaded = bool(load_dotenv(path, override=False)) if exists else False
        statuses.append(EnvFileStatus(path=path, exists=exists, loaded=loaded))
    return tuple(statuses)


def provider_api_key_name() -> str:
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if not provider:
        return _PROVIDER_API_KEYS["openai"]
    return _PROVIDER_API_KEYS.get(provider, f"{provider.upper()}_API_KEY")


def required_env_keys() -> tuple[str, ...]:
    keys = [*_BASE_REQUIRED_ENV, provider_api_key_name()]
    return tuple(dict.fromkeys(keys))


def is_configured(key: str) -> bool:
    return bool(os.getenv(key, "").strip())


def is_secret_key(key: str) -> bool:
    normalized = key.upper()
    return any(marker in normalized for marker in _SECRET_MARKERS)


def redact_secrets(text: object) -> str:
    redacted = str(text)
    for key, value in os.environ.items():
        if not value or len(value) < 4:
            continue
        safe = _url_with_redacted_password(value)
        if safe != value:
            redacted = redacted.replace(value, safe)
            password = urlsplit(value).password
            if password:
                redacted = redacted.replace(password, "[redacted]")
        elif is_secret_key(key):
            redacted = redacted.replace(value, "[redacted]")
    return redacted


def safe_url(url: str | None) -> str | None:
    if not url:
        return None
    return _url_with_redacted_password(url)


def _url_with_redacted_password(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    if not parsed.password:
        return url

    username = parsed.username or ""
    hostname = parsed.hostname or ""
    try:
        parsed_port = parsed.port
    except ValueError:
        parsed_port = None
    port = f":{parsed_port}" if parsed_port else ""
    auth = f"{username}:***@" if username else ""
    netloc = f"{auth}{hostname}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
