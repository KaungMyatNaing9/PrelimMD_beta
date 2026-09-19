"""Fill stored values onto a blank template and emit a machine-filled PDF.

Placement uses the template's own recorded field coordinates rather than the boxes
extraction inferred from the upload: the template numbers are exact, so a regenerated
value lands where the blank form actually puts it, independent of capture quality.
"""

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.schemas.models import BoundingBox, FieldType, TemplateDefinition
from app.services import pdf_backend
from app.services.canonical_registry import CORPUS_KEY_ALIASES
from app.services.field_layout import Box, detect_checkboxes
from app.services.templates import canonical_image_path

# Checkbox glyph drawn when a value is truthy; signature fields are presence-only and are
# never synthesised, per the work plan's "presence and location, not recognition".
TICK = "X"


def _text_for(value: Any, field_type: FieldType | str) -> str | None:
    kind = field_type.value if isinstance(field_type, FieldType) else str(field_type)
    if value is None or value == "":
        return None
    # A yes/no answer is a mark, whatever slot it landed in: a boolean drawn as text put
    # the word "False" on a regenerated form.
    if isinstance(value, bool):
        return TICK if value else None
    if kind == "checkbox":
        return TICK if value in ("true", "True", 1) else None
    if kind == "signature_presence":
        return None
    return _drawable(str(value)) or None


def _drawable(text: str) -> str:
    """``text`` with every character the face cannot draw taken out of it.

    The renderer does not refuse a character it has no glyph for: it puts another one in
    its place. A reader that took a form's own empty box into a value
    ("...@example.com \u25a2") therefore had the copy print a letter that was never on the
    original, which is the one thing a copy must not do. What survives is what a base font
    can set, which is everything these forms are written in.
    """
    kept = []
    for character in text:
        try:
            character.encode("cp1252")
        except UnicodeEncodeError:
            continue
        kept.append(character)
    return " ".join("".join(kept).split())


# Safety margin above the font's own descent, as a fraction of the point size. The glyph
# box the renderer reports is a little deeper than the nominal descender, and without this
# a value sits a fraction of a point past the foot of its own field.
DESCENDER_MARGIN = 0.06


def _descent(fontname: str) -> float:
    """How far below the baseline this face reaches, as a fraction of its point size."""
    return pdf_backend.font_descender(fontname) + DESCENDER_MARGIN


# Regenerated values are drawn in an upright serif and in green. The benchmark's fills
# are navy Helvetica, and a regenerated page in the same lettering could not be told from
# a page whose values were simply carried over from the original (user, 2026-09-16).
# Slanted Helvetica was tried first: OCR misread its glyphs ("Apt 3B" -> "AptSB",
# "Daniel" -> "Danie!") and the round trip lost a value. Sizes and indents are measured in
# Helvetica, so each value is set at the size that gives it Helvetica's width there
# (``MEASURED_FACE``), which keeps its span -- and its placement -- where it was.
FONT = "tiro"
MEASURED_FACE = "helv"
INK = (0.0, 0.42, 0.22)
# A value starts a little in from where its rule does, rather than hard against it. Scaled
# to the text, not a fixed number of page units: a derived template's page is measured in
# the pixels of the render it was learned from, so a constant "2" meant 2px there and 2pt
# on a template stored in points -- a third of a millimetre in one and over one in the
# other, which showed up as a systematic leftward bias on exactly one of the two.
INDENT_EM = 0.35


def _indent(field, size: float, page_width: float) -> float:
    """Where in the region to start the text: as measured, or a small default."""
    if field.text_indent is not None:
        return field.text_indent * page_width
    return size * INDENT_EM


def _width_matched(text: str, size: float) -> float:
    """The size at which ``text`` in ``FONT`` is as wide as in ``MEASURED_FACE`` at ``size``."""
    drawn = pdf_backend.text_length(text, FONT, 1.0)
    if drawn <= 0:
        return size
    return size * pdf_backend.text_length(text, MEASURED_FACE, 1.0) / drawn


# A letter page is 612 points across. A template learned from a render is measured in that
# render's pixels, twice as dense, so the fallback's bounds are scaled by the page's own
# width: a fixed 6-12 read as 3-6 points on a 2x render and printed values nobody could
# read comfortably (2026-09-16).
LETTER_WIDTH_PT = 612.0


