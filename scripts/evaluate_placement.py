"""Measure the Gate 3 placement criterion: regenerated text within ~2mm of target.

What "target" means matters. Comparing a regenerated value against the template coordinate
it was drawn from is circular -- it lands there by construction, and would report a
flattering zero. The criterion is only meaningful against where the value sat on the
*original* filled form: refill the form and the text should reappear where it was.

So each value is located twice, once in the upload and once in the regenerated page, and
the distance between the two is reported in millimetres of printed page.

    python scripts/evaluate_placement.py [--api http://127.0.0.1:8000]
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.templates import DERIVED_ROOT, registry

FORMS = ROOT / "data/real-forms"

def _release(template_id: str) -> None:
    """Delete a template learned only to score a run.

    Derived ids come from a fixed range (T50-T99). A script that learns one per form per
    run and never releases it exhausts that range, and then every later run fails.
    """
    folder = DERIVED_ROOT / template_id
    if folder.exists():
        shutil.rmtree(folder)
    registry.load()


# US Letter, the size every form in the set is laid out for. Coordinates are page
# fractions on both sides, so only this physical size converts them to millimetres.
PAGE_WIDTH_MM = 215.9
PAGE_HEIGHT_MM = 279.4
TARGET_MM = 2.0


def _curl(*args: str) -> str:
    return subprocess.run(
        ["curl", "-s", *args], capture_output=True, text=True, check=False
    ).stdout


def analyse(api: str, path: Path) -> dict:
    mime = "application/pdf" if path.suffix.lower() == ".pdf" else "image/jpeg"
    out = _curl("-X", "POST", f"{api}/api/v1/form-analyses", "-F", f"file=@{path};type={mime}")
    if not out.strip():
        raise SystemExit(f"No response from {api}. Is the backend running?")
    return json.loads(out)


def key(value) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def drawn_positions(pdf: Path) -> dict[str, tuple[float, float]]:
    """Centre of every run of text in the regenerated page, as page fractions.

    Words are joined back into the runs they were drawn as, so a multi-word value is
    located as one thing rather than as its first word.
    """
    page = fitz.open(pdf)[0]
    width, height = page.rect.width, page.rect.height
    lines: dict[tuple[int, int], list[tuple]] = {}
    for word in page.get_text("words"):
        lines.setdefault((word[5], word[6]), []).append(word)
    found = {}
    for run in lines.values():
        run.sort(key=lambda w: w[0])
        text = " ".join(w[4] for w in run)
        x0 = min(w[0] for w in run) / width
        x1 = max(w[2] for w in run) / width
        y0 = min(w[1] for w in run) / height
        y1 = max(w[3] for w in run) / height
        found.setdefault(key(text), ((x0 + x1) / 2, (y0 + y1) / 2))
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    truth = json.loads((FORMS / "ground_truth.json").read_text())
    offsets: list[tuple[float, str, str]] = []
    for name in truth:
        if name.startswith("_") or name.endswith(".pdf"):
            continue
        image = FORMS / name
        if not image.exists():
            continue

        first = analyse(args.api, image)
        derived = json.loads(
            _curl(
                "-X",
                "POST",
                f"{args.api}/api/v1/form-analyses/{first['analysis_id']}"
                f"/derive-template?template_name={image.stem}",
            )
        )
        if "template_id" not in derived:
            print(f"{name}: no template ({str(derived)[:60]})")
            continue

        out = ROOT / "artifacts" / f"placement_{image.stem}.pdf"
        out.parent.mkdir(parents=True, exist_ok=True)
        _curl(
            "-o",
            str(out),
            f"{args.api}/api/v1/form-analyses/{first['analysis_id']}"
            f"/regenerate?template_id={derived['template_id']}",
        )
        after = drawn_positions(out)
        _release(derived["template_id"])

        page = []
        for field in first["detected_fields"]:
            value = field["normalized_value"]
            if value in (None, "", False):
                continue
            there = after.get(key(value))
            if there is None:
                continue  # value never reached the page; that is a round-trip miss, not placement
            box = field["answer_bbox"]
            was = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            dx = (there[0] - was[0]) * PAGE_WIDTH_MM
            dy = (there[1] - was[1]) * PAGE_HEIGHT_MM
            distance = (dx * dx + dy * dy) ** 0.5
            page.append((distance, field["label_text"], str(value)))

        offsets += page
        if page:
            page.sort()
            within = sum(1 for d, _, _ in page if d <= TARGET_MM)
            print(f"\n{name}  {len(page)} values placed, {within} within {TARGET_MM}mm")
            print(f"   median {page[len(page)//2][0]:.2f}mm   worst {page[-1][0]:.2f}mm")
            for d, label, value in page[-3:]:
                if d > TARGET_MM:
                    print(f"      {d:6.2f}mm  {label[:24]:26} {value[:28]!r}")
        else:
            print(f"\n{name}  no values survived to the page")

    if not offsets:
        print("\nNothing to measure.")
        return 1
    offsets.sort()
    within = sum(1 for d, _, _ in offsets if d <= TARGET_MM)
    print(f"\nPLACEMENT {within}/{len(offsets)} within {TARGET_MM}mm = {within/len(offsets)*100:.1f}%")
    print(f"median {offsets[len(offsets)//2][0]:.2f}mm   worst {offsets[-1][0]:.2f}mm")
    print("Gate 3 criterion: regenerated field text within ~2mm of target position")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
