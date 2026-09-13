"""What separates an answer from the form's own printing.

Measured across TEST2 and TEST4, the pipeline proposed 379 values for 132 real answers --
two thirds of everything a reviewer saw was printed form text, at a mean confidence of
0.90. No existing test covered that, because `evaluate_real_forms.py` scores only labels
the ground truth names, so an invented field could not lower any reported number.
"""

from app.services.form_analyzer import _is_printed_prose, is_blank_marker


class TestBlankIsBlank:
    """A value with nothing alphanumeric in it is empty space, whatever it is made of."""

    def test_rules_and_separators_are_blank(self):
        for text in ["____", "_ _ / _ _", "/", "(", "- -", "...", ". . ."]:
            assert is_blank_marker(text), text

    def test_symbol_font_checkbox_is_blank(self):
        # Wingdings and friends draw a tick box from the Unicode private use area.
        assert is_blank_marker("")

    def test_answers_are_not_blank(self):
        for text in ["04/17/1986", "Amara", "N/A", "PA", "3B"]:
            assert not is_blank_marker(text), text

    def test_empty_is_not_a_marker(self):
        assert not is_blank_marker("")
        assert not is_blank_marker(None)


class TestPrintedProse:
    """An answer is a noun phrase; the form's own writing is sentences.

    Across 256 answers written onto TEST2, TEST4, NEW and the dev forms, the function-word
    share of a real answer is 0.00 -- not one contains a single "the", "you" or "have".
    """

    def test_instructions_are_prose(self):
        for text in [
            "Please list all medications you are currently taking",
            "have you ever been diagnosed with any of the following",
            "What do you",
            "do you live?",
        ]:
            assert _is_printed_prose(text), text

    def test_real_answers_are_not_prose(self):
        for text in [
            "1420 Bellefield Avenue Apt 3B",
            "Dr Helen Osei-Bonsu",
            "Operations Manager",
            "amara.okafor@example.com",
            "Riverside Logistics",
        ]:
            assert not _is_printed_prose(text), text

    def test_short_runs_are_left_alone(self):
        # Two words cannot establish a share, and blanking them would cost real answers.
        assert not _is_printed_prose("of Patient")


class TestPhantomTemplateRelease:
    """A registered template whose folder has gone must still be removable.

    The registry caches what it loaded, so an entry whose directory was deleted without a
    reload stays live: matched against every upload, and previously undeletable through the
    API because `release` keyed on the folder existing. Restarting the server was the only
    cure, which is not something a benchmark run can do.
    """

    def test_release_clears_an_entry_with_no_folder(self, monkeypatch, tmp_path):
        from app.services import templates as mod

        monkeypatch.setattr(mod, "DERIVED_ROOT", tmp_path / "gone")
        monkeypatch.setattr(mod, "TEMPLATE_ROOTS", (tmp_path / "gone",))
        loaded = []
        monkeypatch.setattr(mod.registry, "load", lambda: loaded.append(True))
        monkeypatch.setattr(
            mod.registry, "all", lambda: [type("T", (), {"template_id": "T99"})()]
        )
        assert mod.release("T99") is True
        assert loaded, "registry was never reloaded, so the phantom would survive"

    def test_release_still_reports_an_unknown_template(self, monkeypatch, tmp_path):
        from app.services import templates as mod

        monkeypatch.setattr(mod, "DERIVED_ROOT", tmp_path / "gone")
        monkeypatch.setattr(mod, "TEMPLATE_ROOTS", (tmp_path / "gone",))
        monkeypatch.setattr(mod.registry, "all", list)
        assert mod.release("T99") is False


class TestInitialsAreNotFunctionWords:
    """A one-letter token is an initial, not a word.

    "a" is a function word, so "K. A. Sparrow" scored 1 of 3 and was blanked as prose --
    and so would "Amara A Okafor". None of the 256 answers the rule was calibrated on
    carried the initial "A", so only FUNSD's answers exposed it.
    """

    def test_a_name_with_a_middle_initial_survives(self):
        for name in ["K. A. Sparrow", "Amara A Okafor", "J. A. O'Brien"]:
            assert not _is_printed_prose(name), name

    def test_sentences_are_still_caught(self):
        assert _is_printed_prose("Is it well balanced")
        assert _is_printed_prose("Please list all medications you are currently taking")


