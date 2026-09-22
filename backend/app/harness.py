"""Deterministic policy controller. Models propose observations, never permissions."""
import re
import time
from datetime import date, datetime, timezone

from .business import FixtureRepository
from .constants import EMPATHY_MESSAGES, PHASES
from .schemas import TurnAnalysis


def event(snapshot, kind, detail):
    snapshot["events"].append({"kind": kind, "phase": snapshot["state"]["phase"], "detail": detail,
                               "at": datetime.now(timezone.utc).isoformat()})


def transition(snapshot, phase):
    previous = snapshot["state"]["phase"]
    if PHASES.index(phase) != PHASES.index(previous) + 1:
        raise PermissionError("Invalid SOP transition")
    snapshot["state"]["phase"] = phase
    event(snapshot, "phase_changed", f"{previous} → {phase}")


def new_snapshot(session_id, demo_date):
    return {"session_id": session_id, "state": {
        "phase": "VERIFY_ID", "status": "active", "verified": False, "verified_party_id": None,
        "verified_at": None, "matched_fields": [], "identity_collected": [], "intent": None,
        "case_hints": {}, "hint_sources": {}, "selected_case_id": None,
        "email_status": "not_offered", "demo_date": demo_date,
        "pending": "identity", "out_of_scope_count": 0, "refusal_count": 0,
        "discussed_topics": [], "summary_version": 0, "representative_required": False,
    }, "messages": [{"role": "assistant", "content":
        "Hi, I'm your claims support assistant. Tell me what brings you here. Before I can look up a claim, I'll verify at least three details: full name, date of birth, phone, email, or the last four digits of your SSN. You can choose which to share.",
        "turn_id": "welcome"}], "events": [], "email_summary": None}


def public_snapshot(snapshot):
    state = {key: value for key, value in snapshot["state"].items()
             if key not in ("verified_party_id", "verified_at")}
    return {**snapshot, "state": state}


