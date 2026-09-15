"""Fill stored values onto a blank template and emit a machine-filled PDF.

Placement uses the template's own recorded field coordinates rather than the boxes
extraction inferred from the upload: the template numbers are exact, so a regenerated
value lands where the blank form actually puts it, independent of capture quality.
"""

import re
from pathlib import Path
from typing import Any

import fitz

from app.schemas.models import FieldType, TemplateDefinition
from app.services.canonical_registry import CORPUS_KEY_ALIASES
from app.services.templates import canonical_image_path

# Checkbox glyph drawn when a value is truthy; signature fields are presence-only and are
# never synthesised, per the work plan's "presence and location, not recognition".
TICK = "X"


def _text_for(value: Any, field_type: FieldType | str) -> str | None:
    kind = field_type.value if isinstance(field_type, FieldType) else str(field_type)
    if value is None or value == "":
        return None
    if kind == "checkbox":
        return TICK if value in (True, "true", "True", 1) else None
    if kind == "signature_presence":
        return None
    return str(value)


# Safety margin above the font's own descent, as a fraction of the point size. The glyph
# box the renderer reports is a little deeper than the nominal descender, and without this
# a value sits a fraction of a point past the foot of its own field.
DESCENDER_MARGIN = 0.06


def _descent(fontname: str) -> float:
    """How far below the baseline this face reaches, as a fraction of its point size."""
    return abs(fitz.Font(fontname).descender) + DESCENDER_MARGIN


FONT = "helv"
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


def _font_size(height_pt: float, learned: float | None = None) -> float:
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
    return max(6.0, min(12.0, height_pt * 0.55))


def unplaced(
    template: TemplateDefinition, values: dict[str, Any], page_index: int = 0
) -> list[str]:
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
        CORPUS_KEY_ALIASES.get(f.field_id, f.field_id) for f in template.pages[page_index].fields
    }
    missing: dict[str, str] = {}
    for key, value in values.items():
        if key in slots:
            continue
        text = _text_for(value, FieldType.SHORT_TEXT)
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
    template: TemplateDefinition, values: dict[str, Any], page_index: int = 0
) -> bytes:
    """Render ``values`` onto the blank master for ``template`` and return PDF bytes.

    ``values`` is keyed by canonical field key; template fields carry the corpus key, so
    they are translated through CORPUS_KEY_ALIASES on the way in. Values with no slot are
    not rendered; ``unplaced`` names them so a caller can say so.
    """
    blank = canonical_image_path(template.template_id, page_index)
    if not blank.exists():
        raise FileNotFoundError(f"No blank master for template {template.template_id}")

    page_def = template.pages[page_index]
    width, height = float(page_def.canonical_width), float(page_def.canonical_height)

    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    page.insert_image(fitz.Rect(0, 0, width, height), filename=str(blank))

    for field in page_def.fields:
        canonical = CORPUS_KEY_ALIASES.get(field.field_id, field.field_id)
        text = _text_for(values.get(canonical), field.field_type)
        if text is None:
            continue
        box = field.bbox
        rect = fitz.Rect(
            box.x * width,
            box.y * height,
            (box.x + box.width) * width,
            (box.y + box.height) * height,
        )
        size = _font_size(rect.height, (field.font_size or 0) * height or None)
        if "\n" in text:
            # A multi-line value needs the whole region and grows downwards from its top.
            page.insert_textbox(
                rect + (2, 1, -2, -1),
                text,
                fontsize=size,
                fontname=FONT,
                color=(0.05, 0.05, 0.15),
                align=fitz.TEXT_ALIGN_LEFT,
            )
            continue
        # A person writes *on* the rule at the foot of the space, not floating at its top,
        # but a text box lays its first line from the top edge. Filling the whole region
        # therefore printed every value about 4.5mm high -- a constant offset, measured
        # identically on every correctly placed field. Setting the baseline directly puts
        # the value back on the rule. Shrinking the box instead silently renders nothing
        # when the text no longer fits.
        page.insert_text(
            fitz.Point(rect.x0 + _indent(field, size, width), rect.y1 - size * _descent(FONT)),
            text,
            fontsize=size,
            fontname=FONT,
            color=(0.05, 0.05, 0.15),
        )

    return doc.tobytes()


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
    values: dict[str, Any] = {}
    filled = [f for f in analysis.detected_fields if f.normalized_value not in (None, "")]
    claimed: set[int] = set()

    if template is not None:
        slots = template.pages[page_index].fields
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

    for j, field in enumerate(filled):
        if j in claimed:
            continue
        values.setdefault(label_key(field.label_text), field.normalized_value)
        if field.canonical_key:
            values.setdefault(field.canonical_key, field.normalized_value)
    return values


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
