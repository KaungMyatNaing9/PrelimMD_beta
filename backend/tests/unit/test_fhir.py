"""Emitting an analysis as a FHIR QuestionnaireResponse."""

import re
from types import SimpleNamespace

from app.schemas.models import FieldType
from app.services.fhir import to_questionnaire_response


def _field(**over):
    base = {
        "field_id": "box_1", "label_text": "Date of Birth", "canonical_key": "patient.date_of_birth",
        "field_type": FieldType.DATE, "normalized_value": "04/17/1986", "needs_review": False,
    }
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
    """A code is emitted only where one was assigned, never guessed from the label.

    A wrong code would be believed by whatever receives it, so the only source is the
    table in `loinc.py`. A field LOINC does not code -- a name, a phone number -- carries
    no `code` at all rather than an approximate one.
    """
    coded = to_questionnaire_response(_analysis(_field()))["item"][0]
    assert coded["code"] == [
        {"system": "http://loinc.org", "code": "21112-8", "display": "Birth date"}
    ]
    uncoded = to_questionnaire_response(
        _analysis(_field(canonical_key="patient.full_name", label_text="Patient Name"))
    )["item"][0]
    assert "code" not in uncoded
    unmapped = to_questionnaire_response(
        _analysis(_field(canonical_key=None, label_text="Referring Physician"))
    )["item"][0]
    assert "code" not in unmapped
    assert unmapped["linkId"] == "box_1", "an unmapped field is still identifiable"


def test_a_model_overruling_geometry_is_recorded_as_a_disagreement():
    """The disputed signal fired 0 times in 118 fields because every real conflict was
    resolved here, where geometry had read something, doubted it, and been overruled --
    and that was filed as "unread", which is what a field only one reader saw looks like."""
    from app.schemas.form_understanding import DetectedFormField
    from app.schemas.models import BoundingBox, FieldType
    from app.services.fusion import _adopt

    box = BoundingBox(x=0.1, y=0.1, width=0.2, height=0.05)
    def field(value):
        return DetectedFormField(
            field_id="f1", page_index=0, label_text="Zip Code", normalized_label="zip code",
            field_type=FieldType.SHORT_TEXT, label_bbox=box, answer_bbox=box,
            required_confidence=0.3, detection_confidence=0.9, source="ocr_layout",
            mapping_confidence=0.9, mapping_reason="alias", raw_value=value,
            normalized_value=value, is_blank=value is None, confidence=0.5,
        )

    # Geometry read something and is overruled: the readers conflict.
    conflicted = field("15206")
    _adopt(conflicted, "45206")
    assert conflicted.agreement == "disputed"
    assert conflicted.needs_review

    # Geometry read nothing: nobody contradicted anybody.
    filled = field(None)
    _adopt(filled, "15206")
    assert filled.agreement == "unread"

    # The same value written differently is not a conflict.
    agreed = field("04/17/1986")
    _adopt(agreed, "04 / 17 / 1986")
    assert agreed.agreement == "unread"


def test_a_standard_item_carries_its_loinc_code():
    """The items LOINC does code carry it; the ones it does not, do not.

    A name or a phone number belongs on a FHIR Patient resource, not as a coded answer,
    so an absent code here is the design rather than a gap.
    """
    from app.services.loinc import CODES, code_for

    assert code_for("patient.date_of_birth") == ("21112-8", "Birth date")
    assert code_for("medical.allergies")[0] == "48765-2"
    # Identity and contact fields are deliberately uncoded.
    for key in ("patient.full_name", "patient.phone", "insurance.member_id"):
        assert code_for(key) is None
    assert code_for(None) is None
    # Every code is a LOINC part number: digits, a hyphen, and a check digit.
    for code, display in CODES.values():
        assert re.fullmatch(r"\d{4,5}-\d", code), code
        assert display.strip()


def test_a_page_with_nothing_read_is_not_completed():
    """Nothing answered means nothing flagged, and that is not a finished form."""
    assert to_questionnaire_response(_analysis())["status"] == "in-progress"