def _font_size(height_pt: float, learned: float | None = None, page_width: float = LETTER_WIDTH_PT) -> float:
    """The size to redraw a value at: the one the form was filled at, where that is known.

    Deriving it from the *box* is a guess, and on a ruled field the box is the whole
    writable band, so the guess saturated at the 12pt ceiling on a form actually filled
    at 11pt. A run drawn 9% too wide carries its own centre half that excess to the
    right, which is why the placement error grew with the length of the value -- 2mm on
    a zip code, 14mm on an email address. The box-derived guess remains the fallback for
    a template learned from a blank form, where there is no filled text to measure.
    """
    if learned:
        return learned
    scale = page_width / LETTER_WIDTH_PT
    return max(6.0 * scale, min(12.0 * scale, height_pt * 0.55))


def unplaced(template: TemplateDefinition, values: dict[str, Any]) -> list[str]:
    """Values that were read but have nowhere on this template to go.

    A template only carries the fields its form was seen to have. Where a form combines
    two in one caption -- "City/State" is one region on a real registration form -- the
    slot is named after the pair and no single canonical value matches it, so a correctly
    read city is dropped on the way out. It was dropped *silently*, which is the part worth
    fixing: everywhere else this system reports what it could not do rather than quietly
    doing less, and a regenerated form missing a value a person can see on the original is
    exactly the kind of thing that has to be said out loud.
    """
    slots = {
        CORPUS_KEY_ALIASES.get(f.field_id, f.field_id)
        for page in template.pages
        for f in page.fields
    }
    # The same value sits under its canonical key and under one made from its printed
    # caption, and only the canonical one matches a slot. Reported by key alone, a city
    # drawn on the copy was announced as missing from it.
    placed = {
        _text_for(value, FieldType.SHORT_TEXT) for key, value in values.items() if key in slots
    }
    missing: dict[str, str] = {}
    for key, value in values.items():
        # A yes/no answer is a mark: it is drawn from the boxes the reader says were
        # ticked, not from a slot, so it is not a value with nowhere to go.
        if key in slots or isinstance(value, bool):
            continue
        text = _text_for(value, FieldType.SHORT_TEXT)
        if text in placed:
            continue
        # "( )" is an empty phone rule, not an answer. Nothing was lost by not placing it,
        # and listing it would bury the values that were.
        if text is None or not any(c.isalnum() for c in text):
            continue
        # ``values_from_analysis`` files each value under both its canonical key and one
        # derived from its printed label, so an unplaced value would otherwise be named
        # twice. The canonical key is the one worth reporting.
        seen = missing.get(text)
        if seen is None or ("." in key and "." not in seen):
            missing[text] = key
    return sorted(set(missing.values()))


def regenerate(
    template: TemplateDefinition,
    values: dict[str, Any],
    ticks: Mapping[int, Sequence[BoundingBox]] | Sequence[BoundingBox] = (),
    loose: Mapping[int, Sequence[tuple[BoundingBox, str, set[str]]]]
    | Sequence[tuple[BoundingBox, str, set[str]]] = (),
) -> bytes:
    """Render ``values`` onto the blank masters for ``template`` and return PDF bytes.

    Every page of the form, because a form is usually a packet: on the benchmark's 109
    templates, 2772 of 5730 slots are on a page after the first, and a copy of only the
    first page left every one of those answers off without a word (2026-09-18).

    ``values`` is keyed by canonical field key; template fields carry the corpus key, so
    they are translated through CORPUS_KEY_ALIASES on the way in. Values with no slot are
    not rendered; ``unplaced`` names them so a caller can say so.

    ``ticks`` are the boxes a patient ticked, as page fractions (``DetectedFormField.
    ticked_boxes``); each is marked with an X in the box the blank prints there. ``loose``
    are values with no slot, drawn where they were read (``loose_values``). Both are given
    per page, keyed by page index; a plain run of either is taken to be the first page's.
    """
    pages = []
    for page_def in template.pages:
        blank = canonical_image_path(template.template_id, page_def.page_index)
        if not blank.exists():
            if page_def.page_index == 0:
                raise FileNotFoundError(f"No blank master for template {template.template_id}")
            # A packet learned before a later page had a master of its own: the pages that
            # do have one are still worth regenerating.
            continue
        width, height = float(page_def.canonical_width), float(page_def.canonical_height)
        index = page_def.page_index
        texts, boxes = plan(template, values, index, _for_page(loose, index))
        texts += _tick_marks(blank, _for_page(ticks, index), width, height)
        pages.append(pdf_backend.PageDraw(blank, width, height, texts, boxes))
    if not pages:
        raise FileNotFoundError(f"No blank master for template {template.template_id}")
    return pdf_backend.compose_pages(pages, fontname=FONT, color=INK)


