"""Scoring a value as the answer to a field of a given type."""

import pytest

from app.schemas.models import FieldType
from app.services.field_validity import CERTAIN, IMPLAUSIBLE, plausible


@pytest.mark.parametrize(
    ("value", "field_type"),
    [
        ("312-555-0109", FieldType.PHONE),
        ("(312) 555 0109", FieldType.PHONE),
        ("04/17/1986", FieldType.DATE),
        ("1988-12-14", FieldType.DATE),
        ("58", FieldType.NUMBER),
        ("AB100033", FieldType.INSURANCE_ID),
        ("amara.okafor@example.com", FieldType.SHORT_TEXT),
        ("15206", FieldType.SHORT_TEXT),
    ],
)
def test_a_well_formed_value_scores_certain(value, field_type):
    assert plausible(value, field_type) == CERTAIN


@pytest.mark.parametrize(
    ("value", "field_type"),
    [
        ("( ) - O None", FieldType.PHONE),
        ("Phone", FieldType.PHONE),
        ("Please Specify", FieldType.DATE),
        ("Married", FieldType.NUMBER),
    ],
)
def test_a_value_of_the_wrong_shape_is_implausible(value, field_type):
    assert plausible(value, field_type) == IMPLAUSIBLE


@pytest.mark.parametrize(
    "value",
    [
        "[_]Decline",
        "|single Divorced",
        "____________",
        "Name .... Relation",
        "{}",
    ],
)
def test_the_forms_own_furniture_is_never_an_answer(value):
    """The failure both readers make: a rule line or an empty box read as text.

    This is the distinction the signal exists for, so it holds whatever the field claims
    to be -- an unmapped field must not launder furniture through as a plausible value.
    """
    assert plausible(value, None) == IMPLAUSIBLE
    assert plausible(value, FieldType.SHORT_TEXT) == IMPLAUSIBLE


def test_a_row_of_captions_scores_below_a_real_answer():
    """"Name Relation Phone Number" is a caption row swept up as one value."""
    captions = plausible("Name Relation Phone Number Date of Birth", FieldType.SHORT_TEXT)
    answer = plausible("Ngozi Okafor", FieldType.SHORT_TEXT)
    assert captions < answer


def test_prose_is_allowed_where_the_field_expects_prose():
    """A notes field legitimately holds many words, so length must not condemn it."""
    long_note = "Patient reports intermittent headaches over the past three weeks"
    assert plausible(long_note, FieldType.MULTILINE_TEXT) > plausible(
        long_note, FieldType.SHORT_TEXT
    )


def test_nothing_is_implausible_and_a_tick_is_certain():
    assert plausible(None, FieldType.SHORT_TEXT) == IMPLAUSIBLE
    assert plausible("", FieldType.SHORT_TEXT) == IMPLAUSIBLE
    assert plausible(False, FieldType.CHECKBOX) == IMPLAUSIBLE
    assert plausible(True, FieldType.CHECKBOX) == CERTAIN


def test_an_unmapped_field_is_not_punished_for_having_no_type():
    """A value with no type known scores mid-range, not zero, or every unmapped field
    would lose to any mapped one regardless of what it holds."""
    assert 0 < plausible("Riverside Logistics", None) < CERTAIN


def test_an_email_with_an_underscore_is_not_furniture():
    from app.schemas.models import FieldType
    from app.services.field_validity import CERTAIN, plausible

    assert plausible("noor_k@example.com", FieldType.SHORT_TEXT) == CERTAIN


def test_a_date_with_spaced_separators_is_still_a_date():
    from app.schemas.models import FieldType
    from app.services.field_validity import CERTAIN, IMPLAUSIBLE, plausible

    assert plausible("04 / 17 / 1986", FieldType.DATE) == CERTAIN
    assert plausible("STREET,", FieldType.DATE) == IMPLAUSIBLE
