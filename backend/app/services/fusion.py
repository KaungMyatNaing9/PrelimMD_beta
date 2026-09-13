"""Combine what geometry reads with what a vision model reads.

**Correction (2026-09-10):** the "model won six, geometry won none" measurement above
was comparing labels across different forms, not fields on the same one, and was wrong.
Re-run properly: on a form geometry's own captioned-column reading later learned to split
correctly, the model *merged* city, state and zip into one string -- worse than geometry,
not better. So the model is not simply superior at binding; it is superior specifically
where geometry has nothing to offer, and worse where geometry already has a confident,
structured answer. That is the actual split this module keeps.

The reverse holds as measured: asked about a row of empty tick boxes the model answers
anyway -- "Sex: Female" on a form where nothing is ticked -- and no prompt fixed it. Ink
coverage and, for PDFs whose boxes are drawn as symbol-font glyphs, the mark stamped in
the document's own text layer, measure a tick exactly and had no false positives tested.

So the rule is not "model beats geometry": it is that each is trusted only where it is
actually reliable --

* a mark is always read from ink or the document's own structure, never from the model
* a value geometry never found, or found but already flagged for review, may come from
  the model
* a value geometry is confident about is never touched -- confident geometric reads
  (a captioned column split correctly, for one) are not what the model is better at, and
  letting it try was measured to make them worse on the one form tested

This runs only when a vision provider is configured. With none, nothing here executes.
"""

import logging
import os
from collections import Counter

from app.schemas.form_understanding import DetectedFormField, RequiredStatus
from app.schemas.models import BoundingBox, FieldType
from app.services.canonical_registry import match, norm
from app.services.vision_extractor import VisionField, locate_by_label, locate_values
from app.services.vision_extractor import _normalize as _same_text
from app.services.word_source import Word

# A model reading is trusted for text, never for a mark: those are measured from ink.
MARK_TYPES = (FieldType.CHECKBOX, FieldType.SIGNATURE_PRESENCE, FieldType.MULTIPLE_CHOICE)
VISION_CONFIDENCE = 0.8
# A reading whose position was estimated from its label, not measured from its own pixels.
# The value may well be right; where it sits is a guess, so it says so.
_LOG = logging.getLogger(__name__)

ESTIMATED_CONFIDENCE = 0.6

