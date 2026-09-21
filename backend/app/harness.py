"""Deterministic policy controller. Models propose observations, never permissions."""
import copy
import re
import time
from datetime import date, datetime, timezone

from .business import FixtureRepository
from .schemas import PHASES, TurnAnalysis


def event(snapshot, kind, detail):
    snapshot["events"].append({"kind": kind, "phase": snapshot["state"]["phase"], "detail": detail,
                               "at": datetime.now(timezone.utc).isoformat()})


def transition(snapshot, phase):
    previous = snapshot["state"]["phase"]
    if PHASES.index(phase) != PHASES.index(previous) + 1:
        raise PermissionError("Invalid SOP transition")
    snapshot["state"]["phase"] = phase
    event(snapshot, "phase_changed", f"{previous} → {phase}")


def new_snapshot(session_id, mode, demo_date):
    return {"session_id": session_id, "state": {
        "phase": "VERIFY_ID", "status": "active", "verified": False, "verified_party_id": None,
        "verified_at": None, "matched_fields": [], "identity_collected": [], "intent": None,
        "case_hints": {}, "hint_sources": {}, "selected_case_id": None, "language": "en",
        "email_status": "not_offered", "model_mode": mode, "demo_date": demo_date,
        "pending": "identity", "out_of_scope_count": 0, "refusal_count": 0,
        "discussed_topics": [], "summary_version": 0, "representative_required": False,
    }, "messages": [{"role": "assistant", "content":
        "Hi, I’m your claims support assistant. Tell me what brings you here. Before I can look up a claim, I’ll verify at least three details: full name, date of birth, phone, email, or the last four digits of your SSN. You can choose which to share.",
        "turn_id": "welcome"}], "events": [], "email_summary": None}


def public_snapshot(snapshot):
    result = copy.deepcopy(snapshot)
    for key in ("verified_party_id", "verified_at"):
        result["state"].pop(key, None)
    return result


