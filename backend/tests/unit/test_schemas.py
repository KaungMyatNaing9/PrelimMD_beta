import pytest
from pydantic import ValidationError

from app.schemas.models import BoundingBox, ExtractedField, ExtractionSource, FieldType


def test_bbox_must_remain_inside_page():
    with pytest.raises(ValidationError):
        BoundingBox(x=0.9, y=0.1, width=0.2, height=0.1)


def test_confidence_bounds_are_enforced():
    with pytest.raises(ValidationError):
        ExtractedField(
            field_id="x",
            label="X",
            field_type=FieldType.DATE,
            bbox=BoundingBox(x=0, y=0, width=0.1, height=0.1),
            confidence=1.1,
            source=ExtractionSource.OCR,
            needs_review=False,
        )
