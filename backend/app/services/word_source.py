"""Word extraction from PDF text layers and image OCR, normalized to one shape.

Downstream field detection consumes ``Word`` tuples and does not care whether they
came from a digital PDF text layer or from OCR over a rasterized page.
"""

from pathlib import Path
from typing import NamedTuple

from fastapi import HTTPException


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
    return [Word(w[0], w[1], w[2], w[3], w[4], int(w[5]), 1.0) for w in page.get_text("words")]


def words_from_image(image_path: Path, language: str) -> list[Word]:
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
    return words


def group_lines_exact(words: list[Word]) -> list[list[Word]]:
    """Line grouping for PDF words, whose baselines share exact y-coordinates."""
    groups: dict[tuple[int, float], list[Word]] = {}
    for word in words:
        groups.setdefault((word.block, round(word.y0, 1)), []).append(word)
    return [sorted(line, key=lambda w: w.x0) for line in groups.values()]


def group_lines_tolerant(words: list[Word]) -> list[list[Word]]:
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
    tolerance = max(1.0, median_height * 0.6)

    rows: list[list[Word]] = []
    centre_of: list[float] = []
    for word in sorted(words, key=lambda w: (w.y0 + w.y1) / 2):
        centre = (word.y0 + word.y1) / 2
        if rows and abs(centre - centre_of[-1]) <= tolerance:
            rows[-1].append(word)
            # Running mean keeps the reference centred as the row accumulates.
            centre_of[-1] += (centre - centre_of[-1]) / len(rows[-1])
        else:
            rows.append([word])
            centre_of.append(centre)

    gap_limit = max(2.0, median_height * 2.0)
    lines: list[list[Word]] = []
    for row in rows:
        segment: list[Word] = []
        for word in sorted(row, key=lambda w: w.x0):
            # A colon already in the segment means we are past a label and into its
            # value, where a wide gap is just tab-stop alignment, not a column break.
            splittable = segment and not any(":" in z.text for z in segment)
            if splittable and word.x0 - segment[-1].x1 > gap_limit:
                lines.append(segment)
                segment = []
            segment.append(word)
        if segment:
            lines.append(segment)
    return lines