def redact(text, identity):
    """Only a redacted transcript is persisted or returned by the API."""
    # Callers may mistakenly supply a full SSN despite the last-four prompt.
    value = re.sub(r"(?<!\d)\d{3}[- ]?\d{2}[- ]?\d{4}(?!\d)", "[SSN redacted]", text)
    for field, supplied in sorted(identity.items(), key=lambda item: -len(str(item[1]))):
        if supplied:
            pattern = re.escape(str(supplied)).replace(r"\ ", r"\s+") if field == "name" else re.escape(str(supplied))
            value = re.sub(pattern, f"[{field} provided]", value, flags=re.I)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email provided]", value)
    value = re.sub(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", "[date provided]", value)
    value = re.sub(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}\b", "[date provided]", value, flags=re.I)
    value = re.sub(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)", "[phone provided]", value)
    value = re.sub(r"(?i)((?:ssn|last four|last 4|后四位)[^\d]{0,25})\d{4}", r"\1[redacted]", value)
    return value


def wording(state, en, zh):
    return zh if state["language"] == "zh" else en


def explicit_email_consent(message):
    value = re.sub(r"\s+", " ", message.strip().casefold().replace("’", "'"))
    if re.fullmatch(r"(?:send|yes|yes please|sure|ok|okay|go ahead|please do|是|好的|好|可以|同意|发送)[.!。！ ]*", value):
        return True
    # Require the entire utterance to be affirmative. Unknown qualifications,
    # conditions, questions, and negative objects require clarification.
    english = (
        r"(?:(?:yes|sure|okay|ok)[,!\.\s]+)?"
        r"(?:(?:please|go ahead and|i would like you to|i'd like you to|you can) )?"
        r"(?:send|email)(?: me)? "
        r"(?:it|that|(?:(?:the|a|my) )?(?:email summary|summary|email))"
        r"(?: to (?:me|my (?:verified|registered|on-file) (?:email|email address)))?"
        r"(?: now)?(?:[, ]+please)?[.! ]*"
    )
    chinese = r"(?:好的[，, ]*)?(?:请)?(?:发送|发给我|发一下)(?:这封|这个|本次)?(?:邮件总结|邮件摘要|总结|摘要|邮件)?[。！! ]*"
    return bool(re.fullmatch(english, value) or re.fullmatch(chinese, value))


def identity_reset(snapshot, reason):
    state = snapshot["state"]
    state.update(phase="VERIFY_ID", verified=False, verified_party_id=None, verified_at=None,
                 matched_fields=[], selected_case_id=None, email_status="not_offered", pending="identity", status="active")
    snapshot["email_summary"] = None
    event(snapshot, "verification_revoked", reason)


def supported_value(field, value, message):
    """Do not accept PII invented by a model. Allow simple punctuation normalization."""
    normalize = lambda x: re.sub(r"[^\w]", "", x.casefold())
    if normalize(value) in normalize(message):
        return True
    # Common natural English DOBs can be normalized to ISO without guessing dates.
    if field == "dob":
        for match in re.finditer(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", message):
            try:
                if datetime.strptime(match.group().replace("/", "-"), "%Y-%m-%d").date().isoformat() == value:
                    return True
            except ValueError:
                pass
        for fmt in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"):
            for match in re.finditer(r"[A-Za-z]+\s+\d{1,2},?\s+\d{4}", message):
                try:
                    if datetime.strptime(match.group(), fmt).date().isoformat() == value:
                        return True
                except ValueError:
                    pass
    return False


class Harness:
    def __init__(self, repository: FixtureRepository, email_failure=False):
        self.repo = repository
        self.email_failure = email_failure

    def run(self, snapshot, analysis: TurnAnalysis, identity: dict, message: str, turn_id: str):
        state = snapshot["state"]
        if state["status"] in ("completed", "handoff_requested"):
            return wording(state, "This session is closed. Start a new session for another request.", "本次会话已结束，请新建会话处理其他请求。")
        state["language"] = analysis.language
        if state["verified"] and (time.time() - (state.get("verified_at") or 0) > 3600):
            identity.clear()
            identity_reset(snapshot, "Verification expired; protected access closed.")
        submitted = analysis.identity.model_dump(exclude_none=True)
        accepted = {k: v.strip() for k, v in submitted.items() if v.strip() and supported_value(k, v, message)}
        # A new recipient is not an identity correction or consent to send to that address.
        if state["phase"] == "POST_PROCESS":
            accepted.pop("email", None)
        changed = any(k in identity and identity[k].casefold() != v.casefold() for k, v in accepted.items())
        if state["verified"] and (changed or any(k not in identity for k in accepted)):
            identity_reset(snapshot, "Caller corrected identity; re-verification required.")
        identity.update(accepted)
        state["identity_collected"] = [k for k in identity if k != "policy_number"]
        hints = analysis.hints.model_dump(exclude_none=True)
        if hints.get("date_kind") == "unspecified":
            hints.pop("date_kind", None)
        if state["phase"] == "RESOLVE_INTENT" and state["pending"] == "case" and (hints.get("year") or hints.get("month")):
            # The preceding clarification explicitly asked for a creation year.
            if state["case_hints"].get("date_kind") != "service" and "date_kind" not in hints:
                hints["date_kind"] = "created"
        if state.get("selected_case_id") and hints.get("case_id") and hints["case_id"] != state["selected_case_id"]:
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
            empathy = wording(state, {
                "frustrated": "I understand this has been frustrating. ",
                "angry": "I hear how upsetting this is, and I’ll help you take the next step. ",
                "anxious": "I understand why you’re concerned. We can take this one step at a time. ",
                "confused": "I’m happy to walk through this with you. ",
            }[analysis.emotion], "我理解这让您感到困扰，我们可以一步一步处理。")

        if analysis.human_requested or (state["pending"] == "human_offer" and re.fullmatch(r"(?i)\s*(yes|please|ok|好的|是的|转人工)[.!。！ ]*", message)):
            state.update(status="handoff_requested", pending=None)
            event(snapshot, "handoff_requested", "Simulated human handoff; no live representative connected.")
            return empathy + wording(state, "I’ve recorded a simulated request for a human representative. This demo does not connect to a live agent. Your verification status stays unchanged.", "已记录模拟人工转接请求。本 Demo 不会连接真实客服，身份核验状态也不会因此改变。")
        if analysis.representative:
            state["representative_required"] = True
        if state.get("representative_required"):
            state.update(status="handoff_offered", pending="human_offer")
            event(snapshot, "representative_gate", "Relationship records are not proof of representative authorization.")
            return empathy + wording(state, "I can help with the process, but a family relationship alone does not authorize access to another person’s claims. This demo does not implement representative authorization. Would you like the simulated human handoff option?", "亲属关系不能代替代办授权。本 Demo 尚未实现代办授权核验；您可以选择模拟人工转接。")

        if analysis.scope in ("out_of_scope", "mixed"):
            state["out_of_scope_count"] += 1
            event(snapshot, "scope_guard", "Out-of-scope question declined; useful caller fields retained.")
            if state["out_of_scope_count"] >= 3:
                state.update(status="handoff_offered", pending="human_offer")
                return empathy + wording(state, "I can only help with insurance claims support. Would you like to speak with a human representative through the demo’s simulated handoff, or return to your claim?", "我只能处理保险理赔客服问题。您希望选择模拟人工转接，还是回到理赔问题？")
            if analysis.scope == "out_of_scope":
                return empathy + wording(state, "I can help with insurance claims, verification, documents, and next steps. I can’t answer unrelated questions. ", "我可以帮助处理保险理赔、身份核验、材料和下一步，无法回答无关问题。") + self.resume_prompt(state)
            empathy += wording(state, "I’ve kept the details relevant to your claim, but can’t answer the unrelated part. ", "已保留与理赔有关的信息，无关问题不在服务范围内。")
        else:
            state["out_of_scope_count"] = 0
            if state["status"] == "handoff_offered":
                state["status"] = "active"
        if analysis.refusal and state["phase"] == "VERIFY_ID":
            state["refusal_count"] += 1
            if state["refusal_count"] >= 3:
                state.update(status="handoff_offered", pending="human_offer")
                return empathy + wording(state, "I won’t keep asking for information you don’t want to share. Claim details remain protected. Would you prefer a simulated human handoff?", "我不会继续要求您提供不愿分享的信息。理赔详情仍受保护；您可以选择模拟人工转接。")

        verified_this_turn = False
        if state["phase"] == "VERIFY_ID":
            check = self.repo.verify_identity(identity)
            event(snapshot, "verify_identity", "passed" if check["verified"] else "not_verified")
            state["matched_fields"] = check.get("matched_fields", []) if check["verified"] else []
            if not check["verified"]:
                state["pending"] = "identity"
                if check.get("reason") in ("conflicting_fields", "no_match", "invalid_fields", "ambiguous_identity") and len(identity) >= 3:
                    return empathy + wording(state, "I couldn’t verify those details together. Please check what you entered or use another identity field. I need three matching details before I can access protected claim information; your claim question is saved.", "这些信息暂时无法共同完成核验。请检查输入或换用其他身份字段；至少三项匹配后才能访问理赔详情，您的来意已保存。")
                return empathy + self.resume_prompt(state)
            state.update(verified=True, verified_party_id=check["party_id"], verified_at=time.time(), pending=None)
            state["refusal_count"] = 0
            transition(snapshot, "RESOLVE_INTENT")
            verified_this_turn = True
            empathy += wording(state, "Thank you—your identity is verified. ", "谢谢，身份核验已通过。")

        if state["phase"] == "RESOLVE_INTENT":
            if not state["intent"] and not state["case_hints"]:
                state["pending"] = "intent"
                return empathy + wording(state, "What would you like help with—claim status, a denial, documents, or a payment?", "您想咨询理赔进度、拒赔原因、补充材料还是支付情况？")
            if state["case_hints"].get("date_kind") == "service" and not state["case_hints"].get("case_id"):
                state["pending"] = "case"
                return empathy + wording(state, "I only have claim creation dates, not treatment dates. Could you share the claim number, or the month and year the claim was opened?", "记录中只有案件创建日期，没有就诊日期。请提供案件编号，或案件创建的月份和年份。")
            claims = self.repo.guarded_claims(state, state["case_hints"])
            event(snapshot, "find_my_claims", f"Authorized lookup returned {len(claims)} candidate(s).")
            if not claims:
                state["pending"] = "case"
                return empathy + wording(state, "I couldn’t find a matching claim in your records. Could you check the claim number or share a different type or creation date? You can also ask for a human representative.", "没有找到匹配的本人案件。请核对案件编号，或提供其他案件类型、创建日期；也可以选择人工服务。")
            if len(claims) > 1:
                state["pending"] = "case"
                options = "; ".join(f"{c['case_id']} ({c['case_type']}, created {c['created_at']}, {c['status']})" for c in claims)
                return empathy + wording(state, f"I found more than one possible claim: {options}. Which claim do you mean? A claim number or creation year will help.", f"找到多个可能的案件：{options}。请提供案件编号或创建年份，以确认是哪一笔。")
            claim = claims[0]
            if state["case_hints"].get("date_kind", "unspecified") == "unspecified":
                created = date.fromisoformat(claim["created_at"])
                if any(state["case_hints"].get(key) and state["case_hints"][key] != value for key, value in (("year", created.year), ("month", created.month))):
                    state["pending"] = "case"
                    return empathy + wording(state, f"The matching claim {claim['case_id']} was created on {claim['created_at']}, which differs from the date you mentioned. Is your date a treatment date? Please clarify the claim number or creation date.", f"匹配到的案件 {claim['case_id']} 创建于 {claim['created_at']}，与您提供的日期不同。请说明您说的是就诊日期还是创建日期，或提供案件编号。")
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
            return empathy + answer + wording(state, "\n\nWhat else would you like to know about this claim? When you’re ready, I can prepare an optional email summary.", "\n\n关于这笔理赔，您还想了解什么？处理完后，我可以提供可选择发送的邮件总结。")

        if state["phase"] == "POST_PROCESS":
            if analysis.email_choice == "skip":
                state.update(email_status="skipped", status="completed", pending=None)
                snapshot["email_summary"]["status"] = "skipped"
                event(snapshot, "email_skipped", "Caller chose not to send; no email action executed.")
                return empathy + wording(state, "Understood—I’ve skipped the email. Your claim status has not changed. Thank you for speaking with me.", "好的，已跳过邮件发送。案件状态没有改变，感谢您的沟通。")
            if analysis.email_choice == "send":
                # Bind consent to the existing verified address and exact draft version.
                contact = self.repo.get_contact(state)
                supplied_email = analysis.identity.email
                if supplied_email and supplied_email.casefold() != contact["email"].casefold():
                    return wording(state, "This demo sends only to the verified email on your record. Changing the recipient needs a separate verification process. Would you like to send to the verified address or skip?", "本 Demo 仅向客户记录中已核验的邮箱发送。更换收件地址需要单独核验。您希望发送到登记邮箱，还是跳过？")
                if not explicit_email_consent(message):
                    return wording(state, "Please explicitly say whether to send the email summary or skip it. I haven’t sent anything.", "请明确选择发送邮件总结或跳过，目前没有发送任何内容。")
                event(snapshot, "email_consent", f"Explicit consent for summary version {state['summary_version']} and verified recipient.")
                if self.email_failure:
                    state["email_status"] = "failed"
                    snapshot["email_summary"]["status"] = "failed"
                    event(snapshot, "email_failed", "Simulated delivery failure; no success claimed.")
                    return wording(state, "The simulated email service failed. Nothing was sent. You can retry or skip the email.", "模拟邮件服务失败，未发送邮件。您可以重试或跳过。")
                state.update(email_status="simulated_sent", status="completed", pending=None)
                snapshot["email_summary"]["status"] = "simulated_sent"
                event(snapshot, "email_simulated", "Saved once to the local outbox. No external email sent.")
                return wording(state, "The summary was saved to the simulated email outbox. No real email was sent; you can review the full draft in the summary panel.", "总结已保存到模拟邮件发件箱，没有发送真实邮件。您可以在总结面板查看完整内容。")
            if analysis.topic not in ("overview", "unknown"):
                claim = self.repo.guarded_claim(state, state["selected_case_id"])
                answer = self.answer_claim(snapshot, claim, analysis.topic)
                if analysis.topic not in state["discussed_topics"]:
                    state["discussed_topics"].append(analysis.topic)
                self.make_summary(snapshot)
                return empathy + answer + "\n\n" + self.email_offer(state)
            return empathy + self.email_offer(state)
        raise RuntimeError("Unknown workflow phase")

    def resume_prompt(self, state):
        if state["phase"] == "VERIFY_ID":
            return wording(state, "To protect your claim details, I need to match at least three identity fields. You can use your full name, date of birth, phone, email, or SSN last four. You don’t have to use SSN. Please share another detail, or ask for a human representative.", "为保护您的理赔信息，需要核对至少三项身份信息。可选择姓名、生日、电话、邮箱或 SSN 后四位，不必使用 SSN。请补充信息，或选择人工服务。")
        if state["phase"] == "POST_PROCESS":
            return self.email_offer(state)
        return wording(state, "Let’s continue with your claim. What would you like to know?", "我们继续处理您的理赔问题，您想了解什么？")

    def answer_claim(self, snapshot, claim, topic):
        state = snapshot["state"]
        today = date.fromisoformat(state["demo_date"])
        facts = [f"Claim {claim['case_id']} ({claim['case_type']}, created {claim['created_at']}) is {claim['status']}."]
        if topic == "unknown":
            facts.append("I don’t have a verified rule or tool result for that specific question. A human claims representative can review it; I can help with the recorded status, reason, documents, payments, and next steps.")
        else:
            guidance = self.repo.get_guidance(claim, topic, today)
            for block in guidance:
                if block["id"] not in (f"claim:{claim['case_id']}:status", f"claim:{claim['case_id']}:created_at") and block["text"] not in facts:
                    facts.append(block["text"])
                event(snapshot, "grounded_source", block["id"])
        event(snapshot, "grounded_source", f"claims.json:{claim['case_id']}")
        if state["language"] == "zh":
            # Keep original approved policy wording rather than silently inventing translations.
            facts.insert(0, "以下是这笔案件的已核实信息（业务指南保留英文原文）：")
        return "\n\n".join(facts)

    def begin_post(self, snapshot):
        transition(snapshot, "POST_PROCESS")
        self.make_summary(snapshot)
        return wording(snapshot["state"], "I’ve prepared a summary of what we discussed and the next steps. ", "已整理讨论内容和下一步。") + self.email_offer(snapshot["state"])

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

    def email_offer(self, state):
        return wording(state, "Would you like me to send the summary to the verified email on your record, or skip it? Sending is simulated in this local demo.", "您希望将总结发送到登记邮箱，还是跳过？本地 Demo 使用模拟邮件发送。")
