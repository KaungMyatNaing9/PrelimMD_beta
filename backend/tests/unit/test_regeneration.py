"""Filling stored values back onto a blank template."""

import fitz
import pytest

from app.services.regeneration import regenerate, values_from_analysis
from app.services.templates import canonical_image_path, registry


@pytest.fixture
def template():
    return registry.get("T03")


def _words(pdf: bytes):
    """Every word in the regenerated page with its rectangle."""
    page = fitz.open(stream=pdf, filetype="pdf")[0]
    return [(w[4], fitz.Rect(w[0], w[1], w[2], w[3])) for w in page.get_text("words")]


def _field(template, field_id):
    return next(f for f in template.pages[0].fields if f.field_id == field_id)


def _rect_of(template, field_id):
    page = template.pages[0]
    box = _field(template, field_id).bbox
    w, h = page.canonical_width, page.canonical_height
    return fitz.Rect(box.x * w, box.y * h, (box.x + box.width) * w, (box.y + box.height) * h)


@pytest.mark.skipif(
    not canonical_image_path("T03").exists(), reason="blank master not generated"
)
def test_values_land_inside_their_target_fields(template):
    """The Gate 2 criterion: a filled value must sit within its own field box."""
    pdf = regenerate(template, {"patient.full_name": "Jordan Lee", "patient.age": "58"})
    words = _words(pdf)

    name_rect = _rect_of(template, "patient.full_name")
    placed = [r for text, r in words if text == "Jordan"]
    assert placed, "value was not rendered at all"
    assert name_rect.contains(placed[0]), f"{placed[0]} escaped its field {name_rect}"

    age_rect = _rect_of(template, "patient.age")
    age = [r for text, r in words if text == "58"]
    assert age and age_rect.contains(age[0])


@pytest.mark.skipif(
    not canonical_image_path("T03").exists(), reason="blank master not generated"
)
def test_a_ticked_checkbox_renders_and_an_unticked_one_does_not(template):
    ticked = {t for t, _ in _words(regenerate(template, {"consent.treatment": True}))}
    unticked = {t for t, _ in _words(regenerate(template, {"consent.treatment": False}))}
    assert "X" in ticked
    assert "X" not in unticked


@pytest.mark.skipif(
    not canonical_image_path("T03").exists(), reason="blank master not generated"
)
def test_signatures_are_never_synthesised(template):
    """Presence is recorded, but a signature must not be drawn onto the output."""
    pdf = regenerate(template, {"consent.signature_present": True})
    sig_rect = _rect_of(template, "signature.present")
    assert not [r for _, r in _words(pdf) if sig_rect.contains(r)]


@pytest.mark.skipif(
    not canonical_image_path("T03").exists(), reason="blank master not generated"
)
def test_multiline_values_keep_their_rows(template):
    pdf = regenerate(template, {"notes.visit": "First line.\nSecond line."})
    rows = {round(r.y0) for text, r in _words(pdf) if text in {"First", "Second"}}
    assert len(rows) == 2, "both lines should render on separate baselines"


def test_unknown_template_is_reported(template):
    template.template_id = "T99"
    with pytest.raises(FileNotFoundError):
        regenerate(template, {})


def test_values_from_analysis_skips_unmapped_and_empty_fields():
    class Field:
        def __init__(self, key, value):
            self.canonical_key, self.normalized_value = key, value

    class Analysis:
        def __init__(self):
            self.detected_fields = [
                Field("patient.age", "58"),
                Field(None, "orphan"),
                Field("patient.phone", None),
                Field("notes.visit", ""),
            ]

    assert values_from_analysis(Analysis()) == {"patient.age": "58"}
