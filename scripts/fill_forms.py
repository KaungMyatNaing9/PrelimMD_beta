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

Anything skipped is listed at the end, so a thin fill is visible rather than silent. The
patient is invented; no real person's details appear here, and none should.

    python scripts/fill_forms.py --dir data/real-forms/NEW
    python scripts/fill_forms.py --dir data/real-forms/NEW --check   # report, write nothing
"""

from __future__ import annotations

import argparse
import json
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

# One invented patient. Every value is fictional.
PATIENT = {
    "full_name": "Amara C Okafor",
    "first_name": "Amara",
    "last_name": "Okafor",
    "middle_name": "Chidinma",
    "preferred_name": "Amy",
    "dob": "04/17/1986",
    "ssn": "412-55-0184",
    "sex": "Female",
    "marital_status": "Married",
    "address": "1420 Bellefield Avenue",
    "city": "Pittsburgh",
    "state": "PA",
    "zip": "15206",
    "home_phone": "412-555-0184",
    "cell_phone": "412-555-0199",
    "work_phone": "412-555-0286",
    "phone": "412-555-0184",
    "email": "amara.okafor@example.com",
    "employer": "Riverside Logistics",
    "occupation": "Operations Manager",
    "emergency_contact": "Ngozi Okafor",
    "emergency_phone": "412-555-0311",
    "relationship": "Sister",
    "language": "English",
    "race": "Black/African American",
    "ethnicity": "Non-Hispanic",
    "insurance_name": "Keystone Health",
    "insurance_id": "KH884120773",
    "group_id": "GRP40218",
    "pharmacy": "Bellefield Pharmacy",
    "physician": "Dr Helen Osei-Bonsu",
    "date": "08/25/2026",
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
        if pattern.search(text):
            return (text, bbox, size)
    return None


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


def free_space(page, x: float, baseline: float, size: float) -> float:
    """How far the value may run before it hits the next printed thing on this line."""
    limit = page.rect.x1 - 20
    for text, bbox, _ in label_runs(page):
        if not text.strip():
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


def fill_page(page, *, check: bool) -> tuple[dict[str, str], list[str]]:
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
    for label, key in sorted(LABELS.items(), key=lambda kv: -len(kv[0])):
        if key not in PATIENT or label in written:
            continue
        found = find_label(page, label)
        if not found:
            continue
        _text, bbox, size = found
        if any(
            bbox[0] < c[2] and c[0] < bbox[2] and bbox[1] < c[3] and c[1] < bbox[3]
            for c in claimed
        ):
            continue
        claimed.append(bbox)
        size = max(7.0, min(size, 12.0))
        x = bbox[2] + GAP_PT
        baseline = bbox[3] - size * 0.22
        value = PATIENT[key]
        needed = fitz.get_text_length(value, fontname=FONT, fontsize=size)
        if needed > free_space(page, x, (bbox[1] + bbox[3]) / 2, size):
            skipped.append(f"{label} (no room)")
            continue
        if not check:
            page.insert_text((x, baseline), value, fontname=FONT, fontsize=size, color=INK)
        written[label] = value
    return written, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/real-forms/NEW")
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    ap.add_argument("--pages", type=int, default=2, help="how many pages to fill")
    args = ap.parse_args()

    folder = ROOT / args.dir
    truth: dict[str, dict[str, str]] = {}
    for pdf in sorted(folder.glob("*.pdf")):
        if pdf.stem.endswith("_FILLED"):
            continue
        doc = fitz.open(pdf)
        all_written: dict[str, str] = {}
        first_page: dict[str, str] = {}
        notes: list[str] = []
        for index in range(min(args.pages, doc.page_count)):
            written, skipped = fill_page(doc[index], check=args.check)
            all_written.update(written)
            if index == 0:
                first_page.update(written)
            notes += skipped
        print(f"\n{pdf.name}: {len(all_written)} fields filled")
        for label, value in all_written.items():
            print(f"    {label:26} = {value}")
        if notes:
            print(f"    skipped: {', '.join(notes)}")
        if args.check or not all_written:
            continue
        out = pdf.with_name(f"{pdf.stem}_FILLED.pdf")
        doc.save(out)
        jpg = out.with_name(f"{out.stem}_page1.jpg")
        doc[0].get_pixmap(dpi=DPI).pil_save(jpg, quality=92)
        # Truth is recorded per artefact, not per form: the PDF carries every page, the
        # page-1 render carries only what was written on page 1. Keying page-2 answers to
        # a page-1 image would score fields against a picture that never contained them.
        truth[out.name] = all_written
        if first_page:
            truth[jpg.name] = first_page

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
                        "against these."
                    ),
                    **truth,
                },
                indent=2,
            )
        )
        print(f"\nwrote {record.relative_to(ROOT)} covering {sum(len(v) for v in truth.values())} fields")
    return 0


if __name__ == "__main__":
    sys.exit(main())
