"""Geometric field detection for forms that draw input boxes.

The colon heuristic in ``form_analyzer`` only sees ``label: value`` on one line. Most
real intake forms instead draw a box and put its label above or to the left of it.
This module finds those boxes from the page's rule lines and binds each to its
nearest label, so the value is whatever OCR read *inside* the box.
"""

from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np

from app.services.word_source import Word, group_lines_tolerant


class Box(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


def detect_boxes(image_path: Path) -> list[Box]:
    """Find input boxes by isolating the page's horizontal and vertical rule lines.

    Returns only leaf boxes: any rectangle enclosing another is a section frame or the
    page border, not a field.
    """
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return []
    h, w = image.shape
    binary = cv2.adaptiveThreshold(
        image, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, 10
    )
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, w // 40), 1))
    )
    vertical = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, h // 40)))
    )
    grid = cv2.dilate(horizontal | vertical, np.ones((3, 3), np.uint8))

    found = []
    contours, _ = cv2.findContours(grid, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        # Too small to hold a value, or large enough to be the page border.
        if bw < w * 0.02 or bh < h * 0.005 or (bw > w * 0.95 and bh > h * 0.95):
            continue
        found.append(Box(float(x), float(y), float(x + bw), float(y + bh)))

    # A drawn rectangle yields two contours, its outer and inner edge, a pixel apart.
    # Collapse those before discarding containers, or each would eliminate the other.
    unique: list[Box] = []
    for candidate in sorted(found, key=lambda b: b.width * b.height, reverse=True):
        if not any(_iou(candidate, k) > 0.85 for k in unique):
            unique.append(candidate)
    return [b for b in unique if not any(_encloses(b, o) for o in unique if o is not b)]


# A tick fills a small box densely (~9% ink) but a signature is a thin stroke across a
# wide one (~0.8%), so the threshold sits below both while staying clear of scan speckle.
MARKED_INK_RATIO = 0.004


def ink_ratio(gray: np.ndarray, box: Box) -> float:
    """Fraction of dark pixels strictly inside a box, ignoring its own rule lines.

    Checkbox ticks and signatures carry no text for OCR to read, so presence is measured
    as ink instead. The inset must clear the border or every empty box reads as marked.
    """
    inset = max(3, int(min(box.width, box.height) * 0.2))
    y0, y1 = int(box.y0) + inset, int(box.y1) - inset
    x0, x1 = int(box.x0) + inset, int(box.x1) - inset
    if y1 <= y0 or x1 <= x0:
        return 0.0
    crop = gray[y0:y1, x0:x1]
    return float((crop < 128).mean()) if crop.size else 0.0


# A write-rule is long enough to hold an answer and thin enough not to be a box edge.
MIN_RULE_WIDTH_FRACTION = 0.04
WRITE_HEIGHT_FRACTION = 0.022


def detect_write_rules(image_path: Path, boxes: list[Box]) -> list[Box]:
    """Find ruled lines a person writes *on*, as the region just above each.

    Corpus forms draw a closed box per field, but real intake forms mostly print a rule
    ("Full Legal Name: ______"). Those carry no box for ``detect_boxes`` to find, so a
    real form yields almost no fields until the rules themselves are treated as
    answer regions.
    """
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return []
    h, w = image.shape
    binary = cv2.adaptiveThreshold(
        image, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, 10
    )
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, w // 30), 1))
    )

    write_height = max(12.0, h * WRITE_HEIGHT_FRACTION)
    found: list[Box] = []
    contours, _ = cv2.findContours(horizontal, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        if bw < w * MIN_RULE_WIDTH_FRACTION or bh > max(4, h // 300):
            continue
        # A box's own top or bottom edge is also a horizontal run; skip those or every
        # boxed field would be counted twice.
        if any(b.x0 - 4 <= x <= b.x1 + 4 and b.y0 - 4 <= y <= b.y1 + 4 for b in boxes):
            continue
        found.append(Box(float(x), float(max(0, y - write_height)), float(x + bw), float(y)))

    return [b for b in found if not any(_iou(b, k) > 0.5 for k in boxes)]


def _iou(a: Box, b: Box) -> float:
    overlap = _span(a.x0, a.x1, b.x0, b.x1) * _span(a.y0, a.y1, b.y0, b.y1)
    union = a.width * a.height + b.width * b.height - overlap
    return overlap / union if union > 0 else 0.0


def _encloses(outer: Box, inner: Box) -> bool:
    """True when ``outer`` is a genuine container: it covers ``inner`` and is materially
    larger, so a section frame is dropped but a field box is not."""
    return (
        outer.x0 <= inner.x0 + 2
        and outer.y0 <= inner.y0 + 2
        and outer.x1 >= inner.x1 - 2
        and outer.y1 >= inner.y1 - 2
        and inner.width * inner.height < outer.width * outer.height * 0.9
    )


def _centre_inside(word: Word, box: Box) -> bool:
    cx, cy = (word.x0 + word.x1) / 2, (word.y0 + word.y1) / 2
    return box.x0 <= cx <= box.x1 and box.y0 <= cy <= box.y1


def partition(boxes: list[Box], words: list[Word]) -> tuple[list[list[Word]], list[Word]]:
    """Split words into each box's contents and the free words outside every box.

    Free words are the label candidates; box contents are the values.
    """
    contents: list[list[Word]] = [[] for _ in boxes]
    free: list[Word] = []
    for word in words:
        for i, box in enumerate(boxes):
            if _centre_inside(word, box):
                contents[i].append(word)
                break
        else:
            free.append(word)
    # Flatten each box back into reading order; a plain y-then-x sort interleaves the
    # rows of a multi-line value because OCR baselines within a row are not identical.
    return [[w for line in group_lines_tolerant(c) for w in line] for c in contents], free


def _span(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def label_for_box(
    box: Box, candidates: list[list[Word]], left_only: bool = False
) -> list[Word] | None:
    """Pick the label line nearest to a box, preferring one directly above it.

    A label must overlap the box on the perpendicular axis, so a caption belonging to a
    neighbouring column is not stolen. Search range scales with box height to stay
    resolution-independent.
    """
    reach = max(box.height * 2.5, 40.0)
    best: tuple[float, list[Word]] | None = None
    for line in candidates:
        x0, y0 = min(w.x0 for w in line), min(w.y0 for w in line)
        x1, y1 = max(w.x1 for w in line), max(w.y1 for w in line)
        if not left_only and y1 <= box.y0 + 2 and _span(x0, x1, box.x0, box.x1) > 0:
            distance = box.y0 - y1  # above
        elif x1 <= box.x0 + 2 and _span(y0, y1, box.y0, box.y1) > 0:
            distance = (box.x0 - x1) * (1.0 if left_only else 1.5)
        else:
            continue
        if distance <= reach and (best is None or distance < best[0]):
            best = (distance, line)
    return best[1] if best else None
