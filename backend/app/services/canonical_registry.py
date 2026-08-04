import re

from app.schemas.form_understanding import CanonicalFieldDefinition
from app.schemas.models import FieldType


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", value.lower())).strip()


FIELDS = [
    (
        "patient.full_name",
        "Full name",
        "demographics",
        FieldType.SHORT_TEXT,
        ["Full Name", "Patient Name", "Legal Name", "Name of Patient", "Name"],
    ),
    (
        "patient.date_of_birth",
        "Date of birth",
        "demographics",
        FieldType.DATE,
        ["Date of Birth", "DOB", "Birth Date", "Birthdate"],
    ),
    (
        "patient.phone",
        "Phone",
        "demographics",
        FieldType.PHONE,
        ["Phone", "Phone Number", "Telephone"],
    ),
    ("patient.email", "Email", "demographics", FieldType.SHORT_TEXT, ["Email", "Email Address"]),
    (
        "insurance.member_id",
        "Insurance member ID",
        "insurance",
        FieldType.INSURANCE_ID,
        ["Member ID", "Policy ID", "Subscriber ID", "Insurance ID"],
    ),
    (
        "medical.current_medications",
        "Current medications",
        "medical",
        FieldType.MEDICATION_LIST,
        ["Current Medications", "Medications"],
        False,
        "refresh",
        "confirm",
    ),
    (
        "medical.allergies",
        "Allergies",
        "medical",
        FieldType.MULTILINE_TEXT,
        ["Known Allergies", "Allergies"],
        False,
        "refresh",
        "confirm",
    ),
    (
        "consent.treatment",
        "Consent to treatment",
        "consent",
        FieldType.CHECKBOX,
        ["Consent to Treatment", "Consent Accepted", "Consent"],
        False,
        "never_inherit",
        "confirm",
    ),
    (
        "consent.signature_present",
        "Signature present",
        "consent",
        FieldType.SIGNATURE_PRESENCE,
        ["Patient Signature", "Signature", "Signature Present"],
        False,
        "never_inherit",
        "confirm",
    ),
    ("patient.age", "Age", "demographics", FieldType.NUMBER, ["Age"]),
    (
        "notes.visit",
        "Visit notes",
        "clinical",
        FieldType.MULTILINE_TEXT,
        ["Visit Notes", "Notes", "Visit Note"],
        False,
        "refresh",
        "optional",
    ),
]

# The labelled corpus was authored against its own field vocabulary. Extraction always
# emits canonical keys; this table exists so evaluation can line the two up without
# rewriting every expected.json.
CORPUS_KEY_ALIASES = {
    "patient.full_name": "patient.full_name",
    "patient.dob": "patient.date_of_birth",
    "patient.phone": "patient.phone",
    "patient.age": "patient.age",
    "patient.member_id": "insurance.member_id",
    "medications.current": "medical.current_medications",
    "notes.visit": "notes.visit",
    "consent.accepted": "consent.treatment",
    "signature.present": "consent.signature_present",
}
REGISTRY = {
    key: CanonicalFieldDefinition(
        canonical_key=key,
        display_name=name,
        category=category,
        value_type=kind,
        aliases=aliases,
        reusable=reusable if len(row) > 5 else True,
        freshness_policy=freshness if len(row) > 6 else "stable",
        confirmation_policy=confirm if len(row) > 7 else "optional",
    )
    for row in FIELDS
    for key, name, category, kind, aliases, *rest in [row]
    for reusable in [rest[0] if rest else True]
    for freshness in [rest[1] if len(rest) > 1 else "stable"]
    for confirm in [rest[2] if len(rest) > 2 else "optional"]
}


def match(label: str):
    value = norm(label)
    for field in REGISTRY.values():
        if value in {norm(a) for a in field.aliases}:
            return field, 1.0, "exact alias"
    tokens = set(value.split())
    best = (0.0, None)
    for field in REGISTRY.values():
        for alias in field.aliases:
            alias_tokens = set(norm(alias).split())
            score = len(tokens & alias_tokens) / max(1, len(tokens | alias_tokens))
            if score > best[0]:
                best = (score, field)
    return (
        (best[1], best[0], "token similarity")
        if best[0] >= 0.5
        else (None, best[0], "no confident registry match")
    )