def _for_page(per_page, index: int):
    """What was given for one page: a mapping by page index, or a run meaning the first."""
    if isinstance(per_page, Mapping):
        return per_page.get(index, ())
    return per_page if index == 0 else ()


def _page(template: TemplateDefinition, page_index: int):
    """The template page with this index, which need not be the one at that position."""
    return next(
        (page for page in template.pages if page.page_index == page_index),
        template.pages[page_index],
    )


def plan(
    template: TemplateDefinition,
    values: dict[str, Any],
    page_index: int = 0,
    loose: Sequence[tuple[BoundingBox, str, set[str]]] = (),
) -> tuple[list[pdf_backend.Text], list[pdf_backend.TextBox]]:
    """Exactly what regeneration will write on a page, and where.

    Separate from the drawing so that what was written can be checked against what was
    asked for: a copy that quietly leaves a value off, or writes one twice, is the kind of
    mistake nobody sees (`scripts/evaluate_regeneration.py`).
    """
    page_def = _page(template, page_index)
    width, height = float(page_def.canonical_width), float(page_def.canonical_height)
    texts: list[pdf_backend.Text] = []
    boxes: list[pdf_backend.TextBox] = []
    # What a value has to keep off: everything the form prints, and then every value
    # already written on this page.
    spans = [
        (sx0 * width, sy0 * height, sx1 * width, sy1 * height)
        for sx0, sy0, sx1, sy1 in page_def.printed_spans
    ]
    for field in page_def.fields:
        canonical = CORPUS_KEY_ALIASES.get(field.field_id, field.field_id)
        text = _text_for(values.get(canonical), field.field_type)
        if text is None:
            continue
        box = field.bbox
        x0, y0 = box.x * width, box.y * height
        x1, y1 = (box.x + box.width) * width, (box.y + box.height) * height
        size = _font_size(y1 - y0, (field.font_size or 0) * height or None, width)
        if "\n" in text:
            # A multi-line value needs the whole region and grows downwards from its top.
            boxes.append(pdf_backend.TextBox(x0 + 2, y0 + 1, x1 - 2, y1 - 1, text, size))
            continue
        # A person writes *on* the rule at the foot of the space, not floating at its top,
        # but a text box lays its first line from the top edge. Filling the whole region
        # therefore printed every value about 4.5mm high -- a constant offset, measured
        # identically on every correctly placed field. Setting the baseline directly puts
        # the value back on the rule. Shrinking the box instead silently renders nothing
        # when the text no longer fits.
        start, baseline, drawn = _place(
            text,
            _width_matched(text, size),
            x0 + _indent(field, size, width),
            y1,
            (x0, y0, x1, y1),
            spans,
            width,
        )
        texts.append(pdf_backend.Text(start, baseline, text, drawn))
        spans.append(_written(texts[-1]))
    learned = next((f.font_size for f in page_def.fields if f.font_size), None)
    for bbox, text, _ in loose:
        x0, y0 = bbox.x * width, bbox.y * height
        x1, y1 = (bbox.x + bbox.width) * width, (bbox.y + bbox.height) * height
        # The page's fill size where it was measured; otherwise the read words' own height,
        # which runs from ascender to descender and is a little more than their size.
        floor = 6.0 * width / LETTER_WIDTH_PT
        size = learned * height if learned else max((y1 - y0) * LOOSE_SIZE, floor)
        start, baseline, drawn = _place(
            text, _width_matched(text, size), x0, y1, (x0, y0, x1, y1), spans, width
        )
        texts.append(pdf_backend.Text(start, baseline, text, drawn))
        spans.append(_written(texts[-1]))
    return texts, boxes


