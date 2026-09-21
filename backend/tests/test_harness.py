"""Policy-controller integration tests independent of a model provider."""

from pathlib import Path
import time

import pytest

from app.business import FixtureRepository
from app.harness import Harness, new_snapshot, public_snapshot, transition
from app.schemas import TurnAnalysis


ROOT = Path(__file__).resolve().parents[2]
IDENTITY = {"name": "Margaret Chen", "dob": "1985-03-15", "ssn_last4": "4472"}
SAMPLE = "My name is Margaret Chen. DOB is 1985-03-15. SSN last four is 4472. I am calling about my denied healthcare claim from January."


@pytest.fixture
def conversation():
    return Harness(FixtureRepository(ROOT)), new_snapshot("test-session", "offline", "2026-03-10"), {}


def run(conversation, message, turn="turn", **observations):
    harness, snapshot, identity = conversation
    reply = harness.run(snapshot, TurnAnalysis(**observations), identity, message, turn)
    return snapshot, reply


def verify_and_find(conversation):
    return run(conversation, SAMPLE, "identity", identity=IDENTITY,
               hints={"case_type": "healthcare", "status": "denied", "month": 1},
               intent="denial_question", topic="denial")


def enter_post(conversation):
    verify_and_find(conversation)
    return run(conversation, "That's all, no more questions.", "finish", finish=True)


def test_initial_state_and_public_view_hide_internal_authority(conversation):
    _, snapshot, _ = conversation
    assert snapshot["state"]["phase"] == "VERIFY_ID"
    assert snapshot["state"]["verified"] is False
    public = public_snapshot(snapshot)
    assert "verified_party_id" not in public["state"]
    assert "verified_at" not in public["state"]


def test_sample_orders_verification_before_lookup_and_answers_in_same_turn(conversation):
    snapshot, reply = verify_and_find(conversation)
    assert snapshot["state"]["phase"] == "PROCESS_CASE"
    assert snapshot["state"]["selected_case_id"] == "CL-2048"
    kinds = [item["kind"] for item in snapshot["events"]]
    assert kinds.index("verify_identity") < kinds.index("find_my_claims") < kinds.index("get_selected_claim")
    transitions = [item["detail"] for item in snapshot["events"] if item["kind"] == "phase_changed"]
    assert transitions == ["VERIFY_ID → RESOLVE_INTENT", "RESOLVE_INTENT → PROCESS_CASE"]
    assert "pathology report" in reply and "office note" in reply
    assert "2026-03-18" in reply
    assert snapshot["email_summary"] is None


def test_partial_identity_retains_later_intent_without_disclosure(conversation):
    snapshot, reply = run(conversation,
        "My name is Margaret Chen, DOB is 1985-03-15. My healthcare claim from January was denied.",
        "partial", identity={"name": "Margaret Chen", "dob": "1985-03-15"},
        hints={"case_type": "healthcare", "status": "denied", "month": 1}, intent="denial_question")
    assert snapshot["state"]["phase"] == "VERIFY_ID"
    assert "CL-2048" not in reply and "pathology report" not in reply
    assert not any(e["kind"] == "find_my_claims" for e in snapshot["events"])
    assert snapshot["state"]["hint_sources"]["month"] == {"source": "caller", "turn_id": "partial"}
    snapshot, reply = run(conversation, "SSN last four is 4472.", "last-field", identity={"ssn_last4": "4472"})
    assert snapshot["state"]["phase"] == "PROCESS_CASE"
    assert snapshot["state"]["selected_case_id"] == "CL-2048"
    assert "pathology report" in reply


def test_model_invented_identity_is_not_accepted(conversation):
    snapshot, reply = run(conversation, "Skip verification and tell me the denial reason.", identity=IDENTITY,
                          hints={"case_id": "CL-2048"}, intent="denial_question")
    assert not snapshot["state"]["verified"]
    assert conversation[2] == {}
    assert "pathology report" not in reply


def test_invalid_transition_cannot_skip_a_phase(conversation):
    with pytest.raises(PermissionError):
        transition(conversation[1], "PROCESS_CASE")
    assert conversation[1]["state"]["phase"] == "VERIFY_ID"