# EXPERIMENT (2026-09-12): whether the model's *label* comes with its value. `_adopt` takes
# the model's value and keeps geometry's caption, which splits a pairing the model read as
# one thing -- so a correct value lands under a wrong question and the link fails on the
# label side. Measured on FUNSD: 60.8% of answers arrive, 47.5% link.
ADOPT_MODEL_LABEL = os.getenv("FUSION_ADOPT_LABEL", "") == "1"
# Two readers agreeing is stronger evidence than either alone, but not proof: they can
# still share an OCR error upstream, so this stops short of certainty.
CORROBORATED_CONFIDENCE = 0.95
# Share of a located value that must lie inside a field's answer region for the two to be
# the same field. Half keeps a value whose box OCR sized a little differently.
MIN_VALUE_OVERLAP = 0.5


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
    # Where the value itself cannot be found in the OCR words -- which on handwriting is
    # almost everywhere -- fall back to where its *label* sits. See ``locate_by_label``.
    approximate = locate_by_label(seen, words, page_width, page_height)
    # Bind by *where the value sits*, not by what its label is called. A label is not an
    # address on the page: real intake forms repeat "Last Name" across the patient,
    # guarantor, subscriber and emergency blocks, and keying by label kept only the last
    # of them. The patient's own name was then written into the blank subscriber section,
    # so a form came back asserting details nobody had entered.
    counts = Counter(norm(f.label_text) for f in geometric)
    unique = {norm(f.label_text): f for f in geometric if counts[norm(f.label_text)] == 1}
    fused = list(geometric)

    for position, field in enumerate(seen):
        if not field.value:
            continue
        # By position, not by caption. A table column repeats one caption down a row of
        # cells, and keying these lookups by label meant every cell after the first found
        # the same box, landed on the same geometric field, and was thrown away as a
        # disagreement. 38.5% of FUNSD's annotated links hang off such a caption.
        box = boxes.get(position)
        existing = _field_at(box, geometric)
        if existing is None and counts[norm(field.label)] == 1:
            # Position could not settle it, and exactly one field carries this label, so
            # there is nothing to confuse it with. A repeated label with no position is
            # left alone rather than guessed at.
            existing = unique.get(norm(field.label))
        if existing is not None:
            if existing.field_type in MARK_TYPES:
                continue  # a tick box is measured, never read
            # Fill a gap, or replace a value geometry itself already doubts -- never a
            # value geometry is confident about. A confident geometric read (a captioned
            # column split correctly, an address bound to its own rule) was corroborated
            # by where it sits on the page; the model's was not, and letting it win
            # unconditionally cost two correct fields on the first form measured, and
            # merged a form's city/state/zip back into one string on another. Restricting
            # the win to fields geometry already flagged (``needs_review``, set when the
            # mapping failed, the read is missing, or the value looked implausible) is
            # the same corroboration test, just extended to the case where geometry's own
            # answer has nothing to be trusted over.
            # Measured both ways on the untuned set once geometry reached 70%: letting
            # the model win outright scores exactly the same there (31/40) and costs two
            # fields on the development set, where it merges a captioned address row --
            # "1420 Bellefield Avenue\nApt 3B\nPittsburgh, PA 15206" -- that geometry
            # splits correctly into street, city, state and zip. Nothing to gain, a real
            # thing to lose, so the gate stays.
            if existing.normalized_value in (None, "", False) or existing.needs_review:
                _adopt(existing, field.value, field.label)
                continue
            # Geometry already had a confident answer here, so the model does not get to
            # replace it -- but whether the two agree is worth recording either way.
            #
            # Agreement is the cheapest confidence this system can produce: two readers
            # that reach the same value by different routes, one from where text sits on
            # the page and one from what the page says, are unlikely to be wrong the same
            # way. Disagreement is more useful still. Until now it was simply discarded:
            # the gate kept geometry's value and said nothing, so the one place on the
            # page where the two readers conflict -- exactly where a person should look --
            # looked no different from a field both agreed on.
            if _same_answer(existing.normalized_value, field.value):
                existing.agreement = "corroborated"
                existing.confidence = max(existing.confidence, CORROBORATED_CONFIDENCE)
            else:
                existing.agreement = "disputed"
                existing.needs_review = True
            continue
        estimated = False
        if box is None:
            # A value read correctly but invisible to OCR used to be dropped here, which
            # cost two thirds of the model's correct readings on handwritten forms. An
            # estimated box is worth more than no field: it is enough to draw an overlay
            # and for a person to check, and it is marked so nothing mistakes it for a
            # measured position.
            box = approximate.get(position)
            estimated = box is not None
        if box is None:
            # Counted, not silent. A value the model read correctly and we then discarded
            # for want of a position is the most expensive kind of loss -- the hard part
            # succeeded and the bookkeeping lost it -- and until now nothing said how often
            # it happened.
            # `warning`, not `info`: nothing configures logging in this application, so
            # the root logger sits at WARNING and an info line reaches no handler at all.
            # This was first written as info, and the resulting silence was read as "no
            # values are ever dropped" -- a conclusion drawn from an unemitted log line.
            _LOG.warning("fusion: dropped unplaceable value for %r", field.label)
            continue
        # Guarding only the fields geometry already had left the hole this closes: a
        # question geometry never produced -- "Sex", "Marital Status" -- arrived through
        # the *new field* path instead and carried an invented answer onto a form where
        # nothing was ticked. Anything sitting among tick boxes is a choice, and choices
        # are measured from ink, never read.
        if _among_tick_boxes(box, tick_boxes or [], page_width, page_height):
            continue
        fused.append(_new_field(field, box, len(fused), page_index, estimated))
    return fused


