from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class IdentityFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
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
    language: Literal["en", "zh"] = "en"


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["live", "offline"] = "offline"
    api_key: str | None = Field(default=None, max_length=4096, repr=False)
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=150)


class SessionCreate(ModelConfig):
    demo_date: str | None = None


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4000)
    turn_id: str = Field(min_length=1, max_length=100)


PHASES = ["VERIFY_ID", "RESOLVE_INTENT", "PROCESS_CASE", "POST_PROCESS"]
