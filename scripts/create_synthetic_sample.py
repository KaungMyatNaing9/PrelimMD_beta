"""Create deterministic, synthetic-only templates and the 36-sample Sprint 1–2 corpus."""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.schemas.models import TemplateDefinition
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "corpus"
W, H = 1275, 1650


def _font(size: int):
    """A real TrueType face at a legible size.

    Pillow's bitmap default renders far too small for OCR: on noise-free pages it still
    misread digits (500 -> 600) and dropped characters, which made the corpus unusable
    as ground truth. Falls back through common system paths, then to the old default.
    """
    for path in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


FONT = _font(26)
BIG = _font(32)
FIELDS = [
    ("patient.full_name", "Patient name", "short_text", 0.15, 0.17, 0.58, 0.035, True),
    ("patient.dob", "Date of birth", "date", 0.15, 0.25, 0.25, 0.035, True),
    ("patient.phone", "Phone", "phone", 0.55, 0.25, 0.28, 0.035, True),
    (
        "patient.member_id",
        "Insurance ID",
        "insurance_id",
        0.15,
        0.33,
        0.40,
        0.035,
        True,
    ),
    ("patient.age", "Age", "number", 0.65, 0.33, 0.18, 0.035, False),
    ("consent.accepted", "Consent accepted", "checkbox", 0.15, 0.47, 0.035, 0.03, True),
    (
        "medications.current",
        "Current medications",
        "medication_list",
        0.15,
        0.56,
        0.70,
        0.10,
        False,
    ),
    ("notes.visit", "Visit notes", "multiline_text", 0.15, 0.73, 0.70, 0.10, False),
    (
        "signature.present",
        "Signature present",
        "signature_presence",
        0.15,
        0.89,
        0.50,
        0.045,
        True,
    ),
]


def definition(template_id):
    offset = {"T01": 0, "T02": 0.01, "T03": -0.01}[template_id]
    fs = []
    for ident, label, typ, x, y, w, h, required in FIELDS:
        fs.append(
            {
                "field_id": ident,
                "label": label,
                "field_type": typ,
                "bbox": {"x": x + offset, "y": y, "width": w, "height": h},
                "required": required,
                "normalizer": typ,
                "review_threshold": 0.8 if typ in ("date", "insurance_id") else 0.75,
                "pattern": "[A-Z]{2}\\d{6}" if typ == "insurance_id" else None,
            }
        )
    return {
        "schema_version": "1.0",
        "template_id": template_id,
        "template_name": f"Synthetic Intake {template_id}",
        "version": 1,
        "page_count": 1,
        "pages": [
            {"page_index": 0, "canonical_width": W, "canonical_height": H, "fields": fs}
        ],
    }


def draw_form(defn, values=None):
    image = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(image)
    d.rectangle((35, 35, W - 35, H - 35), outline="#1e3a5f", width=5)
    d.text(
        (90, 75),
        f"PRELIMMD SYNTHETIC MEDICAL INTAKE — {defn['template_id']}",
        fill="#111",
        font=BIG,
    )
    for f in defn["pages"][0]["fields"]:
        b = f["bbox"]
        x, y, w, h = (
            int(b["x"] * W),
            int(b["y"] * H),
            int(b["width"] * W),
            int(b["height"] * H),
        )
        # Clear of the box top so the larger face does not collide with the rule line.
        d.text((x, y - 34), f["label"], fill="#222", font=FONT)
        d.rectangle((x, y, x + w, y + h), outline="#444", width=1)
        val = (values or {}).get(f["field_id"], "")
        if f["field_type"] == "checkbox":
            d.rectangle((x, y, x + w, y + h), outline="#222", width=2)
            if val:
                d.line(
                    (x + 3, y + h // 2, x + w // 2, y + h - 4, x + w - 3, y + 3),
                    fill="#111",
                    width=2,
                )
        elif f["field_type"] == "signature_presence":
            if val:
                d.arc((x + 20, y, x + 150, y + h), 180, 350, fill="#111", width=2)
                d.line((x + 40, y + h - 5, x + 220, y + 5), fill="#111", width=1)
        else:
            d.multiline_text(
                (x + 5, y + 4), str(val), fill="#111", font=FONT, spacing=2
            )
    return image


def values(n):
    names = ["Alex Johnson", "Samira Patel", "Jordan Lee", "Taylor Morgan"]
    meds = ["Metformin 500 mg\nVitamin D", "Lisinopril 10 mg", "None reported"]
    return {
        "patient.full_name": names[n % 4],
        "patient.dob": "1988-12-14",
        "patient.phone": f"312-555-01{n:02d}",
        "patient.member_id": f"AB{100000 + n:06d}",
        "patient.age": str(24 + n % 60),
        "consent.accepted": n % 5 != 0,
        "medications.current": meds[n % 3],
        "notes.visit": "Synthetic sample only.\nNo real patient data.",
        "signature.present": n % 4 != 0,
    }


def main():
    random.seed(20260728)
    records = []
    for ti, template_id in enumerate(("T01", "T02", "T03")):
        defn = definition(template_id)
        TemplateDefinition.model_validate(defn)
        directory = CORPUS / "templates" / template_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "template.json").write_text(json.dumps(defn, indent=2))
        blank = draw_form(defn)
        blank.save(directory / "blank_preview.png")
        for index in range(12):
            split = (
                "development" if index < 8 else "validation" if index < 10 else "test"
            )
            sample_id = f"{template_id}_{'DEV' if split == 'development' else 'VAL' if split == 'validation' else 'TEST'}_{index % 8 + 1:03d}"
            folder = CORPUS / split / template_id / sample_id
            folder.mkdir(parents=True, exist_ok=True)
            vals = values(ti * 12 + index)
            image = draw_form(defn, vals)
            capture = ("clean_scan", "degraded_scan", "phone_photo")[index % 3]
            if capture == "degraded_scan":
                image = image.filter(ImageFilter.GaussianBlur(0.4))
            if capture == "phone_photo":
                image = image.rotate(
                    (-4, 5)[index % 2], expand=False, fillcolor="white"
                )
            image.save(folder / "source.jpg", quality=88)
            labels = {
                "sample_id": sample_id,
                "template_id": template_id,
                "fields": [
                    {
                        "field_id": k,
                        "normalized_value": v,
                        "content_type": "handwritten"
                        if k in {"medications.current", "notes.visit"}
                        else "printed",
                    }
                    for k, v in vals.items()
                ],
            }
            (folder / "expected.json").write_text(json.dumps(labels, indent=2))
            records.append(
                {
                    "sample_id": sample_id,
                    "template_id": template_id,
                    "split": split,
                    "source_path": str((folder / "source.jpg").relative_to(CORPUS)),
                    "expected_path": str(
                        (folder / "expected.json").relative_to(CORPUS)
                    ),
                    "capture_type": capture,
                    "content_mix": ["printed", "handwritten", "checkbox"],
                    "synthetic_data": True,
                    "approved_for_repository": True,
                }
            )
    (CORPUS / "manifest.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records)
    )
    (CORPUS / "README.md").write_text(
        "# Synthetic corpus\n\nGenerated deterministic fixtures only; no real patient data. Test labels are for formal evaluation only. Regenerate with `python scripts/create_synthetic_sample.py`.\n"
    )


if __name__ == "__main__":
    main()
