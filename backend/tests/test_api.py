"""HTTP acceptance tests for the complete local demo and its safety gates."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import time

from fastapi.testclient import TestClient
import pytest

from app import main
from app.llm import ModelError


ROOT = Path(__file__).resolve().parents[2]
SAMPLE = "I’m the policyholder. My name is Margaret Chen, policy POL-9921. I’m calling about my denied healthcare claim from January. DOB is 1985-03-15, SSN last four is 4472."
LIVE = {"mode": "live", "api_key": "test-provider-secret-123", "base_url": "https://example.test/v1", "model": "test-model"}


@pytest.fixture
def api(tmp_path):
    config = {"database": str(tmp_path / "test.db"), "fixtures": ROOT / "fixtures",
              "static": tmp_path / "static", "demo_date": "2026-03-10", "api_key": "",
              "model": "", "base_url": "https://example.test/v1", "email_failure": False,
              "secret_ttl": 3600}
    app = main.create_app(config)
    with TestClient(app) as client:
        yield client, app, config


def session(api, **payload):
    response = api[0].post("/api/sessions", json={"mode": "offline", **payload})
    assert response.status_code == 201, response.text
    created = response.json()
    return created, {"Authorization": "Bearer " + created["access_token"]}


def post(api, actor, message, turn_id):
    snapshot, headers = actor
    return api[0].post(f"/api/sessions/{snapshot['session_id']}/messages", headers=headers,
                       json={"message": message, "turn_id": turn_id})


def say(api, actor, message, turn_id):
    response = post(api, actor, message, turn_id)
    assert response.status_code == 200, response.text
    return response.json()


def reach_post(api, actor):
    first = say(api, actor, SAMPLE, "identity")
    assert first["state"]["phase"] == "PROCESS_CASE"
    response = say(api, actor, "That's all, no more questions.", "finish")
    assert response["state"]["phase"] == "POST_PROCESS"
    return response


def test_health_configuration_and_cache_headers(api):
    response = api[0].get("/health")
    assert response.status_code == 200 and response.json()["email_mode"] == "mock"
    response = api[0].get("/api/config")
    assert response.json()["default_mode"] == "offline"
    assert "api_key" not in response.json()
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_complete_sample_and_send_have_ordered_stages_and_one_outbox_item(api):
    actor = session(api)
    result = say(api, actor, SAMPLE, "identity")
    assert result["state"]["phase"] == "PROCESS_CASE"
    assert result["state"]["verified"]
    assert result["state"]["selected_case_id"] == "CL-2048"
    assert result["state"]["case_hints"]["month"] == 1
    assert "pathology report" in result["reply"] and "office note" in result["reply"]
    assert "verified_party_id" not in result["state"]
    phases = [e["detail"] for e in result["events"] if e["kind"] == "phase_changed"]
    assert phases == ["VERIFY_ID → RESOLVE_INTENT", "RESOLVE_INTENT → PROCESS_CASE"]
    result = say(api, actor, "That's all, no more questions.", "finish")
    assert result["state"]["phase"] == "POST_PROCESS"
    assert result["state"]["email_status"] == "awaiting_choice"
    assert "No claim decision was changed" in result["email_summary"]["body"]
    sent = say(api, actor, "Yes, please send the summary.", "send")
    assert sent["state"]["email_status"] == "simulated_sent"
    assert sent["state"]["status"] == "completed"
    assert "No real email was sent" in sent["reply"]
    repeated = say(api, actor, "Yes, please send the summary.", "send")
    assert repeated == sent
    with sqlite3.connect(api[2]["database"]) as db:
        assert db.execute("SELECT count(*) FROM email_outbox").fetchone()[0] == 1
    assert actor[0]["session_id"] not in api[1].state.identities


def test_skip_email_does_not_create_an_outbox_entry(api):
    actor = session(api)
    reach_post(api, actor)
    result = say(api, actor, "No thanks", "skip")
    assert result["state"]["email_status"] == "skipped"
    assert result["state"]["status"] == "completed"
    with sqlite3.connect(api[2]["database"]) as db:
        assert db.execute("SELECT count(*) FROM email_outbox").fetchone()[0] == 0


def test_partial_answers_remember_intent_and_only_query_after_verification(api):
    actor = session(api)
    result = say(api, actor, "My name is Margaret Chen. I'm calling about my denied healthcare claim from January.", "early-hint")
    assert result["state"]["phase"] == "VERIFY_ID"
    assert "pathology report" not in result["reply"]
    assert not any(e["kind"] == "find_my_claims" for e in result["events"])
    assert result["state"]["hint_sources"]["month"]["turn_id"] == "early-hint"
    result = say(api, actor, "1985-03-15", "dob")
    assert result["state"]["phase"] == "VERIFY_ID"
    result = say(api, actor, "4472", "ssn")
    assert result["state"]["phase"] == "PROCESS_CASE"
    assert result["state"]["selected_case_id"] == "CL-2048"


def test_two_january_claims_accept_a_short_creation_year_answer(api):
    actor = session(api)
    result = say(api, actor, SAMPLE.replace("denied healthcare", "healthcare"), "ambiguous")
    assert result["state"]["phase"] == "RESOLVE_INTENT"
    assert "CL-2048" in result["reply"] and "CL-2011" in result["reply"]
    result = say(api, actor, "2025", "creation-year")
    assert result["state"]["phase"] == "PROCESS_CASE"
    assert result["state"]["selected_case_id"] == "CL-2011"


def test_registered_aliases_verify_and_no_claims_does_not_fabricate_one(api):
    actor = session(api)
    result = say(api, actor, "My name is Yaven Li. DOB is 1989-12-03. Email is yawen.li@example.com. What is my claim status?", "aliases")
    assert result["state"]["verified"]
    assert result["state"]["phase"] == "RESOLVE_INTENT"
    assert result["state"]["selected_case_id"] is None
    assert "couldn’t find" in result["reply"]


def test_dob_correction_recloses_gate_and_does_not_disclose_claim(api):
    actor = session(api)
    say(api, actor, SAMPLE, "verified")
    result = say(api, actor, "My DOB is actually 1985-03-16. Why was my claim denied?", "correction")
    assert result["state"]["phase"] == "VERIFY_ID"
    assert not result["state"]["verified"]
    assert "pathology report" not in result["reply"]


def test_policy_number_is_not_a_third_pii_field(api):
    actor = session(api)
    result = say(api, actor, "My name is Margaret Chen, DOB is 1985-03-15, policy POL-9921. Why was my healthcare claim denied?", "two-only")
    assert not result["state"]["verified"]
    assert result["state"]["phase"] == "VERIFY_ID"
    assert "pathology report" not in result["reply"]


def test_emotion_and_repeated_irrelevant_questions_preserve_verification_gate(api):
    actor = session(api)
    result = say(api, actor, "This is ridiculous. I already told you who I am. Just tell me why my claim was denied.", "upset")
    assert result["reply"].startswith("I hear how upsetting")
    assert "pathology report" not in result["reply"]
    for index in range(3):
        result = say(api, actor, "What is RL?", f"irrelevant-{index}")
        assert result["state"]["phase"] == "VERIFY_ID"
    assert result["state"]["status"] == "handoff_offered"
    result = say(api, actor, "yes", "human")
    assert result["state"]["status"] == "handoff_requested"
    assert not result["state"]["verified"]


def test_representative_cannot_bypass_on_a_later_plain_turn(api):
    actor = session(api)
    result = say(api, actor, "I'm calling for my mother. " + SAMPLE, "representative")
    assert not result["state"]["verified"]
    result = say(api, actor, "Please continue with the denial reason.", "continue")
    assert not result["state"]["verified"]
    assert "pathology report" not in result["reply"]


def test_sending_before_post_does_not_execute_email(api):
    actor = session(api)
    say(api, actor, SAMPLE, "verified")
    result = say(api, actor, "Please send me an email summary.", "early-send")
    assert result["state"]["phase"] == "PROCESS_CASE"
    assert result["state"]["email_status"] == "not_offered"
    assert result["email_summary"] is None
    assert not any(e["kind"] == "email_simulated" for e in result["events"])


def test_missing_wrong_and_cross_session_bearers_cannot_read_or_change_sessions(api):
    first, second = session(api), session(api)
    path = f"/api/sessions/{first[0]['session_id']}"
    assert api[0].get(path).status_code == 401
    assert api[0].get(path, headers={"Authorization": "Bearer wrong-token"}).status_code == 404
    assert api[0].get(path, headers=second[1]).status_code == 404
    assert api[0].get(path + "/trace", headers=second[1]).status_code == 404
    assert api[0].post(path + "/messages", headers=second[1], json={"message": SAMPLE, "turn_id": "unauthorized"}).status_code == 404
    assert api[0].post(path + "/model", headers=second[1], json=LIVE).status_code == 404
    assert api[0].delete(path + "/model", headers=second[1]).status_code == 404
    original = api[0].get(path, headers=first[1]).json()
    assert original["state"]["phase"] == "VERIFY_ID"


def test_duplicate_turn_is_idempotent_and_conflicting_reuse_is_rejected(api):
    actor = session(api)
    first = say(api, actor, SAMPLE, "same-id")
    second = say(api, actor, SAMPLE, "same-id")
    assert second == first
    assert post(api, actor, "Different text", "same-id").status_code == 409
    persisted = api[0].get(f"/api/sessions/{actor[0]['session_id']}", headers=actor[1]).json()
    assert len(persisted["messages"]) == 3


def test_concurrent_duplicate_turns_commit_once(api):
    actor = session(api)
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(lambda _: post(api, actor, SAMPLE, "concurrent"), range(4)))
    assert all(response.status_code == 200 for response in responses)
    assert all(response.json() == responses[0].json() for response in responses)
    with sqlite3.connect(api[2]["database"]) as db:
        assert db.execute("SELECT count(*) FROM turns").fetchone()[0] == 1


def test_validation_rejects_client_authority_and_invalid_messages(api):
    actor = session(api)
    path = f"/api/sessions/{actor[0]['session_id']}/messages"
    for body in [{"message": "", "turn_id": "empty"}, {"message": " ", "turn_id": "spaces"},
                 {"message": "hello", "turn_id": "extra", "verified": True},
                 {"message": "x" * 4001, "turn_id": "too-long"}]:
        assert api[0].post(path, headers=actor[1], json=body).status_code == 422
    assert api[0].post("/api/sessions", json={"mode": "offline", "demo_date": "2026-02-31"}).status_code == 422


def test_raw_pii_and_model_keys_are_not_persisted_or_returned(api):
    actor = session(api)
    response = say(api, actor, SAMPLE, "sensitive")
    encoded = json.dumps(response)
    for secret in ["1985-03-15", "4472", "Margaret Chen", "POL-9921"]:
        assert secret not in encoded
    model_path = f"/api/sessions/{actor[0]['session_id']}/model"
    configured = api[0].post(model_path, headers=actor[1], json=LIVE)
    assert configured.status_code == 200
    assert LIVE["api_key"] not in configured.text
    with sqlite3.connect(api[2]["database"]) as db:
        persisted = " ".join(str(row) for table in ["sessions", "turns", "email_outbox"] for row in db.execute(f"SELECT * FROM {table}"))
    for secret in [LIVE["api_key"], "1985-03-15", "4472", "Margaret Chen", "POL-9921", actor[0]["access_token"]]:
        assert secret not in persisted
    assert actor[0]["session_id"] in api[1].state.credentials
    cleared = api[0].delete(model_path, headers=actor[1])
    assert cleared.status_code == 200 and cleared.json()["state"]["model_mode"] == "offline"
    assert actor[0]["session_id"] not in api[1].state.credentials


def test_custom_endpoint_cannot_borrow_deployment_key(api):
    api[1].state.settings["api_key"] = "deployment-key-must-not-leak"
    api[1].state.settings["model"] = "test-model"
    response = api[0].post("/api/sessions", json={"mode": "live", "base_url": "https://different.example/v1", "model": "test-model"})
    assert response.status_code == 400
    assert "deployment-key" not in response.text


def test_tool_failure_rolls_back_state_and_returns_safe_error(api, monkeypatch):
    actor = session(api)

    def fail(*args, **kwargs):
        raise RuntimeError("database-password-private-tool-detail")

    monkeypatch.setattr(api[1].state.harness.repo, "guarded_claims", fail)
    response = post(api, actor, SAMPLE, "tool-failure")
    assert response.status_code == 502
    assert "database-password" not in response.text
    persisted = api[0].get(f"/api/sessions/{actor[0]['session_id']}", headers=actor[1]).json()
    assert persisted["state"]["phase"] == "VERIFY_ID"
    assert not persisted["state"]["verified"]
    assert len(persisted["messages"]) == 1
    assert api[1].state.identities[actor[0]["session_id"]][0] == {}


def test_model_failure_does_not_commit_a_turn(api, monkeypatch):
    actor = session(api, **LIVE)

    async def fail(*args, **kwargs):
        raise ModelError("The configured model provider was unavailable.")

    monkeypatch.setattr(main, "analyze_turn", fail)
    response = post(api, actor, SAMPLE, "model-failure")
    assert response.status_code == 502
    assert LIVE["api_key"] not in response.text
    persisted = api[0].get(f"/api/sessions/{actor[0]['session_id']}", headers=actor[1]).json()
    assert len(persisted["messages"]) == 1
    assert persisted["state"]["phase"] == "VERIFY_ID"


def test_expired_verification_recloses_gate_before_new_claim_question(api):
    actor = session(api)
    say(api, actor, SAMPLE, "verified")
    internal = api[1].state.store.load(actor[0]["session_id"], actor[0]["access_token"])
    internal["state"]["verified_at"] = time.time() - 3601
    api[1].state.store.save(actor[0]["session_id"], internal)
    result = say(api, actor, "What documents do I need?", "expired")
    assert result["state"]["phase"] == "VERIFY_ID"
    assert not result["state"]["verified"]
    assert "pathology report" not in result["reply"]


def test_expired_credentials_require_explicit_reconnection(api):
    actor = session(api, **LIVE)
    key = actor[0]["session_id"]
    config, _ = api[1].state.credentials[key]
    api[1].state.credentials[key] = (config, time.monotonic() - 1)
    response = post(api, actor, "hello", "credentials-expired")
    assert response.status_code == 409
    assert "Reconnect" in response.text
    assert LIVE["api_key"] not in response.text


def test_past_business_date_explains_expired_appeal(api):
    actor = session(api, demo_date="2026-09-21")
    result = say(api, actor, SAMPLE, "expired-deadline")
    assert "2026-03-18" in result["reply"]
    assert "has passed" in result["reply"]
    assert "late appeal" in result["reply"]
    assert "within a week" not in result["reply"]


def test_email_service_failure_has_no_outbox_side_effect(api):
    api[1].state.harness.email_failure = True
    actor = session(api)
    reach_post(api, actor)
    result = say(api, actor, "Yes, please send the summary.", "email-failure")
    assert result["state"]["email_status"] == "failed"
    assert "Nothing was sent" in result["reply"]
    with sqlite3.connect(api[2]["database"]) as db:
        assert db.execute("SELECT count(*) FROM email_outbox").fetchone()[0] == 0
    result = say(api, actor, "skip", "skip-failed")
    assert result["state"]["status"] == "completed"
