from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class IdentityFields(BaseModel):
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
    identity_evidence: IdentityFields = Field(default_factory=IdentityFields)
    hints: CaseHints = Field(default_factory=CaseHints)
    intent: Literal["status_inquiry", "denial_question", "document_submission", "payment_question", "next_steps", "general_claim_question"] | None = None
    topic: Literal["overview", "denial", "documents", "alternatives", "submission_method", "processing_time", "deadline", "payment", "receipt", "format", "unknown"] = "overview"
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
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    caller_action: Literal["send_summary", "skip_summary", "finish_case"] | None = None