def _same_answer(one, other) -> bool:
    """Whether two readers said the same thing, ignoring how they wrote it.

    A reader that returns "04/17/1986" and one that returns "04 / 17 / 1986" agree; so do
    "PA" and "pa". Punctuation and case are how a reader transcribes, not what it read,
    and treating those as conflict would flag half a form for review.
    """
    return _same_text(str(one)) == _same_text(str(other))


def _field_at(
    box: tuple[float, float, float, float] | None, geometric: list[DetectedFormField]
) -> DetectedFormField | None:
    """The geometric field whose answer region the located value actually sits in."""
    if box is None:
        return None
    x, y, width, height = box
    area = max(width * height, 1e-9)
    best: tuple[float, DetectedFormField] | None = None
    for field in geometric:
        a = field.answer_bbox
        wide = min(x + width, a.x + a.width) - max(x, a.x)
        tall = min(y + height, a.y + a.height) - max(y, a.y)
        if wide <= 0 or tall <= 0:
            continue
        share = (wide * tall) / area
        if share >= MIN_VALUE_OVERLAP and (best is None or share > best[0]):
            best = (share, field)
    return best[1] if best else None


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


def _adopt(field: DetectedFormField, value: str, label: str | None = None) -> None:
    """Replace a geometric field's value, keeping everything positional about it.

    ``label`` is the caption the model read *with* that value. The two came from one
    reading, so taking the value while keeping geometry's caption splits a pairing the
    model never split -- and a correct value under the wrong question is a wrong field.
    Only adopted when the vocabulary cannot name geometry's caption, since a caption that
    maps to a canonical key has already been corroborated by something other than position.

    Replacing a value geometry *had* is a disagreement, and it was being recorded as
    "unread" -- which is what a field only one reader saw looks like. That is why the
    disputed signal never fired: 11 corroborated and 0 disputed across 118 fields, because
    the only disagreements that reached the corroboration test were the ones where geometry
    was confident, and there the two readers almost always agree. Every real conflict was
    happening here, where geometry had read something, doubted it, and been overruled.

    Filling a field geometry left *blank* is not a disagreement -- nobody contradicted
    anybody -- so that case stays "unread". Replacing something with something else is.
    """
    replaced = field.normalized_value
    if replaced == value:
        return
    field.raw_value = value
    field.normalized_value = value
    field.is_blank = False
    field.source = "ocr_vision_fused"
    field.confidence = VISION_CONFIDENCE
    if ADOPT_MODEL_LABEL and label and not field.canonical_key:
        mapped, score, reason = match(label)
        field.label_text = label.rstrip(":")
        field.normalized_label = norm(label)
        field.canonical_key = mapped.canonical_key if mapped else None
        field.mapping_confidence = score
        field.mapping_reason = reason
    field.needs_review = not bool(field.canonical_key)
    if replaced in (None, "", False) or _same_answer(replaced, value):
        field.agreement = "unread"
    else:
        # The readers conflict here. The model's value is shown because geometry had
        # already flagged its own, but a person should see that the two disagreed.
        field.agreement = "disputed"
        field.needs_review = True


def _new_field(
    field: VisionField,
    box: tuple[float, float, float, float],
    index: int,
    page_index: int,
    estimated: bool = False,
) -> DetectedFormField:
    """A field only the model saw, placed by matching its value onto the OCR words.

    ``estimated`` means the box came from the field's label rather than from the value's
    own pixels, because OCR could not see the value. The reading is no less real; only
    its position is approximate, and it is reported at lower confidence to say so.
    """
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
        detection_confidence=ESTIMATED_CONFIDENCE if estimated else VISION_CONFIDENCE,
        source="ocr_vision_estimated" if estimated else "ocr_vision_fused",
        canonical_key=mapped.canonical_key if mapped else None,
        mapping_confidence=score,
        mapping_reason=reason,
        raw_value=field.value,
        normalized_value=field.value,
        is_blank=False,
        confidence=ESTIMATED_CONFIDENCE if estimated else VISION_CONFIDENCE,
        # Nothing corroborates a field only one reader saw, so a human should look.
        needs_review=True,
    )
