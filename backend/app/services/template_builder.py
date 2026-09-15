"""Derive a reusable blank template from a single filled form.

Regeneration needs a blank master plus field coordinates. When a form arrives that no
registered template matches, both can be recovered from the upload itself: the boxes
already located during analysis give the coordinates, and whiting out their interiors
leaves the pre-printed form behind.

Limits worth knowing. This only works for forms that draw their input boxes, because the
box is what separates a filled value from the printed labels and logos around it. A form
that writes values onto blank space or a dotted line needs a different signal — that is
where a vision model would classify printed-versus-filled instead. And a single sample
cannot prove a mark is a value rather than pre-printed text; two filled copies of the same
form make that unambiguous, since whatever is identical across them is the template.
"""

import logging
from pathlib import Path

import cv2
import fitz
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
from app.services.canonical_registry import match, norm
from app.services.field_layout import split_runs
from app.services.form_analyzer import geometric_fields, widget_boxes
from app.services.regeneration import label_key
from app.services.templates import DERIVED_ROOT, preview_name, registry
from app.services.vision_extractor import available as vision_available
from app.services.vision_extractor import extract as vision_extract
from app.services.vision_extractor import locate_values
from app.services.word_source import words_from_image

logger = logging.getLogger(__name__)

# Corpus masters own T01-T49; derived templates are allocated above that so the two never
# collide. TemplateDefinition constrains ids to T followed by two digits.
# The largest real answer region measured in the corpus (a multi-line notes box)
# covers 0.07 of the page. Anything appreciably larger is a section frame whose
# printed captions must survive into the blank master, so it is left alone.
MAX_ERASABLE_AREA_FRACTION = 0.12

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


def blank_from_filled(image: np.ndarray, boxes: list[BoundingBox]) -> np.ndarray:
    """Whiten the interior of each field box, leaving the printed form intact.

    The inset keeps each box's own rule lines, so the blank still looks like the form
    rather than a page with holes punched in it.
    """
    blank = image.copy()
    height, width = blank.shape[:2]
    # The inset only has to spare the box's own rule line, so it scales with page
    # resolution rather than box size. Scaling it with the box would leave a tall field's
    # first line of text un-erased, since values sit flush against the top edge.
    inset = max(2, round(min(height, width) * 0.003))
    for box in boxes:
        # A section frame ("Primary Insurance") encloses printed captions of its own.
        # Erasing its interior strips those from the blank master and mangles the form,
        # so only clear regions small enough to be a single answer.
        # bbox dimensions are page fractions, so their product is already the share of
        # the page this region covers.
        if box.width * box.height > MAX_ERASABLE_AREA_FRACTION:
            continue
        x0, y0 = int(box.x * width) + inset, int(box.y * height) + inset
        x1, y1 = int((box.x + box.width) * width) - inset, int((box.y + box.height) * height) - inset
        if x1 > x0 and y1 > y0:
            blank[y0:y1, x0:x1] = 255
    return blank


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
PRINTED_MARGIN = 0.012


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
        at_one_point = fitz.get_text_length(
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
    analysis: FormAnalysis, page_image: Path, template_name: str | None = None
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
    """
    folder = page_image.parent
    pdf = folder / "source.pdf"
    images = [page_image] + [
        folder / f"page_{index}.png" for index in range(1, max(1, len(analysis.pages)))
    ]
    learned = [
        _learn_page(image, index, pdf if pdf.exists() else None)
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
    for page, blank, _ in learned:
        cv2.imwrite(str(out / preview_name(page.page_index)), blank)
    definition = TemplateDefinition(
        template_id=template_id,
        template_name=template_name or f"Derived from {analysis.analysis_id}",
        version=1,
        page_count=len(learned),
        pages=[page for page, _, _ in learned],
    )
    (out / "template.json").write_text(definition.model_dump_json(indent=2))
    registry.load()
    return definition


def _learn_page(
    page_image: Path, index: int, pdf: Path | None
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
    blank = blank_from_filled(image, [f.answer_bbox for f in boxed])

    # The values are still on the page at this point -- ``blank_from_filled`` erases them
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
    fill_size = sorted(measured)[len(measured) // 2] if measured else None
    used: set[str] = set()
    fields = []
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
        page_index=index, canonical_width=width, canonical_height=height, fields=fields
    )
    return page, blank, found


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
