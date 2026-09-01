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
WRITE_HEIGHT_FRACTION = 0.026


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


# How much of its bounding rectangle a genuine box outline fills.
BORDER_COVERAGE = 0.7


# A tick box is square within tolerance and about the height of a line of text. Anything
# squarer-but-larger is a framed section; anything smaller is scan speckle.
CHECKBOX_ASPECT = 1.5
CHECKBOX_MIN_SIDE = 0.006
CHECKBOX_MAX_SIDE = 0.030


def detect_checkboxes(image_path: Path, words: list[Word] | None = None) -> list[Box]:
    """Find tick boxes, which ``detect_boxes`` deliberately discards.

    That function drops anything under 2% of the page width, because a region that small
    cannot hold a written answer. A tick box is about 1%, so it is always dropped there.

    Detection is by polygon approximation rather than the morphological pass used for
    answer regions: a tick box is a small convex quadrilateral, and asking for that shape
    directly separates it from the letter strokes of the same size around it. Opening with
    a kernel short enough to preserve a box that small also preserves glyph strokes, which
    made a page of text read as hundreds of checkboxes.
    """
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return []
    h, w = image.shape
    small = min(w, h)
    binary = cv2.adaptiveThreshold(
        image, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, 10
    )
    found: list[Box] = []
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        approx = cv2.approxPolyDP(contour, 0.04 * cv2.arcLength(contour, True), True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        x, y, bw, bh = cv2.boundingRect(approx)
        if bw <= 0 or bh <= 0:
            continue
        aspect = max(bw / bh, bh / bw)
        side = max(bw, bh) / small
        if not (aspect <= CHECKBOX_ASPECT and CHECKBOX_MIN_SIDE <= side <= CHECKBOX_MAX_SIDE):
            continue
        # A drawn box traces a true rectangle; a glyph that happens to have four corners
        # fills its bounding rect poorly.
        if cv2.contourArea(approx) / (bw * bh) < BORDER_COVERAGE:
            continue
        found.append(Box(float(x), float(y), float(x + bw), float(y + bh)))

    # The decisive test: a tick box is drawn in the form's rule ink, so it is not part of
    # any word OCR read. A character that happens to approximate a quadrilateral -- the
    # 0 and 4 in "1420" -- sits inside a recognised multi-character word, and dropping
    # those needs no assumption about how a particular form spaces its options. A box that
    # OCR mistook for a lone glyph keeps its place, since that reading is one character.
    if words is not None:
        text_runs = [w for w in words if len(_alphanumeric(w.text)) >= 2]
        found = [
            b
            for b in found
            if not any(_iou(b, Box(t.x0, t.y0, t.x1, t.y1)) > 0.3 for t in text_runs)
        ]

    # A drawn box yields an inner and an outer contour a pixel or two apart.
    unique: list[Box] = []
    for candidate in sorted(found, key=lambda b: b.width * b.height, reverse=True):
        if not any(_iou(candidate, k) > 0.5 for k in unique):
            unique.append(candidate)
    return unique


def _alphanumeric(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum())


def captions_for_row(
    row: list[Box], words: list[Word], checkboxes: list[Box]
) -> list[Word] | None:
    """Name every box in one option row.

    Forms disagree about which side the caption is printed on: "Single [ ] Married [ ]"
    puts it left, "[ ] Male [ ] Female" right, and a row with its captions underneath puts
    it below. Picking the nearest word per box gets the middle of a row wrong, because a
    neighbouring option's caption is often nearer than the box's own.

    Resolved as a matching instead: a word names at most one box, and the closest pairs are
    settled first. That needs no decision about the form's convention -- with each word
    spoken for, the only consistent reading is the correct one. Returns ``None`` if any box
    goes unnamed.
    """
    reach = max(b.height for b in row) * 4
    pairs = []
    for i, box in enumerate(row):
        for word in words:
            if any(_centre_inside(word, other) for other in checkboxes):
                continue
            if _span(word.y0, word.y1, box.y0, box.y1) > 0:
                if word.x0 >= box.x1:
                    distance = word.x0 - box.x1
                elif word.x1 <= box.x0:
                    distance = box.x0 - word.x1
                else:
                    continue
            elif 0 <= word.y0 - box.y1 <= reach and _span(word.x0, word.x1, box.x0, box.x1) > 0:
                distance = word.y0 - box.y1
            else:
                continue
            if distance <= reach:
                pairs.append((distance, i, word))

    names: dict[int, Word] = {}
    taken: set[int] = set()
    for _, i, word in sorted(pairs, key=lambda p: p[0]):
        key = id(word)
        if i in names or key in taken:
            continue
        names[i] = word
        taken.add(key)
    return [names[i] for i in range(len(row))] if len(names) == len(row) else None


# A caption sits within about this much of the region it describes, as a share of the
# region's own height.
CAPTION_REACH = 1.2


def _is_caption(text: str) -> bool:
    """Whether a word can be a sub-caption rather than a label or an answer.

    The band beneath a row also contains the next row's label and sometimes its value, and
    the three are told apart by what they are made of: a row label carries the colon that
    introduces its answer, and an answer usually carries digits. A printed sub-caption --
    "STREET", "CITY", "ZIP CODE", "Last", "First" -- has neither.
    """
    stripped = text.strip()
    return (
        any(c.isalpha() for c in stripped)
        and ":" not in stripped
        and not any(c.isdigit() for c in stripped)
        # A ruled line OCRs as a run of underscores and carries no letters, but a form
        # built from rules has one under every field, so this must be explicit.
        and "_" not in stripped
    )


def captions_below(box: Box, words: list[Word]) -> list[list[Word]]:
    """Sub-captions printed underneath an answer space, left to right.

    A city/state/zip line carries no label of its own: the row is named by "CITY",
    "STATE" and "ZIP CODE" printed beneath it. Those words are searched for among *all*
    words rather than the free ones, because a caption row often falls inside the region
    of the rule below it and would otherwise be invisible as a label.
    """
    reach = box.height * CAPTION_REACH
    below = [
        w
        for w in words
        if box.y1 <= w.y0 <= box.y1 + reach
        and _span(w.x0, w.x1, box.x0, box.x1) > 0
        and _is_caption(w.text)
    ]
    if not below:
        return []
    groups: list[list[Word]] = []
    for word in sorted(below, key=lambda w: w.x0):
        gap = word.x0 - groups[-1][-1].x1 if groups else None
        if groups and gap is not None and gap < word.y1 - word.y0:
            groups[-1].append(word)
        else:
            groups.append([word])
    return groups


def split_by_captions(
    box: Box, captions: list[list[Word]], inside: list[Word] | None = None
) -> list[Box]:
    """Cut a region into one column per caption.

    Captions are centred under their column while a written value is left-aligned in it,
    so cutting between caption centres puts every boundary too far right and each value
    lands one column to the left of its own caption. Where the region's own content falls
    into as many clusters as there are captions, the gaps between those clusters are the
    real boundaries and are used instead; the captions only supply the names.
    """
    groups = clusters(inside or [])
    if len(groups) == len(captions):
        edges = [box.x0]
        edges += [
            (max(w.x1 for w in groups[i]) + min(w.x0 for w in groups[i + 1])) / 2
            for i in range(len(groups) - 1)
        ]
    else:
        centres = [(min(w.x0 for w in c) + max(w.x1 for w in c)) / 2 for c in captions]
        edges = [box.x0]
        edges += [(centres[i] + centres[i + 1]) / 2 for i in range(len(centres) - 1)]
    edges.append(box.x1)
    return [Box(edges[i], box.y0, edges[i + 1], box.y1) for i in range(len(captions))]


def clusters(words: list[Word]) -> list[list[Word]]:
    """Group words left to right, breaking where the gap exceeds a word's own height."""
    groups: list[list[Word]] = []
    for word in sorted(words, key=lambda w: w.x0):
        if groups and word.x0 - groups[-1][-1].x1 < word.y1 - word.y0:
            groups[-1].append(word)
        else:
            groups.append([word])
    return groups


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
