"""Deriving a reusable template from a single filled form."""

import numpy as np
import pytest

from app.schemas.models import BoundingBox
from app.services.regeneration import label_key, values_from_analysis
from app.services.template_builder import _slug, answer_ink, master_from_page


def _page(shade: int = 255) -> np.ndarray:
    return np.full((400, 600, 3), shade, dtype=np.uint8)


def test_an_answer_is_lifted_and_the_rule_under_it_survives():
    page = _page()
    page[200:202, 50:550] = 0  # a rule
    page[170:200, 100:160] = 0  # an answer written on it, touching the rule

    master = master_from_page(page, [(100, 170, 160, 200)])

    assert (master[175:195, 105:155] > 200).all(), "the answer is lifted"
    assert (master[200:202, 60:540] == 0).all(), "the rule is kept, pixel for pixel"


def test_a_tinted_panel_stays_tinted_where_an_answer_was_lifted():
    page = _page(shade=225)
    page[170:200, 100:160] = 30

    master = master_from_page(page, [(100, 170, 160, 200)])

    lifted = master[175:195, 105:155].astype(int)
    assert (abs(lifted - 225) <= 12).all(), "filled from the panel around it, not painted white"


def test_everything_but_the_answers_is_left_as_it_was():
    page = _page()
    page[40:60, 100:300] = 0  # a caption
    page[170:200, 100:160] = 0  # an answer

    master = master_from_page(page, [(100, 170, 160, 200)])

    assert (master[40:60, 100:300] == 0).all()


def test_a_blank_form_is_its_own_master():
    page = _page()
    page[40:60, 100:300] = 0

    assert (master_from_page(page, [], []) == page).all()


def test_a_ticked_box_loses_its_mark_and_keeps_its_outline():
    page = _page()
    page[100, 100:140] = page[139, 100:140] = 0
    page[100:140, 100] = page[100:140, 139] = 0
    for i in range(10, 30):  # an X inside
        page[100 + i, 100 + i] = page[100 + i, 139 - i] = 0

    master = master_from_page(page, [], [(100, 100, 140, 140)])

    assert (master[112:128, 112:128] > 200).all(), "the mark is lifted"
    assert (master[100, 105:135] == 0).all() and (master[105:135, 100] == 0).all(), "the box stays"


def test_a_stroke_reaching_past_its_word_box_goes_with_the_answer():
    page = _page()
    page[200:202, 50:550] = 0  # the rule
    page[172:198, 120:122] = 0  # a stroke of the answer
    for i in range(14):  # ...curling down and left, past the box OCR drew round the word
        page[182 + i // 2: 184 + i // 2, 106 + i: 108 + i] = 0

    master = master_from_page(page, [(114, 170, 160, 200)])

    assert (master[182:190, 106:113] > 200).all(), "the part outside the word box is lifted too"
    assert (master[200:202, 60:540] == 0).all()


def test_a_short_rule_under_an_answer_survives():
    page = _page()
    page[200:202, 100:280] = 0  # a rule just long enough for the answer written on it
    for x in range(105, 275, 12):  # glyphs standing on it
        page[176:200, x:x + 3] = 0

    master = master_from_page(page, [(100, 174, 280, 200)])

    assert (master[180:195, 106:108] > 200).all(), "the answer is lifted"
    assert (master[200:202, 100:280] == 0).all(), "a rule longer than any stroke is kept"


def test_option_captions_beside_a_box_are_not_an_answer():
    from app.schemas.form_understanding import DetectedFormField
    from app.services.field_layout import Box
    from app.services.word_source import Word

    words = [
        Word(120, 100, 180, 120, "MtoF", 1, 0.4),  # a caption OCR glued, read as a value
        Word(300, 200, 420, 220, "Hollingsworth", 1, 0.9),
    ]
    boxes = [Box(100, 100, 118, 118), Box(200, 100, 218, 118)]

    def field(label, value, x, y, w, h):
        return DetectedFormField(
            field_id=label, page_index=0, label_text=label, normalized_label=label,
            field_type="short_text", label_bbox=BoundingBox(x=0, y=0, width=0.01, height=0.01),
            answer_bbox=BoundingBox(x=x, y=y, width=w, height=h), required_confidence=0.3,
            detection_confidence=0.9, source="ocr_layout", mapping_confidence=0.5,
            mapping_reason="", raw_value=value, normalized_value=value, is_blank=False,
        )

    fields = [field("Transgender", "MtoF", 0.2, 0.25, 0.1, 0.05), field("Name", "Hollingsworth", 0.5, 0.5, 0.2, 0.05)]
    inked, _ = answer_ink(fields, words, 600, 400, boxes)

    assert inked == [(300, 200, 420, 220)]


def test_a_text_value_read_across_a_row_of_tick_boxes_is_not_lifted():
    from app.schemas.form_understanding import DetectedFormField
    from app.services.field_layout import Box
    from app.services.word_source import Word

    # "Gender: M [ ]   F [ ]" read as the text "Mo F o": the F sits well clear of its box.
    words = [Word(100, 100, 112, 116, "Mo", 1, 0.5), Word(200, 100, 210, 116, "F", 1, 0.9)]
    boxes = [Box(118, 102, 128, 112), Box(240, 102, 250, 112)]
    gender = DetectedFormField(
        field_id="g", page_index=0, label_text="Gender", normalized_label="gender",
        field_type="short_text", label_bbox=BoundingBox(x=0, y=0, width=0.01, height=0.01),
        answer_bbox=BoundingBox(x=100 / 600, y=100 / 400, width=150 / 600, height=16 / 400),
        required_confidence=0.3, detection_confidence=0.9, source="ocr_layout",
        mapping_confidence=0.5, mapping_reason="", raw_value="Mo F o", normalized_value="Mo F o",
        is_blank=False,
    )

    inked, _ = answer_ink([gender], words, 600, 400, boxes)

    assert inked == []


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
