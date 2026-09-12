"""Reading the stored record as a timeline, and comparing a patient's forms."""

from datetime import UTC, datetime, timedelta

from app.schemas.form_understanding import DetectedFormField, FieldRevision, FormAnalysis
from app.schemas.models import BoundingBox, FieldType
from app.services.history import diff, form_timeline, patient_key

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


def _field(key, value, **over):
    box = BoundingBox(x=0.1, y=0.1, width=0.2, height=0.05)
    base = {
        "field_id": f"f_{key}", "page_index": 0, "label_text": key, "normalized_label": key,
        "field_type": FieldType.SHORT_TEXT, "label_bbox": box, "answer_bbox": box,
        "required_confidence": 0.3, "detection_confidence": 0.9, "source": "ocr_layout",
        "canonical_key": key, "mapping_confidence": 0.9, "mapping_reason": "alias",
        "raw_value": value, "normalized_value": value, "is_blank": value is None,
        "confidence": 0.9,
    }
    return DetectedFormField(**{**base, **over})


def _analysis(*fields, analysis_id="run_1"):
    return FormAnalysis(
        analysis_id=analysis_id, source_type="scanned_image", pages=[],
        detected_fields=list(fields), form_fingerprint="x", processing_time_ms=1,
    )


def _patient(last="Villanueva", first="Rosa", born="03/08/1974", **extra):
    fields = [
        _field("patient.last_name", last),
        _field("patient.first_name", first),
        _field("patient.date_of_birth", born),
    ]
    fields += [_field(k, v) for k, v in extra.items()]
    return _analysis(*fields)


def test_a_patient_needs_both_a_name_and_a_date_of_birth():
    """Either alone would merge two people, so neither alone is enough."""
    assert patient_key(_patient()) is not None
    assert patient_key(_analysis(_field("patient.last_name", "Villanueva"))) is None
    assert patient_key(_analysis(_field("patient.date_of_birth", "03/08/1974"))) is None


def test_the_key_ignores_how_a_name_was_written():
    """Case and punctuation are not identity; the same person keeps the same key."""
    assert patient_key(_patient(last="VILLANUEVA")) == patient_key(_patient(last="Villanueva"))


def test_an_unread_form_still_has_one_event():
    """The extraction is itself a thing that happened. A timeline starting at the first
    human correction would imply the values before it came from nowhere."""
    events = form_timeline(_patient(), NOW)
    assert [e.kind for e in events] == ["extracted"]


def test_a_correction_appears_on_the_timeline_with_what_it_replaced():
    field = _field("patient.home_phone", "312-555-0200")
    field.history.append(
        FieldRevision(value="312-555-0109", source="ocr_layout", confidence=0.8,
                      superseded_at=NOW + timedelta(hours=1), superseded_by="a reviewer")
    )
    events = form_timeline(_analysis(field), NOW)
    assert [e.kind for e in events] == ["extracted", "corrected"]
    assert events[1].was == "312-555-0109"
    assert events[1].now == "312-555-0200"


def test_a_diff_reports_only_fields_both_forms_answered():
    """A key one form asks for and the other does not is a difference between
    questionnaires, not a change in the patient."""
    earlier = _patient(**{"patient.city": "Scranton", "patient.occupation": "Dental Hygienist"})
    later = _patient(**{"patient.city": "Pittsburgh"})
    assert [(c.key, c.was, c.now) for c in diff(earlier, later)] == [
        ("patient.city", "Scranton", "Pittsburgh")
    ]


def test_a_rewritten_but_identical_answer_is_not_a_change():
    assert diff(_patient(**{"patient.city": "Scranton"}),
                _patient(**{"patient.city": "SCRANTON "})) == []


def test_the_reading_is_dated_by_the_upload_not_the_last_edit(tmp_path):
    """``analysis.json`` is rewritten by every correction, so its mtime is the time of the
    latest change. Dating the extraction by it put the extraction *after* the corrections
    that followed it -- an order that cannot happen."""
    import os

    from app.services.history import read_at

    folder = tmp_path / "run_1"
    folder.mkdir()
    (folder / "source.jpg").write_bytes(b"x")
    (folder / "analysis.json").write_text("{}")
    os.utime(folder / "source.jpg", (1_000_000, 1_000_000))
    os.utime(folder / "analysis.json", (2_000_000, 2_000_000))
    assert read_at(folder) == datetime.fromtimestamp(1_000_000, tz=UTC)
