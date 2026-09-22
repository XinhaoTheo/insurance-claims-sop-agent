from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


def canonical_name(value: str) -> str:
    """The single match form for names: trimmed, single-spaced, casefolded."""
    return " ".join(value.split()).casefold()


class IdentityFields(BaseModel):
    """Standardized identity observations. Non-conforming values are rejected.

    The model standardizes; this schema validates. No repair happens later, so a
    non-conforming value is reported as a model error and retried by the caller.
    """
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, str_min_length=1)
    name: str | None = None
    dob: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    phone: str | None = Field(default=None, pattern=r"^\+1\d{10}$")
    email: str | None = Field(default=None, pattern=r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")
    ssn_last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    policy_number: str | None = None

    @field_validator("name")
    @classmethod
    def name_is_a_match_key(cls, value: str | None) -> str | None:
        if value is not None and value != canonical_name(value):
            raise ValueError("name must be lowercase with single spaces")
        return value

    @field_validator("dob")
    @classmethod
    def dob_is_a_real_date(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise ValueError("dob must be a real calendar date") from None
        return value

    @field_validator("policy_number")
    @classmethod
    def policy_number_is_uppercase(cls, value: str | None) -> str | None:
        if value is not None and value != value.upper():
            raise ValueError("policy_number must be uppercase")
        return value


class IdentityEvidence(BaseModel):
    """Raw spans for redaction; a span with a null identity value needs clarification."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, str_min_length=1)
    name: str | None = None
    dob: str | None = None
    phone: str | None = None
    email: str | None = None
    ssn_last4: str | None = None
    policy_number: str | None = None


class CaseHints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str | None = None
    case_type: Literal["healthcare", "dental", "auto"] | None = None
    status: Literal["denied", "open", "closed"] | None = None
    month: int | None = Field(default=None, ge=1, le=12)
    year: int | None = Field(default=None, ge=1900, le=2100)
    date_kind: Literal["unspecified", "created", "service"] = "unspecified"


class TurnAnalysis(BaseModel):
    """Untrusted model observations. Intentionally no phase or verified fields."""
    model_config = ConfigDict(extra="forbid")
    identity: IdentityFields = Field(default_factory=IdentityFields)
    identity_evidence: IdentityEvidence = Field(default_factory=IdentityEvidence)
    hints: CaseHints = Field(default_factory=CaseHints)
    intent: Literal["status_inquiry", "denial_question", "document_submission", "payment_question", "next_steps", "general_claim_question"] | None = None
    topic: Literal["overview", "denial", "documents", "alternatives", "submission_method", "processing_time", "deadline", "payment", "receipt", "format", "summary", "unknown"] = Field(
        default="overview",
        description="The question to answer this turn, even when identity is also supplied. "
        "Use payment for recorded expected or finalized insurer payments; summary for reviewing the email draft. "
        "Use unknown for questions outside the supported claim topics, such as predicting future premiums. "
        "Overview is for the claim's general status, not a substitute for a more specific question.",
    )
    scope: Literal["in_scope", "out_of_scope", "mixed"] = "in_scope"
    emotion: Literal["neutral", "frustrated", "anxious", "angry", "confused"] = "neutral"
    refusal: bool = False
    human_requested: bool = False
    representative: bool = False
    finish: bool = False
    email_choice: Literal["send", "skip", "unclear"] = "unclear"


class ReplyPresentation(BaseModel):
    """Customer-facing wording without business state or action fields."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    caller_language: str = Field(min_length=1, description="Identify the language used by the caller samples. Write reply in this language.")
    reply: str = Field(min_length=1)


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_protocol: Literal["openai", "anthropic"] | None = None
    api_key: str | None = Field(default=None, repr=False)
    base_url: str | None = None
    model: str | None = None


class SessionCreate(ModelConfig):
    demo_date: str | None = None


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    caller_action: Literal["send_summary", "skip_summary", "finish_case"] | None = None
