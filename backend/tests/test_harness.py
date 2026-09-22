"""Policy-controller integration tests independent of a model provider."""

from pathlib import Path

import pytest

from app.business import FixtureRepository
from app.harness import Harness, new_snapshot, public_snapshot, transition
from app.schemas import TurnAnalysis


ROOT = Path(__file__).resolve().parents[2]
IDENTITY = {"name": "margaret chen", "dob": "1985-03-15", "ssn_last4": "4472"}


@pytest.fixture
def conversation():
    return Harness(FixtureRepository(ROOT)), new_snapshot("test-session", "2026-03-10"), {}


def run(conversation, turn="turn", **observations):
    harness, snapshot, identity = conversation
    reply = harness.run(snapshot, TurnAnalysis(**observations), identity, turn)
    return snapshot, reply


def verify_and_find(conversation):
    return run(conversation, "identity", identity=IDENTITY,
               hints={"case_type": "healthcare", "status": "denied", "month": 1},
               intent="denial_question", topic="denial")


def enter_post(conversation):
    verify_and_find(conversation)
    return run(conversation, "finish", finish=True)


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
        "partial", identity={"name": "margaret chen", "dob": "1985-03-15"},
        hints={"case_type": "healthcare", "status": "denied", "month": 1}, intent="denial_question")
    assert snapshot["state"]["phase"] == "VERIFY_ID"
    assert "CL-2048" not in reply and "pathology report" not in reply
    assert not any(e["kind"] == "find_my_claims" for e in snapshot["events"])
    assert snapshot["state"]["hint_sources"]["month"] == {"source": "caller", "turn_id": "partial"}
    snapshot, reply = run(conversation, "last-field", identity={"ssn_last4": "4472"})
    assert snapshot["state"]["phase"] == "PROCESS_CASE"
    assert snapshot["state"]["selected_case_id"] == "CL-2048"
    assert "pathology report" in reply


def test_unusable_fourth_field_blocks_verification_until_clarified(conversation):
    snapshot, reply = run(conversation, "unusable",
        identity={"name": "margaret chen", "email": "margaret@email.com", "ssn_last4": "4472", "dob": None},
        identity_evidence={"dob": "1985-02-31"},
        hints={"case_type": "healthcare", "status": "denied", "month": 1}, intent="denial_question")
    assert not snapshot["state"]["verified"]
    assert snapshot["state"]["phase"] == "VERIFY_ID"
    assert "dob" not in snapshot["state"]["identity_collected"]
    assert "clarify" in reply and "CL-2048" not in reply
    snapshot, reply = run(conversation, "followup", topic="denial")
    assert not snapshot["state"]["verified"] and "CL-2048" not in reply
    assert not any(e["kind"] == "find_my_claims" for e in snapshot["events"])
    snapshot, reply = run(conversation, "clarified", identity={"dob": "1985-03-15"})
    assert snapshot["state"]["verified"]
    assert snapshot["state"]["selected_case_id"] == "CL-2048"
    assert "pathology report" in reply


@pytest.mark.parametrize("phase", ["PROCESS_CASE", "POST_PROCESS"])
def test_unusable_correction_revokes_access_and_clears_the_old_value(conversation, phase):
    if phase == "POST_PROCESS":
        enter_post(conversation)
    else:
        verify_and_find(conversation)
    snapshot, reply = run(conversation, "correction", identity={"dob": None},
        identity_evidence={"dob": "1985-02-31"}, hints={"case_id": "CL-2048"},
        intent="denial_question", email_choice="send")
    assert snapshot["state"]["phase"] == "VERIFY_ID"
    assert not snapshot["state"]["verified"]
    assert snapshot["state"]["selected_case_id"] is None
    assert snapshot["email_summary"] is None
    assert conversation[2]["dob"] is None
    assert "CL-2048" not in reply and "pathology report" not in reply
    assert any(e["kind"] == "verification_revoked" for e in snapshot["events"])
    assert not any(e["kind"] == "email_consent" for e in snapshot["events"])
    snapshot, reply = run(conversation, "still-unresolved", topic="denial")
    assert not snapshot["state"]["verified"] and "CL-2048" not in reply
    snapshot, _ = run(conversation, "clarified", identity={"dob": "1985-03-15"})
    assert snapshot["state"]["verified"]
    assert snapshot["state"]["selected_case_id"] == "CL-2048"


def test_null_without_evidence_does_not_clear_collected_identity(conversation):
    verify_and_find(conversation)
    snapshot, reply = run(conversation, "followup", identity={"dob": None}, topic="documents")
    assert snapshot["state"]["verified"]
    assert conversation[2]["dob"] == "1985-03-15"
    assert "pathology report" in reply


