"""Word extraction from PDF text layers and image OCR, normalized to one shape.

Downstream field detection consumes ``Word`` tuples and does not care whether they
came from a digital PDF text layer or from OCR over a rasterized page.
"""

import re
from pathlib import Path
from typing import NamedTuple

from fastapi import HTTPException

from app.config import settings


class Word(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block: int
    confidence: float


def words_from_pdf(page) -> list[Word]:
    """Words from a digital PDF text layer. Confidence is 1.0: the text is exact.

    Deliberately unfiltered so grouping matches the raw ``get_text("words")`` output.
    """
    words = [Word(w[0], w[1], w[2], w[3], w[4], int(w[5]), 1.0) for w in page.get_text("words")]
    # A PDF that draws its rules with underscores glues them to whatever text abuts them,
    # so the same separation the OCR path needs is needed here.
    return _split_glued_labels(words)


def words_from_image(image_path: Path, language: str) -> list[Word]:
    """Words from whichever OCR engine is configured."""
    if settings.ocr_engine == "rapidocr":
        return _drop_rule_artifacts(_split_glued_labels(_words_from_rapidocr(image_path)))
    return _words_from_tesseract(image_path, language)


_RAPIDOCR = None


def _rapidocr():
    """One engine instance: loading the models costs more than reading a page."""
    global _RAPIDOCR
    if _RAPIDOCR is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise HTTPException(503, "OCR engine rapidocr is not installed") from exc
        _RAPIDOCR = RapidOCR()
    return _RAPIDOCR


def _words_from_rapidocr(image_path: Path) -> list[Word]:
    """Words from RapidOCR, which recognises a whole line at a time.

    The line is the unit it is accurate on, but this pipeline is built on word geometry.
    Asking for character boxes and splitting them on spaces recovers a box per word, and
    the gaps stay real because they come from measured glyph positions.
    """
    result, _ = _rapidocr()(str(image_path), return_word_box=True)
    words: list[Word] = []
    for block, line in enumerate(result or []):
        if len(line) < 6:
            continue
        char_boxes, chars, scores = line[3], line[4], line[5]
        run: list[int] = []
        for index, char in list(enumerate(chars)) + [(None, " ")]:
            if char != " " and index is not None:
                run.append(index)
                continue
            if run:
                xs = [pt[0] for i in run for pt in char_boxes[i]]
                ys = [pt[1] for i in run for pt in char_boxes[i]]
                words.append(
                    Word(
                        float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys)),
                        "".join(chars[i] for i in run), block,
                        sum(scores[i] for i in run) / len(run),
                    )
                )
                run = []
    return words