def redact(text, identity, evidence=None):
    """Only a redacted transcript is persisted or returned by the API."""
    # Callers may mistakenly supply a full SSN despite the last-four prompt.
    value = re.sub(r"(?<!\d)\d{3}[- ]?\d{2}[- ]?\d{4}(?!\d)", "[SSN redacted]", text)
    supplied_values = [*identity.items(), *(evidence or {}).items()]
    for field, supplied in sorted(supplied_values, key=lambda item: -len(str(item[1]))):
        if supplied:
            pattern = re.escape(str(supplied)).replace(r"\ ", r"\s+") if field == "name" else re.escape(str(supplied))
            value = re.sub(pattern, f"[{field} provided]", value, flags=re.I)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email provided]", value)
    value = re.sub(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", "[date provided]", value)
    value = re.sub(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)", "[phone provided]", value)
    return value


def identity_reset(snapshot, reason):
    state = snapshot["state"]
    state.update(phase="VERIFY_ID", verified=False, verified_party_id=None, verified_at=None,
                 matched_fields=[], selected_case_id=None, email_status="not_offered", pending="identity", status="active")
    snapshot["email_summary"] = None
    event(snapshot, "verification_revoked", reason)


class Harness:
    def __init__(self, repository: FixtureRepository, email_failure=False):
        self.repo = repository
        self.email_failure = email_failure

    def run(self, snapshot, analysis: TurnAnalysis, identity: dict, turn_id: str):
        """Apply SOP rules and return approved content for the presentation model."""
        state = snapshot["state"]
        submitted = analysis.identity.model_dump(exclude_none=True)
        # A new recipient is not an identity correction or consent to send to that address.
        if state["phase"] == "POST_PROCESS":
            submitted.pop("email", None)
        changed = any(k not in identity or identity[k].casefold() != v.casefold() for k, v in submitted.items())
        if state["verified"] and changed:
            identity_reset(snapshot, "Caller corrected identity; re-verification required.")
        identity.update(submitted)
        state["identity_collected"] = [k for k in identity if k != "policy_number"]
        hints = analysis.hints.model_dump(exclude_none=True)
        if hints.get("date_kind") == "unspecified":
            hints.pop("date_kind", None)
        if state["phase"] == "RESOLVE_INTENT" and state["pending"] == "case" and (hints.get("year") or hints.get("month")):
            # The preceding clarification explicitly asked for a creation year.
            if state["case_hints"].get("date_kind") != "service" and "date_kind" not in hints:
                hints["date_kind"] = "created"
        if state["selected_case_id"] and hints.get("case_id") and hints["case_id"] != state["selected_case_id"]:
            identity_reset(snapshot, "Caller selected another case; starting a new ordered business cycle.")
            state["case_hints"] = {}
            state["hint_sources"] = {}
            state["discussed_topics"] = []
        if hints:
            state["case_hints"].update(hints)
            for key in hints:
                state["hint_sources"][key] = {"source": "caller", "turn_id": turn_id}
            event(snapshot, "memory_updated", "Saved caller case hints; these are not verified claim facts.")
        if analysis.intent:
            state["intent"] = analysis.intent
        empathy = ""
        if analysis.emotion != "neutral":
            empathy = EMPATHY_MESSAGES[analysis.emotion]

        if analysis.human_requested:
            state.update(status="handoff_requested", pending=None)
            event(snapshot, "handoff_requested", "Simulated human handoff; no live representative connected.")
            return empathy + "I've recorded a simulated request for a human representative. This demo does not connect to a live agent. Your verification status stays unchanged."
        if analysis.representative:
            state["representative_required"] = True
        if state["representative_required"]:
            state.update(status="handoff_offered", pending="human_offer")
            event(snapshot, "representative_gate", "Relationship records are not proof of representative authorization.")
            return empathy + "I can help with the process, but a family relationship alone does not authorize access to another person's claims. This demo does not implement representative authorization. Would you like the simulated human handoff option?"

        if analysis.scope in ("out_of_scope", "mixed"):
            state["out_of_scope_count"] += 1
            event(snapshot, "scope_guard", "Out-of-scope question declined; useful caller fields retained.")
            if state["out_of_scope_count"] >= 3:
                state.update(status="handoff_offered", pending="human_offer")
                return empathy + "I can only help with insurance claims support. Would you like to speak with a human representative through the demo's simulated handoff, or return to your claim?"
            if analysis.scope == "out_of_scope":
                return empathy + "I can help with insurance claims, verification, documents, and next steps. I can't answer unrelated questions. " + self.resume_prompt(state)
            empathy += "I've kept the details relevant to your claim, but can't answer the unrelated part. "
        else:
            state["out_of_scope_count"] = 0
            if state["status"] == "handoff_offered":
                state["status"] = "active"
        if analysis.refusal and state["phase"] == "VERIFY_ID":
            state["refusal_count"] += 1
            if state["refusal_count"] >= 3:
                state.update(status="handoff_offered", pending="human_offer")
                return empathy + "I won't keep asking for information you don't want to share. Claim details remain protected. Would you prefer a simulated human handoff?"

        verified_this_turn = False
        if state["phase"] == "VERIFY_ID":
            check = self.repo.verify_identity(identity)
            event(snapshot, "verify_identity", "passed" if check["verified"] else "not_verified")
            state["matched_fields"] = check["matched_fields"] if check["verified"] else []
            if not check["verified"]:
                state["pending"] = "identity"
                if check["reason"] in ("conflicting_fields", "no_match", "invalid_fields", "ambiguous_identity") and len(identity) >= 3:
                    return empathy + "I couldn't verify those details together. Please check what you entered or use another identity field. I need three matching details before I can access protected claim information; your claim question is saved."
                return empathy + self.resume_prompt(state)
            state.update(verified=True, verified_party_id=check["party_id"], verified_at=time.time(), pending=None)
            state["refusal_count"] = 0
            transition(snapshot, "RESOLVE_INTENT")
            verified_this_turn = True
            empathy += "Thank you—your identity is verified. "

        if state["phase"] == "RESOLVE_INTENT":
            if not state["intent"] and not state["case_hints"]:
                state["pending"] = "intent"
                return empathy + "What would you like help with—claim status, a denial, documents, or a payment?"
            if state["case_hints"].get("date_kind") == "service" and not state["case_hints"].get("case_id"):
                state["pending"] = "case"
                return empathy + "I only have claim creation dates, not treatment dates. Could you share the claim number, or the month and year the claim was opened?"
            claims = self.repo.guarded_claims(state, state["case_hints"])
            event(snapshot, "find_my_claims", f"Authorized lookup returned {len(claims)} candidate(s).")
            if not claims:
                state["pending"] = "case"
                return empathy + "I couldn't find a matching claim in your records. Could you check the claim number or share a different type or creation date? You can also ask for a human representative."
            if len(claims) > 1:
                state["pending"] = "case"
                options = "; ".join(f"{c['case_id']} ({c['case_type']}, created {c['created_at']}, {c['status']})" for c in claims)
                return empathy + f"I found more than one possible claim: {options}. Which claim do you mean? A claim number or creation year will help."
            claim = claims[0]
            if state["case_hints"].get("date_kind", "unspecified") == "unspecified":
                created = date.fromisoformat(claim["created_at"])
                if any(state["case_hints"].get(key) and state["case_hints"][key] != value for key, value in (("year", created.year), ("month", created.month))):
                    state["pending"] = "case"
                    return empathy + f"The matching claim {claim['case_id']} was created on {claim['created_at']}, which differs from the date you mentioned. Is your date a treatment date? Please clarify the claim number or creation date."
            state["selected_case_id"] = claim["case_id"]
            state["intent"] = state["intent"] or "general_claim_question"
            state["pending"] = None
            transition(snapshot, "PROCESS_CASE")
            event(snapshot, "case_selected", f"Selected authorized claim {claim['case_id']} using remembered caller hints.")

        if state["phase"] == "PROCESS_CASE":
            if analysis.finish and not verified_this_turn:
                return empathy + self.begin_post(snapshot)
            claim = self.repo.guarded_claim(state, state["selected_case_id"])
            event(snapshot, "get_selected_claim", f"Read {claim['case_id']} after authorization and ownership checks.")
            topic = analysis.topic
            if topic == "overview" and state["intent"] == "denial_question":
                topic = "denial"
            if topic not in state["discussed_topics"]:
                state["discussed_topics"].append(topic)
            answer = self.answer_claim(snapshot, claim, topic)
            state["pending"] = "claim_question"
            return empathy + answer + "\n\nWhat else would you like to know about this claim? When you're ready, I can prepare an optional email summary."

        if state["phase"] == "POST_PROCESS":
            if state["pending"] != "email_choice":
                state["pending"] = "email_choice"
                return empathy + self.email_offer()
            if analysis.email_choice == "skip":
                state.update(email_status="skipped", status="completed", pending=None)
                snapshot["email_summary"]["status"] = "skipped"
                event(snapshot, "email_skipped", "Caller chose not to send; no email action executed.")
                return empathy + "Understood—I've skipped the email. Your claim status has not changed. Thank you for speaking with me."
            if analysis.email_choice == "send":
                # Bind consent to the existing verified address and exact draft version.
                contact = self.repo.get_contact(state)
                supplied_email = analysis.identity.email
                if supplied_email and supplied_email.casefold() != contact["email"].casefold():
                    return "This demo sends only to the verified email on your record. Changing the recipient needs a separate verification process. Would you like to send to the verified address or skip?"
                event(snapshot, "email_consent", f"Explicit consent for summary version {state['summary_version']} and verified recipient.")
                if self.email_failure:
                    state["email_status"] = "failed"
                    snapshot["email_summary"]["status"] = "failed"
                    event(snapshot, "email_failed", "Simulated delivery failure; no success claimed.")
                    return "The simulated email service failed. Nothing was sent. You can retry or skip the email."
                state.update(email_status="simulated_sent", status="completed", pending=None)
                snapshot["email_summary"]["status"] = "simulated_sent"
                event(snapshot, "email_simulated", "Saved once to the local outbox. No external email sent.")
                return "The summary was saved to the simulated email outbox. No real email was sent; you can review the full draft in the summary panel."
            if analysis.topic not in ("overview", "unknown"):
                claim = self.repo.guarded_claim(state, state["selected_case_id"])
                answer = self.answer_claim(snapshot, claim, analysis.topic)
                if analysis.topic not in state["discussed_topics"]:
                    state["discussed_topics"].append(analysis.topic)
                self.make_summary(snapshot)
                return empathy + answer + "\n\n" + self.email_offer()
            return empathy + self.email_offer()

    def resume_prompt(self, state):
        if state["phase"] == "VERIFY_ID":
            return "To protect your claim details, I need to match at least three identity fields. You can use your full name, date of birth, phone, email, or SSN last four. You don't have to use SSN. Please share another detail, or ask for a human representative."
        if state["phase"] == "POST_PROCESS":
            return self.email_offer()
        return "Let's continue with your claim. What would you like to know?"

    def answer_claim(self, snapshot, claim, topic):
        state = snapshot["state"]
        today = date.fromisoformat(state["demo_date"])
        facts = [f"Claim {claim['case_id']} ({claim['case_type']}, created {claim['created_at']}) is {claim['status']}."]
        if topic == "unknown":
            facts.append("I don't have a verified rule or tool result for that specific question. A human claims representative can review it; I can help with the recorded status, reason, documents, payments, and next steps.")
        else:
            guidance = self.repo.get_guidance(claim, topic, today)
            for block in guidance:
                if block["id"] not in (f"claim:{claim['case_id']}:status", f"claim:{claim['case_id']}:created_at") and block["text"] not in facts:
                    facts.append(block["text"])
                event(snapshot, "grounded_source", block["id"])
        event(snapshot, "grounded_source", f"claims.json:{claim['case_id']}")
        return "\n\n".join(facts)

    def begin_post(self, snapshot):
        transition(snapshot, "POST_PROCESS")
        self.make_summary(snapshot)
        return "I've prepared a summary of what we discussed and the next steps. " + self.email_offer()

    def make_summary(self, snapshot):
        state = snapshot["state"]
        claim = self.repo.guarded_claim(state, state["selected_case_id"])
        contact = self.repo.get_contact(state)
        local, domain = contact["email"].split("@", 1)
        masked = local[:1] + "***@" + domain
        topics = ", ".join(state["discussed_topics"]) or "claim overview"
        body = [f"Claim {claim['case_id']}", f"Discussed: {topics}.", f"Current claim status: {claim['status']}.",
                "Outcome of this conversation: information and guidance provided. No claim decision was changed; no appeal or document upload was submitted."]
        if claim.get("denial_reason"):
            body.append("Recorded denial reason: " + claim["denial_reason"] + ".")
        if claim.get("documents_needed"):
            body.append("Next steps: obtain " + " and ".join(claim["documents_needed"]) + "; follow the member portal instructions, or ask support about submission options.")
        for block in self.repo.get_guidance(claim, "deadline", date.fromisoformat(state["demo_date"])):
            if "appeal_deadline" in block["id"]:
                body.append(block["text"])
        state["summary_version"] += 1
        state.update(email_status="awaiting_choice", pending="email_choice")
        snapshot["email_summary"] = {"subject": f"Your claim support summary — {claim['case_id']}", "body": "\n\n".join(body),
                                     "to_masked": masked, "status": "awaiting_choice", "version": state["summary_version"]}
        event(snapshot, "summary_prepared", f"Prepared version {state['summary_version']}; no sending consent yet.")

    def email_offer(self):
        return "Would you like me to send the summary to the verified email on your record, or skip it? Sending is simulated in this local demo."