def test_invalid_transition_cannot_skip_a_phase(conversation):
    with pytest.raises(PermissionError):
        transition(conversation[1], "PROCESS_CASE")
    assert conversation[1]["state"]["phase"] == "VERIFY_ID"


def test_unknown_transition_target_is_rejected_without_index_error(conversation):
    with pytest.raises(PermissionError):
        transition(conversation[1], "NOT_A_PHASE")


def test_unhandled_phase_fails_loudly_instead_of_returning_none(conversation):
    harness, snapshot, identity = conversation
    snapshot["state"]["phase"] = "NOT_A_PHASE"
    with pytest.raises(PermissionError):
        harness.run(snapshot, TurnAnalysis(), identity, "unknown-phase")


def test_two_january_claims_require_disambiguation(conversation):
    snapshot, reply = run(conversation, "first", identity=IDENTITY,
                          hints={"case_type": "healthcare", "month": 1}, intent="status_inquiry")
    assert snapshot["state"]["phase"] == "RESOLVE_INTENT"
    assert "CL-2048" in reply and "CL-2011" in reply
    snapshot, _ = run(conversation, "year", hints={"year": 2025, "date_kind": "created"})
    assert snapshot["state"]["selected_case_id"] == "CL-2011"


def test_service_dates_are_not_treated_as_creation_dates(conversation):
    snapshot, reply = run(conversation, identity=IDENTITY,
                          hints={"case_type": "healthcare", "month": 1, "date_kind": "service"},
                          intent="status_inquiry")
    assert snapshot["state"]["phase"] == "RESOLVE_INTENT"
    assert "not treatment dates" in reply
    assert snapshot["state"]["selected_case_id"] is None


def test_exact_case_number_resolves_a_previous_service_date_clarification(conversation):
    run(conversation, "service-date", identity=IDENTITY,
        hints={"case_type": "healthcare", "month": 1, "date_kind": "service"}, intent="status_inquiry")
    snapshot, _ = run(conversation, "case-number", hints={"case_id": "CL-2048"})
    assert snapshot["state"]["phase"] == "PROCESS_CASE"
    assert snapshot["state"]["selected_case_id"] == "CL-2048"


def test_exact_case_number_replaces_stale_descriptive_hints(conversation):
    snapshot, _ = run(conversation, "mistaken-description", identity=IDENTITY,
        hints={"case_type": "dental", "status": "denied", "year": 2024, "date_kind": "created"},
        intent="denial_question")
    assert snapshot["state"]["phase"] == "RESOLVE_INTENT"
    snapshot, reply = run(conversation, "claim-number", hints={"case_id": "CL-2048"})
    assert snapshot["state"]["selected_case_id"] == "CL-2048"
    assert snapshot["state"]["case_hints"] == {"case_id": "CL-2048"}
    assert set(snapshot["state"]["hint_sources"]) == {"case_id"}
    assert "pathology report" in reply


@pytest.mark.parametrize("phase", ["PROCESS_CASE", "POST_PROCESS"])
def test_changed_case_description_starts_an_ordered_cycle(conversation, phase):
    snapshot, _ = enter_post(conversation) if phase == "POST_PROCESS" else verify_and_find(conversation)
    events_before = len(snapshot["events"])
    snapshot, reply = run(conversation, "different-case",
        hints={"case_type": "dental", "status": "closed"}, intent="status_inquiry")
    assert snapshot["state"]["phase"] == "PROCESS_CASE"
    assert snapshot["state"]["selected_case_id"] == "CL-1899"
    assert snapshot["state"]["case_hints"] == {"case_type": "dental", "status": "closed"}
    assert snapshot["state"]["discussed_topics"] == ["overview"]
    assert snapshot["state"]["email_status"] == "not_offered"
    assert snapshot["email_summary"] is None
    assert "CL-1899" in reply and "CL-2048" not in reply
    new_events = snapshot["events"][events_before:]
    assert [e["detail"] for e in new_events if e["kind"] == "phase_changed"] == [
        "VERIFY_ID → RESOLVE_INTENT", "RESOLVE_INTENT → PROCESS_CASE",
    ]
    kinds = [e["kind"] for e in new_events]
    assert kinds.index("verify_identity") < kinds.index("find_my_claims") < kinds.index("get_selected_claim")


def test_changed_creation_date_resolves_another_claim(conversation):
    verify_and_find(conversation)
    snapshot, reply = run(conversation, "different-year",
        hints={"month": 1, "year": 2025, "date_kind": "created"}, intent="status_inquiry")
    assert snapshot["state"]["selected_case_id"] == "CL-2011"
    assert "CL-2011" in reply and "CL-2048" not in reply


