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


def _font_size(height_pt: float) -> float:
    """Fit the face to the field box, clamped to a legible range."""
    return max(6.0, min(12.0, height_pt * 0.55))


def regenerate(
    template: TemplateDefinition, values: dict[str, Any], page_index: int = 0
) -> bytes:
    """Render ``values`` onto the blank master for ``template`` and return PDF bytes.

    ``values`` is keyed by canonical field key; template fields carry the corpus key, so
    they are translated through CORPUS_KEY_ALIASES on the way in.
    """
    blank = canonical_image_path(template.template_id)
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
        # Small inset keeps the fill clear of the printed rule line.
        page.insert_textbox(
            rect + (2, 1, -2, -1),
            text,
            fontsize=_font_size(rect.height),
            fontname="helv",
            color=(0.05, 0.05, 0.15),
            align=fitz.TEXT_ALIGN_LEFT,
        )

    return doc.tobytes()


def label_key(label: str) -> str:
    """Stable field id derived from a label, for fields with no canonical mapping.

    Derived templates name such fields this way, so regeneration must key their values
    identically or an unknown form would regenerate almost entirely blank.
    """
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "field"
    return base if base[0].isalpha() else f"f_{base}"


def values_from_analysis(analysis) -> dict[str, Any]:
    """Collapse an analysis into field id -> value.

    Each field is offered under its canonical key when it has one, and always under its
    label-derived key, so both registry-backed templates and derived ones resolve.
    """
    values: dict[str, Any] = {}
    for field in analysis.detected_fields:
        if field.normalized_value in (None, ""):
            continue
        values.setdefault(label_key(field.label_text), field.normalized_value)
        if field.canonical_key:
            values[field.canonical_key] = field.normalized_value
    return values


def stored_values_path(folder: Path) -> Path:
    return folder / "values.json"
