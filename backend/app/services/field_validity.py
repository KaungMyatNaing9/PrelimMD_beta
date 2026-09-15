"""How plausible a value is as the answer to a field of a given type.

Two readers disagree constantly about what a form says, and until now the disagreement was
settled per *page* -- whichever read more fields won outright. That is unstable: a
regenerated page is cleaner than the scan it came from, the other reader wins there, and
the same values come back under different labels.

Settling it per *field* needs a signal that does not depend on which reader produced the
value, and a field's own type is exactly that. A phone number has ten digits whoever read
it. This is the "structural" half of the multi-signal confidence work in the document
extraction literature, and unlike the model-probability half it costs nothing and needs no
model.

The score is deliberately blunt: it separates "this could be the answer" from "this is the
form's own furniture", which is the distinction the readers get wrong. It is not a measure
of correctness -- ``312-555-0109`` and ``312-555-9999`` score the same.
"""

import re

from app.schemas.models import FieldType

# A value carrying these is a rule line, an empty box or a caption that OCR read as text,
# not something a person wrote.
FURNITURE = re.compile(r"[_|\[\]}{]|\.\.\.")
CERTAIN = 1.0
LIKELY = 0.75
NEUTRAL = 0.5
DOUBTFUL = 0.25
IMPLAUSIBLE = 0.0

DATE = re.compile(r"^\d{1,4}[-/. ]\d{1,2}[-/. ]\d{1,4}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.IGNORECASE)
ZIP = re.compile(r"^\d{5}(-\d{4})?$")


def plausible(value, field_type: FieldType | None) -> float:
    """Score ``value`` as an answer to a field of ``field_type``, from 0 to 1.

    A value nothing is known about scores ``NEUTRAL`` rather than zero, so an unmapped
    field is never beaten purely for lacking a type.
    """
    if value is None or value is False:
        return IMPLAUSIBLE
    if value is True:
        return CERTAIN
    text = str(value).strip()
    if not text:
        return IMPLAUSIBLE
    # An e-mail address is allowed its underscore: "noor_k@example.com" is not a rule.
    if EMAIL.match(text):
        return CERTAIN
    # Furniture is the failure both readers make, whatever the field claims to be.
    if FURNITURE.search(text):
        return IMPLAUSIBLE

    digits = re.sub(r"\D", "", text)
    if field_type == FieldType.PHONE:
        if len(digits) in (10, 11):
            return CERTAIN
        # A seven-digit local number is real but rarer on an intake form than a fragment.
        return LIKELY if len(digits) == 7 else IMPLAUSIBLE
    if field_type == FieldType.DATE:
        # A PDF text layer hands over a date typed into boxed cells as "04 / 17 / 1986";
        # the spaces around the separators are the cells, not the date.
        return CERTAIN if DATE.match(re.sub(r"\s*([-/.])\s*", r"\1", text)) else IMPLAUSIBLE
    if field_type == FieldType.NUMBER:
        return CERTAIN if text.replace(".", "", 1).isdigit() else IMPLAUSIBLE
    if field_type == FieldType.INSURANCE_ID:
        # Policy numbers are alphanumeric runs; prose is not.
        return CERTAIN if re.fullmatch(r"[A-Za-z0-9-]{4,24}", text) else DOUBTFUL

    if EMAIL.match(text) or ZIP.match(text):
        return CERTAIN
    # A run of several words is usually a caption row the reader has swept up, unless the
    # field is meant to hold prose.
    words = text.split()
    if field_type in (FieldType.MULTILINE_TEXT, FieldType.MEDICATION_LIST):
        return LIKELY
    if len(words) > 6:
        return DOUBTFUL
    return LIKELY if words else IMPLAUSIBLE
