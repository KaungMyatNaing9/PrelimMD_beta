"""A learned template may only add to what the page itself read.

Measured on all 85 LARGE blanks (2026-09-13), letting the template outrank the page read
*worse* than no template: 64.9% -> 61.1% where a form had its own template, 14 -> 1 on one
PDF. These pin the rule that cannot lose: the page's reading is a floor.
"""

from app.schemas.form_understanding import DetectedFormField
from app.schemas.models import BoundingBox, FieldType
from app.services.form_analyzer import _merge_template


def _field(label: str, value, x: float, y: float, w: float = 0.2, h: float = 0.03,
           source: str = "ocr") -> DetectedFormField:
    box = BoundingBox(x=x, y=y, width=w, height=h)
    return DetectedFormField(
        field_id=f"{source}_{label}",
        page_index=0,
        label_text=label,
        normalized_label=label.lower(),
        field_type=FieldType.SHORT_TEXT,
        label_bbox=box,
        answer_bbox=box,
        required_confidence=0.3,
        detection_confidence=0.9,
        source=source,
        mapping_confidence=0.5,
        mapping_reason="test",
        raw_value=value,
        normalized_value=value,
        is_blank=value is None,
    )


def _values(fields) -> list[tuple[str, object]]:
    return [(f.label_text, f.normalized_value) for f in fields]


class TestPageLeads:
    def test_a_slot_never_replaces_a_value_the_page_read(self):
        page = [_field("Last Name", "Okonkwo", 0.1, 0.1)]
        slot = [_field("Last Name", "Last Name", 0.1, 0.1, source="template")]
        assert _values(_merge_template(slot, page)) == [("Last Name", "Okonkwo")]

    def test_the_page_comes_first_in_the_result(self):
        page = [_field("City", "Erie", 0.1, 0.5)]
        slot = [_field("Zip", "16501", 0.6, 0.5, source="template")]
        merged = _merge_template(slot, page)
        assert [f.source for f in merged] == ["ocr", "template"]

    def test_a_wide_page_region_covering_the_slot_still_leads(self):
        # An inline reading's region can span the row; the slot's centre falls inside it.
        page = [_field("Address", "12 Oak St", 0.05, 0.3, w=0.9)]
        slot = [_field("Address", "12 Oak", 0.4, 0.3, w=0.1, source="template")]
        assert _values(_merge_template(slot, page)) == [("Address", "12 Oak St")]


class TestTemplateAdds:
    def test_a_slot_fills_a_value_the_page_missed(self):
        page = [_field("Last Name", "Okonkwo", 0.1, 0.1)]
        slot = [_field("Date of Birth", "04/08/1987", 0.1, 0.2, source="template")]
        assert _values(_merge_template(slot, page)) == [
            ("Last Name", "Okonkwo"),
            ("Date of Birth", "04/08/1987"),
        ]

    def test_a_slot_answers_where_the_page_left_a_field_blank(self):
        page = [_field("Email", None, 0.1, 0.4)]
        slot = [_field("Email", "a@b.org", 0.1, 0.4, source="template")]
        merged = _merge_template(slot, page)
        assert _values(merged) == [("Email", None), ("Email", "a@b.org")]

    def test_a_blank_slot_is_kept_only_where_the_page_has_no_field(self):
        page = [_field("Email", None, 0.1, 0.4)]
        slots = [
            _field("Email", None, 0.1, 0.4, source="template"),
            _field("Employer", None, 0.1, 0.7, source="template"),
        ]
        assert _values(_merge_template(slots, page)) == [("Email", None), ("Employer", None)]

    def test_no_template_changes_nothing(self):
        page = [_field("Last Name", "Okonkwo", 0.1, 0.1), _field("Email", None, 0.1, 0.4)]
        assert _merge_template([], page) == page


class TestQuestionIdentity:
    def test_a_slot_answering_a_question_the_page_answered_elsewhere_is_dropped(self):
        # The page read the birth date under "DOB"; the slot captioned "Date of Birth"
        # caught printed text at another position. Same canonical key, so it adds nothing.
        page = [_field("DOB", "11/02/1967", 0.1, 0.1)]
        page[0].canonical_key = "patient.dob"
        slot = [_field("Date of Birth", "STREET,", 0.5, 0.5, source="template")]
        slot[0].canonical_key = "patient.dob"
        assert _values(_merge_template(slot, page)) == [("DOB", "11/02/1967")]

    def test_the_same_caption_wording_counts_as_the_same_question(self):
        page = [_field("Zip Code", "71105", 0.1, 0.1)]
        slot = [_field("Zip Code", "State ____ LA", 0.5, 0.5, source="template")]
        assert _values(_merge_template(slot, page)) == [("Zip Code", "71105")]

    def test_a_different_question_is_still_added(self):
        page = [_field("DOB", "11/02/1967", 0.1, 0.1)]
        page[0].canonical_key = "patient.dob"
        slot = [_field("Date", "06/23/2026", 0.5, 0.5, source="template")]
        slot[0].canonical_key = "form.date"
        assert _values(_merge_template(slot, page)) == [
            ("DOB", "11/02/1967"), ("Date", "06/23/2026")]


