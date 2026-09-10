"""Emit an analysis as a FHIR R4 QuestionnaireResponse.

The faculty review's sharpest technical point: intake written as a text blob into a note is
"the single decision that makes the system un-integratable later". A QuestionnaireResponse
is the standard shape for "these questions were asked and here is what was answered", so
emitting one is what makes this data mean something to a system that did not produce it.

This is a mapping layer, not a rewrite, because `canonical_registry` is already a controlled
vocabulary: every field carries a stable key, a display name and a value type. Those become
`linkId`, `text` and the `value[x]` variant respectively.

Deliberately absent: LOINC codes. The review asks for them on standardized items and the
structure here carries them (`item.code`), but assigning a clinical code is a clinical
decision. A wrong code is worse than no code -- it would be believed by the receiving
system -- so codes are read from the registry and none are invented here.
"""

from datetime import UTC, datetime
from typing import Any

from app.schemas.models import FieldType

# https://hl7.org/fhir/R4/questionnaireresponse.html
IN_PROGRESS = "in-progress"
COMPLETED = "completed"
LOINC = "http://loinc.org"

# Which value[x] the FHIR answer uses for each of our field types. Anything unlisted is a
# string, which is always valid and never loses information.
VALUE_KEY = {
    FieldType.DATE: "valueDate",
    FieldType.NUMBER: "valueInteger",
    FieldType.CHECKBOX: "valueBoolean",
    FieldType.SIGNATURE_PRESENCE: "valueBoolean",
}


def to_questionnaire_response(
    analysis, questionnaire: str | None = None, subject: str | None = None
) -> dict[str, Any]:
    """Build a QuestionnaireResponse from a finished analysis.

    Status is `in-progress` while any answered field is still flagged for review, and
    `completed` only once none is. That is the honest reading: a receiving system should be
    able to tell a reviewed form from one the machine has merely had a go at.
    """
    answered = [f for f in analysis.detected_fields if _has_value(f)]
    response: dict[str, Any] = {
        "resourceType": "QuestionnaireResponse",
        "status": COMPLETED if not any(f.needs_review for f in answered) else IN_PROGRESS,
        "authored": datetime.now(UTC).isoformat(),
        "item": [_item(f) for f in answered],
    }
    if questionnaire:
        response["questionnaire"] = questionnaire
    if subject:
        response["subject"] = {"reference": subject}
    return response


def _has_value(field) -> bool:
    return field.normalized_value not in (None, "", False)


def _item(field) -> dict[str, Any]:
    """One question and its answer.

    `linkId` falls back to the field id when a label matched nothing in the registry, so an
    unmapped field is still transmitted and identifiable rather than silently dropped --
    it just cannot claim a canonical meaning.
    """
    item: dict[str, Any] = {
        "linkId": field.canonical_key or field.field_id,
        "text": field.label_text,
        "answer": [{_value_key(field): _value(field)}],
    }
    code = getattr(field, "loinc_code", None)
    if code:
        item["code"] = [{"system": LOINC, "code": code}]
    return item


def _value_key(field) -> str:
    return VALUE_KEY.get(field.field_type, "valueString")


def _value(field) -> Any:
    """The answer itself, in the shape the chosen value[x] requires."""
    raw = field.normalized_value
    if field.field_type in (FieldType.CHECKBOX, FieldType.SIGNATURE_PRESENCE):
        return bool(raw)
    if field.field_type == FieldType.NUMBER:
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return str(raw)
    if field.field_type == FieldType.DATE:
        # valueDate must be YYYY-MM-DD. Anything we cannot put in that shape stays a
        # string rather than being guessed into a wrong date.
        return _as_iso_date(str(raw)) or str(raw)
    return str(raw)


def _as_iso_date(text: str) -> str | None:
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m/%d/%y"):
        try:
            # A calendar date carries no timezone -- a date of birth is the same date
            # everywhere -- so parsing it naively is correct, not an oversight.
            return datetime.strptime(text.strip(), pattern).date().isoformat()  # noqa: DTZ007
        except ValueError:
            continue
    return None