class TestFurnitureThatIsNotAnAnswer:
    """Two shapes of printed text that were being reported as patient answers.

    Found by auditing `_looks_answered` against 144 known-false intake values: it vouched
    for 60 of them, including "you drink?" as the answer to "How much alcohol do".
    """

    def test_a_question_tail_is_not_an_answer(self):
        from app.services.form_analyzer import _is_question_tail

        for value in ["you drink?", "day?", "Detailed information?"]:
            assert _is_question_tail(value), value

    def test_real_answers_never_ask_a_question(self):
        from app.services.form_analyzer import _is_question_tail

        for value in ["Amara", "04/17/1986", "Riverside Logistics", "PA"]:
            assert not _is_question_tail(value), value

    def test_an_option_row_is_a_question_not_a_choice(self):
        from app.services.form_analyzer import _is_option_glyphs

        # A symbol font draws the boxes; OCR returns them as private-use characters.
        assert _is_option_glyphs("FM")
        assert _is_option_glyphs("M F")

    def test_single_values_survive(self):
        from app.services.form_analyzer import _is_option_glyphs

        for value in ["F", "PA", "Amara", "04/17/1986", "Riverside Logistics"]:
            assert not _is_option_glyphs(value), value


class TestSuppressionRunsAfterTheCompetition:
    """Blanking furniture must not change which reader wins the page.

    The count of answered fields is what scores the boxed and inline strategies against
    each other, so suppressing a value during the read does not merely remove it -- it
    moves the score. Measured: doing these same two tests during the read flipped one page
    to the boxed reader and cost three correct fields (TEST2 72.5% -> 68.1%), while doing
    them after the winner is chosen cost nothing at all.
    """

    def test_blank_furniture_clears_the_value_but_keeps_the_field(self):
        from app.schemas.form_understanding import DetectedFormField, RequiredStatus
        from app.schemas.models import BoundingBox, FieldType
        from app.services.form_analyzer import _blank_furniture

        bb = BoundingBox(x=0.1, y=0.1, width=0.1, height=0.02)
        field = DetectedFormField(
            field_id="f", page_index=0, label_text="How much alcohol do",
            normalized_label="how much alcohol do", field_type=FieldType.SHORT_TEXT,
            label_bbox=bb, answer_bbox=bb, required_status=RequiredStatus.UNKNOWN,
            required_confidence=0.3, detection_confidence=0.9, source="ocr",
            raw_value="you drink?", normalized_value="you drink?", is_blank=False,
            confidence=0.92, needs_review=False, mapping_confidence=0.0,
            mapping_reason="t", canonical_key=None,
        )
        fields = [field]
        _blank_furniture(fields)
        assert len(fields) == 1, "the field must survive -- only its value is cleared"
        assert fields[0].normalized_value is None
        assert fields[0].is_blank and fields[0].needs_review


class TestTheValueIsTheNextCaption:
    """A value that is exactly a caption is the next field's label, not an answer.

    Found by censusing what the *hybrid* emits -- every earlier census was geometry-only.
    The vision model reads "City ______" and reports "State", the caption beside it, as the
    answer: the same failure `_has_answer_space` prevents on the geometric path, which the
    vision path had no equivalent for because nobody had looked.
    """

    def test_a_bare_caption_is_not_an_answer(self):
        from app.services.form_analyzer import _is_a_caption

        for value in ["State", "Ethnicity", "Address", "Birth Date", "Age ________"]:
            assert _is_a_caption(value), value

    def test_an_answer_that_merely_contains_a_field_name_survives(self):
        from app.services.form_analyzer import _is_a_caption

        # Exact membership, never fuzzy: matching by token overlap also condemns this,
        # a real answer on five of the benchmark forms.
        assert not _is_a_caption("Bellefield Pharmacy")

    def test_ordinary_answers_survive(self):
        from app.services.form_analyzer import _is_a_caption

        for value in ["Amara", "PA", "04/17/1986", "Riverside Logistics", "Pittsburgh"]:
            assert not _is_a_caption(value), value


class TestLetterheadIsNotAField:
    """`P:` and `F:` on a form are the practice's phone and fax, not questions."""

    def test_single_letter_captions_are_letterhead(self):
        from app.services.form_analyzer import _is_letterhead

        for label in ["P", "F", "T", "@", "P:"]:
            assert _is_letterhead(label), label

    def test_two_letter_fields_survive(self):
        from app.services.form_analyzer import _is_letterhead

        # `MI` is a real field -- middle initial -- on the benchmark forms.
        for label in ["MI", "DOB", "Last Name", "SSN"]:
            assert not _is_letterhead(label), label