def _written(text: pdf_backend.Text) -> tuple[float, float, float, float]:
    """The box a drawn value fills, so the next one keeps off it.

    Two answers written up against each other read back as one word -- an email address
    and the phone number after it as "...@example.com509-555-0186" -- which is a copy
    saying something the original did not just as much as a value over a caption is.
    """
    return (
        text.x,
        text.baseline - pdf_backend.font_ascender(FONT) * text.size,
        text.x + pdf_backend.text_length(text.text, FONT, text.size),
        text.baseline + _descent(FONT) * text.size,
    )


# A value is never written over what the form prints. A blank's answer region can reach
# across the caption of the row below it, or be drawn around a caption rather than the
# space after it, and a value set from the region's left edge then covered the form's own
# words: a phone number across "Cell Phone", a name across "In case of emergency, please
# contact:" -- 126 values on 47 of the benchmark's 107 forms (2026-09-18). A value that
# would cover printing moves: along its line, past whatever is printed there, a word at a time; then
# to a clear line inside its own region; then set smaller. Only if nothing anywhere is
# clear is it drawn where it was measured, since a value slightly out of place still
# beats one nobody can read.
#
# The gap kept after printing a value follows and the spacing between lines, both as
# fractions of the size; and how many lines up the region is searched.
PRINT_GAP = 0.3
LINE_STEP = 1.15
LINES_UP = 3
# How many times a value may step right past the printing it lands on: a caption is
# several words, and each step clears one word of it.
MOVES = 5
# What a value is set at, as fractions of the size it was measured at, when the space it
# has is printed in. A region between two printed lines can be shallower than the value
# the form was filled at, and half-size and in the clear reads better than full size
# across a caption.
SMALLER = (1.0, 0.8, 0.65, 0.5)
# How near a printed word a value may come beside it, and how much of its height a value
# has to reach across to be covering it rather than passing under it: lines of print
# nearly touch, and a value on its rule grazes the caption above without hiding a
# character of it.
TOUCH_X = 0.12
TOUCH_Y = 0.35


def _covers(x: float, baseline: float, size: float, text: str, spans: Sequence) -> list:
    """The printed words this value would be written across."""
    x1 = x + pdf_backend.text_length(text, FONT, size)
    # The whole box a reader will see the value in, which is the face's own ascent and
    # descent, not the height of the characters this value happens to use.
    top, bottom = baseline - pdf_backend.font_ascender(FONT) * size, baseline + _descent(FONT) * size
    hit = []
    for sx0, sy0, sx1, sy1 in spans:
        across = min(x1, sx1) - max(x, sx0)
        down = min(bottom, sy1) - max(top, sy0)
        if across > -TOUCH_X * size and down > TOUCH_Y * max(sy1 - sy0, 1e-6):
            hit.append((sx0, sy0, sx1, sy1))
    return hit


def _place(
    text: str,
    size: float,
    x: float,
    bottom: float,
    region: tuple[float, float, float, float],
    spans: Sequence,
    width: float,
) -> tuple[float, float, float]:
    """Where to set ``text``: as measured, where that is clear of the form's printing."""
    _, ry0, _, _ = region
    best: tuple[int, float, float, float] | None = None
    for step in range(LINES_UP if spans else 1):
        baseline = bottom - step * LINE_STEP * size - _descent(FONT) * size
        if step and baseline - pdf_backend.font_ascender(FONT) * size < ry0:
            break
        for shrink in SMALLER:
            at = x
            for _ in range(MOVES):
                drawn = _fits(text, size * shrink, width - at)
                over = _covers(at, baseline, drawn, text, spans)
                if not over:
                    return at, baseline, drawn
                if best is None or len(over) < best[0]:
                    best = (len(over), at, baseline, drawn)
                past = max(span[2] for span in over) + PRINT_GAP * drawn
                # Past the region's own right edge if that is what it takes. A region
                # whose whole width is printed in ("In case of emergency, please
                # contact:") is a region drawn around the caption rather than the space
                # after it, and the answer goes after the printing, which is where the
                # patient wrote it.
                if past <= at or past >= width - EDGE_MARGIN:
                    break
                at = past
    assert best is not None
    return best[1], best[2], best[3]