@pytest.mark.parametrize("case_id", ["CL-3001", "CL-NOT-FOUND"])
def test_replacement_case_number_cannot_fall_back_to_an_owned_claim(conversation, case_id):
    snapshot, _ = enter_post(conversation)
    events_before = len(snapshot["events"])
    snapshot, reply = run(conversation, "replacement-number", hints={"case_id": case_id})
    assert snapshot["state"]["verified"]
    assert snapshot["state"]["phase"] == "RESOLVE_INTENT"
    assert snapshot["state"]["selected_case_id"] is None
    assert snapshot["email_summary"] is None
    assert "couldn't find a matching claim" in reply
    assert "CL-2048" not in reply and "CL-3001" not in reply
    assert not any(e["kind"] == "get_selected_claim" for e in snapshot["events"][events_before:])


def test_correction_revokes_verified_access_before_case_disclosure(conversation):
    verify_and_find(conversation)
    snapshot, reply = run(conversation, "correction",
                          identity={"dob": "1985-03-16"}, topic="denial")
    assert snapshot["state"]["phase"] == "VERIFY_ID"
    assert not snapshot["state"]["verified"]
    assert snapshot["state"]["selected_case_id"] is None
    assert "pathology report" not in reply
    assert any(e["kind"] == "verification_revoked" for e in snapshot["events"])


def test_empathy_precedes_identity_request_without_bypassing_it(conversation):
    snapshot, reply = run(conversation,
        emotion="angry", intent="denial_question", topic="denial")
    assert reply.startswith("I hear how upsetting")
    assert "three identity fields" in reply
    assert "don't have to use SSN" in reply
    assert snapshot["state"]["phase"] == "VERIFY_ID"
    assert "pathology report" not in reply


def test_repeated_irrelevant_questions_offer_human_then_accept(conversation):
    for index in range(3):
        snapshot, reply = run(conversation, f"irrelevant-{index}", scope="out_of_scope")
    assert snapshot["state"]["status"] == "handoff_offered"
    assert "human representative" in reply
    assert not snapshot["state"]["verified"]
    snapshot, reply = run(conversation, "handoff", human_requested=True)
    assert snapshot["state"]["status"] == "handoff_requested"
    assert "does not connect to a live agent" in reply
    assert snapshot["state"]["phase"] == "VERIFY_ID"


def test_human_offer_can_resume_without_requesting_transfer(conversation):
    for index in range(3):
        run(conversation, f"irrelevant-{index}", scope="out_of_scope")
    snapshot, _ = run(conversation, "not-a-human-request", human_requested=False)
    assert snapshot["state"]["status"] != "handoff_requested"
    assert snapshot["state"]["phase"] == "VERIFY_ID"


def test_scope_recovery_keeps_identity_and_remembered_case(conversation):
    run(conversation,
        "mixed", identity={"name": "margaret chen"},
        hints={"case_type": "healthcare", "status": "denied", "month": 1},
        intent="denial_question", scope="mixed")
    run(conversation, "irrelevant", scope="out_of_scope")
    snapshot, _ = run(conversation, "resume",
                      identity={"dob": "1985-03-15", "ssn_last4": "4472"})
    assert snapshot["state"]["selected_case_id"] == "CL-2048"
    assert snapshot["state"]["out_of_scope_count"] == 0


def test_repeated_refusal_stops_persuading_and_preserves_gate(conversation):
    for index in range(3):
        snapshot, reply = run(conversation, f"refusal-{index}", refusal=True)
    assert snapshot["state"]["status"] == "handoff_offered"
    assert "won't keep asking" in reply
    assert not snapshot["state"]["verified"]


def test_representative_guard_persists_across_later_turns(conversation):
    run(conversation, "representative", identity=IDENTITY,
        hints={"case_type": "healthcare", "status": "denied"}, intent="denial_question", representative=True)
    snapshot, reply = run(conversation, "continue", topic="denial")
    assert not snapshot["state"]["verified"]
    assert snapshot["state"]["phase"] == "VERIFY_ID"
    assert "pathology report" not in reply


def test_send_request_before_post_process_does_not_send(conversation):
    verify_and_find(conversation)
    snapshot, _ = run(conversation, "early-send", email_choice="send")
    assert snapshot["state"]["phase"] == "PROCESS_CASE"
    assert snapshot["state"]["email_status"] == "not_offered"
    assert not any(e["kind"] == "email_simulated" for e in snapshot["events"])


