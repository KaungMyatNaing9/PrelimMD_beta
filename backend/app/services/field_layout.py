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
    grid = cv2.dilate(cv2.bitwise_or(horizontal, vertical), np.ones((3, 3), np.uint8))

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
# Above this share of dark pixels a candidate is a solid shape, not a box with something
# drawn in it. A ticked box reads about 0.39, an empty one about 0.15.
MAX_BOX_INK = 0.85
# How much wider than a tick box a text run must be before it may veto that box. The veto
# exists to remove candidates that are really a stray glyph inside a *longer* word, so
# "longer" has to be tested, not assumed -- see the veto's own note in detect_checkboxes.
VETO_WIDTH_RATIO = 1.5
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
        # A tick box is an outline: empty inside, or carrying a mark that still leaves
        # most of the interior white. A solid square is something else -- the two filled
        # squares in a clinic's logo were being read as an option pair, and because they
        # are solid they then read as *ticked*. Excluding them by ink is what keeps that
        # from having to be absorbed by the mark threshold instead.
        if ink_ratio(image, Box(float(x), float(y), float(x + bw), float(y + bh))) > MAX_BOX_INK:
            continue
        found.append(Box(float(x), float(y), float(x + bw), float(y + bh)))

    # OCR gets a veto only over candidates that coincide with something clearly *longer*
    # than a tick box.
    #
    # Tesseract cannot read a ballot box -- U+2610 and U+2612 are absent from its training
    # data, a known and unfixed limitation -- so it renders an empty box as whatever pair
    # of letters the outline resembles: "LJ", "Cl", "CJ", "Qd", "CI". Those are two
    # characters occupying exactly the box's own bounds, so a two-character threshold
    # vetoed every real tick box on a form OCR happened to read that way: 42 detected,
    # 0 surviving. Optical mark recognition is deliberately independent of character
    # recognition for this reason -- geometry finds the box, ink reads the mark.
    #
    # "Coincide" has to mean containment, not IoU. A stray glyph inside a longer word --
    # the ":" in "DOB:", the "0" in "1420" -- is small next to the word's own box, and
    # IoU divides by their *union*, which the word dominates; a 130px candidate inside a
    # 660px word scores an IoU of about 0.2, below any threshold that would not also
    # reject two boxes that merely sit close together. The share of the *candidate's own*
    # area the word covers does not have this problem: near 1.0 for a glyph swallowed by
    # a real word, near 0 for two boxes that just happen to be near each other. On one
    # real form this distinction was the difference between 8 false checkboxes surviving
    # into the page's option rows and none.
    #
    # The run also has to be *longer* than the box, which is what this paragraph always
    # claimed and never tested. A pen X drawn inside a real tick box is unreadable to
    # Tesseract too, so it comes back as a garbage run of letters -- "Wee", "ely" -- sized
    # to the box it was drawn in, not to a word. Containment alone cannot tell that from a
    # genuine word, so on a handwritten page the veto removed precisely the boxes that
    # carried marks: the Sex row lost the one box a patient had ticked and kept Male and
    # Other. Requiring the run to be meaningfully wider keeps the case the veto was built
    # for -- ":" inside "DOB:", a digit inside "1420" -- and drops the case it was not.
    if words is not None:
        text_runs = [w for w in words if len(_alphanumeric(w.text)) >= 3]
        found = [
            b
            for b in found
            if not any(
                _contained_share(b, Box(t.x0, t.y0, t.x1, t.y1)) > 0.6
                and (t.x1 - t.x0) >= (b.x1 - b.x0) * VETO_WIDTH_RATIO
                for t in text_runs
            )
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
    if len(names) != len(row):
        return None
    # A caption is sometimes more than one word -- "Full Time", "Legally Separated" -- and
    # this only ever returns the single nearest one, so a caption like that reports just
    # its first word. Widening the seed into its full phrase was tried and reverted: on a
    # row packed with several questions ("Visually Impaired: Yes No  Hearing Impaired: Yes
    # No") it had no reliable place to stop, and pulled a field's own label into its
    # caption on one row while pulling a neighbouring field's text into another. The
    # single-word result is incomplete but never wrong; the widened one was sometimes both.
    return [names[i] for i in range(len(row))]


def _iou(a: Box, b: Box) -> float:
    overlap = _span(a.x0, a.x1, b.x0, b.x1) * _span(a.y0, a.y1, b.y0, b.y1)
    union = a.width * a.height + b.width * b.height - overlap
    return overlap / union if union > 0 else 0.0


def _contained_share(inner: Box, outer: Box) -> float:
    """Share of ``inner``'s own area that ``outer`` covers."""
    overlap = _span(inner.x0, inner.x1, outer.x0, outer.x1) * _span(inner.y0, inner.y1, outer.y0, outer.y1)
    area = max(inner.width * inner.height, 1e-9)
    return overlap / area


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
        y0, y1 = min(w.y0 for w in line), max(w.y1 for w in line)
        if not left_only and y1 <= box.y0 + 2:
            run = _run_over(line, box)
            if run is None:
                continue
            distance = box.y0 - y1  # above
        else:
            if _span(y0, y1, box.y0, box.y1) <= 0:
                continue
            run = _run_left_of(line, box)
            if run is None:
                continue
            distance = (box.x0 - max(w.x1 for w in run)) * (1.0 if left_only else 1.5)
        if distance <= reach and (best is None or distance < best[0]):
            best = (distance, run)
    return best[1] if best else None


def _run_left_of(box_line: list[Word], box: Box) -> list[Word] | None:
    """The label run nearest the box among the words of a line that sit left of it.

    A form row carries more than one label -- "Date of Birth: ____   Sex: ____" is one
    OCR line -- so measuring the whole line's right edge against the box rejects the line
    whenever the label wanted is not its last. That is how a date of birth went missing
    the moment OCR started reading the "Sex:" beside it well enough to keep.
    """
    left = [w for w in box_line if w.x1 <= box.x0 + 2]
    if not left:
        return None
    return _whole_or_run(box_line, split_runs(left)[-1])


def _run_over(box_line: list[Word], box: Box) -> list[Word] | None:
    """The run of a line sitting above the box that overlaps it horizontally."""
    best: tuple[float, list[Word]] | None = None
    for run in split_runs(box_line):
        overlap = _span(min(w.x0 for w in run), max(w.x1 for w in run), box.x0, box.x1)
        if overlap > 0 and (best is None or overlap > best[0]):
            best = (overlap, run)
    return _whole_or_run(box_line, best[1]) if best else None


def _whole_or_run(box_line: list[Word], run: list[Word]) -> list[Word]:
    """The original line object when the run is all of it, else the run.

    Callers mark a line consumed by its identity, so a run covering the whole line has
    to come back as that same list rather than a copy of it.
    """
    return box_line if len(run) == len(box_line) else run


# A caption printed under a rule is set close beneath it. The reach is a fraction of the
# rule's own writable height so it scales with page resolution.
CAPTION_REACH = 0.7
# Two captions head separate columns when this many median word heights separate them.
# Below it they are one caption: "ZIP CODE", "Phone Number", "APT #".
CAPTION_GAP_RATIO = 1.2


def columns_beneath(box: Box, words: list[Word]) -> list[tuple[list[Word], float, float]]:
    """The columns a rule is divided into by the captions printed under it.

    A row like

        _______________________________________
            CITY            STATE     ZIP CODE

    carries no label to its left. The captions beneath *are* the field names, and the
    wall between two columns is the midpoint between neighbouring caption centres --
    captions are set centred under their column, so the midpoint is the only wall the
    form actually draws.

    This is the right reading only when the rule has no label of its own. On
    ``Address: ______`` captioned ``STREET   APT #`` the left label names one field and
    the captions describe its parts, so splitting there would invent two fields and
    destroy the address. This function reports geometry; the caller decides.

    Captions are searched among *all* words rather than the free label candidates,
    because a caption printed under one rule falls inside the writable area of the next
    rule down, and has normally been claimed as that rule's contents already.
    """
    band_top = box.y1
    band_bottom = box.y1 + box.height * CAPTION_REACH
    inside = [
        w
        for w in words
        if band_top <= (w.y0 + w.y1) / 2 <= band_bottom
        and w.x0 >= box.x0 - 2
        and w.x1 <= box.x1 + 2
    ]
    if len(inside) < 2:
        return []
    inside.sort(key=lambda w: w.x0)
    em = sorted(w.y1 - w.y0 for w in inside)[len(inside) // 2]
    groups: list[list[Word]] = [[inside[0]]]
    for word in inside[1:]:
        if word.x0 - groups[-1][-1].x1 > em * CAPTION_GAP_RATIO:
            groups.append([word])
        else:
            groups[-1].append(word)
    if len(groups) < 2:
        return []
    centres = [(min(z.x0 for z in g) + max(z.x1 for z in g)) / 2 for g in groups]
    columns = []
    for i, group in enumerate(groups):
        left = box.x0 if i == 0 else (centres[i - 1] + centres[i]) / 2
        right = box.x1 if i == len(groups) - 1 else (centres[i] + centres[i + 1]) / 2
        columns.append((group, left, right))
    return columns


def split_runs(words: list[Word]) -> list[list[Word]]:
    """Split a row into the runs of words separated by wide horizontal gaps.

    Inside one cell the words of a value sit a space apart; between two cells the
    unfilled remainder of the rule leaves a gap several times that. This is the same
    tell ``word_source.label_run_start`` uses, for the same reason -- a value and a
    caption cannot be told apart by what the words look like.
    """
    if not words:
        return []
    ordered = sorted(words, key=lambda z: z.x0)
    em = sorted(z.y1 - z.y0 for z in ordered)[len(ordered) // 2]
    runs: list[list[Word]] = [[ordered[0]]]
    for word in ordered[1:]:
        if word.x0 - runs[-1][-1].x1 > em * CAPTION_GAP_RATIO:
            runs.append([word])
        else:
            runs[-1].append(word)
    return runs
