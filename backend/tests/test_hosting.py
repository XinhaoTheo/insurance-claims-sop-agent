"""Hosted deployment boundaries, visitor credentials, and restart behavior."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import sqlite3
import time

from fastapi.testclient import TestClient
import pytest

from app import llm, main
from app.config import ConfigurationError, PROTOCOL_BASE_URLS, settings
from app.schemas import ModelConfig


ROOT = Path(__file__).resolve().parents[2]
VISITOR_CONFIG = {"api_key": "visitor-secret", "model": "visitor-model", "api_protocol": "openai",
                  "base_url": "https://api.openai.com/v1"}


@pytest.fixture
def hosted_api(tmp_path, model_observations):
    config = {"database": str(tmp_path / "hosted.db"), "fixtures": ROOT / "fixtures",
              "static": tmp_path / "static", "demo_date": "2026-03-10", "secret_ttl": 3600,
              "email_failure": False, "hosted": True,
              "allowed_model_base_urls": set(PROTOCOL_BASE_URLS.values()),
              "model_config": ModelConfig(api_key="server-secret", model="default-model",
                                          api_protocol="openai", base_url=PROTOCOL_BASE_URLS["openai"])}
    app = main.create_app(config)
    with TestClient(app) as client:
        yield client, app, config, model_observations


def create_session(client, payload=None):
    response = client.post("/api/sessions", json=payload or {})
    assert response.status_code == 201, response.text
    snapshot = response.json()
    return snapshot, {"Authorization": "Bearer " + snapshot["access_token"]}


def configuration_request(client, entry_point, payload):
    if entry_point == "session":
        return client.post("/api/sessions", json=payload)
    if entry_point == "test":
        return client.post("/api/models/test", json=payload)
    session, headers = create_session(client)
    return client.post(f"/api/sessions/{session['session_id']}/model", headers=headers, json=payload)


@pytest.mark.parametrize("entry_point", ["session", "test", "configure"])
@pytest.mark.parametrize("base_url", ["http://localhost:8000/v1", "https://192.168.1.1/v1",
                                     "https://unapproved-provider.test/v1", "https://api.openai.com/v1/other"])
def test_all_model_entry_points_reject_unapproved_endpoints_before_http(hosted_api, monkeypatch, entry_point, base_url):
    client, app, _, observations = hosted_api
    completion = AsyncMock(side_effect=AssertionError("An unapproved endpoint reached model HTTP"))
    monkeypatch.setattr(llm, "completion", completion)
    response = configuration_request(client, entry_point, {**VISITOR_CONFIG, "base_url": base_url})
    assert response.status_code == 400
    assert "administrator-approved" in response.json()["detail"]
    assert response.headers["Cache-Control"] == "no-store"
    assert app.state.credentials == {}
    assert observations.calls == []
    completion.assert_not_awaited()


def test_configuration_exposes_only_approved_endpoints_and_never_server_credentials(hosted_api):
    client, app, _, _ = hosted_api
    app.state.settings["model_config"] = ModelConfig(
        api_key="server-secret", model="default-model", api_protocol="openai", base_url="http://localhost:8000/v1",
    )
    response = client.get("/api/config")
    config = response.json()
    assert config["hosted"] is True and config["configured"] is False
    assert config["base_url"] == PROTOCOL_BASE_URLS["openai"]
    assert config["allowed_model_base_urls"] == sorted(PROTOCOL_BASE_URLS.values())
    assert "api_key" not in config and "server-secret" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    app.state.settings["allowed_model_base_urls"] = {"https://approved-provider.test/v1"}
    assert client.get("/api/config").json()["base_url"] == "https://approved-provider.test/v1"


@pytest.mark.parametrize("entry_point", ["session", "test", "configure"])
def test_hosted_requests_cannot_use_a_server_default_key(hosted_api, monkeypatch, entry_point):
    client, app, _, _ = hosted_api
    completion = AsyncMock(side_effect=AssertionError("The server key must not fund hosted requests"))
    monkeypatch.setattr(llm, "completion", completion)
    response = configuration_request(client, entry_point, {"model": "visitor-model"})
    assert response.status_code == 400
    assert "api_key" in response.json()["detail"]
    assert "server-secret" not in response.text
    assert app.state.credentials == {}
    completion.assert_not_awaited()
    session, _ = create_session(client)
    assert session["model_configured"] is False


@pytest.mark.parametrize("entry_point", ["session", "test", "configure"])
def test_hosted_requests_accept_visitor_keys(hosted_api, monkeypatch, entry_point):
    client, app, _, _ = hosted_api
    completion = AsyncMock(return_value='{"ok": true}')
    monkeypatch.setattr(llm, "completion", completion)
    response = configuration_request(client, entry_point, VISITOR_CONFIG)
    assert response.status_code == (201 if entry_point == "session" else 200)
    assert "visitor-secret" not in response.text and "server-secret" not in response.text
    if entry_point == "test":
        assert completion.await_args.args[0].api_key == "visitor-secret"
        assert app.state.credentials == {}
    else:
        assert response.json()["model_configured"] is True
        assert app.state.credentials[response.json()["session_id"]][0].api_key == "visitor-secret"
        completion.assert_not_awaited()


def test_visitors_use_isolated_keys_for_analysis_and_reply(hosted_api):
    client, app, _, observations = hosted_api
    first, first_headers = create_session(client, VISITOR_CONFIG)
    second, second_headers = create_session(client, {
        "api_key": "second-visitor-secret", "model": "second-model", "api_protocol": "anthropic",
        "base_url": PROTOCOL_BASE_URLS["anthropic"],
    })
    for session, headers in [(first, first_headers), (second, second_headers)]:
        observations.add("I need help with my claim.", {"intent": "status_inquiry"})
        response = client.post(f"/api/sessions/{session['session_id']}/messages", headers=headers,
                               json={"message": "I need help with my claim.", "turn_id": "first-turn"})
        assert response.status_code == 200
    for calls in [observations.calls, observations.render_calls]:
        assert [call["config"].api_key for call in calls] == ["visitor-secret", "second-visitor-secret"]
        assert [call["config"].api_protocol for call in calls] == ["openai", "anthropic"]
    first_model_path = f"/api/sessions/{first['session_id']}/model"
    assert client.post(first_model_path, headers=second_headers, json=VISITOR_CONFIG).status_code == 404
    assert client.delete(first_model_path, headers=first_headers).status_code == 200
    assert first["session_id"] not in app.state.credentials
    assert app.state.credentials[second["session_id"]][0].api_key == "second-visitor-secret"


def test_hosted_post_limit_keeps_health_available_and_recovers_after_one_minute(hosted_api, monkeypatch):
    client, _, _, observations = hosted_api
    clock = [1000.0]
    monkeypatch.setattr(main, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
    for _ in range(60):
        assert client.post("/api/models/test", json={}).status_code == 400
    limited = client.post("/api/sessions", json={})
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"
    assert limited.headers["Cache-Control"] == "no-store"
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/config").status_code == 200
    assert observations.calls == []
    clock[0] += 59
    assert client.post("/api/sessions", json={}).status_code == 429
    clock[0] += 2
    assert client.post("/api/sessions", json={}).status_code == 201


def test_sqlite_restart_retains_conversation_but_requires_a_new_visitor_key(hosted_api):
    client, _, config, observations = hosted_api
    session, headers = create_session(client, VISITOR_CONFIG)
    path = f"/api/sessions/{session['session_id']}"
    message = "I am calling about my denied healthcare claim from January."
    observations.add(message, {"intent": "denial_question", "hints": {"case_type": "healthcare", "status": "denied", "month": 1}})
    original = client.post(path + "/messages", headers=headers, json={"message": message, "turn_id": "saved-turn"})
    assert original.status_code == 200
    with sqlite3.connect(config["database"]) as database:
        persisted = " ".join(str(row) for table in ["sessions", "turns"] for row in database.execute(f"SELECT * FROM {table}"))
    assert "visitor-secret" not in persisted and "server-secret" not in persisted

    restarted_app = main.create_app(config)
    with TestClient(restarted_app) as restarted:
        restored = restarted.get(path, headers=headers)
        assert restored.status_code == 200
        assert restored.json()["messages"] == original.json()["messages"]
        assert restored.json()["state"]["case_hints"] == original.json()["state"]["case_hints"]
        assert restored.json()["model_configured"] is False
        assert restarted_app.state.credentials == {}
        denied = restarted.post(path + "/messages", headers=headers, json={"message": "Continue", "turn_id": "needs-key"})
        assert denied.status_code == 409
        reconnected = restarted.post(path + "/model", headers=headers,
                                     json={**VISITOR_CONFIG, "api_key": "reconnected-visitor-secret"})
        assert reconnected.status_code == 200
        observations.add("Continue", {})
        assert restarted.post(path + "/messages", headers=headers, json={"message": "Continue", "turn_id": "needs-key"}).status_code == 200
        assert observations.calls[-1]["config"].api_key == "reconnected-visitor-secret"


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_hosted_endpoint_setting_uses_official_defaults(monkeypatch, value):
    monkeypatch.setenv("HOSTED_DEMO", "true")
    monkeypatch.setenv("HOSTED_MODEL_BASE_URLS", value)
    assert settings()["allowed_model_base_urls"] == set(PROTOCOL_BASE_URLS.values())


def test_nonblank_empty_hosted_endpoint_list_reports_configuration_error(monkeypatch):
    monkeypatch.setenv("HOSTED_DEMO", "true")
    monkeypatch.setenv("HOSTED_MODEL_BASE_URLS", " , , ")
    with pytest.raises(ConfigurationError, match="at least one model endpoint"):
        settings()
