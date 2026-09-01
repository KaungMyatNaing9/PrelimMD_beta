"""Filling stored values back onto a blank template."""

import fitz
import pytest

from app.services.regeneration import regenerate, values_from_analysis
from app.services.templates import canonical_image_path, registry


@pytest.fixture
def template():
    # A copy, not the registry's own object: one test renames the template to prove an
    # unknown id is reported, and mutating the cached instance leaked that rename into
    # every test that ran afterwards.
    return registry.get("T03").model_copy(deep=True)


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


def test_values_from_analysis_skips_blank_fields_but_keeps_unmapped_ones():
    class Field:
        def __init__(self, key, label, value):
            self.canonical_key, self.label_text, self.normalized_value = key, label, value

    class Analysis:
        def __init__(self):
            self.detected_fields = [
                Field("patient.age", "Age", "58"),
                Field(None, "Blood group", "O+"),
                Field("patient.phone", "Phone", None),
                Field("notes.visit", "Visit notes", ""),
            ]

    values = values_from_analysis(Analysis())
    assert values["patient.age"] == "58"
    # Unmapped fields carry most of an unknown form's content, so they must survive.
    assert values["blood_group"] == "O+"
    assert "patient.phone" not in values and "phone" not in values
    assert "notes.visit" not in values


@pytest.mark.skipif(
    not canonical_image_path("T03").exists(), reason="blank master not generated"
)
def test_a_single_line_value_sits_on_the_rule_at_the_foot_of_its_field(template):
    """Where a value is drawn vertically, which is the ~2mm placement criterion.

    A person writes *on* the rule at the foot of the space. Laying text from the top of
    the region instead put every value about 4.5mm high -- a constant offset, and the
    single largest placement error measured on real forms.
    """
    pdf = regenerate(template, {"patient.full_name": "Jordan Lee"})
    rect = _rect_of(template, "patient.full_name")
    placed = [r for text, r in _words(pdf) if text == "Jordan"]
    assert placed, "value was not rendered at all"

    # Its foot is near the field's foot, not floating up at the top of the region.
    from_bottom = rect.y1 - placed[0].y1
    assert 0 <= from_bottom < rect.height * 0.25, (
        f"value sits {from_bottom:.1f}pt above the foot of a {rect.height:.1f}pt field"
    )


@pytest.mark.skipif(
    not canonical_image_path("T03").exists(), reason="blank master not generated"
)
def test_a_multi_line_value_starts_at_the_top_and_grows_down(template):
    """The opposite convention, and the reason single-line placement is a special case.

    A notes box is written into from its first line downwards, so top alignment is right
    there. Bottom-aligning a multi-line value would push its later lines off the field.
    """
    rect = _rect_of(template, "notes.visit")
    pdf = regenerate(template, {"notes.visit": "First line\nSecond line"})
    words = _words(pdf)
    first = [r for text, r in words if text == "First"]
    second = [r for text, r in words if text == "Second"]
    assert first and second, "multi-line value was not rendered"

    assert first[0].y0 < second[0].y0, "lines are out of order"
    assert first[0].y0 - rect.y0 < rect.height * 0.5, "first line did not start near the top"
    assert rect.contains(second[0]), f"{second[0]} escaped its field {rect}"
