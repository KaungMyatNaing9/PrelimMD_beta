"""Deriving a reusable template from a single filled form."""

import numpy as np
import pytest

from app.schemas.models import BoundingBox
from app.services.regeneration import label_key, values_from_analysis
from app.services.template_builder import _slug, blank_from_filled


def test_value_pixels_are_erased_but_the_rule_line_survives():
    page = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    # A box outline at 100..300, with "value" ink filling its interior.
    page[100, 100:300] = page[300, 100:300] = 0
    page[100:300, 100] = page[100:300, 300] = 0
    page[150:250, 150:250] = 0

    blank = blank_from_filled(page, [BoundingBox(x=0.1, y=0.1, width=0.2, height=0.2)])

    assert (blank[200, 200] == 255).all(), "interior ink should be erased"
    assert (blank[100, 200] == 0).all(), "the box's own rule line must survive"


def test_ink_flush_against_the_top_edge_is_still_erased():
    """Values sit at the top of tall fields; a size-scaled inset used to spare them."""
    page = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    page[105:130, 150:400] = 0  # text just inside a tall box starting at y=100

    # A plausible multi-line field: wide, but not so tall it becomes a section frame.
    blank = blank_from_filled(page, [BoundingBox(x=0.1, y=0.1, width=0.6, height=0.15)])

    assert blank[105:130, 150:400].all(), "text near the top edge should be gone"


def test_erasing_leaves_content_outside_the_box_untouched():
    page = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    page[50:70, 100:300] = 0  # a label above the box

    blank = blank_from_filled(page, [BoundingBox(x=0.1, y=0.1, width=0.2, height=0.2)])

    assert (blank[50:70, 100:300] == 0).all(), "labels are template, not values"


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Patient name", "patient_name"),
        ("Date of Birth", "date_of_birth"),
        ("Insurance ID #", "insurance_id"),
        ("", "field"),
        ("123 Numeric", "f_123_numeric"),
        ("F", "field_f"),
        ("#", "field"),
        ("M", "field_m"),
    ],
)
def test_slug_matches_the_field_id_pattern(label, expected):
    assert _slug(label, set()) == expected


def test_slug_disambiguates_repeated_labels():
    used = set()
    first = _slug("Phone", used)
    used.add(first)
    assert _slug("Phone", used) == "phone_2"


@pytest.mark.parametrize(
    "label", ["Surname", "Given names", "Policy number", "Blood group", "Reason for visit"]
)
def test_derived_field_ids_match_the_keys_regeneration_looks_up(label):
    """The contract linking the two halves.

    A derived template names unmapped fields by slug; regeneration keys their values the
    same way. If these ever diverge, an unknown form regenerates almost entirely blank,
    because most of its fields have no canonical mapping.
    """
    assert _slug(label, set()) == label_key(label)


def test_values_are_offered_under_both_canonical_and_label_keys():
    class Field:
        def __init__(self, key, label, value):
            self.canonical_key, self.label_text, self.normalized_value = key, label, value

    class Analysis:
        def __init__(self):
            self.detected_fields = [
                Field("patient.email", "Email", "a@b.com"),
                Field(None, "Blood group", "O+"),
            ]

    values = values_from_analysis(Analysis())
    assert values["patient.email"] == "a@b.com"
    assert values["email"] == "a@b.com", "canonical fields still need their label key"
    assert values["blood_group"] == "O+", "unmapped fields resolve by label alone"


