from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.business import FixtureRepository
from app.harness import Harness, new_snapshot
from app.llm import analyze_turn
from app.main import create_app
from app.schemas import TurnAnalysis

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = "My name is Margaret Chen. DOB is 1985-03-15. SSN last four is 4472. My denied healthcare claim was from January."


async def test_natural_dob_correction_is_extracted_after_verification():
    observation = await analyze_turn("My DOB is actually 1985-03-16.", {"phase": "PROCESS_CASE"}, {"mode": "offline"})
    assert observation.identity.dob == "1985-03-16"


def ready_for_post():
    harness = Harness(FixtureRepository(ROOT))
    snapshot, identity = new_snapshot("sample", "offline", "2026-03-10"), {}
    harness.run(snapshot, TurnAnalysis(identity={"name": "Margaret Chen", "dob": "1985-03-15", "ssn_last4": "4472"},
                hints={"case_type": "healthcare", "status": "denied"}, intent="denial_question"), identity, SAMPLE, "verify")
    harness.run(snapshot, TurnAnalysis(finish=True), identity, "That's all", "finish")
    return harness, snapshot, identity


@pytest.mark.parametrize("message", ["What is in the summary?", "Send it if my claim gets approved", "I do not want email", "Maybe later", "Can you explain how to send me the email summary", "I am worried you will send me email", "Send me the summary only after I confirm the address", "Send me nothing", "Send me the summary tomorrow", "发送邮件但是先不要发"])
def test_model_cannot_grant_sending_consent_without_affirmative_caller_text(message):
    harness, snapshot, identity = ready_for_post()
    harness.run(snapshot, TurnAnalysis(email_choice="send"), identity, message, "untrusted-model-consent")
    assert snapshot["state"]["email_status"] == "awaiting_choice"
    assert not any(e["kind"] == "email_simulated" for e in snapshot["events"])


@pytest.mark.parametrize("message", ["Yes, send me the summary.", "Yes, send the email summary to my verified email.", "Please email me a summary", "Send it to my registered email now", "请发送邮件总结"])
def test_explicit_immediate_consent_is_accepted(message):
    harness, snapshot, identity = ready_for_post()
    harness.run(snapshot, TurnAnalysis(email_choice="send"), identity, message, "consent")
    assert snapshot["state"]["email_status"] == "simulated_sent"


@pytest.mark.parametrize("ssn", ["123-45-6789", "123 45 6789", "123456789"])
def test_accidental_full_ssn_is_not_persisted(tmp_path, ssn):
    app = create_app({"database": str(tmp_path / "db")})
    with TestClient(app) as client:
        session = client.post("/api/sessions", json={"mode": "offline"}).json()
        headers = {"Authorization": "Bearer " + session["access_token"]}
        result = client.post(f"/api/sessions/{session['session_id']}/messages", headers=headers,
                             json={"message": f"My SSN is {ssn}. I need claim help.", "turn_id": "full-ssn"})
        assert result.status_code == 200
        snapshot = app.state.store.load(session["session_id"], session["access_token"])
        assert ssn not in str(snapshot)
        assert ssn not in result.text


def test_deployment_key_is_never_forwarded_to_a_caller_endpoint(tmp_path):
    app = create_app({"database": str(tmp_path / "db"), "api_key": "deployment-secret", "model": "model-x", "base_url": "https://trusted.test/v1"})
    with TestClient(app) as client:
        result = client.post("/api/sessions", json={"mode": "live", "base_url": "https://untrusted.test/v1", "model": "model-x"})
        assert result.status_code == 400
        assert "deployment-secret" not in result.text


def test_original_natural_birthdate_is_not_persisted(tmp_path):
    path = tmp_path / "db"
    app = create_app({"database": str(path)})
    with TestClient(app) as client:
        session = client.post("/api/sessions", json={"mode": "offline"}).json()
        headers = {"Authorization": "Bearer " + session["access_token"]}
        message = SAMPLE.replace("1985-03-15", "March 15, 1985")
        result = client.post(f"/api/sessions/{session['session_id']}/messages", headers=headers, json={"message": message, "turn_id": "birthdate"})
        assert result.json()["state"]["verified"]
        assert "March 15, 1985" not in result.text
        snapshot = app.state.store.load(session["session_id"], session["access_token"])
        assert "March 15, 1985" not in str(snapshot)


def test_old_turn_replay_and_verification_expiry_preserve_completed_session(tmp_path):
    import time
    app = create_app({"database": str(tmp_path / "db")})
    with TestClient(app) as client:
        session = client.post("/api/sessions", json={"mode": "offline"}).json()
        url = f"/api/sessions/{session['session_id']}"
        headers = {"Authorization": "Bearer " + session["access_token"]}
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
