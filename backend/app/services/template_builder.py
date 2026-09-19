"""Derive a reusable blank template from a single form, blank or filled.

Regeneration needs a blank master plus field coordinates. The coordinates come from the
page's printed geometry. The master is the page itself: kept exactly as uploaded when it
is a blank form, and with the answers the reader found lifted off when it is a filled one
(``master_from_page``), so the printing, rules, tints and logos around them survive.

Limits worth knowing. A single filled copy cannot prove a mark is an answer rather than
pre-printed text: only what the reader took as an answer is lifted, so an answer it missed
stays on the master in the patient's own ink -- which is also how a regenerated copy shows
what was not extracted. A blank form makes the question disappear, which is why enrolling
one is the better route.
"""

import logging
import re
from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np

from app.config import settings
from app.schemas.form_understanding import DetectedFormField, FormAnalysis, RequiredStatus
from app.schemas.models import (
    BoundingBox,
    FieldType,
    SlotCorrection,
    TemplateDefinition,
    TemplateField,
    TemplatePage,
)
from app.services import pdf_backend
from app.services.canonical_registry import match, norm
from app.services.field_layout import (
    Box,
    detect_checkboxes,
    level_sibling,
    option_phrase,
    split_runs,
)
from app.services.form_analyzer import PRINTED_MARGIN, geometric_fields, widget_boxes
from app.services.regeneration import label_key
from app.services.templates import DERIVED_ROOT, preview_name, registry
from app.services.vision_extractor import available as vision_available
from app.services.vision_extractor import extract as vision_extract
from app.services.vision_extractor import locate_values
from app.services.word_source import group_lines_tolerant, words_from_image, words_from_pdf

logger = logging.getLogger(__name__)

# Corpus masters own T01-T49; derived templates are allocated above that so the two never
# collide. TemplateDefinition constrains ids to T followed by two digits.
DERIVED_ID_START = 50
DERIVED_ID_END = 9999


def next_template_id() -> str:
    taken = {t.template_id for t in registry.all()}
    for number in range(DERIVED_ID_START, DERIVED_ID_END + 1):
        candidate = f"T{number:02d}"
        if candidate not in taken:
            return candidate
    raise RuntimeError("No derived template ids remain")


def _slug(label: str, used: set[str]) -> str:
    """A unique field id from a label, matching TemplateField's required pattern.

    The un-suffixed form must stay identical to ``regeneration.label_key`` or values will
    not resolve back onto the template. A disambiguating suffix therefore only appears
    when one form repeats a label, and that repeat will not refill.
    """
    base = label_key(label)
    candidate, n = base, 2
    while candidate in used:
        candidate, n = f"{base}_{n}", n + 1
    return candidate


# How much darker than the paper around it a pixel has to be to count as ink. Low enough
# to take a glyph's anti-aliased edge too: at 40 a lifted answer left its outline behind.
INK_CONTRAST = 24
# How far around a field's answer box, in word heights, its answer's words may lie.
ANSWER_REACH = 0.6
# How far inside a ticked box its mark is lifted, as a share of the box's side: the box's
# own outline stays.
TICK_BORDER = 0.18