def test_finish_and_send_same_turn_still_requires_post_offer_consent(conversation):
    verify_and_find(conversation)
    snapshot, reply = run(conversation, "finish-send", finish=True, email_choice="send")
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
def test_customer_can_send_or_skip(conversation, choice, status):
    enter_post(conversation)
    snapshot, _ = run(conversation, "choice", email_choice=choice)
    assert snapshot["state"]["status"] == "completed"
    assert snapshot["state"]["email_status"] == status


def test_different_recipient_is_not_silently_accepted(conversation):
    enter_post(conversation)
    snapshot, reply = run(conversation, "wrong-recipient",
                          identity={"email": "stranger@example.com"}, email_choice="send")
    assert snapshot["state"]["email_status"] == "awaiting_choice"
    assert "separate verification" in reply
    assert not any(e["kind"] == "email_consent" for e in snapshot["events"])


def test_changed_summary_requires_a_new_choice_and_version(conversation):
    snapshot, _ = enter_post(conversation)
    version = snapshot["state"]["summary_version"]
    snapshot, _ = run(conversation, "followup", topic="processing_time")
    assert snapshot["state"]["summary_version"] == version + 1
    assert snapshot["state"]["email_status"] == "awaiting_choice"
    assert "processing_time" in snapshot["email_summary"]["body"]


def test_status_followup_after_email_offer_answers_without_sending(conversation):
    snapshot, _ = enter_post(conversation)
    version = snapshot["state"]["summary_version"]
    snapshot, reply = run(conversation, "status-followup", intent="status_inquiry", topic="overview")
    assert "Claim CL-2048" in reply and "is denied" in reply
    assert "send the summary" in reply and "skip" in reply
    assert snapshot["state"]["phase"] == "POST_PROCESS"
    assert snapshot["state"]["email_status"] == "awaiting_choice"
    assert snapshot["state"]["summary_version"] == version + 1
    assert not any(e["kind"] in ("email_consent", "email_simulated") for e in snapshot["events"])
    snapshot, _ = run(conversation, "consent-after-followup", email_choice="send")
    assert snapshot["state"]["email_status"] == "simulated_sent"
    assert snapshot["email_summary"]["version"] == version + 1


def test_reviewing_email_draft_preserves_pending_consent_and_version(conversation):
    snapshot, _ = enter_post(conversation)
    draft = snapshot["email_summary"].copy()
    topics = snapshot["state"]["discussed_topics"].copy()
    events_before = len(snapshot["events"])
    snapshot, reply = run(conversation, "review-draft", topic="summary", email_choice="unclear")
    assert draft["subject"] in reply and draft["body"] in reply
    assert "send the summary" in reply and "skip" in reply
    assert snapshot["email_summary"] == draft
    assert snapshot["state"]["summary_version"] == draft["version"]
    assert snapshot["state"]["email_status"] == "awaiting_choice"
    assert snapshot["state"]["status"] == "active"
    assert snapshot["state"]["pending"] == "email_choice"
    assert snapshot["state"]["discussed_topics"] == topics
    assert not any(e["kind"] in ("summary_prepared", "email_consent", "email_simulated", "email_skipped")
                   for e in snapshot["events"][events_before:])


def test_human_offer_in_post_requires_email_offer_again_before_sending(conversation):
    enter_post(conversation)
    for index in range(3):
        run(conversation, f"irrelevant-{index}", scope="out_of_scope")
    snapshot, _ = run(conversation, "ambiguous-return", email_choice="send")
    assert snapshot["state"]["pending"] == "email_choice"
    assert snapshot["state"]["email_status"] == "awaiting_choice"
    assert not any(e["kind"] == "email_consent" for e in snapshot["events"])
    snapshot, _ = run(conversation, "renewed-consent", email_choice="send")
    assert snapshot["state"]["email_status"] == "simulated_sent"


def test_email_failure_never_claims_success_and_allows_skip(conversation):
    conversation[0].email_failure = True
    enter_post(conversation)
    snapshot, reply = run(conversation, "failed-send", email_choice="send")
    assert snapshot["state"]["email_status"] == "failed"
    assert snapshot["state"]["status"] == "active"
    assert "Nothing was sent" in reply
    assert not any(e["kind"] == "email_simulated" for e in snapshot["events"])
    snapshot, _ = run(conversation, "skip-after-failure", email_choice="skip")
    assert snapshot["state"]["status"] == "completed"


def test_past_deadline_does_not_turn_into_a_new_week_to_appeal(conversation):
    conversation[1]["state"]["demo_date"] = "2026-09-21"
    snapshot, reply = verify_and_find(conversation)
    assert "2026-03-18" in reply
    assert "has passed" in reply
    assert "late appeal" in reply
    assert "within a week" not in reply
