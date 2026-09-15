from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

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
    # The point size the form was actually filled at in this region, recovered when the
    # template was learned by asking what size reproduces the width the text really
    # occupied. Regeneration uses it directly. Sizing from the *box* instead is a guess,
    # and on a ruled field the box is the whole writable band, so the guess ran wide and
    # pushed every value off-centre by an amount that grew with its length. Width is the
    # thing measured, not height: a word's height depends on whether it happens to
    # contain an ascender or a descender, its width does not. ``None`` on templates
    # learned before this existed, and on fields blank when the template was learned.
    # Stored as a fraction of page height, never as points: a derived template's page
    # is measured in the pixels of the render it was learned from (1275x1650 for a
    # 150dpi letter page), not in the 612x792 points of the original PDF, so a size in
    # absolute units means different things on the two and comparing them directly is
    # how this was misdiagnosed in the first place.
    font_size: float | None = Field(default=None, gt=0, le=1)
    # How far in from the left of this region the text actually started, as a fraction of
    # page width. Measured rather than assumed: how far a form is filled in from its rule
    # differs between forms, and picking any single constant fits whichever form it was
    # chosen against and misplaces the others.
    text_indent: float | None = Field(default=None, ge=0, le=1)
    # What the blank form prints *inside* this region -- a sub-caption, the options of a
    # tick row, the neighbouring caption a box drawn a little wide has caught. Read from
    # the blank when the template was learned, so a filled copy read through the slot can
    # subtract it: whatever remains is what the patient added. Measured before this
    # existed, 321 of 527 values that slots added to the benchmark were the form's own
    # printing. ``None`` on templates learned before it existed.
    printed_text: str | None = None
    # Placed by a reviewer looking at a filled copy, not inferred from the blank. A taught
    # slot outranks the page's own reading for its question; an inferred one only adds.
    taught: bool = False
    required: bool = False
    expected_content: str = "printed_or_handwritten"
    normalizer: str | None = None
    review_threshold: float = Field(default=0.75, ge=0, le=1)
    options: list[str] = Field(default_factory=list)
    pattern: str | None = None


class SlotCorrection(BaseModel):
    """Where a reviewer says an answer lives on a form read through a template."""

    page_index: int = Field(ge=0)
    label: str
    bbox: BoundingBox
    value: str | None = None


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
    # Two digits held fifty derived ids, T50-T99, and that was the whole capacity of the
    # enrolled track: the 51st blank a clinic (or the benchmark) learned failed with "no
    # derived template ids remain", which the benchmark runner read as "not derivable"
    # for a day. A practice with more than fifty forms is not exotic.
    template_id: str = Field(pattern=r"^T\d{2,4}$")
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
