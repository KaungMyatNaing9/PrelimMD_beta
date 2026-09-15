"""Reconciling the two readers edge by edge, not page by page.

`_best_layout` chose whichever reader answered more fields. A reader that binds captions
as answers answers *more* -- 21 "filled" against 11 on a page where the eleven were right
-- so the page's reading flipped with the values written on it. These pin the rule that a
reader wins a field only by proposing a better value for it.
"""

from app.schemas.form_understanding import DetectedFormField
from app.schemas.models import BoundingBox, FieldType
from app.services.form_analyzer import _assign_fields as _raw_assign


def _assign_fields(boxed, inline):
    # The page-level leader, exactly as `_best_layout` would pick it.
    def answered(fields):
        return sum(1 for f in fields if f.normalized_value not in (None, '', False))
    leader = 'boxed' if answered(boxed) >= answered(inline) else 'inline'
    return _raw_assign(boxed, inline, leader)


def _field(label: str, value, y: float, lx: float = 0.05, ax: float = 0.3,
           aw: float = 0.3, source: str = "ocr", key: str | None = None,
           typ: FieldType = FieldType.SHORT_TEXT, confidence: float = 0.9) -> DetectedFormField:
    return DetectedFormField(
        field_id=f"{source}_{label}_{y}",
        page_index=0,
        label_text=label,
        normalized_label=label.lower(),
        field_type=typ,
        label_bbox=BoundingBox(x=lx, y=y, width=0.2, height=0.03),
        answer_bbox=BoundingBox(x=ax, y=y, width=aw, height=0.03),
        required_confidence=0.3,
        detection_confidence=0.9,
        source=source,
        canonical_key=key,
        mapping_confidence=0.9 if key else 0.0,
        mapping_reason="test",
        raw_value=value,
        normalized_value=value,
        is_blank=value is None,
        confidence=confidence if value is not None else 0.6,
    )


def _values(fields) -> dict[str, object]:
    return {f.label_text: f.normalized_value for f in fields}


class TestVolumeDoesNotWin:
    def test_caption_binding_reader_loses_to_the_reader_that_read_values(self):
        # The boxed reader "answers" three captions with the form's own printing; the
        # inline reader reads two real values. By count the boxed reader would win the
        # page and every value would be furniture.
        boxed = [
            _field("Last Name", "First Name", 0.1, source="ocr_layout"),
            _field("First Name", "Middle", 0.2, source="ocr_layout"),
            _field("City", "State", 0.3, source="ocr_layout"),
        ]
        inline = [
            _field("Last Name", "Okonkwo", 0.1, key="patient.last_name"),
            _field("First Name", "Ngozi", 0.2, key="patient.first_name"),
        ]
        merged = _values(_assign_fields(boxed, inline))
        assert merged["Last Name"] == "Okonkwo"
        assert merged["First Name"] == "Ngozi"
        # The caption nobody read a value for is kept as a field, and nothing invented.
        assert "City" in merged

    def test_each_reader_wins_the_fields_it_read_better(self):
        boxed = [
            _field("Last Name", "Okonkwo", 0.1, source="ocr_layout", key="patient.last_name"),
            _field("Phone", "Home Phone", 0.2, source="ocr_layout", key="patient.phone",
                   typ=FieldType.PHONE),
        ]
        inline = [
            _field("Last Name", "Last", 0.1, key="patient.last_name"),
            _field("Phone", "412-555-0134", 0.2, key="patient.phone", typ=FieldType.PHONE),
        ]
        merged = _values(_assign_fields(boxed, inline))
        assert merged == {"Last Name": "Okonkwo", "Phone": "412-555-0134"}


class TestOneSpanOneField:
    def test_a_value_serves_only_the_caption_that_wants_it_most(self):
        # Both captions claim the same span; the typed caption's value is plausible for it.
        date = _field("Date of Birth", "04/08/1987", 0.1, key="patient.dob",
                      typ=FieldType.DATE)
        thief = _field("Signature", "04/08/1987", 0.1, lx=0.6, ax=0.3,
                       source="ocr_layout")
        merged = _assign_fields([thief], [date])
        assert _values(merged) == {"Date of Birth": "04/08/1987", "Signature": None}

    def test_the_losing_caption_keeps_its_field_blank(self):
        wins = _field("City", "Erie", 0.5, key="patient.city")
        loses = _field("Town", "Erie", 0.5, lx=0.7, ax=0.3, source="ocr_layout")
        merged = _assign_fields([loses], [wins])
        blank = next(f for f in merged if f.label_text == "Town")
        assert blank.normalized_value is None and blank.is_blank and blank.needs_review