def master_from_page(
    image: np.ndarray,
    answers: Sequence[tuple[float, float, float, float]],
    ticks: Sequence[tuple[float, float, float, float]] = (),
) -> np.ndarray:
    """The page with the patient's answers lifted off and everything the form prints kept.

    A master used to be the page with every answer *region* painted white, and a user
    comparing a regenerated form with the upload saw what that does: white bands across a
    grey panel, an orange banner and a watermark; a rule or a logo cut where a region
    crossed it; the lower half of each answer left under the regenerated one, because a
    rule's region stops at the rule; and answers outside any region -- the patient's name,
    their age -- never erased at all, so they looked regenerated when they were carried over.

    Only ink is lifted now: pixels of the words the reader took as answers (``answers``,
    word boxes in page pixels) and of the marks inside ticked boxes (``ticks``), darker than
    the paper around them, and never part of a rule line. What they covered is filled in
    from the page around it, so a tinted panel stays tinted. With nothing to lift -- a blank
    form -- the page comes back exactly as it was.
    """
    if not answers and not ticks:
        return image.copy()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    mask = np.zeros((height, width), np.uint8)
    near = np.zeros((height, width), np.uint8)
    for x0, y0, x1, y1 in answers:
        pad = max(2, round((y1 - y0) * 0.2))
        tall = max(3, round(y1 - y0))
        # The paper is looked for a whole word's height away: dense or bold writing hides it
        # from a nearer look, and the middle of such a word was left behind.
        _mark_ink(gray, mask, x0 - pad, y0 - pad, x1 + pad, y1 + pad, tall)
        _mark_ink(gray, near, x0 - tall, y0 - pad, x1 + tall, y1 + pad, tall)
    for x0, y0, x1, y1 in ticks:
        inset = max(2, round(min(x1 - x0, y1 - y0) * TICK_BORDER))
        _mark_ink(gray, mask, x0 + inset, y0 + inset, x1 - inset, y1 - inset, max(3, round(min(x1 - x0, y1 - y0))))
    if not mask.any():
        return image.copy()
    rules = _rule_lines(gray)
    # A glyph's stroke often reaches past the box OCR drew round its word -- the first "a"
    # of an email, the foot of a slash -- and stayed on the master. Ink touching what is
    # lifted goes with it, once the rules are cut out: a rule touches every answer written
    # on it, and would join them all to each other and to the printing around them.
    # The rows either side of a rule are its anti-aliasing, and would join the answer's
    # glyphs along it into one blob as long as the rule.
    near[cv2.dilate(rules, np.ones((5, 1), np.uint8)) > 0] = 0
    _, blobs = cv2.connectedComponents(near)
    touching = np.unique(blobs[(near > 0) & (mask > 0)])
    mask[np.isin(blobs, touching[touching > 0])] = 255
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
    # A rule an answer sits on is lifted with it and then put back pixel for pixel: kept out
    # of the fill instead, its dark pixels bled into what was filled beside it, and the foot
    # of every answer stayed on its line as a smudge. A line is a rule only if it runs on
    # past what was lifted: a long stroke of the writing itself lies wholly inside it and
    # goes with the answer.
    count, pieces, stats, _ = cv2.connectedComponentsWithStats(rules)
    running_on = np.unique(pieces[(rules > 0) & (mask == 0)])
    heights = sorted(y1 - y0 for _, y0, _, y1 in answers) or [20.0]
    longest_stroke = max(60.0, 3 * heights[len(heights) // 2])
    long_lines = [i for i in range(1, count) if max(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]) >= longest_stroke]
    keep = (rules > 0) & np.isin(pieces, np.union1d(running_on[running_on > 0], long_lines))
    master = cv2.inpaint(image, mask, 4, cv2.INPAINT_TELEA)
    master[keep] = image[keep]
    return master


def _mark_ink(gray: np.ndarray, mask: np.ndarray, x0: float, y0: float, x1: float, y1: float, reach: int) -> None:
    """Mark the pixels of a region that are darker than the paper within ``reach`` of them."""
    height, width = gray.shape
    left, top = max(0, int(x0)), max(0, int(y0))
    right, bottom = min(width, round(x1) + 1), min(height, round(y1) + 1)
    if right - left < 2 or bottom - top < 2:
        return
    # The paper's own shade from a margin around the region, so a stroke filling the
    # region is still measured against the paper rather than against itself.
    m = reach
    around = gray[max(0, top - m): min(height, bottom + m), max(0, left - m): min(width, right + m)]
    paper = cv2.dilate(around, np.ones((2 * reach + 1, 2 * reach + 1), np.uint8))
    oy, ox = top - max(0, top - m), left - max(0, left - m)
    local = paper[oy: oy + bottom - top, ox: ox + right - left]
    ink = local.astype(np.int16) - gray[top:bottom, left:right].astype(np.int16) > INK_CONTRAST
    mask[top:bottom, left:right][ink] = 255


def _rule_lines(gray: np.ndarray) -> np.ndarray:
    """The page's ruled lines: strokes far longer than any letter, across or down."""
    height, width = gray.shape
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, 10)
    across = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, width // 40), 1)))
    down = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(40, height // 30))))
    return cv2.bitwise_or(across, down)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text).lower())