def _words_from_tesseract(image_path: Path, language: str) -> list[Word]:
    """Words from Tesseract OCR over a preprocessed page image.

    Coordinates are pixels in ``image_path``; confidence is Tesseract's 0-100 score
    rescaled to 0-1. Entries Tesseract could not score (conf -1) are dropped.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise HTTPException(503, "OCR dependency pytesseract is unavailable") from exc

    try:
        data = pytesseract.image_to_data(
            Image.open(image_path), lang=language, output_type=pytesseract.Output.DICT
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise HTTPException(
            503, "Image OCR requires the local Tesseract binary (brew install tesseract)"
        ) from exc

    words = []
    for i, text in enumerate(data["text"]):
        confidence = float(data["conf"][i])
        if not text.strip() or confidence < 0:
            continue
        left, top = float(data["left"][i]), float(data["top"][i])
        words.append(
            Word(
                left,
                top,
                left + float(data["width"][i]),
                top + float(data["height"][i]),
                text,
                int(data["block_num"][i]),
                confidence / 100,
            )
        )
    return _drop_rule_artifacts(_split_glued_labels(words))


RULE_RUN = re.compile(r"(_{3,})")


def _split_rules_from_text(words: list[Word]) -> list[Word]:
    """Separate a drawn rule from the text stuck to it.

    A form that draws its lines with underscores gives back tokens like
    "______________City/State/Zip:", where the rule of one field has swallowed the label of
    the next. The colon pass then puts the boundary between the two fields in the wrong
    place and the first one reads empty, even though its value is sitting right there.

    Splitting is by character count, which is close enough for binding because both halves
    stay on one line.
    """
    out: list[Word] = []
    for word in words:
        parts = [p for p in RULE_RUN.split(word.text) if p]
        if len(parts) < 2:
            out.append(word)
            continue
        # The rule runs are kept as parts, not discarded: dropping them collapses the
        # offsets and the label that followed a rule lands where the rule began, which
        # puts it to the *left* of the value written on that rule.
        span = word.x1 - word.x0
        at = word.x0
        for part in parts:
            width = span * len(part) / len(word.text)
            out.append(word._replace(x0=at, x1=at + width, text=part))
            at += width
    return out


def _split_glued_labels(words: list[Word]) -> list[Word]:
    """Separate a label from its value when OCR returns them as one token.

    A value written onto a ruled line often touches the rule and the label's colon, and
    Tesseract then reads "Email:_daniel@example.com" as a single word. Neither the colon
    pass nor the box pass can recover a value from that, because both work on whole words,
    so the field reads as blank on a form that plainly has an answer on it.

    The split point is the first colon, and the box is divided in proportion to the
    character counts -- close enough for binding, since the two halves stay on one line.
    Leading rule characters are dropped from the value.
    """
    out: list[Word] = []
    for word in _split_rules_from_text(words):
        head, sep, tail = word.text.partition(":")
        value = tail.strip("_ .")
        if not sep or not head.strip() or not value:
            out.append(word)
            continue
        share = (len(head) + 1) / len(word.text)
        cut = word.x0 + (word.x1 - word.x0) * share
        out.append(word._replace(x1=cut, text=f"{head}:"))
        out.append(word._replace(x0=cut, text=value))
    return out


# A rule line read as a glyph is short text with a shape no real character has: a box
# border spanning the field height, or a horizontal rule far wider than it is tall.
MAX_GLYPH_ASPECT = 3.0
MAX_GLYPH_HEIGHT_RATIO = 2.0
MIN_ARTIFACT_CONFIDENCE = 0.5


def _drop_rule_artifacts(words: list[Word]) -> list[Word]:
    """Remove OCR reads that are really the form's own rule lines.

    Tesseract reports a box's vertical border as ``|`` or ``i`` and its horizontal rule
    as ``-``, ``=`` or ``7``. Left in, they corrupt values ("Taylor Morgan |") or replace
    them outright, and they are indistinguishable from real text by string content alone
    — the tell is geometry.
    """
    if not words:
        return words
    heights = sorted(w.y1 - w.y0 for w in words)
    median_height = heights[len(heights) // 2]

    kept = []
    for word in words:
        text = word.text.strip()
        width, height = word.x1 - word.x0, word.y1 - word.y0
        # Only short reads are ever ambiguous; real words are never mistaken for a rule.
        if len(text) <= 2 and height > 0:
            too_wide = width / height > MAX_GLYPH_ASPECT
            too_tall = height > median_height * MAX_GLYPH_HEIGHT_RATIO
            unsure = word.confidence < MIN_ARTIFACT_CONFIDENCE
            if too_wide or too_tall or unsure:
                continue
        kept.append(word)
    return kept


def group_lines_exact(words: list[Word]) -> list[list[Word]]:
    """Line grouping for PDF words, whose baselines share exact y-coordinates."""
    groups: dict[tuple[int, float], list[Word]] = {}
    for word in words:
        groups.setdefault((word.block, round(word.y0, 1)), []).append(word)
    return [sorted(line, key=lambda w: w.x0) for line in groups.values()]


# A label rarely runs past four words, and a caption sits a space from its colon while a
# value is separated from the next column by the remainder of its rule line.
MAX_LABEL_WORDS = 4
LABEL_GAP_RATIO = 1.2


def label_run_start(line: list[Word], lower: int, colon: int) -> int:
    """Index where the label ending at ``colon`` begins, no earlier than ``lower``.

    Walks left from the colon while each word still sits within a normal space of the one
    after it. The wide gap left by an unfilled rule is what separates the previous field's
    value from this label, so distance decides the split rather than word shape -- a
    surname and a caption are indistinguishable as text.
    """
    if not line:
        return colon
    em = sorted(z.y1 - z.y0 for z in line)[len(line) // 2]
    start = colon
    while start > lower and colon - start + 1 < MAX_LABEL_WORDS:
        if line[start].x0 - line[start - 1].x1 > em * LABEL_GAP_RATIO:
            break
        start -= 1
    return start


def group_lines_tolerant(words: list[Word], tolerance_ratio: float = 0.8) -> list[list[Word]]:
    """Line grouping for OCR words, whose baselines jitter by a few pixels.

    Words join a line when their vertical centre sits within ~60% of the median word
    height of that line's running centre. Grouping is purely geometric because
    Tesseract's block numbering splits visually-continuous form rows unpredictably.

    A row is then split on wide horizontal gaps, so two captions sitting side by side
    in separate columns ("Date of birth" and "Phone") do not fuse into one label.
    """
    if not words:
        return []
    heights = sorted(w.y1 - w.y0 for w in words)
    median_height = heights[len(heights) // 2]
    # Swept against FUNSD: tighter fragments long labels, looser merges adjacent rows.
    tolerance = max(1.0, median_height * tolerance_ratio)

    rows: list[list[Word]] = []
    centre_of: list[float] = []
    for word in sorted(words, key=lambda w: (w.y0 + w.y1) / 2):
        centre = (word.y0 + word.y1) / 2
        # The anchor is fixed when a row opens and never moves. A running mean lets the
        # reference creep downward as words accumulate, so a dense paragraph's row
        # gradually swallows the row beneath it and their words interleave.
        if rows and abs(centre - centre_of[-1]) <= tolerance:
            rows[-1].append(word)
        else:
            rows.append([word])
            centre_of.append(centre)

    gap_limit = max(2.0, median_height * 2.0)
    lines: list[list[Word]] = []
    for row in rows:
        ordered = sorted(row, key=lambda w: w.x0)
        segment: list[Word] = []
        for i, word in enumerate(ordered):
            if segment and word.x0 - segment[-1].x1 > gap_limit:
                # A gap mid-value is tab-stop alignment ("Name:    Robert"), so hold the
                # segment together. But if a colon appears further along the row, a second
                # label starts there and the two fields must not be welded into one.
                mid_value = any(":" in z.text for z in segment)
                nxt = next((j for j in range(i, len(ordered)) if ":" in ordered[j].text), None)
                # Split only at the gap the next label actually begins after. Splitting at
                # the first wide gap cut a label from its own value, because the unfilled
                # remainder of its rule is wider than the space before the next column.
                starts_new_label = nxt is not None and label_run_start(ordered, i, nxt) == i
                if not mid_value or starts_new_label:
                    lines.append(segment)
                    segment = []
            segment.append(word)
        if segment:
            lines.append(segment)
    return lines
