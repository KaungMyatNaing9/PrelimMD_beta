"""LOINC codes for the intake items where LOINC is the right code system.

The faculty review asks for LOINC on standardized items. Two things make that narrower
than "put a code on every field", and both are deliberate here.

**Most of an intake form is not a LOINC observation.** A name, a phone number, an address,
a marital status, an insurance member ID: FHIR carries these on `Patient` and `Coverage`
resources, in their own value sets, and LOINC has nothing sensible to say about them.
Coding them would be inventing meaning, not adding it. They stay uncoded, and the
`QuestionnaireResponse` still carries them with a stable `linkId` and their printed text,
so nothing is lost -- a receiving system maps them to `Patient` fields, which is where they
belong.

**A wrong code is worse than no code**, because the receiving system believes it. So this
table holds only codes in common use in US Core and C-CDA, where the meaning is
unambiguous and the mapping to our field is exact. Anything doubtful is left in
``NEEDS_VERIFICATION`` below rather than shipped on a guess.

Provenance: these are the widely used codes for these concepts, written from knowledge of
US Core and C-CDA rather than copied from a LOINC release. **Check each against
https://loinc.org (free account) before anyone relies on this clinically.** The table is
deliberately one flat dict so that verifying or correcting a code is a one-line change,
and `scripts/`-level tests assert the shape rather than the codes themselves.
"""

LOINC_SYSTEM = "http://loinc.org"

# canonical_key -> (LOINC code, LOINC display name)
CODES: dict[str, tuple[str, str]] = {
    # Demographics that LOINC does code, and that intake forms always ask.
    "patient.date_of_birth": ("21112-8", "Birth date"),
    "patient.sex": ("46098-0", "Sex"),
    # Clinical history sections. These are the C-CDA section codes, which is how a
    # medication list or an allergy list travels between systems today.
    "medical.current_medications": ("10160-0", "History of Medication use Narrative"),
    "medical.allergies": ("48765-2", "Allergies and adverse reactions Document"),
}

# Concepts an intake form asks for, where a LOINC code plausibly exists but the exact one
# is not certain enough to ship. Verify against loinc.org and move up into CODES:
#
#   patient.race        54134-2 ?   US Core carries race as an extension with OMB
#                                   categories rather than a LOINC observation, so which
#                                   is wanted depends on the receiving system.
#   patient.ethnicity   69490-1 ?   Same question, same caveat.
#
# Left here on purpose. An unverified code in CODES would be transmitted as fact.
NEEDS_VERIFICATION = ("patient.race", "patient.ethnicity")


def code_for(canonical_key: str | None) -> tuple[str, str] | None:
    """The LOINC code for a canonical field, or None where LOINC does not apply."""
    if not canonical_key:
        return None
    return CODES.get(canonical_key)
