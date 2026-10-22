import re
from functools import lru_cache

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
        # "Name" alone is deliberately NOT an alias here. A form asks for a name in four
        # places -- patient, subscriber, emergency contact, employer -- and a one-token
        # alias matches all of them: "Employer Name" shares one token of two with "Name",
        # which is a Jaccard score of exactly 0.5 and passed the confidence bar. The
        # employer's name was then reported as the patient's.
        ["Full Name", "Patient Name", "Legal Name", "Name of Patient"],
    ),
    (
        "patient.date_of_birth",
        "Date of birth",
        "demographics",
        FieldType.DATE,
        ["Date of Birth", "DOB", "Birth Date", "Birthdate"],
    ),
    # The date the form was filled: every intake packet asks for it beside the signature,
    # and a bare "Date" is never the birth date. Without its own key, "Date" matched
    # "Birth Date" on token similarity at exactly the 0.5 bar, the two slots displaced
    # each other in the merge, and each was scored as the other -- 81 fields on the
    # second fill set (2026-09-15), the largest single loss on the corrected track.
    (
        "form.date",
        "Date completed",
        "administrative",
        FieldType.DATE,
        ["Date", "Today's Date", "Date Signed", "Date of Signature", "Signature Date"],
        False,
        "never_inherit",
        "optional",
    ),
    (
        "patient.phone",
        "Phone",
        "demographics",
        FieldType.PHONE,
        ["Phone", "Phone Number", "Telephone", "Primary Phone"],
    ),
    # A form that asks for three numbers means three numbers. Collapsing them onto one
    # key meant the first read won and the rest were unreachable by construction.
    ("patient.home_phone", "Home phone", "demographics", FieldType.PHONE,
     ["Home Phone", "Home Telephone", "Home Phone Number"]),
    ("patient.cell_phone", "Cell phone", "demographics", FieldType.PHONE,
     ["Cell Phone", "Mobile", "Mobile Phone", "Cell", "Cell Phone Number"]),
    ("patient.work_phone", "Work phone", "demographics", FieldType.PHONE,
     ["Work Phone", "Work Telephone", "Work Phone Number"]),
    ("patient.email", "Email", "demographics", FieldType.SHORT_TEXT, ["Email", "Email Address"]),
    (
        "patient.address",
        "Address",
        "demographics",
        FieldType.SHORT_TEXT,
        ["Address", "Street Address", "Home Address", "Physical Address", "Street"],
    ),
    # A mailing address is a different answer from a street address on any form that
    # prints both, and most do.
    ("patient.mailing_address", "Mailing address", "demographics", FieldType.SHORT_TEXT,
     ["Mailing Address", "Postal Address"]),
    # The parts of a name a form asks for separately.
    ("patient.first_name", "First name", "demographics", FieldType.SHORT_TEXT,
     ["First Name", "First", "Given Name"]),
    ("patient.last_name", "Last name", "demographics", FieldType.SHORT_TEXT,
     ["Last Name", "Last", "Surname", "Family Name"]),
    ("patient.middle_name", "Middle name", "demographics", FieldType.SHORT_TEXT,
     ["Middle Name", "Middle Initial", "MI", "Middle"]),
    ("patient.preferred_name", "Preferred name", "demographics", FieldType.SHORT_TEXT,
     ["Preferred Name", "Nickname", "Goes By"]),
    # Demographics every intake form asks and the vocabulary had no word for.
    ("patient.race", "Race", "demographics", FieldType.SHORT_TEXT,
     ["Race", "Races", "Race(s)"]),
    ("patient.ethnicity", "Ethnicity", "demographics", FieldType.SHORT_TEXT, ["Ethnicity"]),
    ("patient.preferred_language", "Preferred language", "demographics", FieldType.SHORT_TEXT,
     ["Preferred Language", "Language", "Written Language", "Spoken Language"]),
    # Employment.
    ("employment.employer", "Employer", "employment", FieldType.SHORT_TEXT,
     ["Employer", "Employer Name", "Company", "Place of Employment"]),
    ("employment.occupation", "Occupation", "employment", FieldType.SHORT_TEXT,
     ["Occupation", "Job Title", "Position"]),
    # Emergency contact: a person, with their own name, tie and number.
    ("emergency.name", "Emergency contact", "emergency", FieldType.SHORT_TEXT,
     ["Emergency Contact", "Emergency Contact Name", "In Case of Emergency"]),
    ("emergency.relationship", "Relationship to patient", "emergency", FieldType.SHORT_TEXT,
     ["Relationship to Patient", "Relation to Patient", "Relationship to you",
      "Relation to you", "Relationship"]),
    ("emergency.phone", "Emergency contact phone", "emergency", FieldType.PHONE,
     ["Emergency Phone", "Emergency Contact Phone"]),
    # Care team.
    ("care.primary_physician", "Primary care physician", "care", FieldType.SHORT_TEXT,
     ["Primary Care Physician", "PCP", "Primary Care Provider", "Name of Pediatrician",
      "Pediatrician", "Referring Provider", "Referring Physician"]),
    ("care.pharmacy", "Pharmacy", "care", FieldType.SHORT_TEXT,
     ["Pharmacy", "Preferred Pharmacy", "Pharmacy Name"]),
    (
        "patient.sex",
        "Sex",
        "demographics",
        FieldType.SHORT_TEXT,
        ["Sex", "Legal Sex", "Sex at birth", "Birth Sex", "Sex (M/F)"],
    ),
    ("patient.gender", "Gender", "demographics", FieldType.SHORT_TEXT, ["Gender"]),
    (
        "patient.marital_status",
        "Marital status",
        "demographics",
        FieldType.SHORT_TEXT,
        ["Marital Status", "Marital"],
    ),
    (
        "patient.ssn",
        "Social security number",
        "demographics",
        FieldType.SHORT_TEXT,
        ["SSN", "SS#", "Social Security", "Social Security Number", "Social Security #"],
    ),
    # The three columns almost every intake form splits an address into. They are read as
    # separate fields whenever a rule is captioned from beneath ("CITY  STATE  ZIP CODE"),
    # so without keys of their own those values arrive mapped to nothing and cannot be
    # filled back onto a template.
    ("patient.city", "City", "demographics", FieldType.SHORT_TEXT, ["City", "Town"]),
    (
        "patient.state",
        "State",
        "demographics",
        FieldType.SHORT_TEXT,
        ["State", "Province", "State/Province"],
    ),
    (
        "patient.postal_code",
        "ZIP code",
        "demographics",
        FieldType.SHORT_TEXT,
        ["Zip", "Zip Code", "ZIP", "Postal Code", "Postcode"],
    ),
    ("insurance.carrier", "Insurance carrier", "insurance", FieldType.SHORT_TEXT,
     ["Insurance Carrier", "Insurance Name", "Medical Insurance Name", "Insurance Company",
      "Your Insurance Name", "Primary Insurance", "Carrier"]),
    ("insurance.group_id", "Group ID", "insurance", FieldType.SHORT_TEXT,
     ["Group ID", "Group Number", "Group #", "Group/Plan #", "Group Plan"]),
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