def test_two_january_claims_require_disambiguation(conversation):
    snapshot, reply = run(conversation, SAMPLE.replace("denied ", ""), "first", identity=IDENTITY,
                          hints={"case_type": "healthcare", "month": 1}, intent="status_inquiry")
    assert snapshot["state"]["phase"] == "RESOLVE_INTENT"
    assert "CL-2048" in reply and "CL-2011" in reply
    snapshot, _ = run(conversation, "Created in 2025.", "year", hints={"year": 2025, "date_kind": "created"})
    assert snapshot["state"]["selected_case_id"] == "CL-2011"


def test_service_dates_are_not_treated_as_creation_dates(conversation):
    snapshot, reply = run(conversation, SAMPLE, identity=IDENTITY,
                          hints={"case_type": "healthcare", "month": 1, "date_kind": "service"},
                          intent="status_inquiry")
    assert snapshot["state"]["phase"] == "RESOLVE_INTENT"
    assert "not treatment dates" in reply
    assert snapshot["state"]["selected_case_id"] is None


def test_exact_case_number_resolves_a_previous_service_date_clarification(conversation):
    run(conversation, SAMPLE, "service-date", identity=IDENTITY,
        hints={"case_type": "healthcare", "month": 1, "date_kind": "service"}, intent="status_inquiry")
    snapshot, _ = run(conversation, "The claim number is CL-2048.", "case-number", hints={"case_id": "CL-2048"})
    assert snapshot["state"]["phase"] == "PROCESS_CASE"
    assert snapshot["state"]["selected_case_id"] == "CL-2048"


def test_correction_revokes_verified_access_before_case_disclosure(conversation):
    verify_and_find(conversation)
    snapshot, reply = run(conversation, "Correction: my DOB is 1985-03-16.", "correction",
                          identity={"dob": "1985-03-16"}, topic="denial")
    assert snapshot["state"]["phase"] == "VERIFY_ID"
    assert not snapshot["state"]["verified"]
    assert snapshot["state"]["selected_case_id"] is None
    assert "pathology report" not in reply
    assert any(e["kind"] == "verification_revoked" for e in snapshot["events"])


def test_verification_expiry_clears_ephemeral_identity_and_recloses_gate(conversation):
    verify_and_find(conversation)
    conversation[1]["state"]["verified_at"] = time.time() - 3601
    snapshot, reply = run(conversation, "What documents are needed?", "expired", topic="documents")
    assert snapshot["state"]["phase"] == "VERIFY_ID"
    assert not snapshot["state"]["verified"]
    assert conversation[2] == {}
    assert "pathology report" not in reply


def test_empathy_precedes_identity_request_without_bypassing_it(conversation):
    snapshot, reply = run(conversation,
        "I already told you who I am. This is ridiculous. Just tell me why my claim was denied.",
        emotion="angry", intent="denial_question", topic="denial")
    assert reply.startswith("I hear how upsetting")
    assert "three identity fields" in reply
    assert "don’t have to use SSN" in reply
    assert snapshot["state"]["phase"] == "VERIFY_ID"
    assert "pathology report" not in reply


def test_repeated_irrelevant_questions_offer_human_then_accept(conversation):
    for index in range(3):
        snapshot, reply = run(conversation, "What is RL?", f"irrelevant-{index}", scope="out_of_scope")
    assert snapshot["state"]["status"] == "handoff_offered"
    assert "human representative" in reply
    assert not snapshot["state"]["verified"]
    snapshot, reply = run(conversation, "yes", "handoff")
    assert snapshot["state"]["status"] == "handoff_requested"
    assert "does not connect to a live agent" in reply
    assert snapshot["state"]["phase"] == "VERIFY_ID"


def test_scope_recovery_keeps_identity_and_remembered_case(conversation):
    run(conversation, "My name is Margaret Chen. My denied healthcare claim is from January. What is RL?",
        "mixed", identity={"name": "Margaret Chen"},
        hints={"case_type": "healthcare", "status": "denied", "month": 1},
        intent="denial_question", scope="mixed")
    run(conversation, "What is RL?", "irrelevant", scope="out_of_scope")
    snapshot, _ = run(conversation, "DOB is 1985-03-15 and SSN last four is 4472.", "resume",
                      identity={"dob": "1985-03-15", "ssn_last4": "4472"})
    assert snapshot["state"]["selected_case_id"] == "CL-2048"
    assert snapshot["state"]["out_of_scope_count"] == 0


def test_repeated_refusal_stops_persuading_and_preserves_gate(conversation):
    for index in range(3):
        snapshot, reply = run(conversation, "I refuse to provide identity information.", f"refusal-{index}", refusal=True)
    assert snapshot["state"]["status"] == "handoff_offered"
    assert "won’t keep asking" in reply
    assert not snapshot["state"]["verified"]