# The smallest a value is ever set at, in points on a letter page, and the margin kept
# between the end of a value and the edge of the paper.
MIN_DRAWN = 5.0
EDGE_MARGIN = 4.0


def _fits(text: str, size: float, room: float) -> float:
    """The size that keeps a value on the page: a long one is set smaller, never cut.

    A value set at the size the form was filled at can be longer than the paper -- an email
    address a reader ran into the caption after it -- and the characters past the edge were
    simply lost from the copy ("Reference Librarian" arriving as "Reference Librari").
    """
    span = pdf_backend.text_length(text, FONT, size)
    room -= EDGE_MARGIN
    if span <= room or room <= 0:
        return size
    return max(size * room / span, MIN_DRAWN)


# A word box is about this much taller than the size its text was set at.
LOOSE_SIZE = 0.8


# An X fills most of the box it marks, and a recorded tick is matched to a box on the blank
# only within this many of its own sizes, centre to centre.
TICK_FILL = 0.9
TICK_SNAP = 1.0


def _tick_marks(
    blank: Path, ticks: Sequence[BoundingBox], width: float, height: float
) -> list[pdf_backend.Text]:
    """An X in each ticked box, drawn where the blank prints that box.

    A choice used to be regenerated as its caption written into whatever text region its
    question matched -- "Male" typed beside or across the row of boxes, with every box left
    empty, which is not how the form was answered. A tick is recorded where the reader
    found it; it is drawn in the blank's own box nearest that place, and not at all if the
    blank has no box there, since an X somewhere else would be a wrong answer.
    """
    if not ticks:
        return []
    from PIL import Image

    pixels_wide, pixels_high = Image.open(blank).size
    sx, sy = width / pixels_wide, height / pixels_high
    printed = [
        Box(b.x0 * sx, b.y0 * sy, b.x1 * sx, b.y1 * sy) for b in detect_checkboxes(blank)
    ]
    marks = []
    for tick in ticks:
        cx, cy = (tick.x + tick.width / 2) * width, (tick.y + tick.height / 2) * height
        reach = TICK_SNAP * max(tick.width * width, tick.height * height)
        near = [
            b for b in printed
            if abs((b.x0 + b.x1) / 2 - cx) <= reach and abs((b.y0 + b.y1) / 2 - cy) <= reach
        ]
        if not near:
            continue
        box = min(near, key=lambda b: ((b.x0 + b.x1) / 2 - cx) ** 2 + ((b.y0 + b.y1) / 2 - cy) ** 2)
        size = (box.y1 - box.y0) * TICK_FILL
        across = pdf_backend.text_length("X", FONT, size)
        # A capital sits about two thirds of an em above its baseline; centring that
        # height on the box's centre puts the baseline a third of an em below it.
        marks.append(
            pdf_backend.Text((box.x0 + box.x1 - across) / 2, (box.y0 + box.y1) / 2 + size / 3, "X", size)
        )
    return marks


def label_key(label: str) -> str:
    """Stable field id derived from a label, for fields with no canonical mapping.

    Derived templates name such fields this way, so regeneration must key their values
    identically or an unknown form would regenerate almost entirely blank.
    """
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "field"
    key = base if base[0].isalpha() else f"f_{base}"
    # TemplateField ids need at least two characters. A one-letter caption ("F" beside a
    # sex tick box, a bare "#") otherwise fails validation and blocks deriving the whole
    # template -- seen on a real registration form during enrolment on 2026-09-13.
    return key if len(key) > 1 else f"field_{key}"


def values_from_analysis(analysis, template=None, page_index: int = 0) -> dict[str, Any]:
    """Collapse an analysis into field id -> value.

    Two ways a value finds its slot, in order of trustworthiness:

    *Position* first, when a template is given. A derived template names its fields from
    the *geometric* reading of the page, while on a ruled form the values usually come from
    the inline reader, and the two describe the same field in different words. Overlap is a
    statement about the same place on the page, which survives that disagreement. It is a
    one-to-one matching, or a value straddling two regions is printed in both.

    *Name* second, for the fields position could not settle and for registry-backed
    templates, whose ids are canonical rather than derived.
    """
    return _resolve(analysis, template, page_index)[0]