# Words that say what *kind* of value a field holds rather than which field it is. Shared
# on their own, they are no evidence that two captions mean the same thing.
TYPE_WORDS = {"date"}


# Function words. A caption phrased as an instruction or a question is built from these;
# a qualifier naming whose field it is ("Spouse", "Employer", "Guarantor") is not.
PHRASING = {
    "a", "about", "all", "an", "and", "any", "are", "as", "at", "be", "box", "boxes",
    "by", "check", "circle", "describe", "do", "does", "enter", "fill", "for", "give",
    "has", "have", "how", "if", "in", "indicate", "is", "it", "list", "mark", "of", "on",
    "or", "please", "print", "provide", "select", "that", "the", "their", "this", "to",
    "was", "were", "what", "which", "who", "whom", "whose", "will", "with", "would",
    "you", "your",
}


def match(label: str):
    value = norm(label)
    for field in REGISTRY.values():
        if value in {norm(a) for a in field.aliases}:
            return field, 1.0, "exact alias"
    # Forms pluralise their captions as freely as they punctuate them -- "Emergency
    # Contacts", "Race(s)", "Allergies" -- and an exact-alias test that is otherwise the
    # safe one misses every one of them. Listing both spellings of every field would be
    # an alias table that grows with the language rather than with the vocabulary.
    singular = value[:-1] if value.endswith("s") and len(value) > 3 else None
    if singular:
        for field in REGISTRY.values():
            if singular in {norm(a) for a in field.aliases}:
                return field, 1.0, "exact alias (plural)"
    # A caption is not always a noun phrase. Real forms ask questions and give
    # instructions -- "Please check the box(es) that describe your race:", "What is the
    # family's preferred language?" -- and the field being named is the phrase they end
    # on. Both of those were read correctly and then thrown away for want of a canonical
    # key, which is the worst way to lose a value: the work was done.
    #
    # The tail is tried longest first, so "emergency contact" is preferred over "contact"
    # and a question cannot be mapped by a common last word alone. Only an exact alias is
    # accepted, the same bar the direct test uses; token similarity is left to the
    # ordinary path below, where a low score is reported rather than trusted.
    #
    # The prefix that gets dropped has to be *phrasing*, not a qualifier that says whose
    # field this is. "Employer Address" ends in "address" and is not the patient's; it is
    # the employer's, and mapping it to `patient.address` would put a company's street on
    # a chart. "Spouse Date of Birth" is not the patient's date of birth. This is the same
    # failure the fusion module already guards against, arriving by a different route.
    #
    # What separates them is grammar, not a list of relatives: an instruction or a question
    # is built from function words -- "please check the box(es) that describe your",
    # "what is the family's", "name of" -- while a qualifier is a bare noun with none.
    # Requiring at least one function word in the dropped prefix is a fact about English
    # rather than about any form, so it holds for captions nobody has written yet.
    words = value.split()
    for start in range(1, len(words)):
        tail = " ".join(words[start:])
        prefix = words[:start]
        # Skip this split rather than abandoning the label: "Name of emergency contact"
        # has a bare noun at the first split and the phrasing word one further along, and
        # stopping at the first refusal lost it. Every longer prefix that still carries no
        # function word is refused in turn, so a qualifier can never be dropped.
        if not set(prefix) & PHRASING:
            continue
        for field in REGISTRY.values():
            if tail in {norm(a) for a in field.aliases}:
                return field, 1.0, "exact alias (phrase tail)"

    tokens = set(value.split())
    best: tuple[float, CanonicalFieldDefinition | None] = (0.0, None)
    for field in REGISTRY.values():
        for alias in field.aliases:
            alias_tokens = set(norm(alias).split())
            # Overlap is only evidence if some of it names the field. "Date of Injury" and
            # "Date of Birth" share "date" and "of" -- a type and a function word -- and
            # scored exactly the 0.5 bar, so every "Date of <event>" on every form became the
            # patient's birth date: surgery, service, accident, onset. Regeneration then
            # wrote the birth date into each of them, a value the original never had.
            #
            # Generic overlap is still the whole evidence for a field that is itself generic:
            # the alias "Date" names nothing but a date, so "Date Needed" and "Effective Date"
            # rightly match the form's own date. The rule only refuses a field whose alias
            # carries a specific word the caption lacks -- "birth" -- measured on 2026-10-21:
            # refusing every generic-only match lost four of those form dates.
            generic = PHRASING | TYPE_WORDS
            if not (tokens & alias_tokens) - generic and alias_tokens - generic:
                continue
            score = len(tokens & alias_tokens) / max(1, len(tokens | alias_tokens))
            if score > best[0]:
                best = (score, field)
    return (
        (best[1], best[0], "token similarity")
        if best[0] >= 0.5
        else (None, best[0], "no confident registry match")
    )


@lru_cache(maxsize=1)
def known_captions() -> frozenset[str]:
    """Every caption this vocabulary recognises, normalised, for exact comparison.

    A *value* that is exactly one of these is the next field's label read as this field's
    answer -- "City ____" answered "State" -- and callers use it to blank such a reading.
    Exact membership rather than `match`, which scores by token overlap and would also
    condemn "Bellefield Pharmacy", a real answer that merely contains a field name.
    """
    captions = {norm(key) for key in REGISTRY}
    for field in REGISTRY.values():
        captions.update(norm(alias) for alias in field.aliases)
    return frozenset(c for c in captions if c)