class TestBlankSubtraction:
    """What the blank prints inside a slot is furniture; what a fill adds is the answer."""

    def _words(self, *texts):
        from app.services.word_source import Word

        return [Word(x0=10 * i, y0=0, x1=10 * i + 8, y1=8, text=t, block=0, confidence=0.9)
                for i, t in enumerate(texts)]

    def test_printed_tokens_are_removed_once_each(self):
        from app.services.form_analyzer import _without_printed

        words = self._words("APT/TRAILER", "#", "CITY", "____", "Shreveport")
        kept = _without_printed(words, "Apt/Trailer # City ____")
        assert [w.text for w in kept] == ["Shreveport"]

    def test_a_value_repeating_a_printed_word_keeps_the_extra(self):
        from app.services.form_analyzer import _without_printed

        words = self._words("Name", "Name", "Bellefield")
        assert [w.text for w in _without_printed(words, "Name")] == ["Name", "Bellefield"]

    def test_nothing_printed_means_nothing_removed(self):
        from app.services.form_analyzer import _without_printed

        words = self._words("Okonkwo", "-")
        assert [w.text for w in _without_printed(words, None)] == ["Okonkwo"]


class TestATaughtSlotLeads:
    def test_a_reviewers_slot_displaces_the_pages_wrong_reading_of_its_question(self):
        page = [_field("State", "H2A Visa", 0.1, 0.3)]
        page[0].canonical_key = "patient.state"
        slot = [_field("State", "TN", 0.6, 0.3, source="reviewer")]
        slot[0].canonical_key = "patient.state"
        assert _values(_merge_template(slot, page)) == [("State", "TN")]

    def test_a_reviewers_slot_comes_before_the_pages_fields(self):
        page = [_field("Last Name", "Okonkwo", 0.1, 0.1), _field("City", "Erie", 0.1, 0.5)]
        slot = [_field("Zip", "16501", 0.6, 0.5, source="reviewer")]
        merged = _merge_template(slot, page)
        assert [f.source for f in merged] == ["reviewer", "ocr", "ocr"]

    def test_an_inferred_slot_still_only_adds(self):
        page = [_field("State", "H2A Visa", 0.1, 0.3)]
        page[0].canonical_key = "patient.state"
        slot = [_field("State", "TN", 0.6, 0.3, source="template")]
        slot[0].canonical_key = "patient.state"
        assert _values(_merge_template(slot, page)) == [("State", "H2A Visa")]

    def test_a_taught_slot_that_read_nothing_displaces_nothing(self):
        page = [_field("State", "TN", 0.1, 0.3)]
        slot = [_field("State", None, 0.6, 0.3, source="reviewer")]
        assert _values(_merge_template(slot, page)) == [("State", "TN"), ("State", None)]

    def test_the_pages_tighter_reading_beats_a_slot_that_read_into_the_next_field(self):
        page = [_field("State", "TN", 0.1, 0.3)]
        page[0].canonical_key = "patient.state"
        slot = [_field("State", "TN 37421", 0.6, 0.3, source="reviewer")]
        slot[0].canonical_key = "patient.state"
        assert _values(_merge_template(slot, page)) == [("State", "TN")]

    def test_a_disagreement_without_containment_goes_to_the_reviewer(self):
        page = [_field("State", "H2A Visa", 0.1, 0.3)]
        page[0].canonical_key = "patient.state"
        slot = [_field("State", "TN", 0.6, 0.3, source="reviewer")]
        slot[0].canonical_key = "patient.state"
        assert _values(_merge_template(slot, page)) == [("State", "TN")]



class TestGluedCaptions:
    def test_a_caption_glued_to_the_value_by_a_rule_glyph_is_split_off(self):
        from app.services.form_analyzer import _without_printed
        from app.services.word_source import Word

        words = [Word(x0=0, y0=0, x1=80, y1=8, text="Name_Analise", block=0, confidence=0.9)]
        assert [w.text for w in _without_printed(words, "Name ____")] == ["Analise"]


class TestRegionRead:
    def test_an_enlarged_crop_reads_a_date_the_page_read_wrong(self, tmp_path):
        import pytest
        from PIL import Image, ImageDraw, ImageFont

        pytest.importorskip("pytesseract")
        from app.services.word_source import words_in_region

        page = Image.new("L", (600, 200), 255)
        draw = ImageDraw.Draw(page)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 16)
        except OSError:
            pytest.skip("no scalable font available")
        draw.text((60, 90), "12/26/2006", fill=0, font=font)
        path = tmp_path / "page.png"
        page.save(path)
        try:
            words = words_in_region(path, (50, 80, 200, 115), "eng")
        except Exception as exc:  # noqa: BLE001 - no Tesseract binary on this machine
            pytest.skip(str(exc))
        assert "".join(w.text for w in words).replace(" ", "") == "12/26/2006"
