"""Combine what geometry reads with what a vision model reads.

The two disagree in a lopsided way. Measured head to head on the fields both were scored
on, the model won six and geometry won none, and the six were exactly the ones geometry
could not be made to read: city, state, zip code, address, employer, occupation. Those fail
because working out *which label a value belongs to* from position alone is hard, and a
model that sees the page reads that relationship directly.

The reverse is just as lopsided. Asked about a row of empty tick boxes the model answers
anyway -- "Sex: Female", "Marital Status: Single" on a form where nothing is ticked -- and
saying so in the prompt changed nothing. Ink coverage measures a tick box exactly and had
no false positives on any form tested.

So the split is not a compromise, it is what each is good at:

* geometry keeps every mark, and keeps the coordinates regeneration draws from
* the model supplies label-to-value binding for text fields

This runs only when a vision provider is configured. With none, nothing here executes.
"""

from app.schemas.form_understanding import DetectedFormField, RequiredStatus
from app.schemas.models import BoundingBox, FieldType
from app.services.canonical_registry import match, norm
from app.services.vision_extractor import VisionField, locate_values
from app.services.word_source import Word

# A model reading is trusted for text, never for a mark: those are measured from ink.
MARK_TYPES = (FieldType.CHECKBOX, FieldType.SIGNATURE_PRESENCE, FieldType.MULTIPLE_CHOICE)
VISION_CONFIDENCE = 0.8


def fuse(
    geometric: list[DetectedFormField],
    seen: list[VisionField],
    words: list[Word],
    page_width: float,
    page_height: float,
    page_index: int = 0,
    tick_boxes: list | None = None,
) -> list[DetectedFormField]:
    """Overlay the model's text readings onto the geometric ones.

    A geometric field keeps its box whatever happens to its value, because regeneration
    places text from those coordinates and the model is not asked for any. A reading the
    model has and geometry does not becomes a new field, positioned by matching its value
    back onto the OCR words -- and dropped if that fails, since a field with no location
    cannot be drawn, corrected on the page, or regenerated.
    """
    boxes = locate_values(seen, words, page_width, page_height)
    by_label = {norm(f.label_text): f for f in geometric}
    fused = list(geometric)

    for field in seen:
        if not field.value:
            continue
        existing = by_label.get(norm(field.label))
        if existing is not None:
            if existing.field_type in MARK_TYPES:
                continue  # a tick box is measured, never read
            # Fill gaps, never overwrite. A value geometry already read was corroborated
            # by where it sits on the page; the model's was not, and letting it win cost
            # two correct fields on the first form measured -- an address truncated to
            # "1420 Bellefield Avenue APT 3B Pitt", an emergency contact losing its phone.
            if existing.normalized_value in (None, "", False):
                _adopt(existing, field.value)
            continue
        box = boxes.get(field.label)
        if box is None:
            continue
        # Guarding only the fields geometry already had left the hole this closes: a
        # question geometry never produced -- "Sex", "Marital Status" -- arrived through
        # the *new field* path instead and carried an invented answer onto a form where
        # nothing was ticked. Anything sitting among tick boxes is a choice, and choices
        # are measured from ink, never read.
        if _among_tick_boxes(box, tick_boxes or [], page_width, page_height):
            continue
        fused.append(_new_field(field, box, len(fused), page_index))
    return fused


def _among_tick_boxes(
    box: tuple[float, float, float, float], tick_boxes: list, w: float, h: float
) -> bool:
    """Whether a reading sits on a row of tick boxes, making it a choice."""
    x, y, width, height = box
    top, bottom = y * h, (y + height) * h
    left, right = x * w, (x + width) * w
    for tick in tick_boxes:
        overlaps_row = min(bottom, tick.y1) - max(top, tick.y0) > 0
        near_in_x = min(right, tick.x1) - max(left, tick.x0) > -(tick.x1 - tick.x0) * 8
        if overlaps_row and near_in_x:
            return True
    return False


def _adopt(field: DetectedFormField, value: str) -> None:
    """Replace a geometric field's value, keeping everything positional about it."""
    if field.normalized_value == value:
        return
    field.raw_value = value
    field.normalized_value = value
    field.is_blank = False
    field.source = "ocr_vision_fused"
    field.confidence = VISION_CONFIDENCE
    field.needs_review = not bool(field.canonical_key)


def _new_field(
    field: VisionField, box: tuple[float, float, float, float], index: int, page_index: int
) -> DetectedFormField:
    """A field only the model saw, placed by matching its value onto the OCR words."""
    mapped, score, reason = match(field.label)
    x, y, width, height = box
    bbox = BoundingBox(x=x, y=y, width=width, height=height)
    return DetectedFormField(
        field_id=f"vision_{page_index}_{index}",
        page_index=page_index,
        label_text=field.label.rstrip(":"),
        normalized_label=norm(field.label),
        field_type=mapped.value_type if mapped else FieldType.SHORT_TEXT,
        label_bbox=bbox,
        answer_bbox=bbox,
        required_status=RequiredStatus.UNKNOWN,
        required_confidence=0.3,
        detection_confidence=VISION_CONFIDENCE,
        source="ocr_vision_fused",
        canonical_key=mapped.canonical_key if mapped else None,
        mapping_confidence=score,
        mapping_reason=reason,
        raw_value=field.value,
        normalized_value=field.value,
        is_blank=False,
        confidence=VISION_CONFIDENCE,
        # Nothing corroborates a field only one reader saw, so a human should look.
        needs_review=True,
    )
