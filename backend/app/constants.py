"""Static workflow and model protocol data. Credentials belong in config.py."""

PHASES = ("VERIFY_ID", "RESOLVE_INTENT", "PROCESS_CASE", "POST_PROCESS")
TERMINAL_STATUSES = ("completed", "handoff_requested")
PII_FIELDS = ("name", "dob", "phone", "email", "ssn_last4")
AUTHORIZED_PHASES = frozenset(PHASES[1:])
DOCUMENT_ALIASES = {
    "pathology report": "original pathology report",
    "office note": "treating provider office note",
}
FIXTURE_FILES = {
    "policyholders": "policyholders.json",
    "claims": "claims.json",
    "representatives": "representatives.json",
    "consent_scenarios": "consent_scenarios.json",
    "claim_schema": "claim_schema.json",
    "required_document_guideline": "required_document_guideline.json",
}
DOCUMENT_TOPICS = frozenset({"overview", "denial", "documents", "alternatives", "submission_method", "format", "deadline", "unknown"})
FOLLOWUP_TOPICS = {
    "processing_time": "processing_time_after_submission",
    "receipt": "receipt_confirmation",
    "submission_method": "submission_method",
}
PAYMENT_LABELS = {
    "expected_reimbursement_amount": "Recorded expected insurer payment",
    "allowed_max_amount": "Recorded maximum allowed amount",
    "net_pay": "Recorded finalized insurer payment",
    "net_fee": "Recorded adjusted fee schedule amount",
}
EMPATHY_MESSAGES = {
    "frustrated": "I understand this has been frustrating. ",
    "angry": "I hear how upsetting this is, and I’ll help you take the next step. ",
    "anxious": "I understand why you’re concerned. We can take this one step at a time. ",
    "confused": "I’m happy to walk through this with you. ",
}
CONTEXT_KEYS = (
    "phase", "pending", "case_hints", "intent",
    "identity_collected", "previous_assistant", "caller_action",
)
REPLY_CONTEXT_KEYS = ("previous_caller", "previous_assistant")
ANTHROPIC_MAX_TOKENS = 2048
ANTHROPIC_API_VERSION = "2023-06-01"