class TestWidgetGeometry:
    """A fillable PDF's text widgets are the designer's own answer spaces."""

    def _pdf_with_widgets(self, tmp_path):
        import fitz

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        text = fitz.Widget()
        text.field_name = "Last Name"
        text.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        text.rect = fitz.Rect(100, 100, 300, 120)
        page.add_widget(text)
        tick = fitz.Widget()
        tick.field_name = "Male"
        tick.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
        tick.rect = fitz.Rect(100, 200, 112, 212)
        page.add_widget(tick)
        path = tmp_path / "form.pdf"
        doc.save(path)
        return path

    def test_text_widgets_arrive_in_render_pixels(self, tmp_path):
        from app.services.form_analyzer import PDF_RENDER_SCALE, widget_boxes

        boxes = widget_boxes(self._pdf_with_widgets(tmp_path), 0)
        assert len(boxes) == 1, "the tick box is not an answer space"
        assert boxes[0] == (100 * PDF_RENDER_SCALE, 100 * PDF_RENDER_SCALE,
                            300 * PDF_RENDER_SCALE, 120 * PDF_RENDER_SCALE)

    def test_a_page_without_widgets_yields_nothing(self, tmp_path):
        from app.services.form_analyzer import widget_boxes

        assert widget_boxes(self._pdf_with_widgets(tmp_path), 1) == []

    def test_a_missing_pdf_yields_nothing(self, tmp_path):
        from app.services.form_analyzer import widget_boxes

        assert widget_boxes(tmp_path / "absent.pdf", 0) == []


def test_every_template_page_has_its_own_master():
    from app.services.templates import preview_name

    # Page 0 keeps the name templates learned before pages existed already use.
    assert preview_name(0) == "blank_preview.png"
    assert preview_name(2) == "blank_preview_p2.png"


class TestLearningFromCorrections:
    """A reviewer's word on where an answer lives becomes that caption's slot."""

    def _template(self, tmp_path, monkeypatch):
        from app.schemas.models import TemplateDefinition, TemplatePage
        from app.services import template_builder as B

        definition = TemplateDefinition(
            template_id="T50", template_name="t", version=1, page_count=1,
            pages=[TemplatePage(page_index=0, canonical_width=100, canonical_height=100,
                                fields=[])],
        )
        folder = tmp_path / "T50"
        folder.mkdir()
        (folder / "template.json").write_text(definition.model_dump_json())
        monkeypatch.setattr(B, "DERIVED_ROOT", tmp_path)
        monkeypatch.setattr(B.registry, "get", lambda _id: definition)
        monkeypatch.setattr(B.registry, "load", lambda: None)
        return B

    def test_a_correction_becomes_a_slot_under_the_vocabulary_key(self, tmp_path, monkeypatch):
        from app.schemas.models import BoundingBox, SlotCorrection

        B = self._template(tmp_path, monkeypatch)
        taught = B.learn_corrections("T50", [SlotCorrection(
            page_index=0, label="Last Name",
            bbox=BoundingBox(x=0.2, y=0.1, width=0.3, height=0.03))])
        (slot,) = taught.pages[0].fields
        assert slot.field_id == "patient.last_name" and slot.label == "Last Name"
        assert slot.printed_text is None, "no master image, nothing known to be printed"

    def test_a_second_correction_for_the_same_caption_replaces_the_first(self, tmp_path, monkeypatch):
        from app.schemas.models import BoundingBox, SlotCorrection

        B = self._template(tmp_path, monkeypatch)
        first = SlotCorrection(page_index=0, label="City",
                               bbox=BoundingBox(x=0.1, y=0.5, width=0.2, height=0.03))
        second = SlotCorrection(page_index=0, label="City",
                                bbox=BoundingBox(x=0.6, y=0.5, width=0.2, height=0.03))
        taught = B.learn_corrections("T50", [first, second])
        (slot,) = taught.pages[0].fields
        assert slot.bbox.x == 0.6

    def test_a_page_the_blank_never_taught_is_added_in_order(self, tmp_path, monkeypatch):
        from app.schemas.models import BoundingBox, SlotCorrection

        B = self._template(tmp_path, monkeypatch)
        taught = B.learn_corrections("T50", [SlotCorrection(
            page_index=2, label="Employer",
            bbox=BoundingBox(x=0.1, y=0.5, width=0.2, height=0.03))])
        assert [p.page_index for p in taught.pages] == [0, 1, 2] and taught.page_count == 3
        assert taught.pages[2].fields[0].field_id == "employment.employer"
