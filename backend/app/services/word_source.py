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


# Windows symbol fonts (Wingdings and its relatives) map their whole repertoire into the
# Private Use Area, so a character drawn in one of these has no reading as language --- a
# "fillable" PDF draws a tick box as one of these glyphs rather than a bordered rectangle.
DINGBAT_FONTS = ("wingdings", "webdings", "zapfdingbats", "marlett")
# How much of a checkbox glyph's own area a later mark must cover before the box counts
# as ticked, and how much a mark and a glyph's rendered box must actually be found.
MARK_OVERLAP_SHARE = 0.3


class PdfMark(NamedTuple):
    """One checkbox read directly from a PDF's own text layer: where it is, and whether a
    mark was stamped on top of it."""

    x0: float
    y0: float
    x1: float
    y1: float
    ticked: bool


def pdf_marks(page) -> list[PdfMark]:
    """Checkbox state from vector text, not ink measured on a rendered page.

    A tick box drawn as a symbol-font glyph is exact and unambiguous where a rendered
    page is not: a filling tool stamps the user's mark as a separate text object -- on
    every example seen, a bold "X" -- in a different content block, sitting on top of the
    box rather than replacing it, so the state is read from *structure*, not from how dark
    a pixel happens to be.

    Detection is by **font family, never by codepoint**: a symbol font's glyph ids are
    reassigned whenever a PDF generator subsets the font, so the same visual checkbox can
    carry a different numeric character from one PDF to the next, and a rule keyed to one
    codepoint would really be a rule fitted to one generator. The family name is what
    survives subsetting.

    Returns nothing for a PDF whose checkboxes are drawn as rectangles rather than
    glyphs -- most forms in this project's corpus -- and rendered-page ink measurement is
    still how those are read.
    """
    chars: list[tuple[str, str, tuple[float, float, float, float], int]] = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                font = span["font"].lower()
                for ch in span["chars"]:
                    if ch["c"].strip():
                        chars.append((ch["c"], font, tuple(ch["bbox"]), id(block)))

    anchors = [c for c in chars if any(d in c[1] for d in DINGBAT_FONTS)]
    anchor_ids = {id(a) for a in anchors}
    others = [c for c in chars if id(c) not in anchor_ids]

    marks = []
    for _glyph, _font, box, block_id in anchors:
        ticked = any(
            other_block != block_id and _char_overlap(box, other_box) > MARK_OVERLAP_SHARE
            for _c, _f, other_box, other_block in others
        )
        marks.append(PdfMark(box[0], box[1], box[2], box[3], ticked))
    return marks


def _char_overlap(anchor: tuple, other: tuple) -> float:
    """Share of ``anchor``'s own area that ``other`` covers."""
    wide = min(anchor[2], other[2]) - max(anchor[0], other[0])
    tall = min(anchor[3], other[3]) - max(anchor[1], other[1])
    if wide <= 0 or tall <= 0:
        return 0.0
    area = max((anchor[2] - anchor[0]) * (anchor[3] - anchor[1]), 1e-9)
    return (wide * tall) / area


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
        return _drop_rule_artifacts(_strip_box_glyphs(_split_glued_labels(_words_from_rapidocr(image_path))))
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


# Tesseract is trained on roughly 300dpi text and degrades below it: on a 150dpi letter
# page it read "Daniel" as "Danie!" and "NC" as "Nc". Pages arrive at whatever resolution
# they were scanned or rendered at, so upscale short of this width before recognising and
# divide the coordinates back. Wider pages are left alone -- enlarging past 300dpi costs
# time and accuracy both.
OCR_TARGET_WIDTH = 2400
# A word Tesseract is this sure of counts when the two scales are compared. Counting every
# word would let a scale win by producing more noise.
MIN_LEGIBLE = 0.60
# How much more a native-resolution reading must find before it displaces the upscaled one.
NATIVE_MARGIN = 1.15


