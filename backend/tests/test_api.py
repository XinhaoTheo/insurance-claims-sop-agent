"""HTTP acceptance tests for the complete local demo and its safety gates."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import time

from fastapi.testclient import TestClient
import pytest

from app import main
from app.llm import ModelError
from app.schemas import ModelConfig


ROOT = Path(__file__).resolve().parents[2]
SAMPLE = "I’m the policyholder. My name is Margaret Chen, policy POL-9921. I’m calling about my denied healthcare claim from January. DOB is 1985-03-15, SSN last four is 4472."
LIVE = {"api_key": "test-provider-secret-123", "base_url": "https://example.test/v1", "model": "test-model"}

SAMPLE_ANALYSIS = {"identity": {"name": "margaret chen", "dob": "1985-03-15", "ssn_last4": "4472", "policy_number": "POL-9921"}, "hints": {"case_type": "healthcare", "status": "denied", "month": 1}, "intent": "denial_question", "topic": "denial"}


@pytest.fixture
def api(tmp_path, model_observations):
    config = {"database": str(tmp_path / "test.db"), "fixtures": ROOT / "fixtures",
              "static": tmp_path / "static", "demo_date": "2026-03-10",
              "model_config": ModelConfig(), "email_failure": False,
              "secret_ttl": 3600}
    app = main.create_app(config)
    with TestClient(app) as client:
        yield client, app, config, model_observations


def session(api, **payload):
    response = api[0].post("/api/sessions", json={**LIVE, **payload})
    assert response.status_code == 201, response.text
    created = response.json()
    return created, {"Authorization": "Bearer " + created["access_token"]}


def post(api, actor, message, turn_id, analysis=None, *, caller_action=None):
    if analysis is not None:
        api[3].add(message, analysis)
    snapshot, headers = actor
    payload = {"message": message, "turn_id": turn_id}
    if caller_action is not None:
        payload["caller_action"] = caller_action
    return api[0].post(f"/api/sessions/{snapshot['session_id']}/messages", headers=headers, json=payload)


def say(api, actor, message, turn_id, analysis=None, *, caller_action=None):
    response = post(api, actor, message, turn_id, analysis, caller_action=caller_action)
    assert response.status_code == 200, response.text
    return response.json()


def reach_post(api, actor):
    first = say(api, actor, SAMPLE, "identity", SAMPLE_ANALYSIS)
    assert first["state"]["phase"] == "PROCESS_CASE"
    response = say(api, actor, "That's all, no more questions.", "finish", {"finish": True})
    assert response["state"]["phase"] == "POST_PROCESS"
    return response


def test_health_and_configuration(api):
    response = api[0].get("/health")
    assert response.status_code == 200 and response.json()["email_mode"] == "mock"
    response = api[0].get("/api/config")
    assert response.json()["configured"] is False
    assert "default_mode" not in response.json()
    assert "api_key" not in response.json()


def test_complete_sample_and_send_have_ordered_stages_and_one_outbox_item(api):
    actor = session(api)
    result = say(api, actor, SAMPLE, "identity", SAMPLE_ANALYSIS)
    assert result["state"]["phase"] == "PROCESS_CASE"
    assert result["state"]["verified"]
    assert result["state"]["selected_case_id"] == "CL-2048"
    assert result["state"]["case_hints"]["month"] == 1
    assert "pathology report" in result["reply"] and "office note" in result["reply"]
    assert "verified_party_id" not in result["state"]
    phases = [e["detail"] for e in result["events"] if e["kind"] == "phase_changed"]
    assert phases == ["VERIFY_ID → RESOLVE_INTENT", "RESOLVE_INTENT → PROCESS_CASE"]
    result = say(api, actor, "That's all, no more questions.", "finish", {"finish": True})
    assert result["state"]["phase"] == "POST_PROCESS"
    assert result["state"]["email_status"] == "awaiting_choice"
    assert "No claim decision was changed" in result["email_summary"]["body"]
    sent = say(api, actor, "Yes, please send the summary.", "send", {"email_choice": "send"})
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
    result = say(api, actor, "No thanks", "skip", {"email_choice": "skip"})
    assert result["state"]["email_status"] == "skipped"
    assert result["state"]["status"] == "completed"
    with sqlite3.connect(api[2]["database"]) as db:
        assert db.execute("SELECT count(*) FROM email_outbox").fetchone()[0] == 0


def test_partial_answers_remember_intent_and_only_query_after_verification(api):
    actor = session(api)
    result = say(api, actor, "My name is Margaret Chen. I'm calling about my denied healthcare claim from January.", "early-hint", {"identity": {"name": "margaret chen"}, "hints": {"case_type": "healthcare", "status": "denied", "month": 1}, "intent": "denial_question"})
    assert result["state"]["phase"] == "VERIFY_ID"
    assert "pathology report" not in result["reply"]
    assert not any(e["kind"] == "find_my_claims" for e in result["events"])
    assert result["state"]["hint_sources"]["month"]["turn_id"] == "early-hint"
    result = say(api, actor, "1985-03-15", "dob", {"identity": {"dob": "1985-03-15"}})
    assert result["state"]["phase"] == "VERIFY_ID"
    result = say(api, actor, "4472", "ssn", {"identity": {"ssn_last4": "4472"}})
    assert result["state"]["phase"] == "PROCESS_CASE"
    assert result["state"]["selected_case_id"] == "CL-2048"


def test_two_january_claims_accept_a_short_creation_year_answer(api):
    actor = session(api)
    result = say(api, actor, SAMPLE.replace("denied healthcare", "healthcare"), "ambiguous", {**SAMPLE_ANALYSIS, "hints": {"case_type": "healthcare", "month": 1}, "intent": "status_inquiry", "topic": "overview"})
    assert result["state"]["phase"] == "RESOLVE_INTENT"
    assert "CL-2048" in result["reply"] and "CL-2011" in result["reply"]
    result = say(api, actor, "2025", "creation-year", {"hints": {"year": 2025, "date_kind": "created"}})
    assert result["state"]["phase"] == "PROCESS_CASE"
    assert result["state"]["selected_case_id"] == "CL-2011"


def test_registered_aliases_verify_and_no_claims_does_not_fabricate_one(api):
    actor = session(api)
    result = say(api, actor, "My name is Yaven Li. DOB is 1989-12-03. Email is yawen.li@example.com. What is my claim status?", "aliases", {"identity": {"name": "yaven li", "dob": "1989-12-03", "email": "yawen.li@example.com"}, "intent": "status_inquiry"})
    assert result["state"]["verified"]
    assert result["state"]["phase"] == "RESOLVE_INTENT"
    assert result["state"]["selected_case_id"] is None
    assert "couldn't find" in result["reply"]


def test_dob_correction_recloses_gate_and_does_not_disclose_claim(api):
    actor = session(api)
    say(api, actor, SAMPLE, "verified", SAMPLE_ANALYSIS)
    result = say(api, actor, "My DOB is actually 1985-03-16. Why was my claim denied?", "correction", {"identity": {"dob": "1985-03-16"}, "intent": "denial_question"})
    assert result["state"]["phase"] == "VERIFY_ID"
    assert not result["state"]["verified"]
    assert "pathology report" not in result["reply"]


def test_policy_number_is_not_a_third_pii_field(api):
    actor = session(api)
    result = say(api, actor, "My name is Margaret Chen, DOB is 1985-03-15, policy POL-9921. Why was my healthcare claim denied?", "two-only", {"identity": {"name": "margaret chen", "dob": "1985-03-15", "policy_number": "POL-9921"}, "intent": "denial_question"})
    assert not result["state"]["verified"]
    assert result["state"]["phase"] == "VERIFY_ID"
    assert "pathology report" not in result["reply"]


def test_unusable_correction_stays_blocked_across_requests_and_retry(api):
    actor = session(api)
    say(api, actor, SAMPLE, "verified", SAMPLE_ANALYSIS)
    message = "Actually my DOB is 1985-02-31. Why was my claim denied?"
    result = say(api, actor, message, "unusable-correction",
                 {"identity": {"dob": None}, "identity_evidence": {"dob": "1985-02-31"}, "intent": "denial_question"})
    assert not result["state"]["verified"]
    assert result["state"]["phase"] == "VERIFY_ID"
    assert "1985-02-31" not in result["messages"][-2]["content"]
    assert api[1].state.identities[actor[0]["session_id"]][0]["dob"] is None
    assert say(api, actor, message, "unusable-correction") == result
    loaded = api[0].get(f"/api/sessions/{actor[0]['session_id']}", headers=actor[1]).json()
    assert not loaded["state"]["verified"]
    result = say(api, actor, "Tell me the denial reason.", "still-blocked", {"intent": "denial_question"})
    assert not result["state"]["verified"]
    assert "pathology report" not in result["reply"]
    result = say(api, actor, "I meant March 15, 1985.", "corrected",
                 {"identity": {"dob": "1985-03-15"}, "identity_evidence": {"dob": "March 15, 1985"}})
    assert result["state"]["verified"]
    assert result["state"]["selected_case_id"] == "CL-2048"


def test_unusable_email_recipient_does_not_authorize_sending(api):
    actor = session(api)
    reach_post(api, actor)
    result = say(api, actor, "Send the summary to broken@.", "unusable-recipient",
                 {"identity": {"email": None}, "identity_evidence": {"email": "broken@"}, "email_choice": "send"})
    assert result["state"]["phase"] == "POST_PROCESS"
    assert result["state"]["verified"]
    assert result["state"]["email_status"] == "awaiting_choice"
    assert not any(e["kind"] == "email_consent" for e in result["events"])
    with sqlite3.connect(api[2]["database"]) as db:
        assert db.execute("SELECT count(*) FROM email_outbox").fetchone()[0] == 0
    result = say(api, actor, "Send it to my registered address instead.", "registered-recipient", {"email_choice": "send"})
    assert result["state"]["email_status"] == "simulated_sent"


def test_emotion_and_repeated_irrelevant_questions_preserve_verification_gate(api):
    actor = session(api)
    result = say(api, actor, "This is ridiculous. I already told you who I am. Just tell me why my claim was denied.", "upset", {"emotion": "angry", "intent": "denial_question"})
    assert result["reply"].startswith("I hear how upsetting")
    assert "pathology report" not in result["reply"]
    for index in range(3):
        result = say(api, actor, "What is RL?", f"irrelevant-{index}", {"scope": "out_of_scope"})
        assert result["state"]["phase"] == "VERIFY_ID"
    assert result["state"]["status"] == "handoff_offered"
    result = say(api, actor, "yes", "human", {"human_requested": True})
    assert result["state"]["status"] == "handoff_requested"
    assert not result["state"]["verified"]


def test_representative_cannot_bypass_on_a_later_plain_turn(api):
    actor = session(api)
    result = say(api, actor, "I'm calling for my mother. " + SAMPLE, "representative", {**SAMPLE_ANALYSIS, "representative": True})
    assert not result["state"]["verified"]
    result = say(api, actor, "Please continue with the denial reason.", "continue", {"topic": "denial"})
    assert not result["state"]["verified"]
    assert "pathology report" not in result["reply"]


def test_sending_before_post_does_not_execute_email(api):
    actor = session(api)
    say(api, actor, SAMPLE, "verified", SAMPLE_ANALYSIS)
    result = say(api, actor, "Please send me an email summary.", "early-send", {"email_choice": "send"})
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
    first = say(api, actor, SAMPLE, "same-id", SAMPLE_ANALYSIS)
    second = say(api, actor, SAMPLE, "same-id")
    assert second == first
    assert post(api, actor, "Different text", "same-id").status_code == 409
    persisted = api[0].get(f"/api/sessions/{actor[0]['session_id']}", headers=actor[1]).json()
    assert len(persisted["messages"]) == 3


def test_concurrent_duplicate_turns_commit_once(api):
    actor = session(api)
    api[3].add(SAMPLE, SAMPLE_ANALYSIS)
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
                 {"message": "hello", "turn_id": "extra", "verified": True}]:
        assert api[0].post(path, headers=actor[1], json=body).status_code == 422
    assert api[0].post("/api/sessions", json={"demo_date": "2026-02-31"}).status_code == 422


def test_raw_pii_and_model_keys_are_not_persisted_or_returned(api):
    actor = session(api)
    response = say(api, actor, SAMPLE, "sensitive", SAMPLE_ANALYSIS)
    messages_text = " ".join(item["content"] for item in response["messages"])
    for secret in ["1985-03-15", "Margaret Chen", "POL-9921"]:
        assert secret not in messages_text
    assert "4472" not in messages_text and "[ssn_last4 provided]" in messages_text
    model_path = f"/api/sessions/{actor[0]['session_id']}/model"
    configured = api[0].post(model_path, headers=actor[1], json=LIVE)
    assert configured.status_code == 200
    assert LIVE["api_key"] not in configured.text
    with sqlite3.connect(api[2]["database"]) as db:
        persisted = " ".join(str(row) for table in ["sessions", "turns", "email_outbox"] for row in db.execute(f"SELECT * FROM {table}"))
    # Long, unique secrets are safe to search in the whole blob. The four-digit
    # SSN suffix is checked against message text above, because a random token or
    # event timestamp can contain "4472" as a substring without any leak.
    for secret in [LIVE["api_key"], "1985-03-15", "Margaret Chen", "POL-9921", actor[0]["access_token"]]:
        assert secret not in persisted
    assert actor[0]["session_id"] in api[1].state.credentials
    cleared = api[0].delete(model_path, headers=actor[1])
    assert cleared.status_code == 200 and cleared.json()["model_configured"] is False
    assert actor[0]["session_id"] not in api[1].state.credentials


def test_custom_endpoint_cannot_borrow_deployment_key(api):
    api[1].state.settings["model_config"] = ModelConfig(api_key="deployment-key-must-not-leak", model="test-model", base_url="https://example.test/v1")
    response = api[0].post("/api/sessions", json={"base_url": "https://different.example/v1", "model": "test-model"})
    assert response.status_code == 400
    assert "deployment-key" not in response.text


def test_unexpected_tool_failure_does_not_commit_the_turn(api, monkeypatch):
    actor = session(api)

    def fail(*args, **kwargs):
        raise RuntimeError("Fixture service unavailable")

    monkeypatch.setattr(api[1].state.harness.repo, "guarded_claims", fail)
    api[3].add(SAMPLE, SAMPLE_ANALYSIS)
    with TestClient(api[1], raise_server_exceptions=False) as client:
        response = client.post(f"/api/sessions/{actor[0]['session_id']}/messages", headers=actor[1],
                               json={"message": SAMPLE, "turn_id": "tool-failure"})
    assert response.status_code == 500
    persisted = api[0].get(f"/api/sessions/{actor[0]['session_id']}", headers=actor[1]).json()
    assert persisted["state"]["phase"] == "VERIFY_ID"
    assert not persisted["state"]["verified"]
    assert len(persisted["messages"]) == 1
    assert api[1].state.identities[actor[0]["session_id"]][0] == {}
    with sqlite3.connect(api[2]["database"]) as db:
        assert db.execute("SELECT count(*) FROM turns").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM email_outbox").fetchone()[0] == 0


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
    say(api, actor, SAMPLE, "verified", SAMPLE_ANALYSIS)
    internal = api[1].state.store.load(actor[0]["session_id"], actor[0]["access_token"])
    internal["state"]["verified_at"] = time.time() - 3601
    api[1].state.store.save(actor[0]["session_id"], internal)
    result = say(api, actor, "What documents do I need?", "expired", {"topic": "documents"})
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
    assert "Configure a model" in response.text
    assert LIVE["api_key"] not in response.text


def test_past_business_date_explains_expired_appeal(api):
    actor = session(api, demo_date="2026-09-21")
    result = say(api, actor, SAMPLE, "expired-deadline", SAMPLE_ANALYSIS)
    assert "2026-03-18" in result["reply"]
    assert "has passed" in result["reply"]
    assert "late appeal" in result["reply"]
    assert "within a week" not in result["reply"]


def test_email_service_failure_has_no_outbox_side_effect(api):
    api[1].state.harness.email_failure = True
    actor = session(api)
    reach_post(api, actor)
    result = say(api, actor, "Yes, please send the summary.", "email-failure", {"email_choice": "send"})
    assert result["state"]["email_status"] == "failed"
    assert "Nothing was sent" in result["reply"]
    with sqlite3.connect(api[2]["database"]) as db:
        assert db.execute("SELECT count(*) FROM email_outbox").fetchone()[0] == 0
    result = say(api, actor, "skip", "skip-failed", {"email_choice": "skip"})
    assert result["state"]["status"] == "completed"


def test_unconfigured_session_rejects_messages_until_model_is_connected(api):
    response = api[0].post("/api/sessions", json={})
    assert response.status_code == 201
    snapshot = response.json()
    actor = (snapshot, {"Authorization": "Bearer " + snapshot["access_token"]})
    assert snapshot["model_configured"] is False
    assert "model_mode" not in snapshot["state"]
    response = post(api, actor, SAMPLE, "needs-model")
    assert response.status_code == 409
    assert api[3].calls == []
    path = f"/api/sessions/{snapshot['session_id']}"
    persisted = api[0].get(path, headers=actor[1]).json()
    assert persisted["state"]["phase"] == "VERIFY_ID" and len(persisted["messages"]) == 1
    configured = api[0].post(path + "/model", headers=actor[1], json=LIVE)
    assert configured.status_code == 200 and configured.json()["model_configured"] is True
    assert LIVE["api_key"] not in configured.text
    result = say(api, actor, SAMPLE, "needs-model", SAMPLE_ANALYSIS)
    assert result["state"]["phase"] == "PROCESS_CASE"
    assert len(api[3].calls) == 1


def test_disconnect_preserves_conversation_but_cannot_fallback_to_a_parser(api):
    actor = session(api)
    first = say(api, actor, SAMPLE, "verified", SAMPLE_ANALYSIS)
    path = f"/api/sessions/{actor[0]['session_id']}"
    disconnected = api[0].delete(path + "/model", headers=actor[1])
    assert disconnected.status_code == 200 and disconnected.json()["model_configured"] is False
    assert disconnected.json()["messages"] == first["messages"]
    assert post(api, actor, "What documents do I need?", "after-disconnect").status_code == 409
    assert len(api[3].calls) == 1
    anthropic = {"api_protocol": "anthropic", "api_key": "claude-test-key", "model": "claude-test"}
    connected = api[0].post(path + "/model", headers=actor[1], json=anthropic)
    assert connected.status_code == 200 and connected.json()["model_configured"] is True
    assert "claude-test-key" not in connected.text
    result = say(api, actor, "What documents do I need?", "after-disconnect", {"topic": "documents"})
    assert result["state"]["selected_case_id"] == "CL-2048"
    assert api[3].calls[-1]["config"].api_protocol == "anthropic"
    assert api[3].calls[-1]["config"].base_url == "https://api.anthropic.com/v1"


def test_deployment_defaults_configure_a_session_without_exposing_credentials(api):
    api[1].state.settings["model_config"] = ModelConfig(**LIVE)
    public = api[0].get("/api/config")
    assert public.json()["configured"] is True and LIVE["api_key"] not in public.text
    created = api[0].post("/api/sessions", json={})
    assert created.status_code == 201 and created.json()["model_configured"] is True
    assert LIVE["api_key"] not in created.text
    snapshot = created.json()
    config, _ = api[1].state.credentials[snapshot["session_id"]]
    assert config.api_key == LIVE["api_key"]


def test_connection_test_uses_resolved_typed_config_and_does_not_leak_key(api, monkeypatch):
    observed = []

    async def successful_connection(config):
        observed.append(config)
        return {"ok": True, "api_protocol": config.api_protocol, "model": config.model}

    monkeypatch.setattr(main, "test_connection", successful_connection)
    response = api[0].post("/api/models/test", json=LIVE)
    assert response.status_code == 200 and response.json()["ok"]
    assert isinstance(observed[0], ModelConfig) and observed[0].api_key == LIVE["api_key"]
    assert LIVE["api_key"] not in response.text
    assert api[0].post("/api/models/test", json={}).status_code == 400


def test_removed_mode_field_is_not_accepted_as_a_runtime_switch(api):
    assert api[0].post("/api/sessions", json={**LIVE, "mode": "offline"}).status_code == 422
    actor = session(api)
    path = f"/api/sessions/{actor[0]['session_id']}/model"
    assert api[0].post(path, headers=actor[1], json={**LIVE, "mode": "offline"}).status_code == 422


def test_rendered_reply_is_persisted_and_context_contains_only_redacted_conversation(api, monkeypatch):
    actor = session(api)
    spanish_reply = "Necesito tres datos de identidad para consultar su reclamación. Puede elegir cuáles compartir."

    async def render(approved_reply, message, context, config):
        await api[3].render(approved_reply, message, context, config)
        return spanish_reply

    monkeypatch.setattr(main, "render_reply", render)
    first = say(api, actor, "Soy Margaret Chen, nací el 15 de marzo de 1985.", "spanish-identity",
                {"identity": {"name": "margaret chen", "dob": "1985-03-15"},
                 "identity_evidence": {"dob": "15 de marzo de 1985"}})
    assert first["reply"] == spanish_reply
    assert first["messages"][-1]["content"] == spanish_reply
    assert first["state"]["phase"] == "VERIFY_ID"
    assert "pathology report" not in api[3].render_calls[-1]["approved_reply"]
    assert "language" not in first["state"]
    first_caller = first["messages"][-2]["content"]
    say(api, actor, "¿Por qué necesita esos datos?", "spanish-followup", {})
    for call in (api[3].calls[-1], api[3].render_calls[-1]):
        assert call["context"]["previous_assistant"] == spanish_reply
        assert call["context"]["previous_caller"] == first_caller
        assert call["context"]["caller_action"] is None
        assert "language" not in call["context"]
    for call in api[3].render_calls:
        for secret in ("Margaret Chen", "15 de marzo de 1985", "1985-03-15"):
            assert secret not in call["message"]
            assert secret not in str(call["context"])


def test_render_failure_rolls_back_verification_and_same_turn_can_retry(api, monkeypatch):
    actor = session(api)
    session_id, access = actor[0]["session_id"], actor[0]["access_token"]
    before = api[1].state.store.load(session_id, access)
    identity_before = deepcopy(api[1].state.identities[session_id])
    credentials_before = deepcopy(api[1].state.credentials[session_id])

    async def fail(approved_reply, message, context, config):
        assert "pathology report" in approved_reply
        for secret in ("Margaret Chen", "1985-03-15", "4472"):
            assert secret not in message
        raise ModelError("The reply could not be generated. Please retry.")

    monkeypatch.setattr(main, "render_reply", fail)
    response = post(api, actor, SAMPLE, "render-failure", SAMPLE_ANALYSIS)
    assert response.status_code == 502
    assert api[1].state.store.load(session_id, access) == before
    assert api[1].state.identities[session_id] == identity_before
    assert api[1].state.credentials[session_id] == credentials_before
    with sqlite3.connect(api[2]["database"]) as db:
        assert db.execute("SELECT count(*) FROM turns").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM email_outbox").fetchone()[0] == 0
    monkeypatch.setattr(main, "render_reply", api[3].render)
    result = say(api, actor, SAMPLE, "render-failure", SAMPLE_ANALYSIS)
    assert result["state"]["phase"] == "PROCESS_CASE"
    assert len(api[3].calls) == 2
    with sqlite3.connect(api[2]["database"]) as db:
        assert db.execute("SELECT count(*) FROM turns").fetchone()[0] == 1


def test_render_failure_rolls_back_email_send_and_retry_commits_once(api, monkeypatch):
    actor = session(api)
    reach_post(api, actor)
    session_id, access = actor[0]["session_id"], actor[0]["access_token"]
    before = api[1].state.store.load(session_id, access)
    identity_before = deepcopy(api[1].state.identities[session_id])
    credentials_before = deepcopy(api[1].state.credentials[session_id])

    async def fail(approved_reply, message, context, config):
        assert "No real email was sent" in approved_reply
        raise ModelError("The reply could not be generated. Please retry.")

    monkeypatch.setattr(main, "render_reply", fail)
    response = post(api, actor, "Envíame el resumen", "send-retry", {"email_choice": "send"})
    assert response.status_code == 502
    assert api[1].state.store.load(session_id, access) == before
    assert api[1].state.identities[session_id] == identity_before
    assert api[1].state.credentials[session_id] == credentials_before
    with sqlite3.connect(api[2]["database"]) as db:
        assert db.execute("SELECT count(*) FROM turns").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM email_outbox").fetchone()[0] == 0
    monkeypatch.setattr(main, "render_reply", api[3].render)
    sent = say(api, actor, "Envíame el resumen", "send-retry", {"email_choice": "send"})
    assert sent["state"]["email_status"] == "simulated_sent"
    assert sent["state"]["status"] == "completed"
    assert session_id not in api[1].state.credentials
    assert session_id not in api[1].state.identities
    assert say(api, actor, "Envíame el resumen", "send-retry") == sent
    assert len(api[3].calls) == 4
    assert len(api[3].render_calls) == 3
    with sqlite3.connect(api[2]["database"]) as db:
        assert db.execute("SELECT count(*) FROM turns").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM email_outbox").fetchone()[0] == 1


@pytest.mark.parametrize("action,status,outbox_count", [
    ("send_summary", "simulated_sent", 1), ("skip_summary", "skipped", 0),
])
def test_summary_buttons_supply_explicit_choice_after_post_offer(api, action, status, outbox_count):
    actor = session(api)
    before = reach_post(api, actor)
    result = say(api, actor, "Confirm", "button", {"email_choice": "unclear"}, caller_action=action)
    assert result["state"]["email_status"] == status
    assert result["state"]["status"] == "completed"
    assert result["messages"][-2]["caller_action"] == action
    for call in (api[3].calls[-1], api[3].render_calls[-1]):
        assert call["context"]["caller_action"] == action
        assert call["context"]["previous_assistant"] == before["reply"]
    assert say(api, actor, "Confirm", "button", caller_action=action) == result
    other_action = "skip_summary" if action == "send_summary" else "send_summary"
    assert post(api, actor, "Confirm", "button", caller_action=other_action).status_code == 409
    assert post(api, actor, "Confirm", "button").status_code == 409
    with sqlite3.connect(api[2]["database"]) as db:
        assert db.execute("SELECT count(*) FROM email_outbox").fetchone()[0] == outbox_count


@pytest.mark.parametrize("stage", ["VERIFY_ID", "PROCESS_CASE", "entering_post"])
def test_send_button_cannot_bypass_verification_or_post_offer(api, stage):
    actor = session(api)
    if stage != "VERIFY_ID":
        say(api, actor, SAMPLE, "verified", SAMPLE_ANALYSIS)
    observations = {"finish": True} if stage == "entering_post" else {}
    result = say(api, actor, "Confirm", "premature-button", observations, caller_action="send_summary")
    assert result["state"]["email_status"] == ("awaiting_choice" if stage == "entering_post" else "not_offered")
    assert result["state"]["phase"] == ("POST_PROCESS" if stage == "entering_post" else stage)
    assert result["state"]["status"] == "active"
    assert not any(e["kind"] == "email_simulated" for e in result["events"])
    with sqlite3.connect(api[2]["database"]) as db:
        assert db.execute("SELECT count(*) FROM email_outbox").fetchone()[0] == 0


def test_finish_button_requires_an_existing_verified_processing_phase(api):
    actor = session(api)
    blocked = say(api, actor, "Confirm", "early-finish", {}, caller_action="finish_case")
    assert blocked["state"]["phase"] == "VERIFY_ID"
    assert not blocked["state"]["verified"]
    assert blocked["email_summary"] is None
    say(api, actor, SAMPLE, "verified", SAMPLE_ANALYSIS)
    result = say(api, actor, "Confirm", "finish-button", {}, caller_action="finish_case")
    assert result["state"]["phase"] == "POST_PROCESS"
    assert result["state"]["email_status"] == "awaiting_choice"
    assert api[3].calls[-1]["context"]["caller_action"] == "finish_case"
    assert api[3].render_calls[-1]["context"]["caller_action"] == "finish_case"


def test_terminal_session_reuses_its_final_reply_without_models_or_new_turn(api, monkeypatch):
    actor = session(api)
    reach_post(api, actor)
    final_reply = "Entendido, no enviaremos el resumen. La conversación ha terminado."

    async def render(*args):
        return final_reply

    monkeypatch.setattr(main, "render_reply", render)
    completed = say(api, actor, "No envíes el resumen", "skip", {"email_choice": "skip"})
    assert completed["reply"] == final_reply

    async def unexpected(*args):
        pytest.fail("A terminal session must not call either model stage")

    monkeypatch.setattr(main, "analyze_turn", unexpected)
    monkeypatch.setattr(main, "render_reply", unexpected)
    repeated = say(api, actor, "Ahora envíalo", "new-terminal-turn", caller_action="send_summary")
    assert repeated == completed
    with sqlite3.connect(api[2]["database"]) as db:
        assert db.execute("SELECT count(*) FROM turns").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM email_outbox").fetchone()[0] == 0


def test_renderer_output_is_redacted_before_return_and_persistence(api, monkeypatch):
    actor = session(api)

    async def leaky_render(approved_reply, message, context, config):
        return "Margaret Chen, claim CL-2048 is denied; call 650-521-2836 or email margaret@email.com."

    monkeypatch.setattr(main, "render_reply", leaky_render)
    result = say(api, actor, SAMPLE, "reply-redaction", SAMPLE_ANALYSIS)
    for secret in ("Margaret Chen", "650-521-2836", "margaret@email.com"):
        assert secret not in result["reply"]
        assert all(secret not in item["content"] for item in result["messages"])
    assert "CL-2048" in result["reply"]


def test_expired_session_locks_are_reclaimed_while_held_locks_are_kept(api):
    actor = session(api)
    say(api, actor, "hello", "lock-turn", {"intent": "status_inquiry"})
    session_id = actor[0]["session_id"]
    assert session_id in api[1].state.locks
    held, _ = api[1].state.locks[session_id]

    # A held lock must never be reclaimed, even after its recorded expiry.
    held._locked = True
    api[1].state.locks[session_id] = (held, 0)
    api[0].get(f"/api/sessions/{session_id}", headers=actor[1])
    assert session_id in api[1].state.locks
    held._locked = False

    api[1].state.locks[session_id] = (held, 0)
    api[0].get(f"/api/sessions/{session_id}", headers=actor[1])
    assert session_id not in api[1].state.locks