def _resolve(analysis, template, page_index: int) -> tuple[dict[str, Any], list, set[int]]:
    """``values_from_analysis``, also saying which filled fields a slot of the template took."""
    values: dict[str, Any] = {}
    # A choice whose ticks were located is regenerated as those ticks (``ticked_boxes``),
    # not as its caption written into a text region. Once a reviewer changes its value the
    # located ticks no longer say what was chosen, and the corrected text is written.
    filled = [
        f for f in analysis.detected_fields
        if f.normalized_value not in (None, "") and not drawn_as_ticks(f)
    ]
    claimed: set[int] = set()

    if template is not None:
        slots = _page(template, page_index).fields
        pairs = sorted(
            (
                (_overlap(slot.bbox, field.answer_bbox), i, j)
                for i, slot in enumerate(slots)
                for j, field in enumerate(filled)
            ),
            reverse=True,
        )
        taken: set[int] = set()
        for score, i, j in pairs:
            if score < MIN_SLOT_OVERLAP:
                break
            if i in taken or j in claimed:
                continue
            values[slots[i].field_id] = filled[j].normalized_value
            taken.add(i)
            claimed.add(j)

    slot_ids = (
        {CORPUS_KEY_ALIASES.get(f.field_id, f.field_id) for f in _page(template, page_index).fields}
        if template is not None
        else set()
    )
    placed = set(claimed)
    for j, field in enumerate(filled):
        if j in claimed:
            continue
        for key in (label_key(field.label_text), field.canonical_key):
            if key and key not in values:
                values[key] = field.normalized_value
                if key in slot_ids:
                    placed.add(j)
    return values, filled, placed


def loose_values(analysis, template, page_index: int = 0) -> list[tuple[BoundingBox, str, set[str]]]:
    """Values read on a page that no slot of the template took: where each was read, its
    text, and the keys it would have been filed under.

    A template only has slots where its page's geometry offers a region, and a value
    written where none was found -- the patient's name on a rule too long to be read as one
    -- used to be left off the regenerated form. On a template learned from this very
    upload the value can simply go back where it was read: the page is the same page.
    """
    _, filled, placed = _resolve(analysis, template, page_index)
    lifted = _page(template, page_index).lifted
    loose = []
    for j, field in enumerate(filled):
        if j in placed or field.page_index != page_index:
            continue
        text = _text_for(field.normalized_value, field.field_type)
        if text is None or not any(ch.isalnum() for ch in text):
            continue
        # Only where the answer was lifted off the master: a "value" that is still printed
        # there (a row of option captions a reader took for an answer) would be written over.
        if not any(_overlap(field.answer_bbox, box) > 0 for box in lifted):
            continue
        keys = {label_key(field.label_text)} | ({field.canonical_key} if field.canonical_key else set())
        loose.append((field.answer_bbox, text, keys))
    return loose


def drawn_as_ticks(field) -> bool:
    """Whether a field is regenerated by marking the boxes it ticked."""
    return (
        getattr(field, "field_type", None) == FieldType.MULTIPLE_CHOICE
        and bool(getattr(field, "ticked_boxes", None))
        and not getattr(field, "history", None)
    )


def ticks_from_analysis(analysis, page_index: int = 0) -> list[BoundingBox]:
    """The ticked boxes on one page of an analysis, for ``regenerate``."""
    return [
        box
        for field in analysis.detected_fields
        if field.page_index == page_index and drawn_as_ticks(field)
        for box in field.ticked_boxes
    ]


# A value must sit substantially inside the slot it fills; a value merely adjacent to one
# belongs to the neighbouring field.
MIN_SLOT_OVERLAP = 0.35


def _overlap(slot, answer) -> float:
    """Share of ``answer`` lying inside ``slot``. Both are page fractions."""
    wide = max(0.0, min(slot.x + slot.width, answer.x + answer.width) - max(slot.x, answer.x))
    tall = max(0.0, min(slot.y + slot.height, answer.y + answer.height) - max(slot.y, answer.y))
    area = answer.width * answer.height
    return (wide * tall) / area if area > 0 else 0.0


def stored_values_path(folder: Path) -> Path:
    return folder / "values.json"
