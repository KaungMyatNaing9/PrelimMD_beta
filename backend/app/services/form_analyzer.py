import hashlib
import itertools
import re
import time
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NamedTuple

import cv2
import fitz

from app.config import settings
from app.schemas.form_understanding import DetectedFormField, FormAnalysis, RequiredStatus
from app.schemas.models import BoundingBox, FieldType
from app.services.canonical_registry import PHRASING, known_captions, match, norm
from app.services.field_layout import (
    MARKED_INK_RATIO,
    Box,
    captions_for_row,
    columns_beneath,
    detect_boxes,
    detect_checkboxes,
    detect_write_rules,
    ink_ratio,
    label_for_box,
    partition,
    split_runs,
)
from app.services.field_validity import plausible
from app.services.fusion import fuse
from app.services.relinker import available as relink_available
from app.services.relinker import relink
from app.services.templates import MATCH_THRESHOLD, registry
from app.services.vision_extractor import available as vision_available
from app.services.vision_extractor import extract as vision_extract
from app.services.word_source import (
    MAX_LABEL_WORDS,
    MIN_LEGIBLE,
    PdfMark,
    Word,
    group_lines_tolerant,
    label_run_start,
    pdf_marks,
    words_from_image,
    words_from_pdf,
    words_in_region,
)

# Section titles no clinic's form would ask a patient to answer. Generic words only: a
# specific clinic's letterhead once sat here, left over from the synthetic corpus, and a
# rule that names one form is a rule tuned to it.
HEADINGS = {
    "patient intake form",
    "demographics",
    "emergency contact",
    "insurance information",
    "medical history",
    "consent",
}


def box(words, w, h):
    return BoundingBox(
        x=min(x[0] for x in words) / w,
        y=min(x[1] for x in words) / h,
        width=(max(x[2] for x in words) - min(x[0] for x in words)) / w,
        height=(max(x[3] for x in words) - min(x[1] for x in words)) / h,
    )


def is_blank_marker(text) -> bool:
    """True for a printed fill-in line rather than an answer.

    Real forms mark an empty field with a rule to write on — a run of underscores,
    dots or dashes. Read literally that becomes the field's value, so a blank form
    comes back fully "filled" with punctuation.

    The test is that nothing alphanumeric survives, rather than a list of the characters
    a rule is made of. Enumerating them missed every shape not thought of -- "/" between
    an empty day and month, a lone "(" opening an unanswered phone number, "_ _ / _ _"
    for a date -- and each was reported as a confident answer. No answer a patient writes
    is free of letters and digits, so the general form of the test is also the safe one.

    Fill-in glyphs from a symbol font land in the Unicode private use area and carry no
    meaning outside the font that drew them; they are furniture, never an answer.
    """
    raw = "".join(str(text or "").split())
    stripped = "".join(c for c in raw if not ("\ue000" <= c <= "\uf8ff"))
    return bool(raw) and not any(c.isalnum() for c in stripped)


# A parenthetical that opens a value and carries no digits is an instruction printed for
# the patient -- "(circle one)", "(please print)", "(last, first)" -- not part of what they
# answered. The no-digits test is what keeps a phone number safe: "(412) 555-0184" opens
# the same way and is entirely the answer.
INSTRUCTION = re.compile(r"^\s*\([^)\d]*\)\s*")


def strip_instruction(text):
    """Remove a leading printed instruction from a value, if that is what it is."""
    if not isinstance(text, str):
        return text
    without = INSTRUCTION.sub("", text, count=1)
    return without if without.strip() else text


# A run of rule glyphs is the form's writing line, not part of what was written on it.
# PDF text layers hand a value over with its empty rule attached -- "__________ Guerrero"
# -- and a reviewer was shown the rule as if the patient had typed it. Two or more, so a
# single underscore inside an e-mail address survives.
RULE_GLYPHS = re.compile(r"[_.\u2024\u2025\u2026]{2,}")


def normalize(v, t):
    if isinstance(v, str):
        # Line by line, so a medication list keeps its line breaks.
        v = "\n".join(" ".join(RULE_GLYPHS.sub(" ", line).split()) for line in v.split("\n"))
    if t == FieldType.DATE:
        months = {
            name: index
            for index, name in enumerate(
                (
                    "January",
                    "February",
                    "March",
                    "April",
                    "May",
                    "June",
                    "July",
                    "August",
                    "September",
                    "October",
                    "November",
                    "December",
                ),
                1,
            )
        }
        try:
            month, day, year = v.replace(",", "").split()
            return f"{int(year):04d}-{months[month]:02d}-{int(day):02d}"
        except (KeyError, ValueError):
            return v
    if t == FieldType.PHONE:
        # Hyphenated form is the stored contract: it round-trips onto a regenerated form
        # legibly, and bare digits cannot be re-formatted once the grouping is lost.
        digits = "".join(c for c in v if c.isdigit())
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}" if len(digits) == 10 else v
    if t in (FieldType.MULTILINE_TEXT, FieldType.MEDICATION_LIST):
        # Collapse runs of spaces per line, but keep the line breaks: a medication list
        # loses meaning if its entries are run together.
        return "\n".join(" ".join(line.split()) for line in v.split("\n")).strip()
    return " ".join(v.split())


def _mean_confidence(words: list[Word]) -> float:
    return sum(x.confidence for x in words) / len(words) if words else 0.0



# A caption is a caption; past this many words it is a sentence, and a reader that has
# bound a value to a sentence has bound it to the form's instructions.
MAX_CAPTION_WORDS = 6
# How much surer an enlarged crop's reading must be than the page's to replace it.
CROP_MARGIN = 0.05


def _is_question_tail(value) -> bool:
    """A value that is still part of the question it was supposed to answer.

    Nobody answers a form by asking it something back, so a value carrying the question
    mark is the caption's own tail read as a reply.
    """
    return str(value or "").rstrip().endswith("?")


def _is_option_glyphs(value) -> bool:
    """A value made of tick-box glyphs and the single letters labelling them.

    Symbol fonts draw check boxes from the Unicode private use area and OCR returns them as
    letters: "\uf06fF\uf06fM" for a sex field, "OFOM" where the box became an "O". What is
    left once the boxes are removed is one letter per option -- a question offering choices,
    not a choice anybody made.
    """
    text = "".join(str(value or "").split())
    if not text:
        return False
    letters = [c for c in text if not ("\ue000" <= c <= "\uf8ff")]
    if len(letters) == len(text):
        # No symbol-font boxes here, so the only thing that qualifies is a run of single
        # letters standing as options, and at least two of them.
        words = str(value).split()
        return len(words) >= 2 and all(len(w) == 1 and w.isalpha() for w in words)
    return bool(letters) and all(c.isalpha() or not c.isalnum() for c in letters)


def _looks_answered(field: DetectedFormField) -> bool:
    """Whether a field looks like a question somebody actually answered.

    Counting values is not enough to choose between two readers. On one real form the
    boxed reader produced eight values to the inline reader's five and every one of the
    eight was wrong -- headings bound to each other, "PLEASE PRINT AND COMPLETE ALL
    ENTRIES" holding "LAST NAME Okafor" as its answer. Both readings are made of real
    text; what separates them is what the text *is*.

    Three things say a reading is furniture rather than an answer, and none of them
    depends on how much vocabulary happens to exist:

    * the value is itself a printed field label, so it is the form talking, not a patient;
    * the label is a heading, or prose long enough to be an instruction;
    * the label has no letters at all -- OCR leaves ")" and "|" behind as labels.

    Audited 2026-09-11 against 144 known-false intake values: **it vouches for 60 of them,
    41.7%**, including "you drink?" as an answer to "How much alcohol do". Sharpening it
    was tried and reverted (R14): this scores `_best_layout`, so tightening it flipped a
    page to the boxed reader, which produced *more* values and three fewer correct ones.
    Tests that reject furniture belong where values are finalised, not here -- what this
    function changes is which reading of a whole page survives.
    """
    value = field.normalized_value
    if plausible(value, field.field_type) < SUSPECT:
        return False
    if match(str(value))[0] is not None:
        return False
    label = field.label_text
    if norm(label) in HEADINGS or len(label.split()) > MAX_CAPTION_WORDS:
        return False
    return sum(c.isalpha() for c in label) >= 2


# An answer needs somewhere to have been written. Words inside one printed caption sit an
# ordinary space apart; a form that expects an answer leaves more room than that, whether as
# a rule, a box, or plain gap. This is the same distance argument `label_run_start` uses to
# find where a value *ends*, applied to the question it never asked -- whether a value began
# at all. Twice the page's own word spacing is the threshold: derived per page from the text
# itself, so it holds across font sizes and scan resolutions rather than encoding one form's.
ANSWER_GAP_RATIO = 2.0


