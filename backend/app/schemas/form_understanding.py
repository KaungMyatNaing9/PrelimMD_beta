"""Versioned contracts for form understanding and guided completion."""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .models import BoundingBox, FieldType, ValidationError


class RequiredStatus(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    UNKNOWN = "unknown"


class CandidateSource(str, Enum):
    PDF_TEXT = "uploaded_form_pdf_text"
    OCR = "uploaded_form_ocr"
    PROFILE = "patient_profile"
    USER = "current_user_input"
    CORRECTION = "manual_correction"


class ResolutionStatus(str, Enum):
    EXTRACTED = "extracted"
    PREFILLED = "prefilled"
    MISSING = "missing"
    NEEDS_CONFIRMATION = "needs_confirmation"
    UNCERTAIN_MAPPING = "uncertain_mapping"
    CORRECTED = "corrected"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"


class CanonicalFieldDefinition(BaseModel):
    canonical_key: str
    display_name: str
    category: str
    value_type: FieldType
    aliases: list[str]
    reusable: bool = True
    freshness_policy: str = "stable"
    confirmation_policy: str = "optional"
    sensitive: bool = False
    normalizer: str | None = None
    validator: str | None = None


class FieldRevision(BaseModel):
    """A value this field used to hold, and where that value came from.

    The faculty review's rule: never silently overwrite a value a patient entered. A
    correction therefore does not erase what was there -- it files it here first, with the
    reading that produced it and how sure that reading was, so a later chart review can see
    what the machine said before a person changed it.

    Append-only. Entries are never edited or removed.
    """

    value: Any = None
    source: str
    confidence: float = Field(ge=0, le=1)
    superseded_at: datetime
    superseded_by: str


class DetectedFormField(BaseModel):
    field_id: str
    page_index: int
    label_text: str
    normalized_label: str
    field_type: FieldType
    label_bbox: BoundingBox
    answer_bbox: BoundingBox
    options: list[str] = Field(default_factory=list)
    required_status: RequiredStatus = RequiredStatus.UNKNOWN
    required_confidence: float = Field(ge=0, le=1)
    detection_confidence: float = Field(ge=0, le=1)
    source: str
    canonical_key: str | None = None
    mapping_confidence: float = Field(ge=0, le=1)
    mapping_reason: str
    raw_value: Any = None
    normalized_value: Any = None
    is_blank: bool = True
    confidence: float = Field(default=0, ge=0, le=1)
    needs_review: bool = False
    # Oldest first. Empty until something replaces the extracted value.
    history: list[FieldRevision] = Field(default_factory=list)


class AnswerCandidate(BaseModel):
    candidate_id: str
    field_id: str
    raw_value: Any = None
    normalized_value: Any = None
    source: CandidateSource
    confidence: float = Field(ge=0, le=1)
    captured_at: datetime
    verified_at: datetime | None = None
    requires_confirmation: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)


class ResolvedFormField(BaseModel):
    field: DetectedFormField
    candidates: list[AnswerCandidate] = Field(default_factory=list)
    selected_candidate: AnswerCandidate | None = None
    resolution_status: ResolutionStatus
    needs_user_input: bool
    needs_confirmation: bool
    validation_errors: list[ValidationError] = Field(default_factory=list)


class FormAnalysis(BaseModel):
    schema_version: Literal["2.0"] = "2.0"
    analysis_id: str
    source_type: str
    pages: list[dict[str, Any]]
    detected_fields: list[DetectedFormField]
    form_fingerprint: str
    matched_known_template: str | None = None
    warnings: list[str] = Field(default_factory=list)
    processing_time_ms: int


class CompletionSession(BaseModel):
    schema_version: Literal["2.0"] = "2.0"
    session_id: str
    patient_id: str
    analysis_id: str
    resolved_fields: list[ResolvedFormField]
    current_question_id: str | None = None
    required_total: int
    required_completed: int
    total_completed: int
    progress: float = Field(ge=0, le=1)
    status: str
    created_at: datetime
    updated_at: datetime
