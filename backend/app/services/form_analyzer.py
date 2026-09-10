import hashlib
import time
from pathlib import Path
from typing import NamedTuple

import cv2
import fitz

from app.config import settings
from app.schemas.form_understanding import DetectedFormField, FormAnalysis, RequiredStatus
from app.schemas.models import BoundingBox, FieldType
from app.services.canonical_registry import match, norm
from app.services.field_layout import (
    MARKED_INK_RATIO,
    Box,
    captions_for_row,
    detect_boxes,
    detect_checkboxes,
    detect_write_rules,
    ink_ratio,
    label_for_box,
    partition,
)
from app.services.field_validity import plausible
from app.services.fusion import fuse
from app.services.templates import MATCH_THRESHOLD, registry
from app.services.vision_extractor import available as vision_available
from app.services.vision_extractor import extract as vision_extract
from app.services.word_source import (
    Word,
    group_lines_tolerant,
    label_run_start,
    words_from_image,
    words_from_pdf,
)

HEADINGS = {
    "patient intake form",
    "prelimmd medical center",
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
    """
    stripped = "".join(str(text or "").split())
    return bool(stripped) and all(c in "_.-–—…·:" for c in stripped)


def normalize(v, t):
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



def _fields_with_lines(
    lines: list[list[Word]], w: float, h: float, source: str
) -> list[tuple[list[Word], DetectedFormField]]:
    """Split each ``label: value`` line into a detected field, paired with its source
    line so callers can tell which lines were consumed.

    Shared by the PDF and OCR paths; only the confidence model differs, since PDF
    text is exact while OCR carries a per-word recognition score.
    """
    is_ocr = source == "ocr"
    fields = []
    for i, line in enumerate(lines):
        texts = [z.text for z in line]
        if ":" not in " ".join(texts):
            continue
        colons = [j for j, t in enumerate(texts) if ":" in t]
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
            label_words, answer_words = line[start : colon + 1], line[colon + 1 : stop]
            label = " ".join(texts[start : colon + 1]).rstrip(":")
            if not label or norm(label) in HEADINGS:
                start = stop
                continue
            answer = " ".join(texts[colon + 1 : stop]) or None
            if is_blank_marker(answer):
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

MARK_TYPES = (FieldType.CHECKBOX, FieldType.SIGNATURE_PRESENCE)


# Two or more tick boxes sharing a field's answer space make it a choice, not free text.
MIN_CHOICE_OPTIONS = 2
# How far above its row's typical ink a box must sit to count as ticked.
MARK_CONTRAST = 3.0


def _resolve_choice_fields(
    fields: list[DetectedFormField],
    checkboxes: list[Box],
    words: list[Word],
    gray,
    w: float,
    h: float,
) -> None:
    """Report which option a tick-box field selects, instead of its row of printed text.

    "Marital Status: Single [ ] Married [ ] Widowed [ ]" is a choice, and OCR reading of
    the row ("Singleo Marriedo Widowedo") is neither the question nor the answer. The boxes
    inside the field's answer space are measured for ink and the selected captions are
    reported, so an unticked group reads empty rather than reading as prose.
    """
    if gray is None:
        return
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
        inks = [ink_ratio(gray, b) for b, _ in captions]
        baseline = sorted(inks)[len(inks) // 2]
        floor = max(MARKED_INK_RATIO, baseline * MARK_CONTRAST)
        # Captions arrive with whatever spacing the form used to separate the option from
        # its box, including the em spaces PDF text layers often carry.
        chosen = [c.strip() for (b, c), ink in zip(captions, inks) if ink > floor]
        field.field_type = FieldType.MULTIPLE_CHOICE
        field.raw_value = ", ".join(chosen) or None
        field.normalized_value = field.raw_value
        field.is_blank = not chosen
        field.confidence = 0.9 if chosen else 0.6
        field.needs_review = not chosen


def _centre_inside_box(inner: Box, outer: Box) -> bool:
    cx, cy = (inner.x0 + inner.x1) / 2, (inner.y0 + inner.y1) / 2
    return outer.x0 <= cx <= outer.x1 and outer.y0 <= cy <= outer.y1


def _best_layout(
    words: list[Word], page_image: Path, w: float, h: float, source: str
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
        boxes, contents, group_lines_tolerant(free), w, h, gray, rule_indices
    )

    def reads(candidate: list[DetectedFormField]) -> tuple[int, int]:
        return sum(1 for f in candidate if f.canonical_key), len(candidate)

    fields = max((boxed, inline), key=reads)
    _resolve_choice_fields(
        fields, detect_checkboxes(page_image, words), words, gray, w, h
    )
    return fields, ("boxed" if fields is boxed else "inline"), len(boxes)

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


def _is_printed_caption(answer: str | None) -> bool:
    """A run of text that is the form's own printing rather than an answer."""
    if not answer:
        return False
    # A label carries its colon into the value only when the region caught printed text.
    return ":" in answer or norm(answer.split("(")[0]) in HEADINGS


def _build_box_fields(
    boxes: list[Box],
    contents: list[list[Word]],
    candidates: list[list[Word]],
    w: float,
    h: float,
    gray=None,
    rule_indices: set[int] | None = None,
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
            continue
        used.add(id(label_words))
        label = " ".join(z.text for z in label_words).rstrip(":")
        mapped, score, reason = match(label)
        typ = mapped.value_type if mapped else FieldType.SHORT_TEXT

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
    )


def _read_image(folder: Path) -> SourcePage | None:
    """Words from OCR over the contrast-enhanced page, which shares its dimensions with
    the colour page served to the review UI, so overlay boxes line up."""
    from PIL import Image

    image = folder / "ocr_gray.png"
    if not image.exists():
        image = folder / "normalized_color.png"
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
            first = _read_image(folder)
            sources = [first] if first else []
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

        matched, match_score = _match_template(folder, template_hint)
        fields: list[DetectedFormField] = []
        pages = []
        warnings = list(page.warnings)
        for index, source in enumerate(sources):
            read, layout, boxes_detected = _best_layout(
                source.words, source.image, source.width, source.height, source.source
            )
            # Field ids are only unique within the page that produced them.
            for field in read:
                field.page_index = index
                field.field_id = f"p{index}_{field.field_id}" if index else field.field_id
            # Optional second reader. Off unless a vision provider is configured, so the
            # shipped behaviour is unchanged and turning it off is one environment
            # variable rather than a revert.
            if vision_available():
                try:
                    read = fuse(
                        read,
                        vision_extract(source.image),
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


def geometric_fields(page_image: Path) -> list[DetectedFormField]:
    """Every region the form provides for an answer, bound to its label.

    A template describes *where a form gives you space to write*, which is the geometric
    view of the page and nothing to do with which strategy happened to read the values
    better. Most real intake forms are ruled rather than boxed, so the inline reader wins
    on them and their fields carry ``source="ocr"`` -- deriving a template from those
    would record where text happened to sit on one filled copy, not where the blank form
    provides space, and regeneration would place values wherever that one patient wrote.
    """
    from PIL import Image

    words = words_from_image(page_image, settings.ocr_language)
    w, h = Image.open(page_image).size
    boxes = detect_boxes(page_image)
    rules = detect_write_rules(page_image, boxes)
    rule_indices = set(range(len(boxes), len(boxes) + len(rules)))
    boxes += rules
    contents, free = partition(boxes, words)
    gray = cv2.imread(str(page_image), cv2.IMREAD_GRAYSCALE)
    fields, _ = _build_box_fields(
        boxes, contents, group_lines_tolerant(free), float(w), float(h), gray, rule_indices
    )
    return fields


def _image_url(analysis_id: str, index: int = 0) -> str:
    return f"/api/v1/form-analyses/{analysis_id}/pages/{index}/image"


def _match_template(folder: Path, hint: str | None) -> tuple[str | None, float]:
    """Identify the blank master this page came from, so its values can later be
    regenerated onto the template's own exact field coordinates."""
    colour = cv2.imread(str(folder / "normalized_color.png"))
    if colour is None:
        return None, 0.0
    try:
        definition, score = registry.match(colour, hint)
    except RuntimeError:
        return None, 0.0
    matched = definition.template_id if definition and score >= MATCH_THRESHOLD else None
    return matched, score
