"""Environment-driven configuration for M0 verification. Secrets are read, never printed."""

from __future__ import annotations

import os
from dataclasses import dataclass


def load_dotenv(path: str = ".env") -> None:
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = _get(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = _get(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    model: str
    base_url: str
    api_key: str
    timeout_s: int

    @property
    def chat_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def missing_fields(self) -> list[str]:
        missing = []
        if not self.api_key:
            missing.append("api_key")
        if not self.base_url:
            missing.append("base_url")
        if not self.model:
            missing.append("model")
        return missing


@dataclass(frozen=True)
class AppConfig:
    primary: ProviderConfig
    fallback: ProviderConfig
    require_structured: bool
    hindsight_url: str
    hindsight_bank_id: str
    hindsight_api_key: str

    @property
    def hindsight_configured(self) -> bool:
        return bool(self.hindsight_url)


def load_config() -> AppConfig:
    load_dotenv()
    timeout = _int("LLM_TIMEOUT_S", 30)
    return AppConfig(
        primary=ProviderConfig(
            name=_get("LLM_PRIMARY_PROVIDER", "openrouter"),
            model=_get("LLM_PRIMARY_MODEL"),
            base_url=_get("LLM_PRIMARY_BASE_URL"),
            api_key=_get("LLM_PRIMARY_API_KEY"),
            timeout_s=timeout,
        ),
        fallback=ProviderConfig(
            name=_get("LLM_FALLBACK_PROVIDER", "baseten"),
            model=_get("LLM_FALLBACK_MODEL"),
            base_url=_get("LLM_FALLBACK_BASE_URL"),
            api_key=_get("LLM_FALLBACK_API_KEY"),
            timeout_s=timeout,
        ),
        require_structured=_bool("LLM_REQUIRE_STRUCTURED", True),
        hindsight_url=_get("HINDSIGHT_URL"),
        hindsight_bank_id=_get("HINDSIGHT_BANK_ID", "debugagent"),
        hindsight_api_key=_get("HINDSIGHT_API_KEY"),
    )


def redact(value: str) -> str:
    if not value:
        return "<unset>"
    if len(value) <= 8:
        return "<set:redacted>"
    return f"<set:redacted:len={len(value)}>"
