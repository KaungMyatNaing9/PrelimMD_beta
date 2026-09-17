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




class TestTranscription:
    def test_a_misread_digit_is_corrected(self):
        from app.schemas.models import FieldType
        from app.services.form_analyzer import _prefer_transcription

        assert _prefer_transcription("42/26/2006", "12/26/2006", FieldType.DATE) == "12/26/2006"
        assert _prefer_transcription("akag aWwa", "Nakagawa", FieldType.SHORT_TEXT) == "Nakagawa"

    def test_an_unrelated_reading_is_not_adopted(self):
        from app.schemas.models import FieldType
        from app.services.form_analyzer import _prefer_transcription

        assert _prefer_transcription("Savannah", "Willow Creek Pharmacy", FieldType.SHORT_TEXT) == "Savannah"

    def test_empty_ocr_takes_only_a_credible_answer(self):
        from app.schemas.models import FieldType
        from app.services.form_analyzer import _prefer_transcription

        assert _prefer_transcription(None, "Savannah", FieldType.SHORT_TEXT) == "Savannah"
        assert _prefer_transcription(None, "City", FieldType.SHORT_TEXT) is None
        assert _prefer_transcription(None, "", FieldType.SHORT_TEXT) is None
        assert _prefer_transcription(None, "STREET,", FieldType.DATE) is None

    def test_the_blanks_printing_is_taken_out_of_the_models_reading(self):
        from app.services.form_analyzer import _strip_printed_tokens

        assert _strip_printed_tokens("Name: Analise", "Name: ____") == "Analise"
        assert _strip_printed_tokens("Analise", None) == "Analise"

    def test_the_sheets_numbered_lines_are_parsed(self):
        from app.services.vision_extractor import _parse_strips

        text = "1: Nakagawa\n2: EMPTY\n**3:** 12/26/2006\n4. ingrid.nakagawa@example.com\nnoise\n"
        assert _parse_strips(text) == {0: "Nakagawa", 1: "", 2: "12/26/2006", 3: "ingrid.nakagawa@example.com"}

    def test_captions_are_cut_off_both_ends_of_the_models_reading(self):
        from app.services.form_analyzer import _without_captions

        assert _without_captions("ge: Vietnamese", "Preferred Language") == "Vietnamese"
        assert _without_captions("ITY Manchester", "City") == "Manchester"
        assert _without_captions("Savannah, State: 31405", "City") == "Savannah"
        assert _without_captions("Address: 9790 Peregrine Street, City: Savannah", "Address") == "9790 Peregrine Street"
        assert _without_captions("/_ 02/22/2026", "Date of Birth") == "02/22/2026"
        assert _without_captions("Nakagawa", "Last Name") == "Nakagawa"
        assert _without_captions("GA", "State") == "GA"


class TestContainment:
    def test_a_whole_word_part_is_the_tighter_reading(self):
        from app.services.form_analyzer import _strictly_within

        assert _strictly_within(_field("L", "TN", 0.1, 0.1), _field("L", "TN 37421", 0.1, 0.1))
        assert _strictly_within(
            _field("L", "2986 Lantern Hill Road", 0.1, 0.1),
            _field("L", "2986 Lantern Hill Road Apt/Trailer", 0.1, 0.1),
        )

    def test_a_piece_of_a_word_is_a_misread(self):
        from app.services.form_analyzer import _strictly_within

        assert not _strictly_within(_field("L", "rid", 0.1, 0.1), _field("L", "Ingrid", 0.1, 0.1))
        assert not _strictly_within(_field("L", "akag aWwa", 0.1, 0.1), _field("L", "Nakagawa", 0.1, 0.1))
        assert not _strictly_within(_field("L", "Ingrid", 0.1, 0.1), _field("L", "Ingrid", 0.1, 0.1))

    def test_spacing_inside_a_word_does_not_matter(self):
        from app.services.form_analyzer import _strictly_within

        assert _strictly_within(
            _field("L", "GRP 75930", 0.1, 0.1), _field("L", "AAAS EALAW Swe GRP75930", 0.1, 0.1)
        )
        assert not _strictly_within(_field("L", "GRP 75930", 0.1, 0.1), _field("L", "GRP75930", 0.1, 0.1))


