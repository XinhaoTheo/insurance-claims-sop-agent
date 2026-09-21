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


@pytest.mark.parametrize("message", ["What is in the summary?", "Send it if my claim gets approved", "I do not want email", "Maybe later"])
def test_model_cannot_grant_sending_consent_without_affirmative_caller_text(message):
    harness, snapshot, identity = ready_for_post()
    harness.run(snapshot, TurnAnalysis(email_choice="send"), identity, message, "untrusted-model-consent")
    assert snapshot["state"]["email_status"] == "awaiting_choice"
    assert not any(e["kind"] == "email_simulated" for e in snapshot["events"])


def test_deployment_key_is_never_forwarded_to_a_caller_endpoint(tmp_path):
    app = create_app({"database": str(tmp_path / "db"), "api_key": "deployment-secret", "model": "model-x", "base_url": "https://trusted.test/v1"})
    with TestClient(app) as client:
        result = client.post("/api/sessions", json={"mode": "live", "base_url": "https://untrusted.test/v1", "model": "model-x"})
        assert result.status_code == 400
        assert "deployment-secret" not in result.text
