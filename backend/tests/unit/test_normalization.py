"""Caption and value clean-up on the form-understanding path."""


def test_a_printed_instruction_is_not_part_of_the_answer():
    """Forms print an instruction beside the field: "Marital Status (circle one) Married".
    Read literally the instruction becomes part of the answer."""
    from app.services.form_analyzer import strip_instruction

    assert strip_instruction("(circle one) Married") == "Married"
    assert strip_instruction("(please print) Amara") == "Amara"


def test_a_phone_number_is_not_mistaken_for_an_instruction():
    """The rule has to survive the one value that opens the same way. A parenthetical
    carrying digits is an area code, not an instruction."""
    from app.services.form_analyzer import strip_instruction

    assert strip_instruction("(412) 555-0184") == "(412) 555-0184"
    assert strip_instruction("(  )") == "(  )"


def test_a_caption_phrased_as_a_question_still_maps():
    """Forms ask rather than label: "What is the family's preferred language?". The value
    was read correctly and then discarded for want of a canonical key."""
    from app.services.canonical_registry import match

    assert match("What is the family's preferred language?")[0].canonical_key == (
        "patient.preferred_language"
    )
    assert match("Please check the box(es) that describe your race")[0].canonical_key == (
        "patient.race"
    )


def test_a_qualifier_does_not_let_a_relative_claim_the_patients_field():
    """"Spouse Date of Birth" is not the patient's, and "Employer Address" is not the
    patient's address. Only phrasing may be dropped from a caption, never a qualifier."""
    from app.services.canonical_registry import match

    for label in ("Spouse Date of Birth", "Employer Address", "Subscriber Last Name"):
        assert match(label)[2] != "exact alias (phrase tail)"


def test_the_empty_rule_is_not_part_of_the_value():
    from app.schemas.models import FieldType
    from app.services.form_analyzer import normalize

    assert normalize("__________ Guerrero", FieldType.SHORT_TEXT) == "Guerrero"
    assert normalize("____ 04/17/1986", FieldType.DATE) == "04/17/1986"
    assert normalize("noor_k@example.com", FieldType.SHORT_TEXT) == "noor_k@example.com"
    assert normalize("1420 Bellefield Ave.", FieldType.SHORT_TEXT) == "1420 Bellefield Ave."


def test_a_date_of_some_other_event_is_not_the_birth_date():
    """Sharing only "date" and "of" with "Date of Birth" is no evidence of being it.

    Every "Date of <event>" used to score exactly the 0.5 bar against the birth date, and
    regeneration then wrote the patient's birth date into the injury, surgery and service
    dates of a copy -- values the original never had.
    """
    from app.services.canonical_registry import match

    for caption in ("Date of Injury/Onset", "Date of Surgery", "Date of Service", "Date of Accident"):
        field, _, _ = match(caption)
        assert field is None or field.canonical_key != "patient.date_of_birth", caption
    for caption in ("Date of Birth", "DOB", "Birth Date", "Patient Date of Birth"):
        field, _, _ = match(caption)
        assert field is not None and field.canonical_key == "patient.date_of_birth", caption