class TestPrintedRing:
    def test_the_caption_beside_a_slot_takes_its_own_token(self):
        from app.schemas.models import BoundingBox
        from app.services.form_analyzer import _printed_left
        from app.services.word_source import Word

        box = BoundingBox(x=0.30, y=0.50, width=0.20, height=0.02)
        # Page 1000x1000: the caption "Pharmacy:" ends just left of the slot, the value inside it.
        caption = Word(280, 505, 298, 515, "Pharmacy:", 0, 0.9)
        value = [Word(305, 505, 380, 515, "Lakeshore", 0, 0.9),
                 Word(390, 505, 450, 515, "Pharmacy", 0, 0.9)]
        assert _printed_left("Pharmacy:", [caption, *value], box, 1000, 1000) is None
        assert _printed_left("Pharmacy:", value, box, 1000, 1000) == "pharmacy"
        assert _printed_left(None, value, box, 1000, 1000) is None

    def test_the_blanks_printed_words_cut_a_photographs_fragments(self):
        from app.services.form_analyzer import _without_captions

        assert _without_captions("ity Spokane", "City", "City: State: Zip Code:") == "Spokane"
        assert _without_captions("S caspian.beauchamp@example.com", "Email Address", None) == "caspian.beauchamp@example.com"
        assert _without_captions("Adeyemi SS", "Last Name", "Last Name: SSN:") == "Adeyemi"
        assert _without_captions("Person Zephyr Beauchamp", "Emergency Contact", "Contact Person:") == "Zephyr Beauchamp"
        assert _without_captions("Name Analise", "First Name", "First Name:") == "Analise"
        assert _without_captions("T Ravensworth", "Last Name", "PATIENT LAST NAME") == "Ravensworth"
        assert _without_captions("Willow Creek Pharmacy", "Pharmacy", "Pharmacy:") == "Willow Creek Pharmacy"
        assert _without_captions("Male", "Sex", "Male Female") == "Male"


class TestFormDate:
    def test_a_bare_date_is_the_forms_date_not_the_birth_date(self):
        from app.services.canonical_registry import match

        assert match("Date")[0].canonical_key == "form.date"
        assert match("Today's Date")[0].canonical_key == "form.date"
        assert match("Signature Date")[0].canonical_key == "form.date"
        assert match("Date of Birth")[0].canonical_key == "patient.date_of_birth"
        assert match("Birth Date")[0].canonical_key == "patient.date_of_birth"
        assert match("DOB")[0].canonical_key == "patient.date_of_birth"


class TestValueRunEnd:
    def _line(self, spec):
        from app.services.word_source import Word

        # spec: (text, x0, x1); one line of 10pt words on a common baseline.
        return [Word(x0, 100, x1, 110, text, 0, 0.95) for text, x0, x1 in spec]

    def test_a_one_word_caption_inside_a_name_does_not_cut_it(self):
        from app.services.form_analyzer import _value_run_end

        line = self._line([("Preferred", 0, 40), ("Pharmacy:", 42, 85), ("Lakeshore", 92, 130), ("Pharmacy", 132, 170)])
        assert _value_run_end(line, 2, len(line)) == 4
        line = self._line([("Pharmacy:", 0, 43), ("Third", 50, 73), ("Street", 76, 103), ("Drug", 106, 128)])
        assert _value_run_end(line, 1, len(line)) == 4

    def test_a_case_change_or_a_gap_still_marks_the_next_caption(self):
        from app.services.form_analyzer import _value_run_end

        line = self._line([("EMERGENCY", 0, 60), ("CONTACT", 62, 100), ("Ngozi", 106, 130), ("Okafor", 133, 165), ("RELATIONSHIP", 168, 240)])
        assert _value_run_end(line, 2, len(line)) == 4
        line = self._line([("City:", 0, 25), ("Chattanooga", 30, 90), ("State", 110, 135), ("TN", 140, 152)])
        assert _value_run_end(line, 1, len(line)) == 2

    def test_a_caption_of_two_words_cuts_as_before(self):
        from app.services.form_analyzer import _value_run_end

        line = self._line([("Race", 0, 22), ("Black/African", 25, 90), ("American", 93, 135), ("Preferred", 138, 180), ("Language", 183, 225), ("English", 228, 262)])
        assert _value_run_end(line, 1, len(line)) == 3


class TestTranscriptionParts:
    def test_the_models_reading_corrects_a_caption_fragment_in_front_of_the_value(self):
        from app.schemas.models import FieldType
        from app.services.form_analyzer import _prefer_transcription

        assert _prefer_transcription("ress, Fresno", "Fresno", FieldType.SHORT_TEXT) == "Fresno"
        assert _prefer_transcription("Person Zephyr Beauchamp", "Zephyr Beauchamp", FieldType.SHORT_TEXT) == "Zephyr Beauchamp"
        assert _prefer_transcription("akag aWwa", "Nakagawa", FieldType.SHORT_TEXT) == "Nakagawa"

    def test_a_title_with_a_full_stop_is_not_a_caption_fragment(self):
        from app.services.form_analyzer import _without_captions

        assert _without_captions("Dr. Marisol Vandermeer", "Primary Care Physician", None) == "Dr. Marisol Vandermeer"
        assert _without_captions("il; oluwafemi@example.com", "Email", None) == "oluwafemi@example.com"

    def test_glued_caption_letters_are_split_off(self):
        from app.services.form_analyzer import _without_captions

        assert _without_captions("yPortland", "City", None) == "Portland"
        assert _without_captions("eCT", "State", None) == "CT"
        assert _without_captions("p04101", "Zip", None) == "04101"
        assert _without_captions("e03/02/2026", "Date", None) == "03/02/2026"
        assert _without_captions("eNative Hawaiian", "Race", None) == "Native Hawaiian"
        assert _without_captions("e) Male", "Gender", None) == "Male"
        assert _without_captions("u:Son", "Relation to you", None) == "Son"
        assert _without_captions("eBay", "Employer", None) == "eBay"


