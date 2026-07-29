from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION: Literal["1.0"] = "1.0"


class FieldType(str, Enum):
    SHORT_TEXT = "short_text"
    MULTILINE_TEXT = "multiline_text"
    DATE = "date"
    NUMBER = "number"
    PHONE = "phone"
    INSURANCE_ID = "insurance_id"
    CHECKBOX = "checkbox"
    MULTIPLE_CHOICE = "multiple_choice"
    MEDICATION_LIST = "medication_list"
    SIGNATURE_PRESENCE = "signature_presence"


class ExtractionSource(str, Enum):
    TEMPLATE = "template"
    OCR = "ocr"
    VISION = "vision"
    OCR_VISION_FUSED = "ocr_vision_fused"
    HUMAN_CORRECTED = "human_corrected"


class BoundingBox(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def within_page(self):
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("bounding box must fit within normalized page")
        return self


class ValidationError(BaseModel):
    code: str
    message: str


class TemplateField(BaseModel):
    field_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    label: str
    field_type: FieldType
    bbox: BoundingBox
    required: bool = False
    expected_content: str = "printed_or_handwritten"
    normalizer: str | None = None
    review_threshold: float = Field(default=0.75, ge=0, le=1)
    options: list[str] = Field(default_factory=list)
    pattern: str | None = None


class TemplatePage(BaseModel):
    page_index: int = Field(ge=0)
    canonical_width: int = Field(gt=0)
    canonical_height: int = Field(gt=0)
    fields: list[TemplateField]

    @model_validator(mode="after")
    def unique_fields(self):
        ids = [f.field_id for f in self.fields]
        if len(ids) != len(set(ids)):
            raise ValueError("field IDs must be unique per page")
        return self


class TemplateDefinition(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    template_id: str = Field(pattern=r"^T\d{2}$")
    template_name: str
    version: int = Field(ge=1)
    page_count: int = Field(gt=0)
    pages: list[TemplatePage]

    @model_validator(mode="after")
    def page_count_matches(self):
        if len(self.pages) != self.page_count:
            raise ValueError("page_count must match pages")
        return self


class PageQuality(BaseModel):
    blur_score: float = Field(ge=0, le=1)
    contrast_score: float = Field(ge=0, le=1)
    glare_score: float = Field(ge=0, le=1)
    overall: float = Field(ge=0, le=1)


class PreprocessingResult(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    page_index: int
    original_width: int
    original_height: int
    normalized_width: int
    normalized_height: int
    orientation_degrees: int = 0
    deskew_angle_degrees: float = 0
    perspective_corrected: bool = False
    quality: PageQuality
    artifacts: dict[str, str]


class ExtractedField(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    field_id: str
    label: str
    field_type: FieldType
    bbox: BoundingBox
    raw_value: Any = None
    normalized_value: Any = None
    confidence: float = Field(ge=0, le=1)
    source: ExtractionSource
    needs_review: bool
    validation_errors: list[ValidationError] = Field(default_factory=list)
    alternatives: list[Any] = Field(default_factory=list)
    score_components: dict[str, float] = Field(default_factory=dict)


class ExtractedPage(BaseModel):
    page_index: int
    image_url: str
    width: int
    height: int
    alignment_confidence: float = Field(ge=0, le=1)
    fields: list[ExtractedField]


class ExtractionSummary(BaseModel):
    field_count: int
    review_count: int
    processing_ms: int


class ExtractionRun(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str
    sample_id: str | None = None
    template_id: str | None = None
    template_match_confidence: float = Field(ge=0, le=1)
    status: str
    pages: list[ExtractedPage]
    summary: ExtractionSummary


class EvaluationMetrics(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    split: str
    sample_count: int
    field_count: int
    overall_accuracy: float
    printed_accuracy: float | None = None
    handwritten_accuracy: float | None = None
    checkbox_accuracy: float | None = None
    signature_presence_accuracy: float | None = None
    review_rate: float
    error_capture_rate: float
    by_template: dict[str, float] = Field(default_factory=dict)
    by_field_type: dict[str, float] = Field(default_factory=dict)
    failures: list[dict[str, Any]] = Field(default_factory=list)
