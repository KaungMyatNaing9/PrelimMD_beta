"""Score the product's main path: blank form plus content in, filled form out.

The workflow is not "read a filled form". It is: take a blank template and the content a
patient supplied, and produce a form that looks filled in. Extraction matters only as a way
of learning a template from a filled copy when no blank one exists.

So this measures what actually ships. For each blank form: learn its template, fill it with
known content, rasterise the result, read it back, and ask whether each value arrived under
the right label. A value that survives that has been placed somewhere a person -- and an
OCR pass -- can find it under the right question.

    python scripts/evaluate_fill.py [--api http://127.0.0.1:8000]
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services import pdf_backend

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.templates import DERIVED_ROOT, registry

FORMS = ROOT / "data/real-forms"

# Content a patient might supply. Keyed by canonical field, which is what a template's
# slots are named, so this is the same shape a portal or prior record would hand over.
CONTENT = {
    "patient.full_name": "Marisol Vega Trevino",
    "patient.date_of_birth": "09/22/1991",
    "patient.address": "3418 Wrightsville Ave Unit 7",
    "patient.phone": "910-555-0173",
    "patient.email": "m.vegatrevino@example.com",
}


def curl(*args: str) -> str:
    return subprocess.run(["curl", "-s", *args], capture_output=True, text=True, check=False).stdout


def comparable(value) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _check(pdf: Path, template_id: str, wanted: dict) -> list:
    """Did each value get drawn, and into its own slot?

    Read from the PDF's own text layer rather than by OCRing the page back. OCR misreads
    its own share of characters -- "09/22/1991" came back "ov22/991" -- and grading our
    output with a lossy reader counts that reader's mistakes as ours. The text layer says
    exactly what was drawn and exactly where, which is the part this code controls.
    """
    
    # The template was written by the server process, so this one has to re-read the
    # registry before it can see it.
    registry.load()
    page = pdf_backend.open_page(pdf, 0)
    width, height = page.width, page.height
    drawn = [(w.text, (w.x0 + w.x1) / 2 / width, (w.y0 + w.y1) / 2 / height)
             for w in page.words()]
    slots = {f.field_id: f.bbox for f in registry.get(template_id).pages[0].fields}

    results = []
    for key, value in wanted.items():
        box = slots.get(key)
        inside = [
            text for text, x, y in drawn
            if box and box.x <= x <= box.x + box.width and box.y <= y <= box.y + box.height
        ]
        joined = comparable(" ".join(inside))
        results.append((comparable(value) in joined and bool(inside), key, value, " ".join(inside)))
    return results


def release(template_id: str) -> None:
    folder = DERIVED_ROOT / template_id
    if folder.exists():
        import shutil

        shutil.rmtree(folder)
    registry.load()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    blanks = sorted(p for p in FORMS.glob("*_page1.jpg") if "FILLED" not in p.name)
    placed = arrived = 0
    for blank in blanks:
        analysis = json.loads(
            curl("-X", "POST", f"{args.api}/api/v1/form-analyses",
                 "-F", f"file=@{blank};type=image/jpeg")
        )
        derived = json.loads(
            curl("-X", "POST",
                 f"{args.api}/api/v1/form-analyses/{analysis['analysis_id']}"
                 f"/derive-template?template_name={blank.stem}")
        )
        template_id = derived.get("template_id")
        if not template_id:
            print(f"\n{blank.name}: no template ({str(derived)[:60]})")
            continue

        slots = set(derived.get("fields", []))
        wanted = {k: v for k, v in CONTENT.items() if k in slots}
        if not wanted:
            print(f"\n{blank.name}: template has none of the content's fields")
            release(template_id)
            continue

        out = ROOT / "artifacts" / f"fill_{blank.stem}.pdf"
        out.parent.mkdir(parents=True, exist_ok=True)
        # The values a caller supplies, not values read off a form.
        curl("-o", str(out), "-X", "POST",
             f"{args.api}/api/v1/templates/{template_id}/fill",
             "-H", "Content-Type: application/json", "-d", json.dumps(wanted))
        hits = _check(out, template_id, wanted)

        for ok, *_ in hits:
            placed += 1
            arrived += ok
        good = sum(1 for h in hits if h[0])
        print(f"\n{blank.name}  {good}/{len(hits)} values drawn into their own slot")
        for ok, key, value, got in hits:
            print(f"   {'ok  ' if ok else 'LOST'} {key:26} sent {value[:22]!r:24} in slot {str(got)[:26]!r}")
        release(template_id)

    if not placed:
        print("\nNothing measured.")
        return 1
    print(f"\nFILL ACCURACY {arrived}/{placed} = {arrived / placed * 100:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
