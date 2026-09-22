"""One typed model configuration and one precedence/validation path."""

import pytest
from pydantic import ValidationError

from app.config import ConfigurationError, resolve_model_config, settings
from app.schemas import ModelConfig


DEFAULTS = ModelConfig(api_key="deployment-secret", base_url="https://trusted.test/v1", model="default-model", api_protocol="openai")


def test_defaults_are_resolved_to_typed_config_without_mutating_them():
    result = resolve_model_config(DEFAULTS)
    assert isinstance(result, ModelConfig)
    assert result.api_key == "deployment-secret" and result.model == "default-model"
    assert result.api_protocol == "openai"
    assert DEFAULTS.model == "default-model"


def test_supplied_fields_override_defaults_individually():
    result = resolve_model_config(DEFAULTS, ModelConfig(api_key="session-secret", model="session-model"))
    assert result.api_key == "session-secret"
    assert result.model == "session-model"
    assert result.base_url == "https://trusted.test/v1"


def test_same_endpoint_can_use_deployment_key_and_normalizes_trailing_slash():
    result = resolve_model_config(DEFAULTS, ModelConfig(base_url="https://trusted.test/v1/"))
    assert result.api_key == "deployment-secret"
    assert result.base_url == "https://trusted.test/v1"


def test_endpoint_switch_requires_own_key_and_never_leaks_default():
    with pytest.raises(ConfigurationError) as exc:
        resolve_model_config(DEFAULTS, ModelConfig(base_url="https://other.test/v1"))
    assert "deployment-secret" not in str(exc.value)
    result = resolve_model_config(DEFAULTS, ModelConfig(base_url="https://other.test/v1", api_key="session-secret"))
    assert result.api_key == "session-secret" and result.base_url == "https://other.test/v1"


@pytest.mark.parametrize("config", [ModelConfig(), ModelConfig(api_key="test"), ModelConfig(model="test")])
def test_missing_credentials_or_model_are_rejected(config):
    with pytest.raises(ConfigurationError):
        resolve_model_config(config)


@pytest.mark.parametrize("url", [
    "http://example.test/v1", "https://user:pass@example.test/v1", "https://example.test/v1?key=secret",
    "https://example.test/v1#secret", "file:///tmp/api", "http://192.168.1.1/v1",
    "https://example.test/\npath",
])
def test_invalid_or_insecure_remote_endpoints_are_rejected(url):
    with pytest.raises(ConfigurationError):
        resolve_model_config(ModelConfig(api_key="secret", model="model", base_url=url))


@pytest.mark.parametrize("url", ["http://localhost:11434/v1/", "http://127.0.0.1:1234/v1", "http://[::1]:8080/v1"])
def test_loopback_http_is_allowed_for_local_model_servers(url):
    result = resolve_model_config(ModelConfig(api_key="local-key", model="local-model", base_url=url))
    assert result.base_url == url.rstrip("/")


def test_invalid_key_and_schema_errors_do_not_offer_a_mode_switch():
    with pytest.raises(ConfigurationError):
        resolve_model_config(ModelConfig(api_key="secret\nheader", model="test"))
    with pytest.raises(ValidationError):
        ModelConfig(mode="offline")
    assert "api_key" not in repr(DEFAULTS)
    assert "deployment-secret" not in repr(DEFAULTS)


def test_environment_defaults_use_the_same_model_config_type(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "env-secret")
    monkeypatch.setenv("MODEL_BASE_URL", "https://env.test/v1")
    monkeypatch.setenv("MODEL_NAME", "env-model")
    monkeypatch.setenv("MODEL_API_PROTOCOL", "anthropic")
    config = settings()
    assert config["model_config"] == ModelConfig(api_key="env-secret", base_url="https://env.test/v1", model="env-model", api_protocol="anthropic")
    assert "api_key" not in config and "model" not in config and "base_url" not in config


def test_native_provider_defaults_have_correct_base_url():
    openai = resolve_model_config(ModelConfig(api_key="own-key", model="my-model"))
    anthropic = resolve_model_config(ModelConfig(api_key="own-key", model="claude-model", api_protocol="anthropic"))
    assert openai.api_protocol == "openai" and openai.base_url == "https://api.openai.com/v1"
    assert anthropic.api_protocol == "anthropic" and anthropic.base_url == "https://api.anthropic.com/v1"


def test_protocol_switch_requires_own_key():
    with pytest.raises(ConfigurationError):
        resolve_model_config(DEFAULTS, ModelConfig(api_protocol="anthropic", model="claude-model"))
    resolved = resolve_model_config(DEFAULTS, ModelConfig(api_protocol="anthropic", api_key="claude-key", model="claude-model"))
    assert resolved.api_protocol == "anthropic" and resolved.api_key == "claude-key"
    assert resolved.base_url == "https://api.anthropic.com/v1"


@pytest.mark.parametrize("field", ["api_key", "base_url", "model"])
def test_explicit_blank_override_is_not_replaced_by_a_deployment_default(field):
    with pytest.raises(ConfigurationError):
        resolve_model_config(DEFAULTS, ModelConfig(**{field: " "}))
