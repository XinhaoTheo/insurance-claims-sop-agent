"""Resolve environment defaults and explicit model settings in one place."""
import os
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv

from .schemas import ModelConfig

PROJECT = Path(__file__).resolve().parents[2]
PROTOCOL_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}
load_dotenv(PROJECT / ".env")


class ConfigurationError(ValueError):
    """A model configuration error that is safe to display."""


def resolve_model_config(defaults: ModelConfig, supplied: ModelConfig | None = None) -> ModelConfig:
    overrides = supplied.model_dump(exclude_unset=True) if supplied is not None else {}
    values = {**defaults.model_dump(), **overrides}
    default_protocol = defaults.api_protocol or "openai"
    default_url = defaults.base_url or PROTOCOL_BASE_URLS[default_protocol]
    protocol = values["api_protocol"] or "openai"
    values["api_protocol"] = protocol
    if "base_url" not in overrides:
        values["base_url"] = default_url if protocol == default_protocol else PROTOCOL_BASE_URLS[protocol]
    for field in ("api_key", "base_url", "model"):
        values[field] = (values[field] or "").strip()
    missing = [field for field in ("api_key", "base_url", "model") if not values[field]]
    if missing:
        raise ConfigurationError("Configure " + ", ".join(missing) + " before chatting.")
    values["base_url"] = values["base_url"].rstrip("/")
    try:
        url = urlsplit(values["base_url"])
    except ValueError:
        raise ConfigurationError("Check the base URL format.") from None
    if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ConfigurationError("Base URL must be an HTTP(S) API address without credentials or query parameters.")
    if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1", "host.docker.internal"):
        raise ConfigurationError("Use HTTPS for remote model endpoints.")
    if any(char.isspace() for char in values["base_url"]) or any(char in values["api_key"] for char in "\r\n"):
        raise ConfigurationError("Check the API key and base URL format.")
    endpoint_changed = values["base_url"] != default_url.strip().rstrip("/") or protocol != default_protocol
    if defaults.api_key and endpoint_changed and not overrides.get("api_key"):
        raise ConfigurationError("Supply your own API key for a different endpoint or protocol.")
    return ModelConfig(**values)


def settings():
    protocol = os.getenv("MODEL_API_PROTOCOL") or "openai"
    return {
        "database": os.getenv("DATABASE_PATH", str(PROJECT / "data" / "insurance.db")),
        "fixtures": Path(os.getenv("FIXTURES_PATH", str(PROJECT / "fixtures"))),
        "static": Path(os.getenv("STATIC_PATH", str(PROJECT / "frontend" / "dist"))),
        "model_config": ModelConfig(
            api_protocol=protocol,
            api_key=os.getenv("MODEL_API_KEY") or None,
            base_url=os.getenv("MODEL_BASE_URL") or PROTOCOL_BASE_URLS.get(protocol),
            model=os.getenv("MODEL_NAME") or None,
        ),
        "demo_date": os.getenv("DEMO_DATE", "") or date.today().isoformat(),
        "secret_ttl": 3600,
        "email_failure": os.getenv("SIMULATE_EMAIL_FAILURE", "false").lower() == "true",
    }