class TestAgreement:
    def test_both_readers_agreeing_beats_one_reader_alone(self):
        # A weaker value both readers found outranks a stronger one only one found, when
        # they compete for the same caption.
        agreed_boxed = _field("Employer", "Maplewood Library", 0.4, source="ocr_layout",
                              confidence=0.7)
        agreed_inline = _field("Employer", "Maplewood Library", 0.4, confidence=0.7)
        alone = _field("Employer", "Maplewood Library System Inc", 0.4, ax=0.65, aw=0.3,
                       confidence=0.95, source="ocr_layout")
        merged = _values(_assign_fields([agreed_boxed, alone], [agreed_inline]))
        assert merged == {"Employer": "Maplewood Library"}


class TestBlanks:
    def test_a_caption_with_no_value_is_still_a_field(self):
        boxed = [_field("Email", None, 0.6, source="ocr_layout")]
        inline = [_field("Email", None, 0.6)]
        merged = _assign_fields(boxed, inline)
        assert len(merged) == 1 and merged[0].source == "ocr_layout"

    def test_nothing_in_nothing_out(self):
        assert _assign_fields([], []) == []

    def test_result_is_in_reading_order(self):
        boxed = [_field("Zip", "16501", 0.3, source="ocr_layout", key="patient.zip")]
        inline = [_field("Last Name", "Okonkwo", 0.1), _field("City", "Erie", 0.2)]
        assert [f.label_text for f in _assign_fields(boxed, inline)] == [
            "Last Name", "City", "Zip"]


class TestRunExclusivity:
    def test_a_run_is_a_caption_or_a_value_never_both(self):
        # Stacked cells: the inline reader answers "City" with "Portland"; the boxed reader
        # takes "Portland" as the caption of the address printed under it.
        city = _field("City", "Portland", 0.3, key="patient.city")
        # "Portland" is nobody's caption, so the vocabulary cannot name it either.
        stacked = _field("Portland", "2647 Pinecrest Terrace", 0.32, lx=0.3, ax=0.3,
                         source="ocr_layout")
        stacked.label_bbox = city.answer_bbox
        merged = _values(_assign_fields([stacked], [city]))
        assert merged.get("City") == "Portland"
        assert "Portland" not in merged

    def test_the_losing_reader_adds_only_captions_the_vocabulary_knows(self):
        # The inline reader reads this page better as a whole, so it leads it.
        inline = [_field("Last Name", "Okonkwo", 0.1, key="patient.last_name"),
                  _field("City", "Erie", 0.2, key="patient.city"),
                  _field("Zip", "16501", 0.25, key="patient.zip")]
        boxed = [_field("REFERRED BY", "SPOUSE / SIGNIFICANT OTHER", 0.5, source="ocr_layout"),
                 _field("Employer", "Halcyon Dental", 0.6, source="ocr_layout",
                        key="patient.employer")]
        merged = _values(_assign_fields(boxed, inline))
        assert "REFERRED BY" not in merged
        assert merged["Employer"] == "Halcyon Dental"


class TestFurnitureWeighsNothing:
    def test_a_glued_caption_is_never_a_value(self):
        boxed = [_field("City", "STREETADDRESS", 0.3, source="ocr_layout", key="patient.city")]
        merged = _assign_fields(boxed, [])
        assert merged[0].normalized_value is None and merged[0].is_blank

    def test_a_value_crossing_a_caption_loses_to_one_that_does_not(self):
        crossing = _field("State", "ME ZIPCODE 04101", 0.4, source="ocr_layout",
                          key="patient.state", confidence=0.95)
        clean = _field("State", "ME", 0.4, key="patient.state", confidence=0.7)
        assert _values(_assign_fields([crossing], [clean])) == {"State": "ME"}