def _ocr_scale(width: int) -> int:
    """Integer upscale factor bringing a page up to about 300dpi, capped at 2x.

    Swept on the development set: 2x recovers characters that 150dpi loses, and 3x
    costs a field back again -- interpolating past the detail the scan actually holds
    invents edges for Tesseract to trip over.
    """
    if width <= 0:
        return 1
    return max(1, min(2, -(-OCR_TARGET_WIDTH // width)))


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

    original = Image.open(image_path)
    scale = _ocr_scale(original.width)

    def read(at: int) -> list[Word]:
        page: Image.Image = original
        if at > 1:
            page = original.resize((original.width * at, original.height * at), Image.LANCZOS)
        try:
            data = pytesseract.image_to_data(
                page, lang=language, output_type=pytesseract.Output.DICT
            )
        except pytesseract.TesseractNotFoundError as exc:
            raise HTTPException(
                503, "Image OCR requires the local Tesseract binary (brew install tesseract)"
            ) from exc
        found = []
        for i, text in enumerate(data["text"]):
            confidence = float(data["conf"][i])
            if not text.strip() or confidence < 0:
                continue
            left, top = float(data["left"][i]) / at, float(data["top"][i]) / at
            found.append(
                Word(
                    left,
                    top,
                    left + float(data["width"][i]) / at,
                    top + float(data["height"][i]) / at,
                    text,
                    int(data["block_num"][i]),
                    confidence / 100,
                )
            )
        return found

    # Read at both scales and keep whichever the page answered better, the same way
    # ``_best_layout`` runs both readers and keeps the winner rather than guessing in
    # advance which suits a page.
    #
    # Upscaling is meant to recover characters a 150dpi render loses, and across fourteen
    # real pages it is almost always a wash -- within five words either way. On one it was
    # a disaster: 288 confident words at native size against 209 upscaled, and the two it
    # dropped were the patient's first and last name, which the same page's PDF read
    # perfectly. Interpolation does not only add detail; it can smear a glyph into its
    # neighbour. Measuring beats predicting, and the cost is one more OCR pass on image
    # pages only.
    if scale == 1:
        words = read(1)
    else:
        upscaled, native = read(scale), read(1)

        def legible(reading: list[Word]) -> int:
            return sum(1 for z in reading if z.confidence >= MIN_LEGIBLE)

        # The upscale keeps its place unless reading natively is *decisively* better.
        # Simply taking whichever read more words optimises quantity over quality: on a
        # development page the native pass read a few more and rendered "Daniel" as
        # "Danie!", losing a field the upscale had right. The upscale was swept onto this
        # corpus deliberately and is usually a wash, so it stays the default and only
        # yields to a margin no ordinary page produces.
        words = native if legible(native) > legible(upscaled) * NATIVE_MARGIN else upscaled
    return _drop_rule_artifacts(_strip_box_glyphs(_split_glued_labels(words)))


RULE_RUN = re.compile(r"(_{3,})")
RULE_ONLY = re.compile(r"^_+$")
# OCR reads an empty tick box as bracket-ish glyphs, and they arrive glued to the option
# label beside it: "[_|Married", "[|Home", "[_]Decline".
BOX_GLYPHS = "[]|{}_"


def _strip_box_glyphs(words: list[Word]) -> list[Word]:
    """Take the drawn tick box off the front of the option it labels.

    The box is not text and never belongs in a value, but OCR cannot tell and hands it back
    attached to whatever it sits beside. Left in, it turns a two-letter answer into
    "[_|NC" and a caption into "[_]Decline". The box is measured properly elsewhere, by
    ink coverage, so nothing is lost by removing it here.
    """
    out: list[Word] = []
    for word in words:
        cleaned = word.text.strip(BOX_GLYPHS)
        if not cleaned:
            continue  # the glyphs were the whole token: a box, not a word
        if cleaned == word.text:
            out.append(word)
            continue
        # Trim the box off the geometry too, in proportion, so the word still sits where
        # its letters are rather than where the box began.
        lead = len(word.text) - len(word.text.lstrip(BOX_GLYPHS))
        span = word.x1 - word.x0
        start = word.x0 + span * lead / len(word.text)
        out.append(
            word._replace(x0=start, x1=start + span * len(cleaned) / len(word.text), text=cleaned)
        )
    return out


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
            # The rule itself is the form's own furniture, not anything a person wrote, so
            # the cursor advances across it but no word is emitted. Keeping it would leave
            # "____________ 08/25/2026" as the value of the field it underlines.
            if not RULE_ONLY.match(part):
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
# Characters a drawn rule is misread as. Low confidence only condemns a short read when
# the read is one of these: OCR is routinely unsure about a genuine two-character value
# ("04" scored 0.46 on a date, "PA" on a state line), and dropping those loses the answer.
RULE_GLYPHS = set("|iIl!-=~_7.,'\"`:;")


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
            unsure = word.confidence < MIN_ARTIFACT_CONFIDENCE and set(text) <= RULE_GLYPHS
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
                # Tried and reverted: generalising this to "the segment holds a label"
                # -- capitals, or a name the vocabulary knows, not only a colon -- so that
                # "Date        08/25/2026" would stop being severed from its own value.
                # It cost a field on the development set and two on the untuned one while
                # not even fixing its target, because the label it was aimed at ("Date")
                # is not a name the vocabulary knows either. The colon test stays until
                # something is measured to beat it.
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
