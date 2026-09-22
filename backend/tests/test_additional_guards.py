from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.business import FixtureRepository
from app.harness import Harness, new_snapshot
from app.main import create_app
from app.schemas import ModelConfig, TurnAnalysis

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = "My name is Margaret Chen. DOB is 1985-03-15. SSN last four is 4472. My denied healthcare claim was from January."


def ready_for_post():
    harness = Harness(FixtureRepository(ROOT))
    snapshot, identity = new_snapshot("sample", "2026-03-10"), {}
    harness.run(snapshot, TurnAnalysis(identity={"name": "Margaret Chen", "dob": "1985-03-15", "ssn_last4": "4472"},
                hints={"case_type": "healthcare", "status": "denied"}, intent="denial_question"), identity, "verify")
    harness.run(snapshot, TurnAnalysis(finish=True), identity, "finish")
    return harness, snapshot, identity


def test_unclear_observation_keeps_email_choice_pending():
    harness, snapshot, identity = ready_for_post()
    harness.run(snapshot, TurnAnalysis(email_choice="unclear"), identity, "no-current-consent")
    assert snapshot["state"]["email_status"] == "awaiting_choice"
    assert not any(e["kind"] == "email_simulated" for e in snapshot["events"])


@pytest.mark.parametrize("ssn", ["123-45-6789", "123 45 6789", "123456789"])
def test_accidental_full_ssn_is_not_persisted(tmp_path, ssn, model_observations):
    app = create_app({"database": str(tmp_path / "db"), "model_config": ModelConfig(api_key="test-secret", model="test-model", base_url="https://example.test/v1")})
    with TestClient(app) as client:
        session = client.post("/api/sessions", json={}).json()
        headers = {"Authorization": "Bearer " + session["access_token"]}
        model_observations.add(f"My SSN is {ssn}. I need claim help.", {"intent": "general_claim_question"})
        result = client.post(f"/api/sessions/{session['session_id']}/messages", headers=headers,
                             json={"message": f"My SSN is {ssn}. I need claim help.", "turn_id": "full-ssn"})
        assert result.status_code == 200
        snapshot = app.state.store.load(session["session_id"], session["access_token"])
        assert ssn not in str(snapshot)
        assert ssn not in result.text


def test_deployment_key_is_never_forwarded_to_a_caller_endpoint(tmp_path):
    app = create_app({"database": str(tmp_path / "db"), "model_config": ModelConfig(api_key="deployment-secret", model="model-x", base_url="https://trusted.test/v1")})
    with TestClient(app) as client:
        result = client.post("/api/sessions", json={"base_url": "https://untrusted.test/v1", "model": "model-x"})
        assert result.status_code == 400
        assert "deployment-secret" not in result.text


@pytest.mark.parametrize("birthdate", ["March 15, 1985", "15 de marzo de 1985", "15 mars 1985", "1985年3月15日", "١٥ مارس ١٩٨٥"])
def test_normalized_birthdate_evidence_is_redacted(tmp_path, model_observations, birthdate):
    path = tmp_path / "db"
    app = create_app({"database": str(path), "model_config": ModelConfig(api_key="test-secret", model="test-model", base_url="https://example.test/v1")})
    with TestClient(app) as client:
        session = client.post("/api/sessions", json={}).json()
        headers = {"Authorization": "Bearer " + session["access_token"]}
        message = SAMPLE.replace("1985-03-15", birthdate)
        model_observations.add(message, {"identity": {"name": "Margaret Chen", "dob": "1985-03-15", "ssn_last4": "4472"}, "identity_evidence": {"dob": birthdate}, "hints": {"case_type": "healthcare", "status": "denied", "month": 1}, "intent": "denial_question"})
        result = client.post(f"/api/sessions/{session['session_id']}/messages", headers=headers, json={"message": message, "turn_id": "birthdate"})
        assert result.status_code == 200, result.text
        assert result.json()["state"]["verified"]
        snapshot = app.state.store.load(session["session_id"], session["access_token"])
        for raw_value in (birthdate, "1985-03-15", "Margaret Chen", "4472"):
            assert raw_value not in str(result.json())
            assert raw_value not in str(snapshot)
            assert raw_value not in model_observations.render_calls[-1]["message"]
        assert "[dob provided]" in snapshot["messages"][-2]["content"]


def test_old_turn_replay_and_verification_expiry_preserve_completed_session(tmp_path, model_observations):
    import time
    app = create_app({"database": str(tmp_path / "db"), "model_config": ModelConfig(api_key="test-secret", model="test-model", base_url="https://example.test/v1")})
    with TestClient(app) as client:
        session = client.post("/api/sessions", json={}).json()
        url = f"/api/sessions/{session['session_id']}"
        headers = {"Authorization": "Bearer " + session["access_token"]}
        model_observations.add(SAMPLE, {"identity": {"name": "Margaret Chen", "dob": "1985-03-15", "ssn_last4": "4472"}, "hints": {"case_type": "healthcare", "status": "denied", "month": 1}, "intent": "denial_question"})
        model_observations.add("That's all", {"finish": True})
        model_observations.add("Skip the email", {"email_choice": "skip"})
        for turn_id, message in (("initial", SAMPLE), ("finish", "That's all"), ("skip", "Skip the email")):
            response = client.post(url + "/messages", headers=headers, json={"message": message, "turn_id": turn_id})
            assert response.status_code == 200
        original_summary = response.json()["email_summary"]
        replay = client.post(url + "/messages", headers=headers, json={"message": SAMPLE, "turn_id": "initial"}).json()
        assert replay["state"]["status"] == "completed"
        snapshot = app.state.store.load(session["session_id"], session["access_token"])
        snapshot["state"]["verified_at"] = time.time() - 3601
        app.state.store.save(session["session_id"], snapshot)
        restored = client.get(url, headers=headers).json()
        assert restored["state"]["status"] == "completed"
        assert restored["state"]["phase"] == "POST_PROCESS"
        assert restored["email_summary"] == original_summary
