import hashlib
import time
from pathlib import Path

import cv2
import fitz

from app.config import settings
from app.schemas.form_understanding import DetectedFormField, FormAnalysis, RequiredStatus
from app.schemas.models import BoundingBox, FieldType
from app.services.canonical_registry import match, norm
from app.services.field_layout import (
    MARKED_INK_RATIO,
    Box,
    detect_boxes,
    ink_ratio,
    label_for_box,
    partition,
)
from app.services.templates import MATCH_THRESHOLD, registry
from app.services.word_source import (
    Word,
    group_lines_exact,
    group_lines_tolerant,
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
        joined = " ".join(texts)
        if ":" not in joined or norm(joined.split(":", 1)[0]) in HEADINGS:
            continue
        colon = next(j for j, t in enumerate(texts) if ":" in t)
        label = " ".join(texts[: colon + 1]).rstrip(":")
        answer = " ".join(texts[colon + 1 :]) or None
        mapped, score, reason = match(label)
        typ = mapped.value_type if mapped else FieldType.SHORT_TEXT
        label_words, answer_words = line[: colon + 1], line[colon + 1 :]
        lb = box(label_words, w, h)
        ab = (
            box(answer_words, w, h)
            if answer
            else BoundingBox(
                x=min(0.98, lb.x + lb.width + 0.05),
                y=lb.y,
                width=max(0.02, 0.92 - lb.x - lb.width),
                height=lb.height,
            )
        )
        if is_ocr:
            detection = _mean_confidence(label_words)
            confidence = _mean_confidence(answer_words) if answer else 0.6
        else:
            detection, confidence = 0.95, (0.92 if answer else 0.6)
        fields.append(
            (
                line,
                DetectedFormField(
                    field_id=f"field_{i}",
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
                    or (is_ocr and confidence < settings.default_review_threshold),
                ),
            )
        )
    return fields


MARK_TYPES = (FieldType.CHECKBOX, FieldType.SIGNATURE_PRESENCE)


def _build_box_fields(
    boxes: list[Box],
    contents: list[list[Word]],
    candidates: list[list[Word]],
    w: float,
    h: float,
    gray=None,
) -> tuple[list[DetectedFormField], set[int]]:
    """Bind each detected input box to its nearest label; the value is what OCR read
    inside the box. Returns the fields plus the ids of lines consumed as labels, so the
    colon pass does not re-emit them. ``candidates`` must be the same list objects the
    caller later iterates, since identity is what marks a line as used."""
    fields, used = [], set()
    for i, (bx, value_words) in enumerate(zip(boxes, contents)):
        label_words = label_for_box(bx, candidates)
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
                or confidence < settings.default_review_threshold,
            )
        )
    return fields, used


class FormAnalyzer:
    def analyze(
        self, analysis_id: str, folder: Path, template_hint: str | None = None
    ) -> FormAnalysis:
        started = time.monotonic()
        pdf = folder / "source.pdf"
        if pdf.exists():
            return self._analyze_pdf(analysis_id, folder, pdf, started)
        return self._analyze_image(analysis_id, folder, started, template_hint)

    def _analyze_pdf(
        self, analysis_id: str, folder: Path, pdf: Path, started: float
    ) -> FormAnalysis:
        doc = fitz.open(pdf)
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        pix.save(folder / "normalized_color.png")
        words = words_from_pdf(page)
        w, h = page.rect.width, page.rect.height
        fields = [f for _, f in _fields_with_lines(group_lines_exact(words), w, h, "pdf_text")]
        return FormAnalysis(
            analysis_id=analysis_id,
            source_type="digital_flat_pdf",
            pages=[
                {
                    "page_index": 0,
                    "image_url": f"/api/v1/form-analyses/{analysis_id}/pages/0/image",
                    "pdf_word_count": len(words),
                    "width": w,
                    "height": h,
                }
            ],
            detected_fields=fields,
            form_fingerprint=hashlib.sha256(pdf.read_bytes()).hexdigest(),
            warnings=[],
            processing_time_ms=int((time.monotonic() - started) * 1000),
        )

    def _analyze_image(
        self,
        analysis_id: str,
        folder: Path,
        started: float,
        template_hint: str | None = None,
    ) -> FormAnalysis:
        """OCR a photo or scan. Coordinates come from the contrast-enhanced grayscale
        page, which shares dimensions with the colour page served to the review UI."""
        from PIL import Image

        page_image = folder / "ocr_gray.png"
        if not page_image.exists():
            page_image = folder / "normalized_color.png"
        if not page_image.exists():
            return FormAnalysis(
                analysis_id=analysis_id,
                source_type="scanned_image",
                pages=[
                    {
                        "page_index": 0,
                        "image_url": f"/api/v1/form-analyses/{analysis_id}/pages/0/image",
                    }
                ],
                detected_fields=[],
                form_fingerprint="image",
                warnings=["Page image was not preprocessed; cannot run OCR."],
                processing_time_ms=int((time.monotonic() - started) * 1000),
            )

        words = words_from_image(page_image, settings.ocr_language)
        w, h = Image.open(page_image).size

        # Identify the blank master this page came from, so its values can later be
        # regenerated onto the template's own exact field coordinates.
        matched, match_score = None, 0.0
        colour = cv2.imread(str(folder / "normalized_color.png"))
        if colour is not None:
            try:
                definition, match_score = registry.match(colour, template_hint)
                matched = (
                    definition.template_id
                    if definition and match_score >= MATCH_THRESHOLD
                    else None
                )
            except RuntimeError:
                matched = None

        # A form is either boxed (label above or left of a drawn box) or inline
        # ("label: value" on one row). Running both and merging makes the two compete
        # for the same labels, so score each and keep whichever reads the page better.
        boxes = detect_boxes(page_image)
        contents, free = partition(boxes, words)
        gray = cv2.imread(str(page_image), cv2.IMREAD_GRAYSCALE)
        boxed, _ = _build_box_fields(boxes, contents, group_lines_tolerant(free), w, h, gray)
        inline = [f for _, f in _fields_with_lines(group_lines_tolerant(words), w, h, "ocr")]

        def reads(candidate: list[DetectedFormField]) -> tuple[int, int]:
            return sum(1 for f in candidate if f.canonical_key), len(candidate)

        fields = max((boxed, inline), key=reads)
        layout = "boxed" if fields is boxed else "inline"

        warnings = []
        if not words:
            warnings.append("OCR read no text from this page; check image quality.")
        elif not fields:
            warnings.append("OCR found no labelled boxes or 'label: value' lines.")
        return FormAnalysis(
            analysis_id=analysis_id,
            source_type="scanned_image",
            pages=[
                {
                    "page_index": 0,
                    "image_url": f"/api/v1/form-analyses/{analysis_id}/pages/0/image",
                    "ocr_word_count": len(words),
                    "mean_ocr_confidence": round(_mean_confidence(words), 3),
                    "layout": layout,
                    "boxes_detected": len(boxes),
                    "template_match_confidence": round(match_score, 3),
                    "width": w,
                    "height": h,
                }
            ],
            detected_fields=fields,
            # Hashes the normalized page: raw photo bytes vary with capture noise.
            form_fingerprint=hashlib.sha256(page_image.read_bytes()).hexdigest(),
            matched_known_template=matched,
            warnings=warnings,
            processing_time_ms=int((time.monotonic() - started) * 1000),
        )
