import re
import time
from pathlib import Path
from typing import Any

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore[assignment]
import numpy as np

from app.config import settings
from app.schemas.models import (
    ExtractedField,
    ExtractedPage,
    ExtractionRun,
    ExtractionSource,
    ExtractionSummary,
    FieldType,
    TemplateDefinition,
    ValidationError,
)
from app.services.templates import align_to_template


def normalize(
    value: Any, field_type: FieldType, pattern: str | None = None
) -> tuple[Any, list[ValidationError]]:
    errors: list[ValidationError] = []
    if field_type in {FieldType.CHECKBOX, FieldType.SIGNATURE_PRESENCE}:
        return bool(value), errors
    raw = str(value or "").strip()
    compact = " ".join(raw.split())
    if field_type == FieldType.PHONE:
        digits = re.sub(r"\D", "", compact)
        if len(digits) not in (0, 10, 11):
            errors.append(
                ValidationError(
                    code="INVALID_PHONE", message="Phone number must contain 10 or 11 digits"
                )
            )
        return digits, errors
    if field_type == FieldType.DATE:
        parts = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$", compact)
        if not parts:
            return compact, [
                ValidationError(code="INVALID_DATE", message="Expected an unambiguous date")
            ]
        a, b, y = map(int, parts.groups())
        y += 2000 if y < 100 else 0
        if a <= 12 and b <= 12:
            errors.append(ValidationError(code="AMBIGUOUS_DATE", message="Date order is ambiguous"))
            return compact, errors
        month, day = (a, b) if a <= 12 else (b, a)
        if not (1 <= month <= 12 and 1 <= day <= 31):
            errors.append(ValidationError(code="INVALID_DATE", message="Date is impossible"))
        return f"{y:04d}-{month:02d}-{day:02d}", errors
    if field_type == FieldType.NUMBER:
        candidate = compact.replace("O", "0").replace("l", "1")
        if not re.fullmatch(r"\d+(\.\d+)?", candidate):
            errors.append(
                ValidationError(code="INVALID_NUMBER", message="Expected a numeric value")
            )
        return candidate, errors
    if field_type == FieldType.INSURANCE_ID:
        candidate = compact.upper().replace(" ", "")
        if pattern and not re.fullmatch(pattern, candidate):
            errors.append(
                ValidationError(
                    code="INVALID_IDENTIFIER", message="Identifier does not match template pattern"
                )
            )
        return candidate, errors
    if field_type == FieldType.MEDICATION_LIST:
        return [line.strip() for line in raw.splitlines() if line.strip()], errors
    return compact, errors


def _ocr(crop: np.ndarray) -> tuple[str, float]:
    try:
        import pytesseract

        data = pytesseract.image_to_data(
            crop, config="--psm 6", output_type=pytesseract.Output.DICT, lang=settings.ocr_language
        )
        words = [t for t in data["text"] if t.strip()]
        confs = [float(c) for c in data["conf"] if str(c) != "-1"]
        return " ".join(words), (sum(confs) / len(confs) / 100 if confs else 0.0)
    except (OSError, RuntimeError):
        return "", 0.0


def _ink_present(crop: np.ndarray) -> tuple[bool, float]:
    if cv2 is None:
        return False, 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    ink = float((gray < 150).mean())
    return ink > 0.025, min(0.95, ink * 20)


def extract_run(
    run_id: str, run_dir: Path, template: TemplateDefinition, sample_id: str | None = None
) -> ExtractionRun:
    if cv2 is None:
        raise RuntimeError("OpenCV extraction dependency is unavailable")
    started = time.monotonic()
    normalized = cv2.imread(str(run_dir / "normalized_color.png"))
    aligned, alignment = align_to_template(normalized, template.template_id)
    cv2.imwrite(str(run_dir / "aligned.png"), aligned)
    h, w = aligned.shape[:2]
    fields: list[ExtractedField] = []
    for definition in template.pages[0].fields:
        b = definition.bbox
        x1, y1, x2, y2 = (
            int(b.x * w),
            int(b.y * h),
            int((b.x + b.width) * w),
            int((b.y + b.height) * h),
        )
        crop = aligned[y1:y2, x1:x2]
        crop_path = run_dir / "crops" / f"{definition.field_id}.png"
        crop_path.parent.mkdir(exist_ok=True)
        cv2.imwrite(str(crop_path), crop)
        raw: Any
        if definition.field_type in {
            FieldType.CHECKBOX,
            FieldType.SIGNATURE_PRESENCE,
            FieldType.MULTIPLE_CHOICE,
        }:
            raw, engine_score = _ink_present(crop)
            source = ExtractionSource.TEMPLATE
        else:
            raw, engine_score = _ocr(crop)
            source = ExtractionSource.OCR
        value, errors = normalize(raw, definition.field_type, definition.pattern)
        blank_required = definition.required and (not value)
        validation_score = 0.0 if errors or blank_required else 1.0
        confidence = max(
            0.0,
            min(1.0, 0.4 * engine_score + 0.15 * alignment + 0.15 * validation_score + 0.1 * 0.8),
        )
        # Simple explicit occupancy fields get a meaningful confidence even without OCR installed.
        if definition.field_type in {FieldType.CHECKBOX, FieldType.SIGNATURE_PRESENCE}:
            confidence = max(confidence, engine_score * 0.8 + alignment * 0.2)
        needs_review = confidence < definition.review_threshold or bool(errors) or blank_required
        if blank_required:
            errors.append(
                ValidationError(
                    code="REQUIRED_VALUE_MISSING", message="Required field appears blank"
                )
            )
        fields.append(
            ExtractedField(
                field_id=definition.field_id,
                label=definition.label,
                field_type=definition.field_type,
                bbox=b,
                raw_value=raw,
                normalized_value=value,
                confidence=confidence,
                source=source,
                needs_review=needs_review,
                validation_errors=errors,
                score_components={
                    "engine": engine_score,
                    "alignment": alignment,
                    "validation": validation_score,
                },
            )
        )
    page = ExtractedPage(
        page_index=0,
        image_url=f"/api/v1/runs/{run_id}/pages/0/image",
        width=w,
        height=h,
        alignment_confidence=alignment,
        fields=fields,
    )
    review_count = sum(f.needs_review for f in fields)
    return ExtractionRun(
        run_id=run_id,
        sample_id=sample_id,
        template_id=template.template_id,
        template_match_confidence=alignment,
        status="completed_with_review" if review_count else "completed",
        pages=[page],
        summary=ExtractionSummary(
            field_count=len(fields),
            review_count=review_count,
            processing_ms=int((time.monotonic() - started) * 1000),
        ),
    )


def write_run(run_dir: Path, run: ExtractionRun) -> None:
    (run_dir / "result.json").write_text(run.model_dump_json(indent=2))


def read_run(run_dir: Path) -> ExtractionRun:
    return ExtractionRun.model_validate_json((run_dir / "result.json").read_text())