def _typical_gap(lines: list[list[Word]]) -> float:
    """The page's ordinary space between two words of the same caption."""
    gaps = sorted(
        b.x0 - a.x1
        for line in lines
        for a, b in itertools.pairwise(line)
        if b.x0 > a.x1
    )
    return gaps[len(gaps) // 2] if gaps else 0.0


def _has_answer_space(line: list[Word], colon: int, typical: float) -> bool:
    """Whether the run after ``colon`` sits in space the form left to be filled in.

    Only consulted for lines carrying no colon. A colon is itself the form stating that an
    answer follows; without one, the gap is the only thing separating "Group Name" the
    caption from "Group  Amara" the answered field, and word shape cannot tell them apart --
    a surname and a caption are both title case.
    """
    if typical <= 0 or colon + 1 >= len(line):
        return True
    before, after = line[colon], line[colon + 1]
    if is_blank_marker(before.text) or is_blank_marker(after.text):
        return True
    return (after.x0 - before.x1) >= typical * ANSWER_GAP_RATIO


def _caps_label_ends(line: list[Word]) -> list[int]:
    """Where an all-capital label ends, on a line that punctuates nothing.

    Capitalisation is what separates caption from answer when no colon does -- the layout
    literature reaches for the same signal, grouping text by "font face and
    capitalisation" rather than by delimiters. It is used *only* on a line with no colon,
    so nothing that already parses changes.

    The run has to name a field the vocabulary already knows. Without that, every shouted
    heading on a page becomes a label and its neighbours become its answer, which is the
    exact failure this is meant to repair rather than reproduce.
    """
    ends = []
    run_start = None
    for index, word in enumerate(line + [None]):
        if word is not None and _is_shouted(word.text):
            run_start = index if run_start is None else run_start
            continue
        if run_start is None:
            continue
        # A caps run that nothing follows on this line has no answer beside it.
        if index < len(line):
            # No HEADINGS test here on purpose. "Emergency Contact" is a section title on
            # one form and a field with an answer beside it on the next, and treating the
            # word as a heading wherever it appears loses the field every time. What
            # settles it is local: something follows this run on the same line, so it is
            # a caption with an answer after it, not a title standing alone.
            label = " ".join(z.text for z in line[run_start:index])
            mapped, _, _ = match(label)
            if mapped:
                ends.append(index - 1)
        run_start = None
    return ends


def _known_label_ends(line: list[Word]) -> list[int]:
    """Where a label the vocabulary already knows ends, when it opens the line.

    Capitalisation separates "FIRST NAME" from "Amara", and punctuation separates
    "First Name:" from it, but neither separates "Patient Name Amara C Okafor" -- the
    caption and the answer are both title case and nothing sits between them. One real
    form writes every field that way, and its answers were being carried around inside a
    single box bound to the page's own title.

    What is left to go on is that the caption is a field this form's vocabulary can name.
    Only an *exact* alias counts: the loose token similarity that serves elsewhere would
    let any line carrying a common word declare itself a field. Longest match first, so
    "Patient Name" wins over "Patient".

    Only the line's start is scanned. Scanning the whole line for every caption was tried
    -- a row does carry several, and "Race Black/African American Preferred Language
    English" is one line on a real form -- and it cost five fields across the two fresh
    sets. Without a colon there is nothing to say where each value ends, so every extra
    caption found splits a row that was being read correctly. The language on that row
    stays unread; that is one field, and the split cost more.
    """
    # Up to the whole line, not one short of it. A line can be nothing but a caption and
    # the rule offered to write on -- "________ Employer Name" -- and insisting that a
    # word be left over for a value made the caption's own tail the answer: the employer
    # was reported as "Name". A field with no value is the truth about such a line, and
    # the wrong value was worse than none because nothing marked it wrong.
    for take in range(min(MAX_LABEL_WORDS, len(line)), 0, -1):
        label = " ".join(z.text for z in line[:take]).rstrip(":")
        mapped, score, _ = match(label)
        if mapped and score >= 1.0:
            return [take - 1]
    return []


def _is_shouted(text: str) -> bool:
    """A word printed in capitals, ignoring anything that carries no letters at all."""
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 2 and all(c.isupper() for c in letters)


def _fields_with_lines(
    lines: list[list[Word]], w: float, h: float, source: str
) -> list[tuple[list[Word], DetectedFormField]]:
    """Split each ``label: value`` line into a detected field, paired with its source
    line so callers can tell which lines were consumed.

    Shared by the PDF and OCR paths; only the confidence model differs, since PDF
    text is exact while OCR carries a per-word recognition score.
    """
    is_ocr = source == "ocr"
    typical = _typical_gap(lines)
    fields = []
    for i, line in enumerate(lines):
        texts = [z.text for z in line]
        colons = [j for j, t in enumerate(texts) if ":" in t]
        if not colons:
            # Plenty of real forms print "FIRST NAME Amara" where others print
            # "First Name: Amara". Skipping those lines outright left the boxed reader to
            # cover a whole page it could not parse, and it bound headings to each other:
            # on one form it produced eight values and every one was wrong.
            #
            # A rule is offered to write on; it is not part of the caption beside it, and
            # a line that opens with one has its first caption hidden behind it. On a real
            # form "________ Employer Name" therefore failed to match the vocabulary at
            # the line's start -- the only place ``_known_label_ends`` looks -- and the
            # label was cut at "Employer", reporting the rest of its own caption, "Name",
            # as the employer. Rules are dropped for the purpose of finding where the
            # caption ends; the words themselves are untouched, so the value's own bounds
            # and ``is_blank_marker`` still see them.
            without = [z for z in line if not is_blank_marker(z.text)]
            colons = _caps_label_ends(line) or _known_label_ends(line)
            if not colons and len(without) < len(line):
                ends = _caps_label_ends(without) or _known_label_ends(without)
                kept = [j for j, z in enumerate(line) if not is_blank_marker(z.text)]
                colons = [kept[e] for e in ends if e < len(kept)]
            # Without a colon the form has not said an answer follows, so the space it left
            # has to say it instead. Measured across TEST2 and TEST4, two thirds of every
            # value this pipeline emitted was the second half of a printed caption split in
            # two -- "Group" answered "Name", "Race /" answered "Ethnicity".
            colons = [c for c in colons if _has_answer_space(line, c, typical)]
        if not colons:
            continue
        start = 0
        for k, colon in enumerate(colons):
            # A value ends where the next column's label begins, not at end of line. On a
            # two-column row ("Last Name: ___ Home Phone: ___") the first field otherwise
            # swallows the whole right-hand column and reports its last token as the value.
            stop = (
                label_run_start(line, colon + 1, colons[k + 1])
                if k + 1 < len(colons)
                else len(texts)
            )
            stop = _value_run_end(line, colon + 1, stop)
            label_words, answer_words = line[start : colon + 1], line[colon + 1 : stop]
            label = " ".join(texts[start : colon + 1]).rstrip(":")
            if not label or norm(label) in HEADINGS:
                start = stop
                continue
            answer = strip_instruction(" ".join(texts[colon + 1 : stop])) or None
            if is_blank_marker(answer) or (answer and _is_printed_prose(answer)):
                answer = None
            mapped, score, reason = match(label)
            typ = mapped.value_type if mapped else FieldType.SHORT_TEXT
            lb = box(label_words, w, h)
            if answer:
                ab = box(answer_words, w, h)
            else:
                # An empty field still needs a region to draw and to refill, bounded by the
                # next label so it does not stretch across the neighbouring column.
                right = (
                    min(z.x0 for z in line[stop:]) / w if stop < len(texts) else 0.92
                )
                left = min(0.98, lb.x + lb.width + 0.01)
                ab = BoundingBox(
                    x=left, y=lb.y, width=max(0.02, right - left), height=lb.height
                )
            field_id = f"field_{i}" if len(colons) == 1 else f"field_{i}_{k}"
            start = stop
            if is_ocr:
                detection = _mean_confidence(label_words)
                confidence = _mean_confidence(answer_words) if answer else 0.6
            else:
                detection, confidence = 0.95, (0.92 if answer else 0.6)
            fields.append(
                (
                    line,
                    DetectedFormField(
                        field_id=field_id,
                        page_index=0,
                        label_text=label,
                        normalized_label=norm(label),
                        field_type=typ,
                        label_bbox=lb,
                        answer_bbox=ab,
                        required_status=RequiredStatus.UNKNOWN,
                        required_confidence=0.3,
                        detection_confidence=detection,
                        source=source,
                        canonical_key=mapped.canonical_key if mapped else None,
                        mapping_confidence=score,
                        mapping_reason=reason,
                        raw_value=answer,
                        normalized_value=normalize(answer, typ) if answer else None,
                        is_blank=answer is None,
                        confidence=confidence,
                        needs_review=not bool(mapped)
                        or answer is None
                        or (is_ocr and confidence < settings.default_review_threshold)
                        # A value that does not look like a credible answer of its type is
                        # the dangerous case: presented as read, and wrong. Flagging costs
                        # a reviewer a glance; not flagging costs a wrong patient record.
                        or plausible(normalize(answer, typ) if answer else None, typ)
                        < SUSPECT,
                    ),
                )
            )
    return fields


# The PDF page is rendered at twice its point size so thin rule lines survive for
# morphological detection.
PDF_RENDER_SCALE = 2

# Below this, a value does not look like a credible answer of its type and is surfaced for
# review rather than presented as read.
SUSPECT = 0.5
# A value never continues across a gap this many word-heights wide. That much blank rule
# is the unfilled remainder of one field, not a space inside somebody's answer.
VALUE_GAP_RATIO = 8.0


def _value_run_end(line: list[Word], first: int, stop: int, em: float | None = None) -> int:
    """Index where the value beginning at ``first`` ends, cut at the first wide gap.

    ``label_run_start`` already stops a value at the next *label*, but only a label
    ending in a colon counts, and a row of tick-box options carries none. OCR groups
    such a row onto the line beside it, so "State: Nc" followed half a page later by
    "Separated Widowed Partner" reported the marital-status options as the state.
    """
    if first >= stop:
        return stop
    if em is None:
        em = sorted(z.y1 - z.y0 for z in line)[len(line) // 2]
    for j in range(first + 1, stop):
        if line[j].x0 - line[j - 1].x1 > em * VALUE_GAP_RATIO:
            return j
        # A second caption can follow the answer with no gap worth the name and no colon
        # to announce it: "EMERGENCY CONTACT Ngozi Okafor RELATIONSHIP" runs one field's
        # answer straight into the next field's name. Where a run from here names a field
        # the vocabulary knows, the answer ended at the word before it.
        #
        # Capitals alone were required here, which is how the case this was written for is
        # printed. Forms that title-case their captions were left running on, and on one
        # the damage was double: "Race Black/African American Preferred Language English"
        # reported the whole tail as the race *and* left preferred language unread, two
        # fields lost to one boundary. A capitalised word is enough to consider; what
        # decides it is ``_known_label_ends``, which demands an exact vocabulary alias at
        # the start of the run, so an ordinary title-case value -- a surname, a street --
        # cuts nothing.
        if (_is_shouted(line[j].text) or line[j].text[:1].isupper()) and _known_label_ends(line[j:]):
            return j
    return stop

MARK_TYPES = (FieldType.CHECKBOX, FieldType.SIGNATURE_PRESENCE)


# Two or more tick boxes sharing a field's answer space make it a choice, not free text.
MIN_CHOICE_OPTIONS = 2
# Characters of text beyond a row's own option captions before it counts as somebody's
# written answer rather than a choice.
MIN_WRITTEN_ANSWER = 3
# How many checkbox-widths apart two boxes can sit and still belong to the same question.
OPTION_GAP_RATIO = 8.0
# How far above its row's typical ink a box must sit to count as ticked.
# How large the step between marked and unmarked has to be, relative to the unmarked
# level, before a row counts as answered at all. A row nobody touched is uniform, so its
# largest internal step is near zero and it correctly reports nothing.
MIN_MARK_STEP = 1.4


def _ticked(boxes: list[Box], gray, marks: tuple[PdfMark, ...]) -> list[bool]:
    """Whether each box in one option row carries a mark.

    A PDF-native mark (see ``word_source.pdf_marks``) is authoritative wherever one
    exists for a box: it comes from the document's own text structure, not a guess about
    contrast. Ink measurement is the fallback for a scanned image, a rasterised PDF whose
    checkboxes are drawn as rectangles rather than symbol-font glyphs, or any box the
    vector reading has nothing to say about.
    """
    from_pdf = [_pdf_tick(b, marks) for b in boxes]
    if all(t is not None for t in from_pdf):
        return [bool(t) for t in from_pdf]
    guessed = _marked([ink_ratio(gray, b) for b in boxes]) if gray is not None else [False] * len(boxes)
    return [t if t is not None else g for t, g in zip(from_pdf, guessed)]


def _pdf_tick(box: Box, marks: tuple[PdfMark, ...]) -> bool | None:
    """The PDF-native reading for a box, or None if no mark overlaps it enough to trust."""
    best: tuple[float, bool] | None = None
    for mark in marks:
        share = _box_overlap(box, Box(mark.x0, mark.y0, mark.x1, mark.y1))
        if share > 0.2 and (best is None or share > best[0]):
            best = (share, mark.ticked)
    return best[1] if best else None


def _box_overlap(a: Box, b: Box) -> float:
    """Share of ``a``'s own area that ``b`` covers."""
    wide = min(a.x1, b.x1) - max(a.x0, b.x0)
    tall = min(a.y1, b.y1) - max(a.y0, b.y0)
    if wide <= 0 or tall <= 0:
        return 0.0
    area = max((a.x1 - a.x0) * (a.y1 - a.y0), 1e-9)
    return (wide * tall) / area


def _marked(inks: list[float]) -> list[bool]:
    """Which boxes in one option row carry a mark.

    Judged against the row itself, never an absolute amount of ink: scan darkness, box
    size and line weight vary between forms, but within a single row they are constant,
    so a mark is what stands out from its siblings. A row nobody touched is uniform and
    correctly reports nothing selected.

    The baseline has to represent the *unmarked* boxes, which means taking the lower
    median. ``sorted(inks)[len(inks) // 2]`` takes the upper one, so on a two-option row
    -- "Legal Sex: [ ] Male [x] Female" -- the baseline was the marked box itself, and no
    two-option question on any form could ever report an answer.
    """
    if not inks:
        return []
    order = sorted(inks)
    baseline = order[(len(order) - 1) // 2]
    # Split the row at the widest step above the unmarked level rather than at a fixed
    # multiple of it. A fixed multiple has to be tuned to how dark a given form draws its
    # marks -- swept across four forms it was safe only between 2.0 and 2.25, and the
    # value inherited from earlier work, 3.0, reported seven of eight ticked rows as
    # empty. What actually distinguishes a mark is that it is *separated* from its
    # siblings, and the size of that separation is measured against the row itself.
    step, cut = 0.0, None
    for lower, upper in itertools.pairwise(order):
        if upper > baseline and upper - lower > step:
            step, cut = upper - lower, (lower + upper) / 2
    if cut is None or step < max(MARKED_INK_RATIO, baseline * MIN_MARK_STEP):
        return [False] * len(inks)
    return [ink > cut for ink in inks]


def _answer_beyond_options(value, names: list[Word]) -> bool:
    """Whether a field's text says anything the option captions beside its boxes do not."""
    if not isinstance(value, str) or not value.strip():
        return False
    options = {_alnum(word.text) for word in names}
    extra = [t for t in (_alnum(part) for part in value.split()) if t and t not in options]
    return len("".join(extra)) >= MIN_WRITTEN_ANSWER


def _alnum(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum()).lower()


def _resolve_choice_fields(
    fields: list[DetectedFormField],
    checkboxes: list[Box],
    words: list[Word],
    gray,
    w: float,
    h: float,
    marks: tuple[PdfMark, ...] = (),
) -> set[Box]:
    """Report which option a tick-box field selects, instead of its row of printed text.

    "Marital Status: Single [ ] Married [ ] Widowed [ ]" is a choice, and OCR reading of
    the row ("Singleo Marriedo Widowedo") is neither the question nor the answer. The boxes
    inside the field's answer space are measured for ink and the selected captions are
    reported, so an unticked group reads empty rather than reading as prose.
    """
    if gray is None:
        return set()
    claimed: set[Box] = set()
    # A tick box belongs to exactly one question. Where answer regions overlap, the
    # tightest one owns the row, so a wide neighbouring field cannot claim it too.
    for field in sorted(fields, key=lambda f: f.answer_bbox.width * f.answer_bbox.height):
        bbox = field.answer_bbox
        region = Box(bbox.x * w, bbox.y * h, (bbox.x + bbox.width) * w, (bbox.y + bbox.height) * h)
        inside = sorted(
            (b for b in checkboxes if b not in claimed and _centre_inside_box(b, region)),
            key=lambda b: b.x0,
        )
        if len(inside) < MIN_CHOICE_OPTIONS:
            continue
        names = captions_for_row(inside, words, checkboxes)
        if names is None:
            continue
        # A field already holding a written answer is not a choice -- but the text sitting
        # in a choice row *is* its option labels, so the test has to be what remains once
        # those are accounted for. Testing the raw text instead fails in both directions:
        # it threw away "1420 Bellefield Avenue Apt 3B" when spurious boxes were detected
        # beside it, and once tick boxes were being found at all it refused every genuine
        # option row, because "Divorced Legally Separated Married" reads as a written
        # answer to any test that only asks whether text is present.
        if _answer_beyond_options(field.normalized_value, names):
            continue
        # A choice field's answer is the row of options itself. Where the region also
        # holds text on a *different* line, that text is a written answer and the boxes
        # belong to a neighbouring question whose region merely overlaps this one.
        top = min(b.y0 for b in inside)
        bottom = max(b.y1 for b in inside) + max(b.height for b in inside) * 2
        if any(
            _centre_inside_box(Box(word.x0, word.y0, word.x1, word.y1), region)
            and not top <= (word.y0 + word.y1) / 2 <= bottom
            and len([c for c in word.text if c.isalnum()]) >= 2
            for word in words
        ):
            continue
        claimed.update(inside)
        captions = [(b, word.text) for b, word in zip(inside, names)]
        # Whether a box is ticked is judged against its own row, not an absolute amount
        # of ink. Scan darkness, box size and line weight vary between forms, but within
        # one option row they are constant, so a mark is what stands out from its
        # siblings. An untouched row is uniform and correctly reports nothing selected.
        # Captions arrive with whatever spacing the form used to separate the option from
        # its box, including the em spaces PDF text layers often carry.
        boxes_only = [b for b, _ in captions]
        chosen = [c.strip() for (b, c), take in zip(captions, _ticked(boxes_only, gray, marks)) if take]
        field.field_type = FieldType.MULTIPLE_CHOICE
        field.raw_value = ", ".join(chosen) or None
        field.normalized_value = field.raw_value
        field.is_blank = not chosen
        field.confidence = 0.9 if chosen else 0.6
        field.needs_review = not chosen
    return claimed


def _cluster_option_rows(
    checkboxes: list[Box], words: Sequence[Word] = ()
) -> list[list[Box]]:
    """Group tick boxes into the option rows they belong to.

    Same test as ``word_source.group_lines_tolerant``, for the same reason: comparing a
    new box only to the *last* one placed lets a row's vertical band drift downward as
    boxes accumulate, so a box several rows away eventually overlaps by a sliver and
    chains on. On a real form that pulled a small, unrelated checkbox 450px to the side
    into a two-box "Legal Sex" row -- the merged span then reached past the row's own
    label, and ``label_for_box`` found nothing there and discarded all three boxes,
    losing a question the vector reading had already answered correctly.

    A vertical band is fixed by the box that opens it and never moves. Within one band,
    a further split on horizontal gap keeps a row to boxes that actually sit near each
    other -- the options of one question, not every checkbox at roughly that height.

    Gap alone is not enough, because two questions printed side by side sit as close as
    one question's own options do:

        Marital Status: [x] Married [ ] Single [ ] Divorced ... Please Address As: [ ] Mr [ ] Mrs

    Here the gap between "Separated" and "Mr" measured 7.8 box-widths against a threshold
    of 8, so nine boxes formed one row, no set of captions named all nine, and the row was
    discarded -- including the tick the patient had actually made. Lowering the threshold
    would be tuning to this one form; the form itself says where the break is. A label ends
    with a colon, and a label lying in the gap between two boxes means the second box
    answers a different question. That reads the form's own punctuation, so it holds for
    any layout that punctuates its labels at all.
    """
    bands: list[list[Box]] = []
    anchors: list[tuple[float, float]] = []
    for box_ in sorted(checkboxes, key=lambda b: (b.y0, b.x0)):
        centre = (box_.y0 + box_.y1) / 2
        for i, (lo, hi) in enumerate(anchors):
            if lo <= centre <= hi:
                bands[i].append(box_)
                break
        else:
            bands.append([box_])
            anchors.append((box_.y0, box_.y1))

    rows: list[list[Box]] = []
    for band in bands:
        band.sort(key=lambda b: b.x0)
        median_width = sorted(b.x1 - b.x0 for b in band)[len(band) // 2]
        # A fresh row per band: reusing ``rows[-1]`` across the loop let a box open a new
        # band's row by continuing the *previous* band's last row whenever its x0 merely
        # happened to sit close enough -- two unrelated questions at different heights on
        # the page, merged because of a horizontal coincidence between them.
        lo = min(b.y0 for b in band)
        hi = max(b.y1 for b in band)
        # The label has to overlap the band vertically, not merely sit near it. A label
        # printed *above* a row belongs to that row as a whole and divides nothing; only
        # one set inline with the boxes marks a break between two questions. Allowing any
        # reach above let "Sex:" from the row above fall between two Marital Status boxes
        # and split that question in half.
        breaks = [
            (word.x0, word.x1)
            for word in words
            if word.text.rstrip().endswith(":") and min(word.y1, hi) > max(word.y0, lo)
        ]
        current: list[Box] = []
        for box_ in band:
            near = current and box_.x0 - current[-1].x1 <= median_width * OPTION_GAP_RATIO
            divided = current and any(
                current[-1].x1 <= (x0 + x1) / 2 <= box_.x0 for x0, x1 in breaks
            )
            if near and not divided:
                current.append(box_)
            else:
                if current:
                    rows.append(current)
                current = [box_]
        if current:
            rows.append(current)
    return rows


def _mark_row_fields(
    checkboxes: list[Box],
    claimed: set[Box],
    words: list[Word],
    candidates: list[list[Word]],
    gray,
    w: float,
    h: float,
    start: int,
    marks: tuple[PdfMark, ...] = (),
) -> list[DetectedFormField]:
    """Make a field out of every row of tick boxes no answer region already owns.

    Until now a choice was only ever found as a by-product of finding a *written-answer*
    region: the boxes had to fall inside a rule or a drawn box before anything looked at
    them. A question like

        Marital Status:  [ ] Divorced  [ ] Legally Separated  [x] Married

    has no rule anywhere on it, so no region existed and the row was invisible. On one
    real registration form that was 41 tick boxes across 12 rows, of which regions covered
    2 -- the form's entire demographic section read as prose.

    A row of tick boxes is a question in its own right, so it makes its own field here.
    """
    if gray is None:
        return []
    loose = [b for b in checkboxes if b not in claimed]
    if not loose:
        return []
    rows = _cluster_option_rows(loose, words)

    fields: list[DetectedFormField] = []
    for row in rows:
        if len(row) < MIN_CHOICE_OPTIONS:
            continue
        names = captions_for_row(row, words, checkboxes)
        if names is None:
            continue
        span = Box(
            min(b.x0 for b in row),
            min(b.y0 for b in row),
            max(b.x1 for b in row),
            max(b.y1 for b in row),
        )
        label_words = label_for_box(span, candidates, left_only=True)
        if not label_words:
            continue
        label = " ".join(z.text for z in label_words).rstrip(":")
        mapped, score, reason = match(label)
        chosen = [n.text.strip() for n, take in zip(names, _ticked(row, gray, marks)) if take]
        answer = ", ".join(chosen) or None
        fields.append(
            DetectedFormField(
                field_id=f"choice_{start + len(fields)}",
                page_index=0,
                label_text=label,
                normalized_label=norm(label),
                field_type=FieldType.MULTIPLE_CHOICE,
                label_bbox=box(label_words, w, h),
                answer_bbox=BoundingBox(
                    x=span.x0 / w,
                    y=span.y0 / h,
                    width=(span.x1 - span.x0) / w,
                    height=(span.y1 - span.y0) / h,
                ),
                required_status=RequiredStatus.UNKNOWN,
                required_confidence=0.3,
                detection_confidence=_mean_confidence(label_words),
                source="ocr_layout",
                canonical_key=mapped.canonical_key if mapped else None,
                mapping_confidence=score,
                mapping_reason=reason,
                raw_value=answer,
                normalized_value=answer,
                is_blank=answer is None,
                confidence=0.9 if chosen else 0.6,
                needs_review=not chosen,
            )
        )
    return fields


def _blank_option_captions(
    fields: list[DetectedFormField], checkboxes: list[Box], words: list[Word], h: float
) -> None:
    """Blank text values that are really the printed names of tick boxes.

    The inline reader runs before anything has looked for tick boxes, so a row of option
    captions is just words on a line to it, and it hands them to whichever label sits to
    their left. On one photographed registration form that produced

        Marital Status -> "Mrs Miss"

    which is wrong twice over: those are the captions of the *neighbouring* question
    ("Please Address As: [ ] Mr [ ] Mrs [ ] Miss [ ] Ms"), and the same form also yielded
    a correct ``Marital Status -> Married`` from the boxes. A reviewer was shown both, and
    a scorer keyed by label saw only the wrong one.

    A word printed beside a tick box is that box's name, never somebody's answer -- the
    answer to a question with tick boxes is which box carries a mark. So a value made
    *entirely* of such words says nothing, and is cleared. Requiring every word to be a
    caption is what keeps a real written answer ("Divorced, remarried 2019") intact: one
    word the boxes cannot account for and the value stands.

    Cleared rather than deleted, because the number of bound fields decides which layout
    reads the page -- see ``_best_layout``.
    """
    captions = [
        word
        for row in _cluster_option_rows(checkboxes, words)
        for word in (captions_for_row(row, words, checkboxes) or ())
    ]
    if not captions:
        return
    for field in fields:
        value = field.normalized_value
        if not isinstance(value, str) or not value.strip():
            continue
        wanted = [part for part in (_alnum(p) for p in value.split()) if part]
        if not wanted:
            continue
        bbox = field.answer_bbox
        top, bottom = bbox.y * h, (bbox.y + bbox.height) * h
        # Only captions on the field's own row can be what it misread; a caption
        # elsewhere on the page matching by wording is a coincidence.
        pool = {
            _alnum(word.text)
            for word in captions
            if min(word.y1, bottom) > max(word.y0, top)
        }
        if all(part in pool for part in wanted):
            field.raw_value = None
            field.normalized_value = None
            field.is_blank = True
            field.needs_review = True


def _centre_inside_box(inner: Box, outer: Box) -> bool:
    cx, cy = (inner.x0 + inner.x1) / 2, (inner.y0 + inner.y1) / 2
    return outer.x0 <= cx <= outer.x1 and outer.y0 <= cy <= outer.y1


# A value that is *exactly* a caption the vocabulary knows is the next field's label read
# as this field's answer -- "City ____" answered "State", "Race /" answered "Ethnicity",
# "Date of Birth ____" answered "Age". Rules are stripped first because the caption usually
# arrives with its own empty rule attached.
#
# Exact, never fuzzy. Matching by token overlap also condemns "Bellefield Pharmacy", a real
# answer that merely contains a field name; measured over 256 real answers the exact test
# costs none of them and removes 21 of 137 false values.
_CAPTION_RULE = re.compile(r"[_.\u2024\u2025\u2026]{2,}")


def _spans_several_blanks(raw) -> bool:
    """Whether a reading runs across more than one writing line.

    "__ Male __ Female" is a tick row drawn as rules, "Home ____ Cell ____" is a row of
    captions with their blanks; in both, words sit *between* rule runs. Nobody's answer
    does that. A rule at the start or the end -- "________ Gustafsson" from a PDF text
    layer -- is one blank with its value and is left alone.
    """
    if not isinstance(raw, str):
        return False
    between = RULE_GLYPHS.split(raw)[1:-1]
    return any(re.search(r"[A-Za-z0-9]", part) for part in between)


def _is_a_caption(value: str) -> bool:
    """Whether a value is itself one of the captions this vocabulary recognises."""
    stripped = _CAPTION_RULE.sub(" ", value).strip(" .,-:/")
    return bool(stripped) and norm(stripped) in known_captions()


def _is_letterhead(label: str) -> bool:
    """A caption of a single letter is the clinic's own contact block, not a field.

    Withdrawn once and restored. The run that first added it showed TEST4 at 52.4% against
    60.3% and TEST5 at 55.2% against 59.7%, which looked damning; re-measured against
    *cached* model readings, so that both arms see byte-identical output, it is exactly
    neutral -- TEST5 61.2% with and without. The apparent drop was the model moving between
    runs, not this rule, which is the confusion `VISION_CACHE` exists to remove.

    Offline it removes 11 of 137 false values and costs none of 49 real captions. The forms
    carrying `P:`/`F:` letterhead are in TEST2, so the benefit shows in precision there
    rather than in TEST5 recall.

    Forms print their practice's phone and fax as "P: 562-340-0607" and "F: 562-340-0606",
    and the reader binds `P` and `F` as captions owning those numbers -- so the clinic's
    switchboard is reported as a patient's answer. Measured over 137 false values this
    removes 11 and costs none of 49 real captions; two letters would also condemn `MI`, a
    real field, so one letter is the boundary.
    """
    return len(re.sub(r"[^A-Za-z]", "", label or "")) <= 1


def _blank_furniture(fields: list[DetectedFormField]) -> None:
    """Clear values that are the form's own printing, once a reading has already won.

    Deliberately after `_best_layout` has chosen, never before. The number of answered
    fields is what scores the two strategies against each other, so suppressing furniture
    beforehand does not just remove bad values -- it changes which reader wins the page.
    Measured: applying these same two tests during the read flipped
    `dallashipknee_FILLED_page1.jpg` from inline to boxed, gaining two values and losing
    three correct ones (TEST2 72.5% -> 68.1%).

    This is the rule `_is_printed_caption` already follows for the boxed reader, and the
    same reason: blank the value, never drop the field.
    """
    for field in fields:
        value = field.normalized_value
        if not isinstance(value, str) or not value.strip():
            continue
        if _is_letterhead(field.label_text) or (
            _is_question_tail(value)
            or _is_option_glyphs(value)
            or _is_a_caption(value)
            or _spans_several_blanks(field.raw_value)
        ):
            field.raw_value = None
            field.normalized_value = None
            field.is_blank = True
            field.needs_review = True


def _best_layout(
    words: list[Word],
    page_image: Path,
    w: float,
    h: float,
    source: str,
    marks: tuple[PdfMark, ...] = (),
) -> tuple[list[DetectedFormField], str, int]:
    """Read a page under both layout strategies and keep whichever reads it better.

    A form is either boxed (label above or left of a drawn region) or inline
    ("label: value" on one row). Running both and merging makes the two compete for the
    same labels, so each is scored whole and the better one wins outright.

    Shared by the PDF and image paths. A ruled PDF form has its values on a different
    baseline from their labels, so the colon pass alone reads nothing from it -- the
    geometry has to come from the rendered page even when the text does not.
    """
    # PDF baselines are exact within one run of text, but a value typed onto a form sits a
    # little off the baseline of the label beside it, and exact grouping never joins the
    # two -- a value line carrying no colon is then discarded. Two of three held-out forms
    # read *nothing* as PDFs while reading 62% and 25% as images. The same tolerance serves
    # both paths; swept separately, the PDF optimum landed on the OCR value anyway.
    grouped = group_lines_tolerant(words)
    inline = [f for _, f in _fields_with_lines(grouped, w, h, source)]
    if not page_image.exists():
        return inline, "inline", 0

    boxes = detect_boxes(page_image)
    rules = detect_write_rules(page_image, boxes)
    rule_indices = set(range(len(boxes), len(boxes) + len(rules)))
    boxes += rules
    contents, free = partition(boxes, words)
    gray = cv2.imread(str(page_image), cv2.IMREAD_GRAYSCALE)
    boxed, _ = _build_box_fields(
        boxes, contents, group_lines_tolerant(free), w, h, gray, rule_indices, words
    )

    def reads(candidate: list[DetectedFormField]) -> tuple[int, int]:
        """How well a reading did, judged without asking the vocabulary's opinion.

        Counting fields that carry a canonical key tied this decision to how large the
        controlled vocabulary happens to be. Adding four ordinary words to it -- sex,
        gender, marital status, SSN -- raised the inline reader's score on a page the
        boxed reader was right about, flipped the winner, and cost nine correct fields,
        though nothing about reading that page had changed. The vocabulary could not grow
        while it also decided this.

        What a good reading produces is *answers*: values that are there, and that look
        like answers rather than like the form's own ruled furniture. ``plausible`` scores
        exactly that, and returns the same neutral score for a field the vocabulary has
        never heard of, so the measure holds still while the vocabulary changes.
        """
        answered = [f for f in candidate if f.normalized_value not in (None, "", False)]
        return sum(1 for f in answered if _looks_answered(f)), len(answered)

    if settings.layout_choice == "assign":
        leader = max((boxed, inline), key=reads)
        fields = _assign_fields(boxed, inline, "boxed" if leader is boxed else "inline")
        layout = "assigned"
    else:
        fields = max((boxed, inline), key=reads)
        layout = "boxed" if fields is boxed else "inline"
    checkboxes = detect_checkboxes(page_image, words)
    claimed = _resolve_choice_fields(fields, checkboxes, words, gray, w, h, marks)
    # Rows of tick boxes no answer region covers are questions of their own.
    choices = _mark_row_fields(
        checkboxes, claimed, words, group_lines_tolerant(free), gray, w, h, len(fields), marks
    )
    _blank_furniture(fields)
    _blank_option_captions(fields, checkboxes, words, h)
    fields = fields + choices
    return fields, layout, len(boxes)

# A printed title is centred inside the band that encloses it; a written answer starts
# hard against the left of its region and leaves the rest of the rule empty. Measured on
# real intake forms, a title's side margins agree within a quarter of the region width.
CENTRED_SIDE_MARGIN = 0.15
CENTRED_SYMMETRY = 0.25
# A section title spans its section. A field region narrower than this is an answer space,
# where a short value ("04 / 17 / 1986") can sit centred by coincidence.
SECTION_BAND_WIDTH = 0.30
# No single answer covers this much of a page. A region that large is a section frame, and
# what it caught is the printed text of the fields inside it. The same fraction guards
# template derivation in ``template_builder``.
MAX_ANSWER_AREA_FRACTION = 0.12


# Both readers proposing the same value for the same caption is independent evidence: they
# find captions by different means (drawn regions against colons and gaps), so agreement is
# not one reader repeated.
AGREEMENT_BONUS = 0.2
# The reader that reads the page better as a whole is the more credible witness for any one
# field on it. A prior, not the verdict: a clearly better edge from the other reader still
# wins, and this only settles the close ones.
WINNER_PRIOR = 0.1
# A value that runs through a printed caption ("ME ZIPCODE 04101") has crossed into the next
# field. Soft, because "Group Health Cooperative" is an insurer.
CAPTION_CROSSING = 0.3
# A person was born before they signed the form. Of the dates a page offers, the earliest is
# the birth date and the latest is today's; a birth caption holding the latest, or a plain
# date caption holding the earliest, has caught the other one's value. `Date` was the most
# wrongly read caption on the benchmark (90 of 2,775 fields) for exactly this reason.
DATE_ORDER = 0.15


def _assign_fields(
    boxed: list[DetectedFormField], inline: list[DetectedFormField], winner: str
) -> list[DetectedFormField]:
    """Choose fields from both readers one caption at a time, never a whole page at once.

    ``_best_layout`` chose by counting answered fields, and a reader that mistakes captions
    for answers answers *more*: on one page the boxed reader "filled" 21 fields to the
    inline reader's 11, and the eleven were right. Volume is what a caption-binding reader
    is best at, so a count rewards exactly the failure it was meant to judge, and the
    choice flipped with the values written on the page.

    Here both readers' proposals are pooled and taken best first under one rule: a run of
    words on the page serves once. A caption place is answered by at most one proposal, a
    value place answers at most one caption, and a run that is one proposal's caption
    cannot be another's value -- the boxed reader on a stacked-cell form otherwise offers
    "Portland" as the caption of the address below it while the inline reader offers it as
    the answer to "City", and a matching that keeps captions and values in separate pools
    accepted both. A reader can then only win a field by proposing a better edge for it,
    never more edges.

    The first attempt was a maximum-weight bipartite matching. It gained recall and lost
    precision (58.1 -> 55.6 on LARGE): a sum objective takes two mediocre edges over one
    good one, and every junk proposal that conflicts with nothing is added to the sum. Two
    things fix that here. Furniture weighs nothing rather than a little, so it is never
    emitted as a value. And the reader that read the page worse as a whole may contribute
    only values, under captions the vocabulary knows: its unfamiliar captions are, on the
    evidence of the page it lost, mostly not captions at all, and its blank proposals add
    no value while their regions can hijack a row of tick boxes from the field that owns
    it (measured: three `Race` rows lost to a blank captioned with an option's own text).

    Weights are hand-set from what the readers are known to get wrong, never fitted: 112
    forms are too few to learn weights without memorising them.
    """
    candidates = list(boxed) + list(inline)
    from_winner = [(i < len(boxed)) == (winner == "boxed") for i in range(len(candidates))]
    quality = [_quality(c) for c in candidates]
    for i in range(len(boxed)):
        for j in range(len(boxed), len(candidates)):
            a, b = candidates[i], candidates[j]
            if (
                quality[i] > 0
                and quality[j] > 0
                and _same_place(a.label_bbox, b.label_bbox)
                and _same_place(a.answer_bbox, b.answer_bbox)
                and comparable(a.normalized_value) == comparable(b.normalized_value)
            ):
                quality[i] += AGREEMENT_BONUS
                quality[j] += AGREEMENT_BONUS
    for i in range(len(candidates)):
        if quality[i] > 0 and from_winner[i]:
            quality[i] += WINNER_PRIOR
    dated = [
        (i, when)
        for i in range(len(candidates))
        if quality[i] > 0
        and _asks_a_date(candidates[i])
        and (when := _as_date(candidates[i].normalized_value)) is not None
    ]
    distinct = {when for _, when in dated}
    if len(distinct) >= 2:
        earliest, latest = min(distinct), max(distinct)
        for i, when in dated:
            birth = _asks_a_birth_date(candidates[i])
            if when == earliest:
                quality[i] += DATE_ORDER if birth else -DATE_ORDER
            elif when == latest:
                quality[i] += -DATE_ORDER if birth else DATE_ORDER

    def admissible(i: int) -> bool:
        return from_winner[i] or bool(candidates[i].canonical_key)

    def free(i: int, as_value: bool) -> bool:
        # Same kind, same place: a caption place answered once, a value place used once.
        # Across kinds the test is stricter -- the *same run of words* -- because a ruled
        # row's answer region spans the page and merely contains the small captions
        # printed beneath the rule for the row below it: on one form the phone row's
        # region held the "CITY  STATE  ZIP CODE" captions, and the either-way test threw
        # all three fields off the page.
        c = candidates[i]
        if any(_same_place(c.label_bbox, L) for L in taken_labels):
            return False
        if any(_same_run(c.label_bbox, V) for V in taken_values):
            return False
        if as_value and any(_same_place(c.answer_bbox, V) for V in taken_values):
            return False
        return not (as_value and any(_same_run(c.answer_bbox, L) for L in taken_labels))

    taken_labels: list[BoundingBox] = []
    taken_values: list[BoundingBox] = []
    chosen: list[DetectedFormField] = []
    order = sorted(
        (i for i in range(len(candidates)) if quality[i] > 0),
        key=lambda i: (quality[i], from_winner[i], i < len(boxed)),
        reverse=True,
    )
    for i in order:
        if admissible(i) and free(i, as_value=True):
            chosen.append(candidates[i])
            taken_labels.append(candidates[i].label_bbox)
            taken_values.append(candidates[i].answer_bbox)
    # A caption that won no value is still a field: the page asks a question there. The
    # reader's own blank proposal where one exists, otherwise the losing proposal with its
    # value cleared -- the value belongs to the caption that won it.
    for i in sorted(range(len(candidates)), key=lambda i: i >= len(boxed)):
        if not from_winner[i] or not free(i, as_value=False):
            continue
        field = candidates[i]
        if _answered(field):
            field = field.model_copy(
                update={"raw_value": None, "normalized_value": None, "is_blank": True,
                        "needs_review": True, "confidence": 0.6}
            )
        chosen.append(field)
        taken_labels.append(field.label_bbox)
    return sorted(chosen, key=lambda f: (f.label_bbox.y, f.label_bbox.x))


def _quality(field: DetectedFormField) -> float:
    """How much a proposed caption-value edge is worth, from the value's own shape.

    Nothing here asks which reader made the proposal. The type test is soft -- a
    plausibility of zero costs a quarter of the weight, not the edge -- because a hard
    per-field validity filter was tried here before and gained nothing: a hard filter has
    to be right every time, a cost can be outvoted by a better alternative. Furniture is
    the exception and weighs nothing: a value that is the form's own printing is not a
    worse answer, it is not an answer.
    """
    if not _answered(field) or _spans_several_blanks(field.raw_value):
        return 0.0
    value = field.normalized_value
    if isinstance(value, str):
        # A PDF text layer hands over the value with its empty rule attached --
        # "__________ Eguchi" -- and the rule is furniture, the name is not. Judged raw,
        # every ruled value on every PDF weighed nothing and the PDF half of the benchmark
        # fell fourteen points. The rule is stripped for the judgement only; the value
        # itself is left as the reader produced it.
        value = _CAPTION_RULE.sub(" ", value).strip(" .,-:/")
        if not value:
            return 0.0
        if (
            match(value)[0] is not None
            or _is_printed_caption(value)
            or _is_question_tail(value)
            or _is_option_glyphs(value)
            or _is_glued_caption(value)
        ):
            return 0.0
    label = field.label_text
    if _is_letterhead(label) or sum(c.isalpha() for c in label) < 2:
        return 0.0
    # A heading, or a caption long enough to be an instruction, is not a question --
    # unless the vocabulary recognises it. "Emergency Contact" is a section title that is
    # also the caption of the contact's name on many forms, and "Please check the box(es)
    # that describe your race" is eight words and a real field; both map, and zeroing
    # them cost real names and real answers. Making the test soft for *every* caption
    # instead re-admitted the unopposed junk it exists for (precision 67.8 -> 61.4).
    if not field.canonical_key and (
        norm(label) in HEADINGS or len(label.split()) > MAX_CAPTION_WORDS
    ):
        return 0.0
    # A stray rule glyph is not the value's shape: "#_GRP24741" is a group number with one
    # underscore that survived the rule stripping, and judged raw it was furniture.
    judged = re.sub(r"[_|\[\]{}]", " ", value).strip() if isinstance(value, str) else value
    credibility = plausible(judged, field.field_type)
    if credibility < SUSPECT:
        return 0.0
    weight = 0.5 + 0.25 * credibility
    if field.canonical_key:
        weight += 0.15
    weight += 0.1 * field.confidence
    if isinstance(value, str) and _crosses_caption(value):
        weight -= CAPTION_CROSSING
    return max(weight, 0.0)


_DATE_TEXT = re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})|(\d{4})-(\d{2})-(\d{2})")


def _as_date(value) -> date | None:
    """The calendar date a value states, if it states one. US order for slashed dates."""
    if not isinstance(value, str):
        return None
    found = _DATE_TEXT.search(_CAPTION_RULE.sub(" ", value))
    if not found:
        return None
    if found.group(4):
        year, month, day = int(found.group(4)), int(found.group(5)), int(found.group(6))
    else:
        month, day, year = int(found.group(1)), int(found.group(2)), int(found.group(3))
        if year < 100:
            year += 2000 if year <= datetime.now(tz=UTC).year % 100 else 1900
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _asks_a_date(field: DetectedFormField) -> bool:
    words = norm(field.label_text).split()
    return "date" in words or "dob" in words or _asks_a_birth_date(field)


def _asks_a_birth_date(field: DetectedFormField) -> bool:
    label = norm(field.label_text)
    return (
        field.canonical_key == "patient.date_of_birth"
        or "birth" in label
        or "dob" in label.split()
    )


def _glued_captions() -> frozenset[str]:
    """Known captions with their spaces removed: OCR reads "STREET ADDRESS" printed in a
    tight cell as one token, and "streetaddress" is still the form talking."""
    return frozenset(c.replace(" ", "") for c in known_captions())


def _is_glued_caption(value: str) -> bool:
    return norm(value).replace(" ", "") in _glued_captions()


def _crosses_caption(value: str) -> bool:
    """Whether a value runs through a multi-word caption and on past it.

    "ME ZIPCODE 04101" is a state, then the next field's caption, then its value: the
    reader's run crossed a field boundary. Single-word captions are not tested -- "Group
    Health Cooperative" and "First Baptist Church" are real answers.
    """
    tokens = norm(value).split()
    caps, glued = known_captions(), _glued_captions()
    for i in range(len(tokens) - 1):
        for j in range(i + 1, min(i + 4, len(tokens))):
            run = tokens[i:j]
            if (len(run) > 1 and " ".join(run) in caps) or (len(run) == 1 and run[0] in glued and run[0] not in caps):
                return True
    return False


def comparable(value) -> str:
    """Collapse the differences that are not disagreements: case, spacing, separators."""
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _is_centred(box: Box, words: list[Word], page_width: float) -> bool:
    """True when text sits centred in its region rather than left-aligned in it.

    This is how a section frame's own title is told apart from a patient's answer without
    knowing what any particular clinic calls its sections. Naming the titles instead
    ("Primary Insurance", "Workers Compensation/Auto") only ever works on forms already
    seen, which is the opposite of what this pipeline needs.
    """
    if not words or box.width <= 0 or box.width < page_width * SECTION_BAND_WIDTH:
        return False
    left = min(z.x0 for z in words) - box.x0
    right = box.x1 - max(z.x1 for z in words)
    return (
        min(left, right) > box.width * CENTRED_SIDE_MARGIN
        and abs(left - right) < box.width * CENTRED_SYMMETRY
    )


# An answer is a noun phrase -- a name, a date, a dosage, an address. The form's own
# writing is sentences: "Please list all medications you are currently taking", "Thank you
# for choosing our clinic". Function words are what separates the two, and the margin is
# not close: across 256 answers written onto TEST2, TEST4, NEW and the dev forms, the
# function-word share of a real answer is 0.00 -- not one contains even a single "the",
# "you" or "have". Three words is the shortest run where a share means anything.
PROSE_MIN_WORDS = 3
PROSE_FUNCTION_SHARE = 0.30


def _is_printed_prose(answer: str) -> bool:
    """Whether a value reads as a sentence the form printed, not an answer to it.

    Single letters are not words and are not counted either way. An initial in a name is
    one character and "a" is a function word, so "K. A. Sparrow" scored 1 of 3 and would
    have been blanked -- and so would "Amara A Okafor". None of the 256 answers this rule
    was checked against happened to carry the initial "A", so the corpus could not show it;
    FUNSD's answers did.
    """
    words = [w for w in re.findall(r"[a-z']+", answer.lower()) if len(w) > 1]
    if len(words) < PROSE_MIN_WORDS:
        return False
    return sum(w in PHRASING for w in words) / len(words) >= PROSE_FUNCTION_SHARE


def _is_printed_caption(answer: str | None) -> bool:
    """A run of text that is the form's own printing rather than an answer."""
    if not answer:
        return False
    # A label carries its colon into the value only when the region caught printed text.
    return (
        ":" in answer
        or norm(answer.split("(")[0]) in HEADINGS
        or _is_printed_prose(answer)
    )


def _captioned_column_fields(
    index: int,
    bx: Box,
    columns: list[tuple[list[Word], float, float]],
    value_words: list[Word],
    w: float,
    h: float,
) -> list[DetectedFormField]:
    """One field per column on a rule whose field names are printed beneath it.

    Values are matched to columns by reading order whenever the counts agree, and only
    otherwise by which column a value starts in. Order is preferred because a value is
    written from the left edge of its cell, which puts it within a few pixels of the wall
    it shares with the cell before; OCR and the printed caption disagree about where that
    wall is by about that much, so containment alone flips assignments. On the St. Clair
    row it put the zip code in the state column, two pixels short.

    Each field keeps its own slice of the rule as its answer box, so regeneration writes
    the city back into the city column rather than across the whole line.
    """
    caption_ids = {id(z) for caption, _, _ in columns for z in caption}
    runs = split_runs([z for z in value_words if id(z) not in caption_ids])
    if len(runs) == len(columns):
        assigned = runs
    else:
        assigned = [[] for _ in columns]
        for run in runs:
            start = run[0].x0
            for j, (_caption, left, right) in enumerate(columns):
                if left <= start < right:
                    assigned[j].extend(run)
                    break

    fields = []
    for j, ((caption, left, right), run) in enumerate(zip(columns, assigned)):
        label = " ".join(z.text for z in caption).rstrip(":")
        mapped, score, reason = match(label)
        typ = mapped.value_type if mapped else FieldType.SHORT_TEXT
        answer = strip_instruction(" ".join(z.text for z in run)) or None
        if is_blank_marker(answer) or _is_printed_caption(answer):
            answer = None
        normalized = normalize(answer, typ) if answer else None
        confidence = _mean_confidence(run) if run else 0.6
        fields.append(
            DetectedFormField(
                field_id=f"box_{index}_{j}",
                page_index=0,
                label_text=label,
                normalized_label=norm(label),
                field_type=typ,
                label_bbox=box(caption, w, h),
                answer_bbox=BoundingBox(
                    x=left / w, y=bx.y0 / h, width=(right - left) / w, height=bx.height / h
                ),
                required_status=RequiredStatus.UNKNOWN,
                required_confidence=0.3,
                detection_confidence=_mean_confidence(caption),
                source="ocr_layout",
                canonical_key=mapped.canonical_key if mapped else None,
                mapping_confidence=score,
                mapping_reason=reason,
                raw_value=answer,
                normalized_value=normalized,
                is_blank=answer is None,
                confidence=confidence,
                needs_review=not bool(mapped)
                or answer is None
                or confidence < settings.default_review_threshold
                or plausible(normalized, typ) < SUSPECT,
            )
        )
    return fields


def _build_box_fields(
    boxes: list[Box],
    contents: list[list[Word]],
    candidates: list[list[Word]],
    w: float,
    h: float,
    gray=None,
    rule_indices: set[int] | None = None,
    words: list[Word] | None = None,
) -> tuple[list[DetectedFormField], set[int]]:
    """Bind each detected input box to its nearest label; the value is what OCR read
    inside the box. Returns the fields plus the ids of lines consumed as labels, so the
    colon pass does not re-emit them. ``candidates`` must be the same list objects the
    caller later iterates, since identity is what marks a line as used."""
    rule_indices = rule_indices or set()
    fields, used = [], set()
    for i, (bx, value_words) in enumerate(zip(boxes, contents)):
        # On a ruled field the caption is always to the left; text above the rule is
        # the previous field's sub-caption ("Last", "STREET") and must not be taken.
        label_words = label_for_box(bx, candidates, left_only=i in rule_indices)
        if not label_words:
            # No label to the left means the captions printed *under* the rule are the
            # field names -- "CITY  STATE  ZIP CODE" beneath one line is three fields.
            # Only here: a rule that has its own label is one field whose captions
            # describe its parts, and splitting it would destroy the value.
            columns = columns_beneath(bx, words or []) if i in rule_indices else []
            if len(columns) < 2:
                continue
            fields.extend(_captioned_column_fields(i, bx, columns, value_words, w, h))
            continue
        used.add(id(label_words))
        label = " ".join(z.text for z in label_words).rstrip(":")
        mapped, score, reason = match(label)
        typ = mapped.value_type if mapped else FieldType.SHORT_TEXT

        # A tick box reads as presence and everything else as text, so both are unions
        # across the branch rather than whatever the first assignment happened to be.
        answer: str | bool | None
        normalized: object
        if typ in MARK_TYPES and gray is not None:
            # Ticks and signatures are ink, not text: read presence from the pixels.
            answer = normalized = ink_ratio(gray, bx) > MARKED_INK_RATIO
            confidence = 0.9
        else:
            # Preserve row structure so a multi-line note keeps its line breaks.
            answer = (
                "\n".join(
                    " ".join(z.text for z in row) for row in group_lines_tolerant(value_words)
                )
                or None
            )
            if is_blank_marker(answer):
                answer = None
            frame = bx.width * bx.height > w * h * MAX_ANSWER_AREA_FRACTION
            if frame or _is_printed_caption(answer) or _is_centred(bx, value_words, w):
                # A section frame ("Primary Insurance") encloses the captions of the
                # fields inside it, so binding it as an answer reports the form's own
                # printed text as a patient value. Blank it rather than drop the field:
                # the count of bound boxes is what scores the boxed strategy against the
                # inline one, and dropping these flipped whole pages to the wrong reader.
                answer = None
            normalized = normalize(answer, typ) if answer else None
            confidence = _mean_confidence(value_words) if answer else 0.6
        fields.append(
            DetectedFormField(
                field_id=f"box_{i}",
                page_index=0,
                label_text=label,
                normalized_label=norm(label),
                field_type=typ,
                label_bbox=box(label_words, w, h),
                answer_bbox=BoundingBox(
                    x=bx.x0 / w, y=bx.y0 / h, width=bx.width / w, height=bx.height / h
                ),
                required_status=RequiredStatus.UNKNOWN,
                required_confidence=0.3,
                detection_confidence=_mean_confidence(label_words),
                source="ocr_layout",
                canonical_key=mapped.canonical_key if mapped else None,
                mapping_confidence=score,
                mapping_reason=reason,
                raw_value=answer,
                normalized_value=normalized,
                is_blank=answer is None,
                confidence=confidence,
                needs_review=not bool(mapped)
                or answer is None
                or confidence < settings.default_review_threshold
                or plausible(normalized, typ) < SUSPECT,
            )
        )
    return fields, used


class SourcePage(NamedTuple):
    """A page reduced to what form understanding actually needs.

    Everything downstream -- line grouping, box and rule detection, label binding, tick
    boxes -- works from words plus a rendered page, and neither cares how they were
    obtained. Keeping the formats apart any further than this duplicates the understanding
    logic once per format, which is how the PDF path silently ended up with no geometric
    detection at all and read nothing from ruled forms.
    """

    words: list[Word]
    image: Path
    width: float
    height: float
    source: str  # "pdf_text" or "ocr": exact text, or text with a confidence
    source_type: str
    fingerprint: str
    stats: dict
    warnings: list[str]
    marks: tuple[PdfMark, ...] = ()


def _read_pdf(folder: Path, pdf: Path, index: int = 0) -> SourcePage:
    """Words from the PDF text layer, with the page rendered so rules can be detected.

    Text coordinates are scaled into the rendered page's pixel space; both are divided by
    the same page size later, so the normalised boxes the API returns are unaffected.
    """
    page = fitz.open(pdf)[index]
    # Page 0 keeps the plain name so everything already pointing at it still works.
    image = folder / ("normalized_color.png" if index == 0 else f"page_{index}.png")
    page.get_pixmap(
        matrix=fitz.Matrix(PDF_RENDER_SCALE, PDF_RENDER_SCALE), alpha=False
    ).save(image)
    words = words_from_pdf(page)
    scaled = [
        word._replace(
            x0=word.x0 * PDF_RENDER_SCALE,
            y0=word.y0 * PDF_RENDER_SCALE,
            x1=word.x1 * PDF_RENDER_SCALE,
            y1=word.y1 * PDF_RENDER_SCALE,
        )
        for word in words
    ]
    marks = tuple(
        mark._replace(
            x0=mark.x0 * PDF_RENDER_SCALE,
            y0=mark.y0 * PDF_RENDER_SCALE,
            x1=mark.x1 * PDF_RENDER_SCALE,
            y1=mark.y1 * PDF_RENDER_SCALE,
        )
        for mark in pdf_marks(page)
    )
    return SourcePage(
        words=scaled,
        image=image,
        width=page.rect.width * PDF_RENDER_SCALE,
        height=page.rect.height * PDF_RENDER_SCALE,
        source="pdf_text",
        source_type="digital_flat_pdf",
        fingerprint=hashlib.sha256(pdf.read_bytes()).hexdigest(),
        stats={"pdf_word_count": len(words)},
        warnings=[] if words else ["This PDF carries no text layer; no fields were read."],
        marks=marks,
    )


def _read_image(folder: Path, index: int = 0) -> SourcePage | None:
    """Words from OCR over the contrast-enhanced page, which shares its dimensions with
    the colour page served to the review UI, so overlay boxes line up.

    ``index`` selects a page of a form photographed one page at a time; page 0 keeps the
    unsuffixed names that every stored run already uses.
    """
    from PIL import Image

    image = folder / ("ocr_gray.png" if index == 0 else f"ocr_gray_{index}.png")
    if not image.exists():
        image = folder / ("normalized_color.png" if index == 0 else f"page_{index}.png")
    if not image.exists():
        return None
    words = words_from_image(image, settings.ocr_language)
    width, height = Image.open(image).size
    return SourcePage(
        words=words,
        image=image,
        width=float(width),
        height=float(height),
        source="ocr",
        source_type="scanned_image",
        # Hashes the normalised page: raw photo bytes vary with capture noise.
        fingerprint=hashlib.sha256(image.read_bytes()).hexdigest(),
        stats={
            "ocr_word_count": len(words),
            "mean_ocr_confidence": round(_mean_confidence(words), 3),
        },
        warnings=[] if words else ["OCR read no text from this page; check image quality."],
    )


class FormAnalyzer:
    def analyze(
        self, analysis_id: str, folder: Path, template_hint: str | None = None
    ) -> FormAnalysis:
        """Understand a form. The format decides only where the words come from."""
        started = time.monotonic()
        pdf = folder / "source.pdf"
        if pdf.exists():
            # Insurance, medications and consent live on later pages of a real intake
            # packet, so reading only the first threw away whole sections of the form.
            count = min(len(fitz.open(pdf)), settings.max_pdf_pages)
            sources = [_read_pdf(folder, pdf, index) for index in range(count)]
        else:
            # A patient photographing a two-page form sends two pictures, so the image
            # path reads every page it was given -- the same way the PDF path already
            # reads every page of a packet -- rather than only the first.
            sources = []
            for index in range(settings.max_pdf_pages):
                page_source = _read_image(folder, index)
                if page_source is None:
                    break
                sources.append(page_source)
        page = sources[0] if sources else None
        if page is None:
            return FormAnalysis(
                analysis_id=analysis_id,
                source_type="scanned_image",
                pages=[{"page_index": 0, "image_url": _image_url(analysis_id)}],
                detected_fields=[],
                form_fingerprint="image",
                warnings=["Page image was not preprocessed; cannot run OCR."],
                processing_time_ms=int((time.monotonic() - started) * 1000),
            )

        matched, matched_page, match_score = _match_template(folder, template_hint)
        fields: list[DetectedFormField] = []
        pages = []
        warnings = list(page.warnings)
        for index, source in enumerate(sources):
            read, layout, boxes_detected = _best_layout(
                source.words,
                source.image,
                source.width,
                source.height,
                source.source,
                source.marks,
            )
            # A layout already understood is read through what was learned about it, not
            # worked out again from scratch. The page's own reading leads and the template
            # adds to it (see ``_merge_template``). Every page: a template carries a page
            # per page of the packet it was learned from, and a filled copy keeps the
            # packet's page order.
            if matched:
                # The upload's first page matched template page ``matched_page``; a
                # photograph of a packet's page 3 is read through page 3's slots.
                guided = _read_with_template(
                    matched,
                    source.words,
                    source.width,
                    source.height,
                    matched_page + index,
                    source.image,
                    source.source,
                )
                if guided:
                    # The same furniture rules the page's reading has already been
                    # through: a slot's box catches the caption printed inside it just
                    # as a detected box does, and "INSURED NAME" is not a phone number.
                    _blank_furniture(guided)
                    read, layout = _merge_template(guided, read), "template"
            # Field ids are only unique within the page that produced them.
            for field in read:
                field.page_index = index
                field.field_id = f"p{index}_{field.field_id}" if index else field.field_id
            # Optional second reader. Off unless a vision provider is configured, so the
            # shipped behaviour is unchanged and turning it off is one environment
            # variable rather than a revert.
            # Captions the page prints that no reader answered are put to a local text
            # model with the page's lines and their positions; only answers it can ground
            # to an exact line survive, and only if they pass the readers' own quality
            # test. Off unless RELINK_PROVIDER is set.
            if relink_available():
                questions = _unanswered_captions(read, source.words)
                if questions:
                    try:
                        found = relink(source.words, source.width, source.height, questions)
                    except Exception as exc:  # noqa: BLE001 - a model must never take the page down
                        warnings.append(f"Re-linker unavailable: {type(exc).__name__}")
                    else:
                        read = _adopt_relinked(read, found, source.width, source.height)
            if vision_available() and _worth_a_model_call(read, source.words):
                try:
                    read = fuse(
                        read,
                        # Geometry having found answers here is independent evidence
                        # that the page is filled in, so an empty reading from the model
                        # is a failure rather than a blank page -- see ``extract``.
                        vision_extract(
                            source.image,
                            expect_answers=any(
                                f.normalized_value not in (None, "", False) for f in read
                            ),
                        ),
                        source.words,
                        source.width,
                        source.height,
                        index,
                        detect_checkboxes(source.image, source.words),
                    )
                except Exception as exc:  # noqa: BLE001 - a second opinion must never
                    # take the page down; the geometric reading stands on its own.
                    warnings.append(f"Vision reading unavailable: {type(exc).__name__}")
            fields += read
            pages.append(
                {
                    "page_index": index,
                    "image_url": _image_url(analysis_id, index),
                    "layout": layout,
                    "boxes_detected": boxes_detected,
                    "template_match_confidence": round(match_score, 3),
                    "template_page": matched_page + index if matched else None,
                    "width": source.width,
                    "height": source.height,
                    **source.stats,
                }
            )
        if page.words and not fields:
            warnings.append("No labelled boxes or 'label: value' lines were found.")
        return FormAnalysis(
            analysis_id=analysis_id,
            source_type=page.source_type,
            pages=pages,
            detected_fields=fields,
            form_fingerprint=page.fingerprint,
            matched_known_template=matched,
            warnings=warnings,
            processing_time_ms=int((time.monotonic() - started) * 1000),
        )


def geometric_fields(
    page_image: Path, widgets: list[Box] | None = None, words: list[Word] | None = None
) -> list[DetectedFormField]:
    """Every region the form provides for an answer, bound to its label.

    A template describes *where a form gives you space to write*, which is the geometric
    view of the page and nothing to do with which strategy happened to read the values
    better. Most real intake forms are ruled rather than boxed, so the inline reader wins
    on them and their fields carry ``source="ocr"`` -- deriving a template from those
    would record where text happened to sit on one filled copy, not where the blank form
    provides space, and regeneration would place values wherever that one patient wrote.

    ``widgets`` are answer spaces the form's designer drew (see ``widget_boxes``), in the
    page's pixels. They are exact where morphology on a render is an inference, so they
    come first, and a detected region that is a widget's own border or the rule beneath
    it is the same space seen twice and is dropped. Morphology still supplies whatever the
    designer did not draw: widgets for the demographics and printed rules for the rest is
    an ordinary form.
    """
    from PIL import Image

    if words is None:
        words = words_from_image(page_image, settings.ocr_language)
    w, h = Image.open(page_image).size
    boxes = detect_boxes(page_image)
    rules = detect_write_rules(page_image, boxes)
    rule_indices = set(range(len(boxes), len(boxes) + len(rules)))
    boxes += rules
    if widgets:
        kept = [
            (i, b)
            for i, b in enumerate(boxes)
            if not any(_centre_inside_box(b, wd) or _centre_inside_box(wd, b) for wd in widgets)
        ]
        rule_indices = {len(widgets) + n for n, (i, _) in enumerate(kept) if i in rule_indices}
        boxes = list(widgets) + [b for _, b in kept]
    contents, free = partition(boxes, words)
    gray = cv2.imread(str(page_image), cv2.IMREAD_GRAYSCALE)
    fields, _ = _build_box_fields(
        boxes,
        contents,
        group_lines_tolerant(free),
        float(w),
        float(h),
        gray,
        rule_indices,
        words,
    )
    return fields


def widget_boxes(pdf: Path, index: int) -> list[Box]:
    """Answer spaces the form's designer drew, in the rendered page's pixels.

    A clinic that publishes a fillable PDF has already said where every value belongs:
    each text widget is exactly the region a template is otherwise left to infer from the
    pixels of a render. Twenty-three of the first 86 benchmark forms are such PDFs. Only
    text widgets are taken; tick boxes are read from ink by the choice stage whichever way
    they were drawn.
    """
    try:
        page = fitz.open(pdf)[index]
    except (RuntimeError, ValueError, IndexError):
        return []
    return [
        Box(
            widget.rect.x0 * PDF_RENDER_SCALE,
            widget.rect.y0 * PDF_RENDER_SCALE,
            widget.rect.x1 * PDF_RENDER_SCALE,
            widget.rect.y1 * PDF_RENDER_SCALE,
        )
        for widget in page.widgets()
        if widget.field_type_string == "Text"
        and widget.rect.width > 0
        and widget.rect.height > 0
    ]


def _image_url(analysis_id: str, index: int = 0) -> str:
    return f"/api/v1/form-analyses/{analysis_id}/pages/{index}/image"


def _merge_template(
    guided: list[DetectedFormField], adhoc: list[DetectedFormField]
) -> list[DetectedFormField]:
    """The page's own reading leads; a learned template may only add to it.

    The rule used to run the other way: the template was authoritative for every slot it
    had answered, and the page's reading contributed only fields outside those slots.
    Measured on all 85 LARGE blanks (2026-09-13) that read *worse* than no template at
    all -- 64.9% -> 61.1% on the 100 artefacts with their own template, 14 -> 1 on one
    PDF. A slot that read *something* is not a slot that read the right thing: a box
    drawn a little off catches the neighbouring caption, and "answered" then silently
    replaced a correct free-form value with a wrong one. Whether a slot answered
    *correctly* cannot be known here, so the only rule that cannot lose is the additive
    one. Everything the page read stands, and a slot is appended only where it brings
    something the page did not: a value where the page read none, or a field where the
    page found none. A reading that only adds cannot score below the reading it adds to,
    which is the floor enrolment had lost.

    "Something the page did not" is judged by *question* as well as by place. Measured
    with the place test alone (2026-09-14): a slot captioned "Date of Birth" whose box
    caught "STREET," was appended beside the page's correct birth date read under "DOB",
    and "Zip Code" holding "State ______ LA Zip ____" beside the page's correct zip under
    "Zip" -- four artefacts lost fields to a rule that was meant to be unable to lose.
    A slot answering a question the page already answered, by canonical key or by the
    caption's wording, adds nothing but a second, worse answer.

    The page's fields come first in the result on purpose: downstream consumers that keep
    the first value per label (the scorer, and a reviewer reading top-down) then see the
    page's reading before the template's.
    """
    # A slot a reviewer placed is the exception to "the page leads". Measured with every
    # slot additive (2026-09-14, second fill set): 350 of 363 wrong values sat on captions
    # the template had been taught, the page's own misreading standing in front of the
    # reviewer's slot. A taught slot that read something displaces the page's fields at
    # its place and on its question, and comes first so a same-key field elsewhere on the
    # page cannot shadow it. Slots inferred from the blank still only add.
    # When a taught slot and the page disagree, containment decides. A page reading that
    # is a strict part of the slot's ("TN" against "TN 37421", "2986 Lantern Hill Road"
    # against "... Road Apt/Trailer") means the slot's region reached into the next
    # field, and the tighter reading wins; measured unconditionally, the override broke
    # 224 fields, 159 of them exactly this. Otherwise the reviewer's slot wins, and every
    # page field at its place or on its question goes with it.
    taught = [s for s in guided if s.source == "reviewer" and _answered(s)]
    leading: list[DetectedFormField] = []
    for slot in taught:
        rivals = [
            f
            for f in adhoc
            if _same_place(slot.answer_bbox, f.answer_bbox)
            or (f.canonical_key is not None and f.canonical_key == slot.canonical_key)
            or norm(f.label_text) == norm(slot.label_text)
        ]
        if any(_answered(f) and _strictly_within(f, slot) for f in rivals):
            continue
        leading.append(slot)
        adhoc = [f for f in adhoc if f not in rivals]
    guided = [s for s in guided if s not in taught]
    kept = list(leading) + list(adhoc)
    asked = {f.canonical_key for f in kept if _answered(f) and f.canonical_key}
    asked |= {norm(f.label_text) for f in kept if _answered(f)}
    for slot in guided:
        overlapping = [f for f in kept if _same_place(slot.answer_bbox, f.answer_bbox)]
        if any(_answered(f) for f in overlapping):
            continue
        if overlapping and not _answered(slot):
            continue
        if _answered(slot) and (slot.canonical_key in asked or norm(slot.label_text) in asked):
            continue
        kept.append(slot)
    return kept


def _answered(field: DetectedFormField) -> bool:
    return field.normalized_value not in (None, "", False)


def _strictly_within(inner: DetectedFormField, outer: DetectedFormField) -> bool:
    """Whether one reading is a proper part of the other, ignoring case and separators."""
    a, b = comparable(inner.normalized_value), comparable(outer.normalized_value)
    return bool(a) and a != b and a in b


def _same_run(a: BoundingBox, b: BoundingBox) -> bool:
    """Whether two regions are the same run of words: each holds the other's centre."""
    return _inside(a, b) and _inside(b, a)


def _inside(inner: BoundingBox, outer: BoundingBox) -> bool:
    cx, cy = inner.x + inner.width / 2, inner.y + inner.height / 2
    return outer.x <= cx <= outer.x + outer.width and outer.y <= cy <= outer.y + outer.height


def _same_place(a: BoundingBox, b: BoundingBox) -> bool:
    """Whether two answer regions are the same place on the page: either one's centre
    lies inside the other. A template slot is tight; an inline reading's region can span
    the whole row, so containment has to be tested both ways."""
    return _inside(a, b) or _inside(b, a)


def _read_with_template(
    template_id: str,
    words: list[Word],
    w: float,
    h: float,
    page_index: int,
    image: Path | None = None,
    source: str = "",
) -> list[DetectedFormField]:
    """Read a page through a layout that has already been understood once.

    This is the point of learning a template at all. The first copy of a form has to be
    worked out -- by geometry, or by a model, at whatever cost that takes. Every copy
    after it is read by looking where the template says the answers live, which needs no
    model call, costs nothing, and gives the same answer every time. A hosted model asked
    the same page twice does not.

    Coordinates are page fractions on both sides, so a template learned from one render
    reads a differently-sized copy of the same form. A page that is skewed or shifted
    relative to the master is not handled here -- ``templates.align_to_template`` computes
    the homography that would be needed, and this deliberately does not reach for it until
    there is a scanned form to test it against.

    The slots come from the template page with this page's index. Packets keep their page
    order between the blank and a filled copy, so the registration page of a packet that
    opens with a letter is read through the letter's template page, not the letter's.
    """
    try:
        definition = registry.get(template_id)
    except KeyError:
        return []
    slots = next((p.fields for p in definition.pages if p.page_index == page_index), [])
    fields = []
    for index, slot in enumerate(slots):
        box = slot.bbox
        inside = [
            word
            for word in words
            if box.x <= ((word.x0 + word.x1) / 2) / w <= box.x + box.width
            and box.y <= ((word.y0 + word.y1) / 2) / h <= box.y + box.height
            and word.text.strip()
        ]
        # The blank said what this region prints; what the filled copy adds is the answer.
        inside = _without_printed(inside, slot.printed_text)
        if source == "ocr" and image is not None:
            # A photograph's slot is read again on its own, enlarged: the page-level OCR
            # of a 150dpi render turned "12/26/2006" into "42/26/2006", and the enlarged
            # crop does not. The reading that Tesseract is surer of wins; an empty page
            # reading yields to the crop only when the crop is legible.
            region = words_in_region(
                image,
                (box.x * w, box.y * h, (box.x + box.width) * w, (box.y + box.height) * h),
                settings.ocr_language,
            )
            region = _without_printed(region, slot.printed_text)
            # The crop replaces the page's reading only when it is clearly surer: on a tie
            # the two readings are as good as each other and the page's is the one every
            # other rule was measured against.
            if region and _mean_confidence(region) >= (
                _mean_confidence(inside) + CROP_MARGIN if inside else MIN_LEGIBLE
            ):
                inside = region
        answer = strip_instruction(
            " ".join(
                " ".join(z.text for z in row) for row in group_lines_tolerant(inside)
            ).strip()
        ) or None
        if is_blank_marker(answer) or _is_printed_caption(answer):
            answer = None
        mapped, score, reason = match(slot.label)
        typ = slot.field_type or FieldType.SHORT_TEXT
        normalized = normalize(answer, typ) if answer else None
        fields.append(
            DetectedFormField(
                field_id=f"tpl_{index}",
                page_index=page_index,
                label_text=slot.label,
                normalized_label=norm(slot.label),
                field_type=typ,
                label_bbox=box,
                answer_bbox=box,
                required_status=RequiredStatus.UNKNOWN,
                required_confidence=0.3,
                detection_confidence=0.9,
                source="reviewer" if slot.taught else "template",
                canonical_key=mapped.canonical_key if mapped else slot.field_id,
                mapping_confidence=score,
                mapping_reason=reason,
                raw_value=answer,
                normalized_value=normalized,
                is_blank=answer is None,
                confidence=_mean_confidence(inside) if inside else 0.6,
                needs_review=answer is None or plausible(normalized, typ) < SUSPECT,
            )
        )
    return fields


def _without_printed(words: list[Word], printed: str | None) -> list[Word]:
    """Drop the words a slot's blank form printed inside it, token for token.

    Matched on normalised tokens with multiplicity, so a caption's word is removed once
    per printing and a value that merely repeats one of its words keeps the extra. Tokens
    that normalise to nothing (a rule, a stray bracket) are dropped too: they were never
    an answer, whichever page they came from.
    """
    remaining: Counter[str] = Counter(norm(printed).split()) if printed else Counter()
    kept = []
    for word in words:
        # OCR glues a caption, its rule and the value into one token ("Name_Analise");
        # the underscore is the seam. Each piece is judged on its own.
        pieces = [piece for piece in word.text.split("_") if piece] if "_" in word.text else [word.text]
        surviving = []
        for piece in pieces:
            token = norm(piece)
            if not token:
                continue
            if remaining[token] > 0:
                remaining[token] -= 1
                continue
            surviving.append(piece)
        if surviving:
            kept.append(word if surviving == [word.text] else word._replace(text=" ".join(surviving)))
    return kept


def _worth_a_model_call(fields: list[DetectedFormField], words: list[Word]) -> bool:
    """Whether a page has anything a model could read a value from.

    Packets open with a welcome letter, a HIPAA notice and a financial policy, and the
    vision model reads each at the same cost as the registration page -- minutes apiece
    on this machine. A page on which geometry found no field and that prints no caption
    the vocabulary knows is not asking anything; a page that does either gets the model.
    """
    if fields:
        return True
    lines = [" " + norm(" ".join(z.text for z in row)) + " " for row in group_lines_tolerant(words)]
    return any(
        len(caption) >= 3 and any(f" {caption} " in line for line in lines)
        for caption in known_captions()
    )


def _unanswered_captions(fields: list[DetectedFormField], words: list[Word]) -> list[str]:
    """Captions the vocabulary knows, printed on this page, that no field answered.

    Longest first: "date of birth" before "date", so a page's specific questions are the
    ones asked when the list is capped."""
    answered = {f.canonical_key for f in fields if _answered(f) and f.canonical_key}
    lines = [" " + norm(" ".join(z.text for z in row)) + " " for row in group_lines_tolerant(words)]
    printed: set[str] = set()
    for caption in known_captions():
        if len(caption) < 3 or not any(f" {caption} " in line for line in lines):
            continue
        mapped, _, _ = match(caption)
        if mapped and mapped.canonical_key not in answered:
            printed.add(caption)
    return sorted(printed, key=lambda c: (-len(c), c))


def _adopt_relinked(
    fields: list[DetectedFormField],
    found: dict[str, tuple[str, list[Word]]],
    w: float,
    h: float,
) -> list[DetectedFormField]:
    """Add the model's grounded answers as fields, under the readers' own rules.

    A span some field already answers is not taken twice; a value that would weigh nothing
    as a reader's proposal (furniture, a caption, an implausible shape) is not added; a
    blank field for the same question gives way. Every adopted value is flagged for review:
    a model linked it, a person should glance at it."""
    kept = list(fields)
    for index, (caption, (value, carrying)) in enumerate(found.items()):
        region = box(carrying, w, h)
        if any(_answered(f) and _same_place(f.answer_bbox, region) for f in kept):
            continue
        mapped, score, reason = match(caption)
        typ = mapped.value_type if mapped else FieldType.SHORT_TEXT
        candidate = DetectedFormField(
            field_id=f"relink_{index}",
            page_index=0,
            label_text=caption,
            normalized_label=norm(caption),
            field_type=typ,
            label_bbox=region,
            answer_bbox=region,
            required_status=RequiredStatus.UNKNOWN,
            required_confidence=0.3,
            detection_confidence=0.7,
            source="relink",
            canonical_key=mapped.canonical_key if mapped else None,
            mapping_confidence=score,
            mapping_reason=reason,
            raw_value=value,
            normalized_value=normalize(value, typ),
            is_blank=False,
            confidence=_mean_confidence(carrying),
            needs_review=True,
        )
        if _quality(candidate) <= 0:
            continue
        kept = [
            f for f in kept
            if _answered(f) or f.canonical_key is None or f.canonical_key != candidate.canonical_key
        ]
        kept.append(candidate)
    return kept


def _match_template(folder: Path, hint: str | None) -> tuple[str | None, int, float]:
    """Identify the blank master this page came from, and which of its pages, so its
    values can later be regenerated onto the template's own exact field coordinates."""
    colour = cv2.imread(str(folder / "normalized_color.png"))
    if colour is None:
        return None, 0, 0.0
    try:
        definition, page, score = registry.match_page(colour, hint)
    except RuntimeError:
        return None, 0, 0.0
    matched = definition.template_id if definition and score >= MATCH_THRESHOLD else None
    return matched, page, score