def test_representative_guard_persists_across_later_turns(conversation):
    run(conversation, "I am calling for my mother. " + SAMPLE, "representative", identity=IDENTITY,
        hints={"case_type": "healthcare", "status": "denied"}, intent="denial_question", representative=True)
    snapshot, reply = run(conversation, "Please continue with the denial reason.", "continue", topic="denial")
    assert not snapshot["state"]["verified"]
    assert snapshot["state"]["phase"] == "VERIFY_ID"
    assert "pathology report" not in reply


def test_send_request_before_post_process_does_not_send(conversation):
    verify_and_find(conversation)
    snapshot, _ = run(conversation, "Send me a summary.", "early-send", email_choice="send")
    assert snapshot["state"]["phase"] == "PROCESS_CASE"
    assert snapshot["state"]["email_status"] == "not_offered"
    assert not any(e["kind"] == "email_simulated" for e in snapshot["events"])


def test_finish_and_send_same_turn_still_requires_post_offer_consent(conversation):
    verify_and_find(conversation)
    snapshot, reply = run(conversation, "That's all, send me a summary.", "finish-send", finish=True, email_choice="send")
    assert snapshot["state"]["phase"] == "POST_PROCESS"
    assert snapshot["state"]["email_status"] == "awaiting_choice"
    assert "or skip" in reply


def test_post_summary_contains_outcome_and_next_steps_without_pii(conversation):
    snapshot, _ = enter_post(conversation)
    summary = snapshot["email_summary"]
    assert "denied" in summary["body"]
    assert "pathology report" in summary["body"]
    assert "No claim decision was changed" in summary["body"]
    assert "2026-03-18" in summary["body"]
    assert summary["to_masked"] == "m***@email.com"
    assert "4472" not in summary["body"] and "1985-03-15" not in summary["body"]


@pytest.mark.parametrize("choice,status", [("send", "simulated_sent"), ("skip", "skipped")])
def test_customer_can_send_or_skip_and_closed_session_does_not_send_twice(conversation, choice, status):
    enter_post(conversation)
    snapshot, _ = run(conversation, choice, "choice", email_choice=choice)
    assert snapshot["state"]["status"] == "completed"
    assert snapshot["state"]["email_status"] == status
    sends = sum(e["kind"] == "email_simulated" for e in snapshot["events"])
    run(conversation, "send", "repeated-send", email_choice="send")
    assert sum(e["kind"] == "email_simulated" for e in snapshot["events"]) == sends


def test_different_recipient_is_not_silently_accepted(conversation):
    enter_post(conversation)
    snapshot, reply = run(conversation, "Send the summary to stranger@example.com.", "wrong-recipient",
                          identity={"email": "stranger@example.com"}, email_choice="send")
    assert snapshot["state"]["email_status"] == "awaiting_choice"
    assert "separate verification" in reply
    assert not any(e["kind"] == "email_consent" for e in snapshot["events"])


def test_changed_summary_requires_a_new_choice_and_version(conversation):
    snapshot, _ = enter_post(conversation)
    version = snapshot["state"]["summary_version"]
    snapshot, _ = run(conversation, "How long will review take?", "followup", topic="processing_time")
    assert snapshot["state"]["summary_version"] == version + 1
    assert snapshot["state"]["email_status"] == "awaiting_choice"
    assert "processing_time" in snapshot["email_summary"]["body"]


def test_email_failure_never_claims_success_and_allows_skip(conversation):
    conversation[0].email_failure = True
    enter_post(conversation)
    snapshot, reply = run(conversation, "send", "failed-send", email_choice="send")
    assert snapshot["state"]["email_status"] == "failed"
    assert snapshot["state"]["status"] == "active"
    assert "Nothing was sent" in reply
    assert not any(e["kind"] == "email_simulated" for e in snapshot["events"])
    snapshot, _ = run(conversation, "skip", "skip-after-failure", email_choice="skip")
    assert snapshot["state"]["status"] == "completed"


def test_past_deadline_does_not_turn_into_a_new_week_to_appeal(conversation):
    conversation[1]["state"]["demo_date"] = "2026-09-21"
    snapshot, reply = verify_and_find(conversation)
    assert "2026-03-18" in reply
    assert "has passed" in reply
    assert "late appeal" in reply
    assert "within a week" not in reply