class TestSoftCosts:
    def test_an_instruction_length_caption_the_vocabulary_maps_keeps_its_value(self):
        race = _field("Please check the box(es) that describe your race", "Asian", 0.4,
                      key="patient.race")
        assert _values(_assign_fields([], [race])) == {
            "Please check the box(es) that describe your race": "Asian"}

    def test_a_heading_that_is_also_a_caption_keeps_its_value(self):
        contact = _field("Emergency Contact", "Lachlan Chandrasekar", 0.3,
                         source="ocr_layout", key="emergency.name")
        assert _values(_assign_fields([contact], [])) == {
            "Emergency Contact": "Lachlan Chandrasekar"}

    def test_a_stray_rule_glyph_does_not_make_a_value_furniture(self):
        group = _field("Group #", "#_GRP24741", 0.6, source="ocr_layout",
                       key="insurance.group_id")
        assert _values(_assign_fields([group], [])) == {"Group #": "#_GRP24741"}

    def test_an_unmapped_instruction_caption_still_yields_no_value(self):
        junk = _field("Please list every medication you take", "see attached list", 0.6,
                      source="ocr_layout")
        assert _assign_fields([junk], [])[0].normalized_value is None

    def test_the_losing_reader_adds_no_blank_fields(self):
        boxed = [_field("Race", None, 0.2, source="ocr_layout", key="patient.race"),
                 _field("Last Name", "Okonkwo", 0.1, source="ocr_layout", key="patient.last_name")]
        inline = [_field("Two or more races", None, 0.2, lx=0.3, key="patient.race")]
        assert [f.label_text for f in _assign_fields(boxed, inline)] == ["Last Name", "Race"]


class TestDatesKeepTheirOrder:
    def test_a_birth_caption_prefers_the_earliest_date_on_the_page(self):
        # One reader binds two spans to "Date of Birth"; the more confident one holds the
        # form's own date and has caught the wrong field.
        wrong = _field("Date of Birth", "06/23/2026", 0.2, ax=0.3, source="ocr_layout",
                       key="patient.date_of_birth", confidence=0.95)
        right = _field("Date of Birth", "11/02/1967", 0.2, ax=0.65, source="ocr_layout",
                       key="patient.date_of_birth", confidence=0.7)
        signed = _field("Date", "06/23/2026", 0.9, ax=0.3, source="ocr_layout")
        merged = _values(_assign_fields([wrong, right, signed], []))
        assert merged["Date of Birth"] == "11/02/1967"

    def test_a_plain_date_caption_prefers_the_latest(self):
        wrong = _field("Date", "11/02/1967", 0.9, ax=0.3, source="ocr_layout", confidence=0.95)
        right = _field("Date", "06/23/2026", 0.9, ax=0.65, source="ocr_layout", confidence=0.7)
        born = _field("DOB", "11/02/1967", 0.2, ax=0.3, source="ocr_layout",
                      key="patient.date_of_birth")
        merged = _values(_assign_fields([wrong, right, born], []))
        assert merged["Date"] == "06/23/2026"

    def test_one_date_on_the_page_says_nothing(self):
        from app.services.form_analyzer import _as_date

        only = _field("Date", "11/02/1967", 0.9, source="ocr_layout")
        assert _values(_assign_fields([only], [])) == {"Date": "11/02/1967"}
        assert _as_date("____ 04/08/1987") is not None
        assert _as_date("Erie") is None and _as_date("13/45/2020") is None


class TestCaptionsBeneathARule:
    def test_a_wide_row_region_does_not_swallow_the_captions_printed_under_it(self):
        # The phone row's region spans the page; the CITY / STATE / ZIP captions for the
        # row above sit inside it. They are different runs of words, not the same one.
        phone = _field("Phone Number", "412-555-0184", 0.424, ax=0.17, aw=0.78,
                       key="patient.phone", typ=FieldType.PHONE, source="ocr_layout")
        city = _field("CITY", "Pittsburgh", 0.422, lx=0.26, ax=0.13, aw=0.27,
                      key="patient.city", source="ocr_layout")
        city.label_bbox = BoundingBox(x=0.26, y=0.422, width=0.02, height=0.01)
        city.answer_bbox = BoundingBox(x=0.13, y=0.392, width=0.27, height=0.026)
        merged = _values(_assign_fields([phone, city], []))
        assert merged == {"Phone Number": "412-555-0184", "CITY": "Pittsburgh"}
