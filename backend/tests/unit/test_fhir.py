"""Emitting an analysis as a FHIR QuestionnaireResponse."""

from types import SimpleNamespace

from app.schemas.models import FieldType
from app.services.fhir import to_questionnaire_response


def _field(**over):
    base = dict(
        field_id="box_1", label_text="Date of Birth", canonical_key="patient.date_of_birth",
        field_type=FieldType.DATE, normalized_value="04/17/1986", needs_review=False,
    )
    return SimpleNamespace(**{**base, **over})


def _analysis(*fields):
    return SimpleNamespace(detected_fields=list(fields))


def test_shape_matches_the_resource():
    r = to_questionnaire_response(_analysis(_field()))
    assert r["resourceType"] == "QuestionnaireResponse"
    assert r["status"] in ("completed", "in-progress")
    assert "authored" in r
    assert r["item"][0]["linkId"] == "patient.date_of_birth"
    assert r["item"][0]["text"] == "Date of Birth"


def test_a_date_is_emitted_as_a_real_date():
    """valueDate must be YYYY-MM-DD or a receiving system cannot parse it."""
    r = to_questionnaire_response(_analysis(_field()))
    assert r["item"][0]["answer"] == [{"valueDate": "1986-04-17"}]


def test_an_unparseable_date_stays_a_string_rather_than_being_guessed():
    """Inventing a date a receiving system would believe is worse than sending text."""
    r = to_questionnaire_response(_analysis(_field(normalized_value="sometime in April")))
    assert r["item"][0]["answer"] == [{"valueDate": "sometime in April"}]


def test_value_type_picks_the_right_variant():
    fields = (
        _field(field_type=FieldType.SHORT_TEXT, normalized_value="Okafor", canonical_key="patient.full_name"),
        _field(field_type=FieldType.CHECKBOX, normalized_value=True, canonical_key="consent.treatment"),
        _field(field_type=FieldType.NUMBER, normalized_value="58", canonical_key="patient.age"),
    )
    answers = [i["answer"][0] for i in to_questionnaire_response(_analysis(*fields))["item"]]
    assert answers == [{"valueString": "Okafor"}, {"valueBoolean": True}, {"valueInteger": 58}]


def test_unanswered_fields_are_left_out():
    """A QuestionnaireResponse carries answers; a blank field has none to carry."""
    r = to_questionnaire_response(_analysis(_field(normalized_value=None), _field()))
    assert len(r["item"]) == 1


def test_an_unmapped_field_is_still_transmitted_under_its_own_id():
    """Dropping it would lose data; it simply cannot claim a canonical meaning."""
    r = to_questionnaire_response(_analysis(_field(canonical_key=None, field_id="box_9")))
    assert r["item"][0]["linkId"] == "box_9"


def test_status_says_whether_a_human_has_finished_with_it():
    """A receiving system must be able to tell a reviewed form from an unreviewed one."""
    assert to_questionnaire_response(_analysis(_field()))["status"] == "completed"
    flagged = to_questionnaire_response(_analysis(_field(needs_review=True)))
    assert flagged["status"] == "in-progress"


def test_no_loinc_code_is_invented():
    """The registry carries codes when a clinician has assigned one; none are guessed here,
    because a wrong code would be believed by whatever receives it."""
    r = to_questionnaire_response(_analysis(_field()))
    assert "code" not in r["item"][0]
