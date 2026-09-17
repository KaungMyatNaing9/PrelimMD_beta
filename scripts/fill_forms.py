"""Fill real blank clinic forms with synthetic answers, and write the ground truth.

Why this is safe to score against
---------------------------------
Placement comes from the *form's own text layer*: each value is written immediately after
the printed label it answers, on that label's baseline, at that label's own font size.
Nothing here imports the extractor. Filling a benchmark with our own detector and then
scoring that detector against it would only prove the detector agrees with itself.

It also refuses to guess. A value is written only when:

  * the label appears exactly once on the page, so the ground truth is unambiguous;
  * there is clear space after it -- the run is measured, and if it would collide with
    the next printed thing on that line, the field is skipped and reported, not squeezed.

Anything skipped is listed at the end, so a thin fill is visible rather than silent.

Every patient is invented, and **a different one goes on every form** -- drawn from
pools, seeded from the form's filename so the draw is reproducible. A single repeated
identity would be a benchmark a reader could pass by memorising one name; varying it
means the only way to score is to read the page. No real person's details appear here,
and none should.

    python scripts/fill_forms.py --dir data/real-forms/LARGE
    python scripts/fill_forms.py --dir data/real-forms/LARGE --check   # report, write nothing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]

INK = (0.05, 0.05, 0.35)
FONT = "helv"
# A value starts a little after the label's colon rather than hard against it.
GAP_PT = 5.0
# Renders scored by the image path. 150dpi matches the rest of the corpus.
DPI = 150

# A different invented patient on every form.
#
# One patient repeated across every artefact is a benchmark that can be passed without
# reading anything: "Okafor" appearing on 150 pages is a string a reader could learn, and
# a tuned threshold that happens to suit one name, one address length and one area code
# would look like accuracy. Drawing a fresh identity per form removes that -- the only way
# to score is to actually read what is on the page.
#
# The draw is seeded from the form's filename, so it is *stable*: re-running the filler
# reproduces the same corpus, and the ground truth stays valid without regenerating it.
#
# Everything here is fictional by construction, not by hope: phone numbers stay inside the
# 555-01xx range reserved for fiction, Social Security numbers use a 9xx area that has
# never been issued, and mail goes to the reserved example.com family.

FIRST = [
    ("Amara", "F"), ("Damaris", "F"), ("Ingrid", "F"), ("Priya", "F"), ("Rosalind", "F"),
    ("Thandiwe", "F"), ("Yelena", "F"), ("Constance", "F"), ("Mireille", "F"), ("Saoirse", "F"),
    ("Beatriz", "F"), ("Noor", "F"), ("Winifred", "F"), ("Analise", "F"), ("Keiko", "F"),
    ("Ottilie", "F"), ("Marisol", "F"), ("Fenna", "F"),
    ("Ezekiel", "M"), ("Tobias", "M"), ("Rashid", "M"), ("Lachlan", "M"), ("Desmond", "M"),
    ("Bartholomew", "M"), ("Nikolai", "M"), ("Emeka", "M"), ("Sung-min", "M"), ("Idris", "M"),
    ("Caspian", "M"), ("Horacio", "M"), ("Anselm", "M"), ("Bram", "M"), ("Teodoro", "M"),
    ("Rafferty", "M"), ("Zephyr", "M"), ("Oluwafemi", "M"),
]

LAST = [
    "Okafor", "Vandermeer", "Castellanos", "Bergstrom", "Nakagawa", "Achterberg",
    "Quintanilla", "Fitzwilliam", "Oyelaran", "Dimitrova", "Haverford", "Sandoval",
    "Brightwater", "Kowalczyk", "Adeyemi", "Thistlewood", "Marchetti", "Nkemelu",
    "Pemberton", "Sorensen", "Villanueva", "Ashworth", "Beauchamp", "Chandrasekar",
    "Delacroix", "Eguchi", "Falkenrath", "Gustafsson", "Hollingsworth", "Iyengar",
    "Jaworski", "Kandinsky", "Lindqvist", "Montenegro", "Novachenko", "Ravensworth",
]

MIDDLE = [
    "Chidinma", "Rae", "Oluwaseun", "Beatrix", "Ingram", "Solveig", "Tamsin", "Aurelio",
    "Mercedes", "Baptiste", "Kealoha", "Evander", "Wisteria", "Osric", "Linnea", "Caradoc",
]

# City, state, ZIP and the area code that actually serves it -- kept consistent so a form
# reading "Portland, ME 04101" beside a 503 number cannot be right by luck.
PLACES = [
    ("Pittsburgh", "PA", "15206", "412"), ("Tucson", "AZ", "85719", "520"),
    ("Portland", "ME", "04101", "207"), ("Asheville", "NC", "28806", "828"),
    ("Duluth", "MN", "55802", "218"), ("Provo", "UT", "84604", "385"),
    ("Shreveport", "LA", "71105", "318"), ("Bellingham", "WA", "98225", "360"),
    ("Waterbury", "CT", "06710", "203"), ("Lubbock", "TX", "79410", "806"),
    ("Rockford", "IL", "61103", "815"), ("Savannah", "GA", "31405", "912"),
    ("Bozeman", "MT", "59715", "406"), ("Wilmington", "DE", "19805", "302"),
    ("Fresno", "CA", "93726", "559"), ("Toledo", "OH", "43606", "419"),
    ("Manchester", "NH", "03104", "603"), ("Sioux Falls", "SD", "57104", "605"),
    ("Chattanooga", "TN", "37405", "423"), ("Hilo", "HI", "96720", "808"),
    ("Cheyenne", "WY", "82001", "307"), ("Burlington", "VT", "05401", "802"),
    ("Albuquerque", "NM", "87106", "505"), ("Spokane", "WA", "99205", "509"),
]

STREETS = [
    "Bellefield Avenue", "Kestrel Lane", "Ironwood Court", "Mockingbird Road",
    "Sable Ridge Drive", "Harlow Street", "Pinecrest Terrace", "Wildacre Boulevard",
    "Quarry Bend", "Thornfield Way", "Juniper Hollow", "Lantern Hill Road",
    "Marbury Place", "Silverbrook Trail", "Oakhaven Circle", "Peregrine Street",
]

EMPLOYERS = [
    "Riverside Logistics", "Halcyon Dental Group", "Northgate Public Schools",
    "Wexler Fabrication", "Blue Heron Cooperative", "Sterling Freight",
    "Maplewood Library System", "Crestline Instruments", "Pelham Grocers",
    "Torrent Software", "Gilmore Roofing", "Aster Veterinary Clinic",
    "Fairbanks Transit Authority", "Lockridge Textiles", "Vantage Home Care",
    "self-employed",
]

OCCUPATIONS = [
    "Operations Manager", "Dental Hygienist", "Middle School Teacher", "Welder",
    "Farm Cooperative Clerk", "Long-Haul Driver", "Reference Librarian",
    "Calibration Technician", "Produce Buyer", "QA Engineer", "Roofer",
    "Veterinary Assistant", "Bus Dispatcher", "Loom Operator", "Home Health Aide",
    "Retired", "Student",
]

INSURERS = [
    ("Keystone Health", "KH"), ("Sunridge Mutual", "SM"), ("Pinnacle Care", "PC"),
    ("Harborview Plan", "HV"), ("Evergreen Choice", "EC"), ("Meridian Benefit", "MB"),
    ("Calderon Health", "CH"), ("Northstar Assurance", "NA"), ("Bright Meadow", "BM"),
    ("Ashland Medical", "AM"), ("Trellis Health Plan", "TH"), ("Copperfield Mutual", "CM"),
    ("Medicaid", "MD"), ("Medicare", "MC"),
]

PHARMACIES = [
    "Bellefield Pharmacy", "Corner Drug", "Halsted Apothecary", "Riverbend Pharmacy",
    "Thrift Drug on Main", "Willow Creek Pharmacy", "Sunset Chemists",
    "Third Street Drug", "Lakeshore Pharmacy", "Prairie Rx",
]

PHYSICIAN_TITLES = ["Dr", "Dr.", ""]

LANGUAGES = ["English", "Spanish", "Vietnamese", "Tagalog", "Haitian Creole", "Somali",
             "Mandarin", "Portuguese", "Arabic", "English"]
RACES = ["Black/African American", "White", "Asian", "American Indian",
         "Native Hawaiian", "Two or more races", "Declined"]
ETHNICITIES = ["Non-Hispanic", "Hispanic or Latino", "Not Hispanic or Latino", "Declined"]
MARITAL = ["Married", "Single", "Divorced", "Widowed", "Separated", "Partnered"]

# The same ten digits written three ways. Scoring strips punctuation, so this costs the
# benchmark nothing and tests that a reader is not keyed to one house style.
PHONE_STYLES = ["{a}-555-{n}", "({a}) 555-{n}", "{a}.555.{n}", "{a} 555 {n}"]


def patient_for(key: str) -> dict[str, str]:
    """A stable, distinct, entirely invented patient for one form."""
    rng = random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:16], 16))

    first, sex = rng.choice(FIRST)
    last = rng.choice(LAST)
    middle = rng.choice(MIDDLE)
    city, state, zip_code, area = rng.choice(PLACES)
    street = f"{rng.randrange(12, 9800)} {rng.choice(STREETS)}"
    style = rng.choice(PHONE_STYLES)
    # Distinct last-four for home, cell and work, all inside the fiction range.
    home_n, cell_n, work_n, kin_n = rng.sample(range(100, 200), 4)

    def phone(tail: int) -> str:
        return style.format(a=area, n=f"01{tail - 100:02d}")

    insurer, prefix = rng.choice(INSURERS)
    # Relation first, then a name that fits it. Drawn independently this produced
    # "Saoirse Eguchi -- Brother", which no form would carry and which would make the
    # corpus read as machine-generated to anyone reviewing it.
    kin_relation = rng.choice(["Sister", "Brother", "Spouse", "Mother", "Father",
                               "Daughter", "Son", "Friend", "Neighbour", "Aunt"])
    kin_sex = {"Sister": "F", "Mother": "F", "Daughter": "F", "Aunt": "F",
               "Brother": "M", "Father": "M", "Son": "M"}.get(kin_relation)
    kin_first = rng.choice(
        [n for n, s in FIRST if n != first and (kin_sex is None or s == kin_sex)]
    )
    # Same surname for a blood relative, a different one for a friend or neighbour.
    kin_last = last if kin_relation in {"Sister", "Brother", "Mother", "Father",
                                        "Daughter", "Son", "Spouse"} else rng.choice(LAST)
    title = rng.choice(PHYSICIAN_TITLES)
    doctor = f"{title} {rng.choice(FIRST)[0]} {rng.choice(LAST)}".strip()

    born_year = rng.randrange(1938, 2007)
    born = f"{rng.randrange(1, 13):02d}/{rng.randrange(1, 29):02d}/{born_year}"
    # A sixteen-year-old is not a Reference Librarian and an eighty-year-old is rarely a
    # Welder. Age does not affect scoring, but a corpus full of impossible people is one
    # nobody can review by eye for the placement errors that *do* affect scoring.
    if born_year > 2004:
        occupation = "Student"
    elif born_year < 1955:
        occupation = rng.choice(["Retired", "Retired", rng.choice(OCCUPATIONS)])
    else:
        occupation = rng.choice([o for o in OCCUPATIONS if o not in ("Retired", "Student")])
    # Numeric, always. `form_analyzer.normalize` rewrites "August 25 2026" to an ISO date
    # and leaves "08/25/2026" alone, so a spelled-out month would score the normaliser
    # rather than the reader.
    today = f"{rng.randrange(1, 10):02d}/{rng.randrange(1, 29):02d}/2026"

    return {
        "full_name": f"{first} {middle[0]} {last}",
        "first_name": first,
        "last_name": last,
        "middle_name": middle,
        "preferred_name": first[: rng.randrange(3, 5)],
        "dob": born,
        "ssn": f"9{rng.randrange(10, 100)}-{rng.randrange(10, 100)}-{rng.randrange(1000, 10000)}",
        "sex": "Female" if sex == "F" else "Male",
        "marital_status": rng.choice(MARITAL),
        "address": street,
        "city": city,
        "state": state,
        "zip": zip_code,
        "home_phone": phone(home_n),
        "cell_phone": phone(cell_n),
        "work_phone": phone(work_n),
        "phone": phone(home_n),
        "email": f"{first.lower()}.{last.lower()}@example.com",
        "employer": rng.choice(EMPLOYERS),
        "occupation": occupation,
        "emergency_contact": f"{kin_first} {kin_last}",
        "emergency_phone": phone(kin_n),
        "relationship": kin_relation,
        "language": rng.choice(LANGUAGES),
        "race": rng.choice(RACES),
        "ethnicity": rng.choice(ETHNICITIES),
        "insurance_name": insurer,
        "insurance_id": f"{prefix}{rng.randrange(100000000, 999999999)}",
        "group_id": f"GRP{rng.randrange(10000, 99999)}",
        "pharmacy": rng.choice(PHARMACIES),
        "physician": doctor,
        "date": today,
    }

# Printed label -> which invented value answers it. Longest labels are tried first so
# "Emergency Contact Name" is not matched by the bare "Name" pattern.
LABELS: dict[str, str] = {
    "Patient Contact Name": "full_name",
    "Full Legal Name": "full_name",
    "Legal Name": "full_name",
    "Preferred Name": "preferred_name",
    "Patient Name": "full_name",
    "Last Name": "last_name",
    "First Name": "first_name",
    "Middle Name": "middle_name",
    "Date of Birth": "dob",
    "Birth Date": "dob",
    "DOB": "dob",
    "Sex at birth": "sex",
    "Sex": "sex",
    "Gender": "sex",
    "Marital Status": "marital_status",
    "Social Security Number": "ssn",
    "SSN": "ssn",
    "Physical Address": "address",
    "Mailing Address": "address",
    "Street Address": "address",
    "Home Address": "address",
    "Address": "address",
    "City": "city",
    "State": "state",
    "Zip Code": "zip",
    "Zip": "zip",
    "Home and/or Cell Phone #": "home_phone",
    "Home Phone": "home_phone",
    "Cell Phone": "cell_phone",
    "Work Phone": "work_phone",
    "Primary Phone": "phone",
    "Phone Number": "phone",
    "Phone": "phone",
    "Email Address": "email",
    "Email": "email",
    "Employer Name": "employer",
    "Employer": "employer",
    "Second Occupation": "occupation",
    "Occupation": "occupation",
    "Emergency Contact": "emergency_contact",
    "Relationship to Patient": "relationship",
    "Relation to you": "relationship",
    "Relationship to you": "relationship",
    "Preferred Language": "language",
    "Ethnicity": "ethnicity",
    "Race": "race",
    "Insurance Carrier": "insurance_name",
    "Medical Insurance Name": "insurance_name",
    "Your Insurance Name": "insurance_name",
    "Insurance Name": "insurance_name",
    "Insurance ID": "insurance_id",
    "Policy ID #": "insurance_id",
    "Member ID": "insurance_id",
    "Group ID": "group_id",
    "Group #": "group_id",
    "Primary Care Physician": "physician",
    "Name of Pediatrician": "physician",
    "Pharmacy": "pharmacy",
    "Today's Date": "date",
    "Date": "date",
}


def label_runs(page) -> list[tuple[str, tuple[float, float, float, float], float]]:
    """Every printed label on the page, with where it ends and what size it is set at.

    Memoised per page: it is read once for every label the filler tries, and once more
    for each of those inside the caption test, which turned a second of work into
    minutes.
    """
    cached = getattr(page, "_prelimmd_runs", None)
    if cached is not None:
        return cached
    spans = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].strip():
                    spans.append((span["text"], span["bbox"], span["size"]))
    try:
        page._prelimmd_runs = spans
    except AttributeError:
        pass
    return spans


# Words beyond the label itself before a span is considered for being a sentence, and how
# lowercase those words have to be for it to read as one.
PROSE_WORDS = 8
PROSE_LOWER = 0.6


def _is_caption(text: str, label: str) -> bool:
    """Whether a span carrying these words is a field's caption rather than a sentence.

    The words of a label also occur in a form's prose -- "the individuals named as
    emergency contact and alternate emergency contact" -- and matching them there wrote
    an invented patient's answer into the middle of a consent paragraph. The benchmark
    then asserted that a reader should find a value inside that sentence, which is both
    unachievable and the wrong thing to teach: no patient writes there, and any change
    made to satisfy it would train the extractor to pull answers out of prose.

    A share-of-the-span test was tried first and was wrong: PDFs disagree about how much
    text belongs in one span, and on a form whose whole row is a single span every real
    caption is a small part of it -- that test refused all seventeen fields of one form.
    What actually separates the two is that prose is a *sentence*: many words, mostly
    lowercase, and none of the colons or rules a form row carries.
    """
    rest = [w for w in re.split(r"\s+", re.sub(re.escape(label), " ", text, flags=re.IGNORECASE)) if w]
    if len(rest) < PROSE_WORDS:
        return True
    if any(":" in w or "_" in w for w in rest):
        return True
    lower = sum(1 for w in rest if w[:1].islower())
    return lower / len(rest) <= PROSE_LOWER


def find_label(page, label: str):
    """Where this label is first printed as a caption, or None if it is not on the page.

    Repeated labels were refused outright, on the reasoning that a form asking for "Name"
    in four sections cannot produce an unambiguous expected answer. Measured across seven
    real forms that refusal was the single largest loss of coverage -- **52 labels refused
    against 39 filled** -- and the reasoning does not hold up: reading order settles it.
    A registration form asks for the patient first and the guarantor, subscriber or
    emergency contact afterwards, so the *first* occurrence is the patient's, which is
    whose answers this benchmark invents. The extractor is scored the same way: a value is
    looked up by label and the first field carrying it wins.

    So the first occurrence is filled and the rest are left blank, which is also what a
    patient does with a form whose later sections do not apply to them.
    """
    pattern = re.compile(rf"\b{re.escape(label)}\s*:?", re.IGNORECASE)
    for text, bbox, size in label_runs(page):
        if not _is_caption(text, label):
            continue
        found = pattern.search(text)
        if found:
            return (text, bbox, size, found)
    return None


# At least this many rule characters after a label before the run is read as the space
# offered to write in. Three, so a hyphenated word inside a caption is not mistaken for one.
_RULE_RUN = re.compile(r"[ \t]*([_.\u00b7\u2024\u2025\u2026\-]{3,})")


def _span_x(text: str, bbox: tuple[float, float, float, float], upto: str) -> float:
    """Where inside a span the characters `upto` end, in page coordinates.

    A form's whole row is often one span -- "Address ______ City ______ S" arrives as a
    single run of text -- and writing after the *span* put the value past the end of the
    row, where there is no room and no field. Twelve of one form's twelve labels were
    refused for exactly this, and eight forms filled nothing at all.

    The span's font is not known here (PyMuPDF reports a name this module cannot load), so
    the position is measured proportionally: Helvetica's width for the prefix over its
    width for the whole string, applied to the width the span actually occupies. A span is
    one font by definition, so the ratio holds whatever that font is.
    """
    whole = fitz.get_text_length(text, fontname=FONT, fontsize=10) or 1.0
    part = fitz.get_text_length(upto, fontname=FONT, fontsize=10)
    return bbox[0] + (bbox[2] - bbox[0]) * (part / whole)


def _exact_x(page, bbox, needle: str, edge: str) -> float | None:
    """The true left or right edge of `needle` inside this span, or None if not found.

    Measuring proportionally was close but not close enough: on a bold caption it put the
    value hard against the label -- "Patient Occupation/EmployerRiverside Logistics" --
    which the eye separates by colour but OCR returns as one token, making the field
    unrecoverable and the benchmark unfair. PyMuPDF can report where a substring actually
    sits, which is exact and needs no font metrics; the proportional measure stays as the
    fallback for the spans it cannot find.
    """
    pad = fitz.Rect(bbox[0] - 1, bbox[1] - 1, bbox[2] + 1, bbox[3] + 1)
    for rect in page.search_for(needle, clip=pad):
        return rect.x1 if edge == "end" else rect.x0
    return None


def write_point(page, text, bbox, found) -> tuple[float, float | None]:
    """Where a value answering this label should start, and where it must stop.

    Two shapes of row. When the label is followed by a rule inside its own span the value
    belongs on that rule and must not run past it -- past it is the next field's label,
    and on these forms a whole row arrives as one span: "Address ____ City ____ State".
    Writing after the *span* put the value past the end of the row, where there is no room
    and no field; twelve of one form's twelve labels were refused for exactly that, and
    eight forms filled nothing at all.

    When the span ends at the label, the space after the span is the answer area and only
    the next printed thing on the line limits it.
    """
    tail = text[found.end():]
    run = _RULE_RUN.match(tail)
    label = text[found.start():found.end()].strip()
    ends = _exact_x(page, bbox, label, "end")
    if ends is None:
        ends = _span_x(text, bbox, text[: found.end()])
    if not run:
        return bbox[2] + GAP_PT, None
    after = tail[run.end():].strip()
    cap = _exact_x(page, bbox, after.split()[0], "start") if after else None
    if cap is None:
        cap = _span_x(text, bbox, text[: found.end() + run.end()]) if after else bbox[2]
    return ends + GAP_PT, cap


# A heading and a field's caption read alike -- "Emergency Contact" is both, on different
# forms -- and four benchmark entries were filled beside a heading whose real field sat on
# the line below. Requiring a colon or a rule to the caption's right was tried as the test
# and reverted: in most of these PDFs the rule is a *drawn line* rather than text and the
# colon often sits in a neighbouring span, so the check refused most genuine captions and
# took the set from 44 filled fields to 14. Those four entries are recorded as defects in
# the ground truth instead, which is honest and costs nothing that matters.



def _is_a_rule(text: str) -> bool:
    """Whether a span is the line offered to write on rather than something printed.

    A rule is punctuation: underscores, dots, dashes, and the colon that introduces it.
    Anything with a letter or a digit in it is the form talking, and must not be drawn
    over.
    """
    return bool(text.strip()) and not any(c.isalnum() for c in text)


def free_space(page, x: float, baseline: float, size: float, own=None, cap=None) -> float:
    """How far the value may run before it hits the next printed thing on this line.

    `own` is the span the label was found in, excluded because the value is being written
    *inside* it, on its rule; `cap` is where that rule ends.
    """
    limit = page.rect.x1 - 20 if cap is None else cap
    for text, bbox, _ in label_runs(page):
        if not text.strip() or bbox == own:
            continue
        # Same line, and starting to the right of where the value would begin. Widening
        # this was tried, to catch a heading and the question set beside it on slightly
        # different baselines -- the case that produced "6. Emergency Contact Who Ngozi
        # should Okafor we call". Every tolerance large enough to catch it also pulled in
        # the line above and reported "no room" for most fields on most forms, taking one
        # form from seventeen filled fields to two. Left as it was; that one interleaving
        # is caught by the caption test instead.
        if not (bbox[1] < baseline < bbox[3] + 2):
            continue
        # Something already occupying the spot itself. Only spans *starting* to the right
        # were considered, so a span beginning to the left and running past the write
        # position was invisible -- which is how a label and the options printed beside it
        # are stored when they share one span. The value was then drawn on top of them: on
        # one form "Non-Hispanic" was written across the printed "Hispanic", leaving two
        # strings in the same place and a benchmark asserting a reader should recover one.
        #
        # But most of what sits there is the *rule*, and a rule is where a patient writes.
        # Many forms store it as text -- ": ______________________________" in one span
        # reaching across the answer space -- and refusing those cost 18 of one form's 19
        # fields. So the test is what the span is made of: words are protected, a run of
        # underscores or dots is the space being offered, and the value may run to its end.
        if bbox[0] <= x < bbox[2]:
            if not _is_a_rule(text):
                return 0.0
            limit = min(limit, bbox[2])
            continue
        if bbox[0] >= x - 1:
            limit = min(limit, bbox[0] - 2)
    return limit - x


# --- fillable PDFs -----------------------------------------------------------------
#
# 23 of the 86 forms in LARGE carry AcroForm widgets: the clinic published a PDF meant to
# be typed into. On those the answer space is a widget rather than a printed rule, so the
# rule-finding pass above sees a caption with nothing after it and fills almost nothing --
# one form went 302 widgets to a single field.
#
# A widget carries the form designer's own name for the field, which is ground truth
# nobody here invented. Only an *exact* name match is used: "First name" answers
# `first_name`, while "demographic t 7" -- which is most of them -- is left alone. Guessing
# from a widget's position would be inferring the form's layout, which is the extractor's
# job and would make the benchmark circular.


def _widget_key(name: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split())


WIDGET_LABELS = {_widget_key(label): (label, key) for label, key in LABELS.items()}


def fill_widgets(
    page, patient: dict[str, str], skip: set[str], *, check: bool,
    labels: dict[str, str] = LABELS,
) -> tuple[dict[str, str], list[str]]:
    written: dict[str, str] = {}
    skipped: list[str] = []
    by_name = WIDGET_LABELS if labels is LABELS else {_widget_key(lab): (lab, key) for lab, key in labels.items()}
    for widget in page.widgets():
        if widget.field_type_string != "Text":
            continue
        known = by_name.get(_widget_key(widget.field_name))
        if not known:
            continue
        label, key = known
        if key not in patient or label in skip or label in written:
            continue
        if (widget.field_value or "").strip():
            continue  # the publisher pre-filled it; leave their value alone
        # A widget the value cannot be read out of is not a field, it is a defect in the
        # benchmark: "Black/African American" typed into a checkbox-sized box renders as an
        # illegible smudge, and the ground truth would then assert that a reader should
        # recover a string nobody can see. Seven points is the floor Tesseract is reliable
        # at 150dpi; below that the widget is left blank and reported.
        room = widget.rect.width - 4
        if fitz.get_text_length(patient[key], fontname=FONT, fontsize=7.0) > room:
            skipped.append(f"{label} (widget too small)")
            continue
        written[label] = patient[key]
        if not check:
            widget.field_value = patient[key]
            widget.update()
    return written, skipped


# --thorough: more of what an intake packet asks, for a set filled as completely as the
# forms allow. Drawn from a second generator seeded apart from the patient's, so the
# existing sets' patients -- and their ground truth -- are exactly what they were.
LABELS_MORE: dict[str, str] = {
    "Emergency Contact Phone": "emergency_phone",
    "Emergency Phone": "emergency_phone",
    "Referring Physician": "referring",
    "Referred by": "referring",
    "Reason for Today's Visit": "reason",
    "Reason for Visit": "reason",
    "Reason for Appointment": "reason",
    "Current Medications": "medications",
    "Medications": "medications",
    "Allergies": "allergies",
    "Pharmacy Phone": "pharmacy_phone",
    "Mobile Phone": "cell_phone",
    "Subscriber Name": "full_name",
    "Policy Holder": "full_name",
    "Height": "height",
    "Weight": "weight",
    "Age": "age",
}

ALLERGIES = ["No known allergies", "Penicillin", "Sulfa drugs", "Latex", "Shellfish",
             "Codeine", "Peanuts", "Bee stings", "None"]
MEDICATIONS = ["None", "Lisinopril 10 mg daily", "Metformin 500 mg", "Levothyroxine 50 mcg",
               "Atorvastatin 20 mg", "Albuterol inhaler", "Sertraline 50 mg", "Omeprazole 20 mg"]
REASONS = ["Annual physical", "Knee pain", "Follow-up visit", "Skin rash", "Headaches",
           "Lower back pain", "Hearing check", "Allergy testing", "Blood pressure check"]


def more_for(key: str, patient: dict[str, str]) -> dict[str, str]:
    """The extra answers --thorough writes, for the patient already drawn for ``key``."""
    rng = random.Random(int(hashlib.sha256(f"{key}#more".encode()).hexdigest()[:16], 16))
    area = re.sub(r"\D", "", patient["home_phone"])[:3]
    feet, inches = rng.choice([4, 5, 5, 5, 6, 6]), rng.randrange(0, 12)
    return {
        "age": str(2026 - int(patient["dob"][-4:])),
        "allergies": rng.choice(ALLERGIES),
        "medications": rng.choice(MEDICATIONS),
        "reason": rng.choice(REASONS),
        "referring": f"Dr. {rng.choice(FIRST)[0]} {rng.choice(LAST)}",
        "pharmacy_phone": f"{area}-555-01{rng.randrange(0, 100):02d}",
        "height": f"{feet} ft {inches} in",
        "weight": f"{rng.randrange(98, 260)} lbs",
    }


# The smallest size --thorough shrinks a value to when the space after its label is short.
# Seven points is the floor Tesseract reads reliably at 150dpi (see ``fill_widgets``).
MIN_FIT_SIZE = 7.0


def fill_page(
    page, patient: dict[str, str], skip: set[str] = frozenset(), *, check: bool,
    labels: dict[str, str] = LABELS, shrink: bool = False,
) -> tuple[dict[str, str], list[str]]:
    written: dict[str, str] = {}
    skipped: list[str] = []
    # A shorter label is a substring of a longer one -- "Zip" inside "Zip Code:", "Email"
    # inside "Email Address:" -- and both would match the same printed text and write a
    # value twice at the same spot. Longer labels are tried first and claim their span,
    # so the shorter one finds it taken.
    claimed: list[tuple[float, float, float, float]] = []
    # Longest label first, always. "Address" matches inside "Email address:", and whichever
    # is tried first claims the span -- so dictionary order silently decided that a street
    # address got written onto the email line. Ordering by length makes the more specific
    # label win wherever one label contains another.
    for label, key in sorted(labels.items(), key=lambda kv: -len(kv[0])):
        if key not in patient or label in written or label in skip:
            continue
        found = find_label(page, label)
        if not found:
            continue
        text, bbox, size, at = found
        size = max(7.0, min(size, 12.0))
        x, cap = write_point(page, text, bbox, at)
        baseline = bbox[3] - size * 0.22
        value = patient[key]
        needed = fitz.get_text_length(value, fontname=FONT, fontsize=size)
        # Claim the space the value will occupy, not the span it was found in. Real rows
        # carry three fields in one span -- "Address ____ City ____ State" -- and claiming
        # the span meant the first label on a row silently refused the other two.
        here = (x, bbox[1], x + needed, bbox[3])
        if any(
            here[0] < c[2] and c[0] < here[2] and here[1] < c[3] and c[1] < here[3]
            for c in claimed
        ):
            continue
        room = free_space(page, x, (bbox[1] + bbox[3]) / 2, size, own=bbox, cap=cap)
        if needed > room and shrink and room > 0 and size * room / needed >= MIN_FIT_SIZE:
            # Written smaller, as a patient does when the line is short -- never below
            # the size a reader can be expected to read.
            size = int(size * room / needed * 10) / 10
            needed = fitz.get_text_length(value, fontname=FONT, fontsize=size)
            here = (x, bbox[1], x + needed, bbox[3])
        if needed > room:
            skipped.append(f"{label} (no room)")
            continue
        claimed.append(here)
        if not check:
            page.insert_text((x, baseline), value, fontname=FONT, fontsize=size, color=INK)
        written[label] = value
    return written, skipped


# --- tick boxes ------------------------------------------------------------------
#
# --tick answers choice questions the way a patient does: an X in the box of one option,
# and no text. Boxes are found on the blank's own text and drawing layers -- a symbol-font
# glyph or a small stroked square, or a checkbox widget -- never with the extractor, so the
# benchmark stays independent of what it scores. The answer recorded is the option's
# caption as printed, under the question's caption as printed.

BOX_CHARS = set("\u2610\u25a1\u2751\u2752\u25a2\u25fb\u2b1c")
# Outline squares in the Wingdings family, under their ASCII and private-use codes.
SYMBOL_BOX_CHARS = set("oqr\u00a8\uf06f\uf071\uf072\uf0a8")
SYMBOL_FONTS = ("wingding", "webding", "dingbat")
BOX_MIN_PT, BOX_MAX_PT = 3.5, 16.0
CAPTION_REACH = 3.0     # box widths to the right in which an option's caption starts
CAPTION_WORDS = 5
LABEL_WORDS = 6
CHOICE_KEYS = (("gender", "sex"), ("sex", "sex"), ("marital", "marital_status"),
               ("race", "race"), ("ethnic", "ethnicity"), ("hispanic", "ethnicity"),
               ("relationship", "relationship"), ("language", "language"))


def _squareish(x0: float, y0: float, x1: float, y1: float) -> bool:
    w, h = x1 - x0, y1 - y0
    return BOX_MIN_PT <= w <= BOX_MAX_PT and BOX_MIN_PT <= h <= BOX_MAX_PT and 0.7 <= w / h <= 1.43


def _drawn_square(page, cell: fitz.Rect) -> fitz.Rect | None:
    """The square a box glyph actually draws inside its character cell."""
    zoom = 8
    pix = page.get_pixmap(clip=cell, matrix=fitz.Matrix(zoom, zoom), alpha=False, colorspace=fitz.csGRAY)
    if pix.width == 0 or pix.height == 0:
        return None
    import numpy as np

    ink = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width) < 128
    ys, xs = np.nonzero(ink)
    if xs.size == 0:
        return None
    return fitz.Rect(cell.x0 + xs.min() / zoom, cell.y0 + ys.min() / zoom,
                     cell.x0 + (xs.max() + 1) / zoom, cell.y0 + (ys.max() + 1) / zoom)


def tick_boxes(page) -> list[tuple[fitz.Rect, object]]:
    """Every empty tick box on the page, as (rect, widget or None)."""
    found: list[tuple[fitz.Rect, object]] = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                symbol = any(f in span["font"].lower() for f in SYMBOL_FONTS)
                for ch in span["chars"]:
                    if ch["c"] in BOX_CHARS or (symbol and ch["c"] in SYMBOL_BOX_CHARS):
                        # A glyph's cell runs from the font's ascent to its descent; where
                        # the square sits inside it depends on the font, so it is read off
                        # the rendered ink rather than guessed.
                        r = _drawn_square(page, fitz.Rect(ch["bbox"]))
                        if r is not None and _squareish(*r):
                            found.append((r, None))
    for drawing in page.get_drawings():
        if drawing.get("fill") not in (None, (1.0, 1.0, 1.0)):
            continue
        rect = drawing.get("rect")
        items = drawing.get("items", [])
        is_square = any(op[0] == "re" for op in items) or (len(items) == 4 and all(op[0] == "l" for op in items))
        if rect is not None and is_square and _squareish(*rect):
            found.append((fitz.Rect(rect), None))
    for widget in page.widgets():
        if widget.field_type_string == "CheckBox" and _squareish(*widget.rect):
            found.append((fitz.Rect(widget.rect), widget))
    unique: list[tuple[fitz.Rect, object]] = []
    for rect, widget in sorted(found, key=lambda item: (item[1] is None, item[0].y0, item[0].x0)):
        if not any(abs(rect.x0 - r.x0) < 3 and abs(rect.y0 - r.y0) < 3 for r, _ in unique):
            unique.append((rect, widget))
    return unique


def _caption_right_of(box: fitz.Rect, words, stop_x: float) -> tuple[str, float]:
    """The option caption printed just right of a box, and where it ends."""
    line = sorted(
        (w for w in words
         if min(w[3], box.y1) - max(w[1], box.y0) > 0.4 * box.height and w[0] >= box.x1 - 1 and w[2] <= stop_x + 1),
        key=lambda w: w[0],
    )
    if not line or line[0][0] > box.x1 + CAPTION_REACH * box.width:
        return "", box.x1
    taken = [line[0]]
    for word in line[1:]:
        if len(taken) >= CAPTION_WORDS or word[0] - taken[-1][2] > 0.6 * (word[3] - word[1]):
            break
        taken.append(word)
    return " ".join(w[4] for w in taken).strip(" :_"), taken[-1][2]


def option_rows(page):
    """Rows of boxed options with the question they answer: (label, [(rect, widget, caption)])."""
    # Upright words only: a banner printed sideways down the margin ("PATIENT INFORMATION")
    # sits level with every row beside it and would be read into their captions.
    words = [w for w in page.get_text("words") if not (len(w[4]) > 2 and (w[3] - w[1]) > 1.8 * (w[2] - w[0]))]
    boxes = tick_boxes(page)
    lines: list[list[tuple[fitz.Rect, object]]] = []
    for rect, widget in sorted(boxes, key=lambda b: ((b[0].y0 + b[0].y1) / 2, b[0].x0)):
        centre = (rect.y0 + rect.y1) / 2
        for line in lines:
            if abs((line[0][0].y0 + line[0][0].y1) / 2 - centre) <= line[0][0].height / 2:
                line.append((rect, widget)); break
        else:
            lines.append([(rect, widget)])
    rows = []
    for line in lines:
        line.sort(key=lambda b: b[0].x0)
        entries = []
        for i, (rect, widget) in enumerate(line):
            stop = line[i + 1][0].x0 if i + 1 < len(line) else page.rect.x1
            caption, end = _caption_right_of(rect, words, stop)
            entries.append((rect, widget, caption, end))
        # A question's caption ends in a colon: one printed between two boxes starts the
        # next question on the same line.
        current: list = []
        current_start = page.rect.x0
        rows_here = []
        for i, entry in enumerate(entries):
            if current:
                gap_words = [w for w in words if current[-1][3] <= w[0] and w[2] <= entry[0].x0 + 1
                             and min(w[3], entry[0].y1) - max(w[1], entry[0].y0) > 0.4 * entry[0].height]
                if any(w[4].endswith(":") for w in gap_words):
                    rows_here.append((current, current_start))
                    current = []
            if not current:
                # The question's caption lies between the previous option and this box.
                current_start = entries[i - 1][3] if i else page.rect.x0
            current.append(entry)
        if current:
            rows_here.append((current, current_start))
        for row, start in rows_here:
            if len(row) < 2 or not all(caption and len(caption.split()) <= CAPTION_WORDS for _, _, caption, _ in row):
                continue
            first = row[0][0]
            left = sorted(
                (w for w in words if start - 1 <= w[0] and w[2] <= first.x0 + 1
                 and min(w[3], first.y1) - max(w[1], first.y0) > 0.4 * first.height),
                key=lambda w: w[0],
            )
            label_words = []
            for word in reversed(left):
                if _is_a_rule(word[4]) or (label_words and label_words[0][0] - word[2] > 1.5 * (word[3] - word[1])):
                    break  # a rule, or a gap: the previous field's answer space
                if label_words and word[4].rstrip().endswith(":"):
                    break  # the previous field's caption
                label_words.insert(0, word)
                if len(label_words) >= LABEL_WORDS:
                    break
            printed = " ".join(w[4] for w in label_words).strip(" _")
            label = printed.strip(" :")
            # Only a question: its caption ends in a colon or a question mark, or names a
            # choice the patient record answers. Headings and wrapped sentence fragments
            # ("INFORMATION", "With") beside a row of boxes are not questions anyone could
            # expect a reader to name.
            is_question = printed.endswith((":", "?")) or any(word in label.lower() for word, _ in CHOICE_KEYS)
            if label and is_question and any(c.isalpha() for c in label):
                rows.append((label, [(r, wd, c) for r, wd, c, _ in row]))
    return rows


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _choose(label: str, captions: list[str], patient: dict[str, str], rng) -> int:
    key = next((k for word, k in CHOICE_KEYS if word in label.lower()), None)
    if key and key in patient:
        want = patient[key]
        for i, caption in enumerate(captions):
            if _tokens(caption) == _tokens(want):
                return i
        if key == "sex":
            for i, caption in enumerate(captions):
                if _tokens(caption) == {want[0].lower()}:
                    return i
        for i, caption in enumerate(captions):
            a, b = _tokens(caption), _tokens(want)
            if a and b and (a <= b or b <= a):
                return i
    return rng.randrange(len(captions))


def tick_page(
    page, patient: dict[str, str], rng, taken: set[str], *, check: bool, pending: list | None = None
) -> dict[str, str]:
    """Tick one option on every boxed choice question; {question caption: option caption}.

    A pen X is not drawn here but appended to ``pending`` as (page number, box), for
    ``draw_ticks`` once the document is flattened: flattening paints each form widget's
    appearance over the page, and on forms whose widgets cover their printed boxes that
    white appearance hid every X drawn before it (24 of 268 ticks on the first build).
    """
    written: dict[str, str] = {}
    for label, row in option_rows(page):
        if label in taken or label in written:
            continue
        captions = [c for _, _, c in row]
        pick = _choose(label, captions, patient, rng)
        rect, widget, caption = row[pick]
        if not check:
            if widget is not None:
                widget.field_value = widget.on_state()
                widget.update()
            elif pending is not None:
                pending.append((page.number, fitz.Rect(rect)))
        written[label] = caption
    return written


def draw_ticks(doc, pending: list) -> None:
    """A pen X in each pending box, on top of everything the page already shows."""
    for number, rect in pending:
        page = doc[number]
        # Inside the box, as a pen X is, with a stroke that scales with the box: a fixed
        # 1.3pt stroke filled a 5pt box solid.
        inset = rect.width * 0.22
        stroke = max(0.5, rect.width * 0.1)
        a = fitz.Rect(rect.x0 + inset, rect.y0 + inset, rect.x1 - inset, rect.y1 - inset)
        page.draw_line(a.tl, a.br, color=INK, width=stroke)
        page.draw_line(a.bl, a.tr, color=INK, width=stroke)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/real-forms/LARGE")
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    # Diagnostic only. Fills every form with the *same* patient, which is what the
    # corpus wrongly was until 2026-09-13. Its one use is measuring how much a repeated
    # identity inflates a score: same forms, same placement, only the values differ.
    ap.add_argument("--same-patient", action="store_true", help=argparse.SUPPRESS)
    # Six, not two. These are packets: a welcome letter, a HIPAA notice and a financial
    # policy commonly sit ahead of the registration page, and at two pages fourteen
    # forms filled nothing or nearly nothing because their fields start on page 3.
    ap.add_argument("--pages", type=int, default=6, help="how many pages to fill")
    # A second fill of the same blanks with a *different* patient per form, for measuring
    # what a template learns from one fill on another: the enrolled track's whole claim.
    ap.add_argument("--variant", default="", help="seed suffix: a different patient per form")
    # Fill as completely as the forms allow: more of an intake packet's questions, and a
    # value shrunk (never below 7pt) where the line after its label is short.
    ap.add_argument("--thorough", action="store_true")
    # Answer boxed choice questions with an X in one option's box, and no text.
    ap.add_argument("--tick", action="store_true")
    args = ap.parse_args()
    labels = {**LABELS, **LABELS_MORE} if args.thorough else LABELS

    folder = ROOT / args.dir
    truth: dict[str, dict[str, str]] = {}
    patients: dict[str, str] = {}
    ticked_labels: dict[str, list[str]] = {}
    for pdf in sorted(folder.glob("*.pdf")):
        if pdf.stem.endswith("_FILLED"):
            continue
        doc = fitz.open(pdf)
        # Seeded from the form's name, so this form always draws this patient.
        seed = "one-patient" if args.same_patient else f"{pdf.stem}#{args.variant}".rstrip("#")
        patient = patient_for(seed)
        if args.thorough:
            patient.update(more_for(seed, patient))
        all_written: dict[str, str] = {}
        per_page: list[dict[str, str]] = []
        choices: dict[int, list[str]] = {}
        pending: list = []
        notes: list[str] = []
        for index in range(min(args.pages, doc.page_count)):
            page = doc[index]
            # Widgets first: where a form offers a typed field, that is where the answer
            # goes, and the printed caption beside it has no rule to write on anyway.
            ticked: dict[str, str] = {}
            if args.tick:
                rng = random.Random(int(hashlib.sha256(f"{seed}#tick{index}".encode()).hexdigest()[:16], 16))
                ticked = tick_page(page, patient, rng, set(all_written), check=args.check, pending=pending)
                all_written.update(ticked)
                choices.setdefault(index, []).extend(ticked)
            # A question answered by a tick is not answered again in writing.
            answered = {lab for lab in labels if any(_tokens(lab) <= _tokens(q) for q in ticked)}
            typed, tight = fill_widgets(page, patient, set(all_written) | answered, check=args.check, labels=labels)
            all_written.update(typed)
            notes += tight
            written, skipped = fill_page(
                page, patient, set(all_written) | answered, check=args.check, labels=labels, shrink=args.thorough
            )
            all_written.update(written)
            per_page.append({**ticked, **typed, **written})
            notes += skipped
        print(f"\n{pdf.name}: {len(all_written)} fields filled"
              + (f", {sum(len(v) for v in choices.values())} by tick" if args.tick else ""))
        for label, value in all_written.items():
            print(f"    {label:26} = {value}")
        if notes:
            print(f"    skipped: {', '.join(notes)}")
        if args.check or not all_written:
            continue
        out = pdf.with_name(f"{pdf.stem}_FILLED.pdf")
        # Flatten typed values into the page itself. Left as live widgets they are
        # invisible to `page.get_text()`, so the PDF reader would be scored against
        # answers it has no way to see; baked, a fillable form is exactly what a patient
        # prints, which is the artefact the rest of the corpus already is.
        doc.bake()
        draw_ticks(doc, pending)
        doc.save(out)
        # Render the page that actually carries answers, not always page 1. Half these
        # packets open with a welcome letter, so a page-1 render was a picture of prose
        # and the image half of the benchmark scored almost nothing.
        best = max(range(len(per_page)), key=lambda i: len(per_page[i]))
        jpg = out.with_name(f"{out.stem}_page{best + 1}.jpg")
        doc[best].get_pixmap(dpi=DPI).pil_save(jpg, quality=92)
        # Truth is recorded per artefact, not per form: the PDF carries every page, the
        # render carries only what was written on the page rendered. Keying another page's
        # answers to it would score fields against a picture that never contained them.
        truth[out.name] = all_written
        patients[out.name] = patient["full_name"]
        if choices:
            ticked_labels[out.name] = sorted({q for qs in choices.values() for q in qs})
        if per_page[best]:
            truth[jpg.name] = per_page[best]
            if choices.get(best):
                ticked_labels[jpg.name] = sorted(set(choices[best]))

    if truth and not args.check:
        record = folder / "ground_truth_new.json"
        record.write_text(
            json.dumps(
                {
                    "_comment": (
                        "Real clinic forms downloaded 2026-09-10 and filled by "
                        "scripts/fill_forms.py, which places each value beside its own "
                        "printed label using the PDF's text layer and never imports the "
                        "extractor. Patient is invented. UNTUNED: nothing has been fixed "
                        "against these. A different invented patient per form."
                    ),
                    "_patients": patients,
                    **({"_ticked": ticked_labels} if ticked_labels else {}),
                    **truth,
                },
                indent=2,
            )
        )
        shown = record.relative_to(ROOT) if record.is_relative_to(ROOT) else record
    print(f"\nwrote {shown} covering {sum(len(v) for v in truth.values())} fields")
    return 0


if __name__ == "__main__":
    sys.exit(main())
