"""Correcting a value must file what it replaced, never erase it."""

from datetime import UTC, datetime

from app.schemas.form_understanding import DetectedFormField, FieldRevision
from app.schemas.models import BoundingBox, FieldType


def _field(**over):
    box = BoundingBox(x=0.1, y=0.1, width=0.2, height=0.05)
    base = dict(
        field_id="f1", page_index=0, label_text="Home Phone",
        normalized_label="home phone", field_type=FieldType.PHONE,
        label_bbox=box, answer_bbox=box, required_confidence=0.3,
        detection_confidence=0.9, source="ocr_layout", mapping_confidence=0.9,
        mapping_reason="alias", raw_value="312-555-0109",
        normalized_value="312-555-0109", is_blank=False, confidence=0.82,
    )
    return DetectedFormField(**{**base, **over})


def test_a_field_starts_with_no_history():
    """History records replacements, so an untouched reading has nothing in it."""
    assert _field().history == []


def test_a_correction_files_the_reading_it_replaces():
    """The machine's reading and how sure it was must survive being corrected.

    Without this a chart review cannot tell an extraction error from a patient
    changing their mind -- the faculty review's objection to silent overwrites.
    """
    field = _field()
    field.history.append(
        FieldRevision(
            value=field.normalized_value,
            source=field.source,
            confidence=field.confidence,
            superseded_at=datetime.now(UTC),
            superseded_by="reviewer",
        )
    )
    field.normalized_value = "312-555-0199"
    field.source = "manual_correction"
    field.confidence = 1

    assert field.normalized_value == "312-555-0199"
    assert len(field.history) == 1
    was = field.history[0]
    assert was.value == "312-555-0109", "the replaced value must still be readable"
    assert was.source == "ocr_layout", "and where it came from"
    assert was.confidence == 0.82, "and how sure that reading was"
    assert was.superseded_by == "reviewer"


def test_history_keeps_every_revision_in_order():
    """Two corrections leave two entries, oldest first -- it is a log, not a slot."""
    field = _field()
    for value, who in (("a", "reviewer"), ("b", "clinician")):
        field.history.append(
            FieldRevision(
                value=field.normalized_value, source=field.source,
                confidence=field.confidence, superseded_at=datetime.now(UTC),
                superseded_by=who,
            )
        )
        field.normalized_value = value
        field.source = "manual_correction"
        field.confidence = 1

    assert [h.value for h in field.history] == ["312-555-0109", "a"]
    assert [h.superseded_by for h in field.history] == ["reviewer", "clinician"]
    assert field.normalized_value == "b"


def test_history_survives_serialisation():
    """The analysis is stored as JSON between requests, so the log must round-trip."""
    field = _field()
    field.history.append(
        FieldRevision(
            value="312-555-0109", source="ocr_layout", confidence=0.82,
            superseded_at=datetime.now(UTC), superseded_by="reviewer",
        )
    )
    again = DetectedFormField.model_validate_json(field.model_dump_json())
    assert again.history[0].value == "312-555-0109"
    assert again.history[0].confidence == 0.82
