"""Derive a reusable blank template from a single filled form.

Regeneration needs a blank master plus field coordinates. When a form arrives that no
registered template matches, both can be recovered from the upload itself: the boxes
already located during analysis give the coordinates, and whiting out their interiors
leaves the pre-printed form behind.

Limits worth knowing. This only works for forms that draw their input boxes, because the
box is what separates a filled value from the printed labels and logos around it. A form
that writes values onto blank space or a dotted line needs a different signal — that is
where a vision model would classify printed-versus-filled instead. And a single sample
cannot prove a mark is a value rather than pre-printed text; two filled copies of the same
form make that unambiguous, since whatever is identical across them is the template.
"""

from pathlib import Path

import cv2
import numpy as np

from app.schemas.form_understanding import FormAnalysis
from app.schemas.models import (
    BoundingBox,
    FieldType,
    TemplateDefinition,
    TemplateField,
    TemplatePage,
)
from app.services.regeneration import label_key
from app.services.templates import DERIVED_ROOT, registry

# Corpus masters own T01-T49; derived templates are allocated above that so the two never
# collide. TemplateDefinition constrains ids to T followed by two digits.
# The largest real answer region measured in the corpus (a multi-line notes box)
# covers 0.07 of the page. Anything appreciably larger is a section frame whose
# printed captions must survive into the blank master, so it is left alone.
MAX_ERASABLE_AREA_FRACTION = 0.12

DERIVED_ID_START = 50
DERIVED_ID_END = 99


def next_template_id() -> str:
    taken = {t.template_id for t in registry.all()}
    for number in range(DERIVED_ID_START, DERIVED_ID_END + 1):
        candidate = f"T{number:02d}"
        if candidate not in taken:
            return candidate
    raise RuntimeError("No derived template ids remain")


def _slug(label: str, used: set[str]) -> str:
    """A unique field id from a label, matching TemplateField's required pattern.

    The un-suffixed form must stay identical to ``regeneration.label_key`` or values will
    not resolve back onto the template. A disambiguating suffix therefore only appears
    when one form repeats a label, and that repeat will not refill.
    """
    base = label_key(label)
    candidate, n = base, 2
    while candidate in used:
        candidate, n = f"{base}_{n}", n + 1
    return candidate


def blank_from_filled(image: np.ndarray, boxes: list[BoundingBox]) -> np.ndarray:
    """Whiten the interior of each field box, leaving the printed form intact.

    The inset keeps each box's own rule lines, so the blank still looks like the form
    rather than a page with holes punched in it.
    """
    blank = image.copy()
    height, width = blank.shape[:2]
    # The inset only has to spare the box's own rule line, so it scales with page
    # resolution rather than box size. Scaling it with the box would leave a tall field's
    # first line of text un-erased, since values sit flush against the top edge.
    inset = max(2, round(min(height, width) * 0.003))
    for box in boxes:
        # A section frame ("Primary Insurance") encloses printed captions of its own.
        # Erasing its interior strips those from the blank master and mangles the form,
        # so only clear regions small enough to be a single answer.
        # bbox dimensions are page fractions, so their product is already the share of
        # the page this region covers.
        if box.width * box.height > MAX_ERASABLE_AREA_FRACTION:
            continue
        x0, y0 = int(box.x * width) + inset, int(box.y * height) + inset
        x1, y1 = int((box.x + box.width) * width) - inset, int((box.y + box.height) * height) - inset
        if x1 > x0 and y1 > y0:
            blank[y0:y1, x0:x1] = 255
    return blank


def derive_template(
    analysis: FormAnalysis, page_image: Path, template_name: str | None = None
) -> TemplateDefinition:
    """Build and persist a template from an analysed form, then register it.

    Only fields the analyser located as boxes are usable: an inline "label: value" match
    describes where the text happened to sit, not where the form provides space for it.
    """
    boxed = [f for f in analysis.detected_fields if f.source == "ocr_layout"]
    if not boxed:
        raise ValueError(
            "No input boxes were detected on this form, so a template cannot be derived "
            "from it. Box-drawn forms are supported; free-position layouts are not yet."
        )

    image = cv2.imread(str(page_image))
    if image is None:
        raise FileNotFoundError(f"Cannot read page image {page_image}")
    height, width = image.shape[:2]

    template_id = next_template_id()
    folder = DERIVED_ROOT / template_id
    folder.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(
        str(folder / "blank_preview.png"),
        blank_from_filled(image, [f.answer_bbox for f in boxed]),
    )

    used: set[str] = set()
    fields = []
    for field in boxed:
        # Real forms repeat a caption per section — "Name" and "Phone Number" appear
        # under primary insurance, secondary insurance and emergency contact alike. Two
        # of them mapping to the same canonical key must still get distinct ids, or the
        # page fails validation and no template can be built at all.
        base = field.canonical_key or _slug(field.label_text, set())
        field_id, n = base, 2
        while field_id in used:
            field_id, n = f"{base}_{n}", n + 1
        used.add(field_id)
        fields.append(
            TemplateField(
                field_id=field_id,
                label=field.label_text,
                field_type=field.field_type or FieldType.SHORT_TEXT,
                bbox=field.answer_bbox,
                review_threshold=0.75,
            )
        )

    definition = TemplateDefinition(
        template_id=template_id,
        template_name=template_name or f"Derived from {analysis.analysis_id}",
        version=1,
        page_count=1,
        pages=[
            TemplatePage(
                page_index=0, canonical_width=width, canonical_height=height, fields=fields
            )
        ],
    )
    (folder / "template.json").write_text(definition.model_dump_json(indent=2))
    registry.load()
    return definition