def answer_ink(
    fields: list[DetectedFormField],
    words: list,
    width: float,
    height: float,
    checkboxes: Sequence[Box] = (),
) -> tuple[list[tuple[float, float, float, float]], list[tuple[float, float, float, float]]]:
    """Where a page's answers are inked: word boxes and ticked boxes, in page pixels.

    A word belongs to an answer when it lies around the field's answer box and reads as one
    of the answer's words, allowing OCR a character; the rest of that word's run on the line
    goes with it, so "(559)" is lifted with "555-0191" though the reader took it for 659.
    A choice's words are its printed options, so only its ticks are lifted, and a word on a
    tick box's line within reach of the box is an option's caption whatever a reader made
    of it: the options of a "Transgender" row, read as a value, were once lifted with their
    boxes. A signature is not lifted.
    """
    tall = sorted(w.y1 - w.y0 for w in words)
    unit = tall[len(tall) // 2] if tall else 20.0
    boxes: list[tuple[float, float, float, float]] = []
    ticks: list[tuple[float, float, float, float]] = []
    taken: set[int] = set()
    for field in fields:
        for b in field.ticked_boxes:
            ticks.append((b.x * width, b.y * height, (b.x + b.width) * width, (b.y + b.height) * height))
        if field.field_type in (FieldType.MULTIPLE_CHOICE, FieldType.CHECKBOX, FieldType.SIGNATURE_PRESENCE):
            continue
        # The normalised value is the answer; a raw reading can carry printed text with it
        # (a phone number read as "CITY STATE ZIP CODE 412-555-0184" took those captions).
        value = field.normalized_value if isinstance(field.normalized_value, str) else field.raw_value
        wanted = set(_tokens(value)) if isinstance(value, str) else set()
        if not wanted:
            continue
        a = field.answer_bbox
        pad = unit * ANSWER_REACH
        x0, y0 = a.x * width - pad, a.y * height - pad
        x1, y1 = (a.x + a.width) * width + pad, (a.y + a.height) * height + pad
        # An answer region holding a row of tick boxes holds their printed options, and a
        # text value read there is those options misread ("Mo F o" for "M [ ] F [ ]"): a
        # choice is answered by its ticks, so nothing of it is lifted as writing.
        if sum(1 for b in checkboxes if x0 <= (b.x0 + b.x1) / 2 <= x1 and y0 <= (b.y0 + b.y1) / 2 <= y1) >= 2:
            continue
        around = [
            w for w in words
            if x0 <= (w.x0 + w.x1) / 2 <= x1 and y0 <= (w.y0 + w.y1) / 2 <= y1
            and not w.text.rstrip().endswith(":")
            and _tokens(w.text)
            and not _beside_a_box(w, checkboxes)
        ]
        matched = {id(w) for w in around if any(_one_of(t, wanted) for t in _tokens(w.text))}
        if not matched:
            continue
        for line in group_lines_tolerant(around):
            for run in split_runs(line):
                if any(id(w) in matched for w in run):
                    for w in run:
                        if id(w) not in taken:
                            taken.add(id(w))
                            boxes.append((w.x0, w.y0, w.x1, w.y1))
    return boxes, ticks


def _beside_a_box(word, checkboxes: Sequence[Box]) -> bool:
    """Whether a word touches a tick box or sits on its line within a box's reach of it."""
    for box in checkboxes:
        side = box.x1 - box.x0
        on_line = min(word.y1, box.y1) - max(word.y0, box.y0) > 0.3 * (word.y1 - word.y0)
        if on_line and word.x0 - side * 1.5 <= box.x1 and word.x1 + side * 1.5 >= box.x0:
            return True
    return False


def _one_of(token: str, wanted: set[str]) -> bool:
    """Whether a word read on the page is one of an answer's words, misread by a character."""
    if token in wanted:
        return True
    if len(token) < 3:
        return False
    from difflib import SequenceMatcher

    return any(
        len(w) >= 3
        and (
            SequenceMatcher(None, token, w).ratio() >= 0.7
            or (len(w) == len(token) and sum(a != b for a, b in zip(w, token)) <= 1)
        )
        for w in wanted
    )


def _named_by_model(fields, page_image: Path):
    """Let the vision model say what each region on this form is for, once.

    Geometry is good at finding *where* a form gives you space to write and bad at
    working out *which question* that space answers -- the second is what a model that
    sees the page does well, and what a rule keeps needing another rule for.

    Learning a template is the one moment that trade is worth making. It happens once per
    layout, not once per upload, so the model's cost and its run-to-run variation are paid
    a single time and then frozen into something deterministic. Every later copy of the
    form is read from the stored template with no model call at all.

    The model names regions; it never supplies them. Coordinates stay geometric, because
    regeneration draws from them and a model asked for pixel positions gets them wrong.
    A region the model says nothing about keeps the label geometry gave it.
    """
    if not vision_available():
        return fields
    try:
        seen = vision_extract(page_image)
    except Exception as exc:  # noqa: BLE001 - a name is not worth failing the learn over
        # Say so. Learning is the one moment the model's understanding gets captured, and
        # a template quietly learned without it is worse than a failed learn: it looks
        # finished, gets reused for every later copy, and nothing ever says why it is
        # thin. A rate-limited call is exactly how that happens.
        logger.warning("Template learned WITHOUT the model (%s): %s", type(exc).__name__, exc)
        return fields

    from PIL import Image

    width, height = Image.open(page_image).size
    words = words_from_image(page_image, settings.ocr_language)
    located = locate_values(seen, words, float(width), float(height))
    for reading in seen:
        box = located.get(reading.label)
        if box is None or not reading.label.strip():
            continue
        centre = (box[0] + box[2] / 2, box[1] + box[3] / 2)
        # The region this value was written in is the region that question is asked in.
        for field in fields:
            a = field.answer_bbox
            if a.x <= centre[0] <= a.x + a.width and a.y <= centre[1] <= a.y + a.height:
                field.label_text = reading.label.rstrip(":").strip()
                mapped, score, reason = match(field.label_text)
                if mapped:
                    field.canonical_key = mapped.canonical_key
                    field.mapping_confidence = score
                    field.mapping_reason = reason
                break
        else:
            # The model found an answer in a place geometry never called a field at all.
            # That is the whole reason for asking it: on one form the city was written
            # beside its caption rather than on a rule, so no region existed there and no
            # amount of renaming could have captured it. Learning has to be able to add a
            # field, not only rename one, or the model's best finds are exactly the ones
            # that never reach the template.
            fields.append(_slot_from_reading(reading, box))
    return fields


def _slot_from_reading(reading, box) -> DetectedFormField:
    """A template slot for a field only the model saw, placed where its value was found."""
    label = reading.label.rstrip(":").strip()
    mapped, score, reason = match(label)
    bbox = BoundingBox(x=box[0], y=box[1], width=box[2], height=box[3])
    return DetectedFormField(
        field_id=f"model_{norm(label) or 'field'}",
        page_index=0,
        label_text=label,
        normalized_label=norm(label),
        field_type=mapped.value_type if mapped else FieldType.SHORT_TEXT,
        label_bbox=bbox,
        answer_bbox=bbox,
        required_status=RequiredStatus.UNKNOWN,
        required_confidence=0.3,
        detection_confidence=0.8,
        source="ocr_vision_fused",
        canonical_key=mapped.canonical_key if mapped else None,
        mapping_confidence=score,
        mapping_reason=reason,
        raw_value=reading.value,
        normalized_value=reading.value,
        is_blank=False,
        confidence=0.8,
        needs_review=True,
    )


# How far beyond a slot's edge the blank's printing is still recorded, as a fraction of
# the page. OCR of a filled copy draws the neighbouring caption's box a little wider than
# the blank's, so a caption sitting just past the edge crept into the read ("Chattanooga
# State") and was not known to be printing. Recording it costs nothing: no answer equals
# the caption beside it.


def _printed_inside(bbox, words, page_w: float, page_h: float) -> str | None:
    """The text the page prints inside a region (and a hair beyond it), in reading order.

    Learned from the blank, this is the region's furniture; a filled copy read through
    the same region can subtract it. Only what a template is learned from is trusted here:
    a template derived from a *filled* form would record that patient's answers as
    printing and erase the next patient's, which is why enrolment starts from the blank.
    """
    m = PRINTED_MARGIN
    inside = [
        z
        for z in words
        if bbox.x - m <= ((z.x0 + z.x1) / 2) / page_w <= bbox.x + bbox.width + m
        and bbox.y - m / 2 <= ((z.y0 + z.y1) / 2) / page_h <= bbox.y + bbox.height + m / 2
        and z.text.strip()
    ]
    text = " ".join(z.text for z in sorted(inside, key=lambda z: (round(z.y0), z.x0)))
    return text or None


def _text_indent(bbox, words, page_w: float, page_h: float) -> float | None:
    """How far in from a region's left edge its text began, as a fraction of page width."""
    inside = [
        w
        for w in words
        if bbox.x <= ((w.x0 + w.x1) / 2) / page_w <= bbox.x + bbox.width
        and bbox.y <= ((w.y0 + w.y1) / 2) / page_h <= bbox.y + bbox.height
        and w.text.strip()
    ]
    if not inside:
        return None
    indent = min(w.x0 for w in inside) / page_w - bbox.x
    return indent if 0 <= indent <= 0.1 else None


# The band a form is filled at, as a fraction of page height: 5pt to 24pt on a letter
# page. Outside it a measured size is a misreading of a rule, a barcode or a heading.
MIN_FILL_SIZE = 0.006
MAX_FILL_SIZE = 0.03
MIN_FILL_MEASUREMENTS = 3


def _font_size_of(bbox, words, page_w: float, page_h: float) -> float | None:
    """The point size the text in a region was set at, or None if it held nothing.

    Recovered from *width*, which is the only thing here that is a clean function of the
    size: rendered width scales linearly with point size, so the size that produced an
    observed run is its width divided by the width that run has at 1pt. Height would be
    the obvious thing to measure and is the wrong one -- "15206" carries no ascender or
    descender and measures shorter than "Ngozi" at the very same size.

    Only a single-line run is measured. Words on two different lines inside one region
    say nothing about size from their combined extent.
    """
    inside = [
        w
        for w in words
        if bbox.x <= ((w.x0 + w.x1) / 2) / page_w <= bbox.x + bbox.width
        and bbox.y <= ((w.y0 + w.y1) / 2) / page_h <= bbox.y + bbox.height
        and w.text.strip()
    ]
    if not inside:
        return None
    top = min(w.y0 for w in inside)
    line = [w for w in inside if w.y0 - top < (w.y1 - w.y0)]
    if not line:
        return None
    # Measure contiguous runs, never the line's full extent. A region can hold several
    # values in separate columns -- "Ngozi Okafor", "Sister", "412-555-0199" under one
    # rule -- and the blank rule between them is far wider than the single spaces that
    # joining the text inserts, so the span-over-text-width ratio reports a size twice
    # what the form was really filled at. Measured on one real form: 50px against a true
    # fill of 22px, which then pushed that value 13mm off when it was redrawn.
    sizes = []
    for run in split_runs(line):
        run.sort(key=lambda w: w.x0)
        at_one_point = pdf_backend.text_length(
            " ".join(w.text for w in run), fontname="helv", fontsize=1.0
        )
        if at_one_point <= 0:
            continue
        width = max(w.x1 for w in run) - min(w.x0 for w in run)
        # Both sides are in the page image's own pixels, so this is a size in pixels;
        # dividing by page height makes it the resolution-free fraction stored.
        sizes.append(width / at_one_point / page_h)
    if not sizes:
        return None
    size = sorted(sizes)[len(sizes) // 2]
    return size if 0.002 <= size <= 0.08 else None


# Words beyond which a bound label is a sentence rather than a field's caption. The same
# bound the reader uses for the same judgement (``form_analyzer.MAX_CAPTION_WORDS``).
MAX_SLOT_LABEL_WORDS = 6


def _is_a_caption(label: str) -> bool:
    """Whether a slot's label names a field, or is a heading the reading bound it to.

    A slot is only worth storing if a value can later be resolved back onto it, by
    canonical key or by the label itself. A field whose label is the form's own
    instruction paragraph can never be: on one real form every one of ten slots came back
    named `please_complete_this_form_in_black_or_blue_ink_only_illegible_forms_will_be_returned`,
    and on another all six carried the page's Spanish title. A template asserting ten
    fields it cannot fill is worse than one asserting none, because only the second is
    honest about what was learned.

    Judged by length and by the heading list the reader already keeps, not by wording, so
    a form phrased in words nobody has seen is treated the same way.
    """
    from app.services.form_analyzer import HEADINGS

    text = (label or "").strip()
    if not text or norm(text) in HEADINGS:
        return False
    return len(text.split()) <= MAX_SLOT_LABEL_WORDS


def derive_template(
    analysis: FormAnalysis, page_image: Path, template_name: str | None = None, blank: bool = False
) -> TemplateDefinition:
    """Build and persist a template from an analysed form, then register it.

    Every page is learned, not only the first. These are packets -- a welcome letter, a
    HIPAA notice and a financial policy commonly sit *ahead of* the registration page --
    and learning page 0 alone left 36 of 85 benchmark blanks "not derivable" while the
    rest read their filled copies through a letter. A page without answer regions is kept
    as an empty page so that indexes line up: a filled copy of a packet keeps the packet's
    page order, which is what lets each filled page be read through the blank's matching
    page.

    Only fields the analyser located as boxes are usable: an inline "label: value" match
    describes where the text happened to sit, not where the form provides space for it.

    ``blank`` says the upload is a blank form: its page is the master exactly as it is.
    Otherwise the answers the analysis found are lifted off the page to make the master.
    """
    folder = page_image.parent
    pdf = folder / "source.pdf"
    images = [page_image] + [
        folder / f"page_{index}.png" for index in range(1, max(1, len(analysis.pages)))
    ]
    learned = [
        _learn_page(
            image,
            index,
            pdf if pdf.exists() else None,
            [] if blank else [f for f in analysis.detected_fields if f.page_index == index],
        )
        for index, image in enumerate(images)
        if image.exists()
    ]
    if not any(page.fields for page, _, _ in learned):
        found = sum(count for _, _, count in learned)
        raise ValueError(
            "No answer regions were detected on this form, so a template cannot be "
            "derived from it. The page may be too low-contrast for its rules to be found."
            if not found
            else f"{found} answer regions were found but none carries a usable caption -- "
            "every one bound to a heading or to the form's printed instructions, so a "
            "template built from them could never be refilled."
        )

    template_id = next_template_id()
    out = DERIVED_ROOT / template_id
    out.mkdir(parents=True, exist_ok=True)
    for page, master, _ in learned:
        cv2.imwrite(str(out / preview_name(page.page_index)), master)
    definition = TemplateDefinition(
        template_id=template_id,
        template_name=template_name or f"Derived from {analysis.analysis_id}",
        version=1,
        page_count=len(learned),
        pages=[page for page, _, _ in learned],
        derived_from=analysis.analysis_id,
    )
    (out / "template.json").write_text(definition.model_dump_json(indent=2))
    registry.load()
    return definition


def _learn_page(
    page_image: Path, index: int, pdf: Path | None, answers: Sequence[DetectedFormField] = ()
) -> tuple[TemplatePage, np.ndarray, int]:
    """One page's slots, its blank master, and how many regions were found at all."""
    # Always the geometric view, never the analysis's own fields: on a ruled form the
    # inline reader usually wins, and its answer boxes describe where one patient's text
    # landed rather than where the form provides space. A fillable PDF's own widgets are
    # the designer's answer spaces and take precedence over anything inferred.
    # Read once: the same words serve the geometry and, below, the size measurement.
    page_words = words_from_image(page_image, settings.ocr_language)
    boxed = geometric_fields(page_image, widget_boxes(pdf, index) if pdf else None, page_words)
    boxed = _named_by_model(boxed, page_image)
    found = len(boxed)
    # A field the vocabulary can name is keepable however it is worded: a caption phrased
    # as an instruction -- "Please check the box(es) that describe your race" -- is eight
    # words long and is a real field, and the length test alone threw two working slots
    # out of the round trip.
    boxed = [f for f in boxed if f.canonical_key or _is_a_caption(f.label_text)]

    image = cv2.imread(str(page_image))
    if image is None:
        raise FileNotFoundError(f"Cannot read page image {page_image}")
    height, width = image.shape[:2]
    if answers:
        detected = detect_checkboxes(page_image, page_words)
        # A letter can pass for a box ("B" in a name); an option's box has company.
        options = [b for b in detected if level_sibling(b, [o for o in detected if o is not b])]
        inked, ticked = answer_ink(list(answers), page_words, width, height, options)
    else:
        inked, ticked = [], []
    blank = master_from_page(image, inked, ticked)

    # The values are still on the page at this point -- ``master_from_page`` lifts them
    # only for the stored master -- so the size the form was actually filled at can be
    # measured here, and is what regeneration should reproduce.
    # One size for the whole page, not one per field. A form is filled at a single size,
    # and the per-field measurement is only as good as what happens to sit in that
    # field's region: a rule's writable band also contains the captions printed under
    # the rule *above* it, so measuring the topmost line in the box reported a phone
    # number at the size of the "CITY STATE ZIP CODE" captions above it. Taking the
    # median across every field that yielded a measurement discards those, and gives the
    # fields that were blank when the template was learned a real size too, instead of
    # leaving them to the box-shaped guess.
    measured = [
        size
        for f in boxed
        if (size := _font_size_of(f.answer_bbox, page_words, width, height)) is not None
    ]
    # A page-wide size needs a page's worth of evidence. One measurement is whatever
    # happened to sit in one region -- a barcode's glyphs read the whole hoag form at 63
    # pixels (2026-09-15) -- and a size outside the band forms are filled at (about 5 to
    # 24 points on a letter page) is a misreading, not a size.
    measured = [size for size in measured if MIN_FILL_SIZE <= size <= MAX_FILL_SIZE]
    fill_size = sorted(measured)[len(measured) // 2] if len(measured) >= MIN_FILL_MEASUREMENTS else None
    used: set[str] = set()
    fields = []
    choices = _choice_slots(page_image, page_words, width, height, used, pdf, index)
    for field in boxed:
        # Real forms repeat a caption per section — "Name" and "Phone Number" appear
        # under primary insurance, secondary insurance and emergency contact alike. Two
        # of them mapping to the same canonical key must still get distinct ids, or the
        # page fails validation and no template can be built at all.
        base = field.canonical_key or _slug(field.label_text, set())
        if base in used and not field.canonical_key:
            # A repeat with no canonical key is provably unfillable, and ``_slug`` says so
            # itself: regeneration resolves a value by ``label_key``, which only ever
            # produces the un-suffixed form, so `x_2` and `x_3` can never be reached. They
            # are not hard slots, they are dead ones -- and a page title that every region
            # fell back to produces six of them in a row. A canonical key is exempt,
            # because a real form does repeat "Name" and "Phone Number" once per section
            # and those resolve by key rather than by label.
            continue
        field_id, n = base, 2
        while field_id in used:
            field_id, n = f"{base}_{n}", n + 1
        used.add(field_id)
        fields.append(
            TemplateField(
                field_id=field_id,
                label=field.label_text,
                field_type=field.field_type or FieldType.SHORT_TEXT,
                bbox=field.answer_bbox,
                font_size=fill_size,
                text_indent=_text_indent(field.answer_bbox, page_words, width, height),
                printed_text=_printed_inside(field.answer_bbox, page_words, width, height),
                review_threshold=0.75,
            )
        )
    page = TemplatePage(
        page_index=index,
        canonical_width=width,
        canonical_height=height,
        fields=fields + choices,
        printed_words=_printed_layer(pdf, index, page_words, width, height),
        printed_spans=_printed_spans(pdf, index, page_words, width, height, inked),
        lifted=[
            BoundingBox(x=x0 / width, y=y0 / height, width=(x1 - x0) / width, height=(y1 - y0) / height)
            for x0, y0, x1, y1 in inked
        ],
    )
    return page, blank, found


# How far beyond a taught region an answer's words are looked for in the printed layer:
# the layer holds word centres, and a value can start a hair before the region drawn.
TAUGHT_LAYER_MARGIN = 0.012


def _choice_slots(
    page_image: Path,
    words: list,
    width: float,
    height: float,
    used: set[str],
    pdf: Path | None = None,
    index: int = 0,
) -> list[TemplateField]:
    """The page's tick-box questions as slots: the boxes, their captions, the question.

    A choice is the one kind of answer a blank fully describes: where the boxes are, what
    each is called and what the question is. Recording it means a filled copy is read by
    measuring ink in known boxes, instead of finding the row, its captions and its question
    again on every copy -- which is where most tick answers were lost.

    A PDF blank's own text layer names the options, where OCR of its render calls them
    "ves" and "L]ves": 19 ticked answers on the benchmark's ticked copies were read under a
    caption that did not match the option, all of them OCR's spelling of it. The boxes are
    still found on the render, which is the only place a drawn box exists.
    """
    printed = _pdf_words(pdf, index, width, height)
    if printed:
        words = printed
    from app.services.form_analyzer import choice_rows

    boxes = detect_checkboxes(page_image, words)

    if not boxes:
        return []
    slots: list[TemplateField] = []
    for row, names, label_words in choice_rows(boxes, set(), words, group_lines_tolerant(words)):
        label = " ".join(z.text for z in label_words).rstrip(":").strip()
        if not label:
            continue
        mapped, _, _ = match(label)
        base = mapped.canonical_key if mapped else _slug(label, used)
        if base in used:
            # A row is not dropped for want of a free key. A form asks "Yes / No" a dozen
            # times and its text slots have already claimed the obvious names, and a row
            # thrown away here is a question the copy has to find for itself -- which is
            # where ticked answers were being lost. The suffixed key still carries the
            # boxes, the option names and the question, which is what reading a tick needs.
            base = _slug(f"{label} choice", used)
        used.add(base)
        span = (min(b.x0 for b in row), min(b.y0 for b in row), max(b.x1 for b in row), max(b.y1 for b in row))
        slots.append(
            TemplateField(
                field_id=base,
                label=label,
                field_type=FieldType.MULTIPLE_CHOICE,
                bbox=BoundingBox(
                    x=span[0] / width, y=span[1] / height,
                    width=(span[2] - span[0]) / width, height=(span[3] - span[1]) / height,
                ),
                # The whole caption, re-read off the page where OCR glued a box to it.
                options=[option_phrase(b, n, row, words, boxes, page_image) for b, n in zip(row, names)],
                option_boxes=[
                    BoundingBox(x=b.x0 / width, y=b.y0 / height,
                                width=(b.x1 - b.x0) / width, height=(b.y1 - b.y0) / height)
                    for b in row
                ],
                review_threshold=0.75,
            )
        )
    return slots


def _pdf_words(pdf: Path | None, index: int, width: float, height: float) -> list:
    """A PDF page's own words, in the pixels of the render the template is measured in."""
    if pdf is None:
        return []
    try:
        page = pdf_backend.open_page(pdf, index)
        try:
            words, w = words_from_pdf(page), page.width
        finally:
            page.close()
    except Exception:  # noqa: BLE001 - no text layer to offer; OCR of the render stands
        return []
    if not words or not w:
        return []
    scale = width / w
    return [
        word._replace(x0=word.x0 * scale, y0=word.y0 * scale, x1=word.x1 * scale, y1=word.y1 * scale)
        for word in words
    ]


def _without_tokens(text: str | None, value: str) -> str | None:
    """``text`` with the words of ``value`` removed, once each."""
    if not text:
        return text
    remaining: dict[str, int] = {}
    for token in re.findall(r"[a-z0-9]+", value.lower()):
        remaining[token] = remaining.get(token, 0) + 1
    kept = []
    for word in text.split():
        key = "".join(re.findall(r"[a-z0-9]+", word.lower()))
        if key and remaining.get(key, 0) > 0:
            remaining[key] -= 1
            continue
        kept.append(word)
    return " ".join(kept) or None


def _layer_without(
    layer: list[tuple[str, float, float]], bbox, value: str
) -> list[tuple[str, float, float]]:
    """The printed layer less the words of ``value`` found inside ``bbox``, once each."""
    remaining: dict[str, int] = {}
    for token in re.findall(r"[a-z0-9]+", value.lower()):
        remaining[token] = remaining.get(token, 0) + 1
    m = TAUGHT_LAYER_MARGIN
    out = []
    for text, cx, cy in layer:
        inside = bbox.x - m <= cx <= bbox.x + bbox.width + m and bbox.y - m / 2 <= cy <= bbox.y + bbox.height + m / 2
        if not inside:
            out.append((text, cx, cy))
            continue
        kept = []
        for token in text.split():
            if remaining.get(token, 0) > 0:
                remaining[token] -= 1
                continue
            kept.append(token)
        if kept:
            out.append((" ".join(kept), cx, cy))
    return out


def _printed_layer(
    pdf: Path | None, index: int, page_words: list, width: int, height: int
) -> list[tuple[str, float, float]]:
    """What the form this template is learned from prints, word by word, in page fractions.

    A PDF's own text layer where there is one -- exact, where OCR of the render drops and
    splits words -- and the render's OCR otherwise. Learned from the blank, this is the
    form's furniture; learned from a filled copy it would also hold that patient's
    answers, which is one more reason enrolment starts from the blank.

    Dropping every word inside a lifted answer region was tried on 2026-09-19 and reverted.
    It fixes a real fault -- the copy a template was learned from, read again, lost ten of
    its eleven values, because each matched "the blank's printing where the blank prints
    it" -- but a lifted region is a box, and the words inside it include the form's own
    captions. Removing those stopped them suppressing the junk they were there to suppress:
    on the no-blank track, recall 90.2 -> 91.5 but precision **94.0 -> 82.9**, some four
    hundred more proposed values that are no answer at all. The narrower fix is to take out
    only the words of the value that was lifted, as ``_layer_without`` already does for a
    reviewer's correction; it is not attempted here because it is unmeasured.
    """
    words, w, h = _layer_words(pdf, index, page_words, width, height)
    return _as_layer(words, w, h)


def _layer_words(
    pdf: Path | None, index: int, page_words: list, width: int, height: int
) -> tuple[list, float, float]:
    """The words of the page's own text layer where it has one, its OCR otherwise."""
    if pdf is not None:
        try:
            page = pdf_backend.open_page(pdf, index)
            try:
                words, w, h = page.words(), page.width, page.height
            finally:
                page.close()
        except Exception:  # noqa: BLE001 - no text layer to offer; fall back to OCR
            words, w, h = [], 0.0, 0.0
        if words and w and h:
            return words, w, h
    return page_words, float(width), float(height)


def _printed_spans(
    pdf: Path | None, index: int, page_words: list, width: int, height: int, inked: Sequence = ()
) -> list[tuple[float, float, float, float]]:
    """The box each printed word occupies, in page fractions.

    The same words as ``_printed_layer``, kept whole rather than split at punctuation:
    regeneration uses them to keep a value off the form's own printing, and for that what
    matters is the space a word takes up, not what it says.

    ``inked`` is where this page's answers were lifted to make its master, in page pixels.
    Those words are the patient's, not the form's, and the master no longer carries them:
    counted as printing, they would push a regenerated value off the very place it was
    read from.
    """
    words, w, h = _layer_words(pdf, index, page_words, width, height)
    lifted = [(x0 / width, y0 / height, x1 / width, y1 / height) for x0, y0, x1, y1 in inked]
    spans = []
    for word in words:
        span = _without_rule(word)
        if not span:
            continue
        x0, x1 = span[0] / w, span[1] / w
        y0, y1 = word.y0 / h, word.y1 / h
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if any(lx0 <= cx <= lx1 and ly0 <= cy <= ly1 for lx0, ly0, lx1, ly1 in lifted):
            continue
        spans.append((round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)))
    return spans


# A rule a form draws with characters: "DOB:______________" is one word to a text layer,
# and its box covers the whole line the answer is meant to go on.
RULE_RUN = re.compile(r"[_.·•─-╿-]{3,}")


def _without_rule(word) -> tuple[float, float] | None:
    """A printed word's horizontal extent, less any rule drawn as part of it."""
    text = word.text.strip()
    if not text:
        return None
    kept = max(RULE_RUN.split(word.text), key=len, default="")
    # A word is something with a letter or a digit in it. The separators a form prints
    # inside a field -- the "-" of an SSN rule, a stray colon -- hide nothing a reader
    # needs, and keeping a value off them only pushes it out of the space it belongs in.
    if not any(ch.isalnum() for ch in kept):
        return None
    start = word.text.index(kept)
    span = word.x1 - word.x0
    # Where in the word the kept part lies, by character count: a text layer gives one box
    # for the whole word, so this is the only measure of it there is.
    return (
        word.x0 + span * start / len(word.text),
        word.x0 + span * (start + len(kept)) / len(word.text),
    )


def _as_layer(words: list, width: float, height: float) -> list[tuple[str, float, float]]:
    layer = []
    for word in words:
        # Split at punctuation, as the reader's printing rule compares words.
        text = " ".join(re.findall(r"[a-z0-9]+", word.text.lower()))
        if text:
            layer.append(
                (
                    text,
                    round((word.x0 + word.x1) / 2 / width, 4),
                    round((word.y0 + word.y1) / 2 / height, 4),
                )
            )
    return layer


def learn_corrections(
    template_id: str, corrections: list[SlotCorrection]
) -> TemplateDefinition:
    """Teach a template where a reviewer says its answers live.

    The blank form says where a form *offers* space; a reviewer looking at a filled copy
    says where an answer *is*, and the second is what the stacked and sub-captioned rows
    that geometry keeps missing need. This is the enrolled track's route past what a
    blank can teach: the second, third and fifth filled copies of a form are free, because
    a receptionist correcting one teaches its template.

    A correction names the caption, the page and the region. The slot takes the
    vocabulary's key for the caption where it knows it, so values resolve on regeneration,
    and replaces an earlier slot with the same key on that page -- the reviewer's word
    beats the geometry's guess. What the blank prints inside the new region is read from
    the stored master, so a later read subtracts it like any other slot's.
    """
    definition = registry.get(template_id).model_copy(deep=True)
    folder = DERIVED_ROOT / template_id
    if not folder.is_dir():
        raise ValueError(f"{template_id} is not a derived template; corpus masters are not taught")
    pages = {page.page_index: page for page in definition.pages}
    words_by_page: dict[int, tuple[list, int, int] | None] = {}
    for correction in corrections:
        if correction.page_index not in pages:
            # Packets keep their page order; a page the blank taught nothing about still
            # has an index, sized like the last page learned.
            last = definition.pages[-1]
            for index in range(len(definition.pages), correction.page_index + 1):
                page = TemplatePage(
                    page_index=index,
                    canonical_width=last.canonical_width,
                    canonical_height=last.canonical_height,
                    fields=[],
                )
                definition.pages.append(page)
                pages[index] = page
        page = pages[correction.page_index]
        mapped, _, _ = match(correction.label)
        field_id = mapped.canonical_key if mapped else label_key(correction.label)
        if correction.page_index not in words_by_page:
            master = folder / preview_name(correction.page_index)
            if master.exists():
                image = cv2.imread(str(master))
                words_by_page[correction.page_index] = (
                    (words_from_image(master, settings.ocr_language), image.shape[1], image.shape[0])
                    if image is not None
                    else None
                )
            else:
                words_by_page[correction.page_index] = None
        known = words_by_page[correction.page_index]
        printed = _printed_inside(correction.bbox, *known) if known else None
        if (
            correction.value
            and correction.analysis_id
            and correction.analysis_id == definition.derived_from
        ):
            # The reviewer is correcting the copy this template was learned from, so the
            # value is that patient's answer. Recorded as printing it would be subtracted
            # from, or cleared as furniture on, the next patient who gives the same
            # answer -- the no-blank path's whole recall loss. It is taken back out.
            printed = _without_tokens(printed, correction.value)
            page.printed_words = _layer_without(page.printed_words, correction.bbox, correction.value)
        slot = TemplateField(
            field_id=field_id,
            label=correction.label,
            field_type=mapped.value_type if mapped else FieldType.SHORT_TEXT,
            bbox=correction.bbox,
            printed_text=printed,
            taught=True,
            review_threshold=0.75,
        )
        page.fields = [f for f in page.fields if f.field_id != field_id] + [slot]
    definition.page_count = len(definition.pages)
    (folder / "template.json").write_text(definition.model_dump_json(indent=2))
    registry.load()
    return definition