class TestBlankPrinting:
    def test_an_option_list_the_blank_prints_is_not_an_answer(self):
        from app.services.form_analyzer import _is_printing

        assert _is_printing("Male Female", ["sex", "male", "female"], ["sex", "male", "female"])
        assert _is_printing("Interpreter Needed? Yes / No", ["interpreter", "needed", "yes", "no"], ["interpreter", "needed", "yes", "no"])
        assert _is_printing("Male/Female", ["male", "female"], ["male", "female"])

    def test_a_word_written_over_its_own_printing_is_an_answer(self):
        from app.services.form_analyzer import _is_printing

        assert not _is_printing("Male", ["male", "male", "female"], ["male", "female"])

    def test_a_patients_words_are_never_printing(self):
        from app.services.form_analyzer import _is_printing

        assert not _is_printing("Analise", ["first", "name", "analise"], ["first", "name"])
        assert not _is_printing("TX", ["tx"], [])
        assert not _is_printing("Savannah Name", ["savannah", "name"], ["name"])
        # A name like a printed word is still the patient's: the page carries both.
        assert not _is_printing("Mariana", ["marital", "status", "mariana"], ["marital", "status"])

    def test_ocr_misreadings_and_cut_pieces_of_printing_are_still_printing(self):
        from app.services.form_analyzer import _is_printing

        assert _is_printing("OMale Female", ["omale", "female"], ["male", "female"])
        assert _is_printing("J Home !4J Cell", ["j", "home", "4j", "cell"], ["home", "cell", "work"])
        assert _is_printing("0 Male 0 Female", ["0", "male", "0", "female"], ["male", "female"])
        assert _is_printing("kelavionsnip to", ["kelavionsnip", "to"], ["relationship", "to"])
        assert _is_printing("ERS", ["home", "phone", "numbers"], ["home", "phone", "numbers"])

    def test_tick_box_answers_are_left_to_the_ink(self):
        from app.schemas.models import FieldType
        from app.services.form_analyzer import _blank_printing
        from app.services.word_source import Word

        married = _field("Marital Status", "Married", 0.3, 0.5, w=0.1, h=0.02)
        married.field_type = FieldType.MULTIPLE_CHOICE
        written = _field("Sex", "Male Female", 0.3, 0.7, w=0.1, h=0.02)
        layer = [("single married", 0.33, 0.51), ("male female", 0.33, 0.71)]
        words = [Word(300, 505, 360, 515, "Single Married", 0, 0.9), Word(300, 705, 360, 715, "Male Female", 0, 0.9)]
        _blank_printing([married, written], layer, words, 1000, 1000)
        assert married.normalized_value == "Married"
        assert written.normalized_value is None

    def test_a_mark_measured_over_printed_text_says_nothing(self):
        from app.schemas.models import FieldType
        from app.services.form_analyzer import _blank_printing

        consent = _field("Signature", True, 0.1, 0.8, w=0.6, h=0.05)
        consent.field_type = FieldType.SIGNATURE_PRESENCE
        tick = _field("Consent", True, 0.1, 0.2, w=0.02, h=0.015)
        tick.field_type = FieldType.CHECKBOX
        layer = [("i hereby authorize the release", 0.3, 0.82), ("of my records", 0.5, 0.83), ("consent", 0.2, 0.19)]
        _blank_printing([consent, tick], layer, [], 1000, 1000)
        assert consent.normalized_value is None
        assert tick.normalized_value is True


class TestCorrectingTheEnrolledCopy:
    def test_the_patients_answer_leaves_the_printed_layer(self):
        from app.schemas.models import BoundingBox
        from app.services.template_builder import _layer_without, _without_tokens

        box = BoundingBox(x=0.30, y=0.50, width=0.20, height=0.02)
        layer = [("sex", 0.25, 0.51), ("male", 0.32, 0.51), ("female", 0.38, 0.51), ("male", 0.45, 0.51), ("tx", 0.9, 0.9)]
        # Patient A wrote "Male" at 0.45: one "male" goes, the printed option stays.
        assert _layer_without(layer, box, "Male") == [("sex", 0.25, 0.51), ("female", 0.38, 0.51), ("male", 0.45, 0.51), ("tx", 0.9, 0.9)] or \
            _layer_without(layer, box, "Male") == [("sex", 0.25, 0.51), ("male", 0.32, 0.51), ("female", 0.38, 0.51), ("tx", 0.9, 0.9)]
        assert _without_tokens("Sex: Male Female Male", "Male") == "Sex: Female Male"
        assert _without_tokens(None, "Male") is None
