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
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services import pdf_backend

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


FORMS = ROOT / "data/real-forms"

def _registered(api: str) -> list[str]:
    """Template ids the server currently has, so a leak is visible rather than inherited."""
    try:
        return sorted(json.loads(_curl(f"{api}/api/v1/health")).get("templates", []))
    except (json.JSONDecodeError, KeyError):
        return []


def _release(api: str, template_id: str) -> None:
    """Delete a template learned only to score a run, *through the server*.

    This used to rmtree the folder and call `registry.load()` -- but in this script's own
    process, not the server's. The server caches its registry in memory, so the template
    stayed live there while its folder vanished from disk: a phantom, matched against every
    later upload and undeletable, curable only by a restart. That is where the stray T50 and
    T51 came from, and the seventeen strays that once inflated placement to 84.2%.

    Derived ids come from a fixed range (T50-T99). A script that learns one per form per
    run and never releases it exhausts that range, and then every later run fails.
    """
    _curl("-X", "DELETE", f"{api}/api/v1/templates/{template_id}")


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


# A value has to be long enough to identify one place on the page. "( )" collapses to
# nothing at all and would match any run of punctuation; three characters match too much.
MIN_KEY_LENGTH = 4


def _lines_from_pdf(pdf: Path) -> list[list[tuple[str, float, float, float, float]]]:
    """Each drawn line of the regenerated page as words in page fractions."""
    page = pdf_backend.open_page(pdf, 0)
    width, height = page.width, page.height
    grouped: dict[tuple[int, int], list[tuple]] = {}
    for word in page.words():
        # One line per block and baseline, as PyMuPDF's (block, line) numbering gave.
        grouped.setdefault((word.block, round(word.y0 * 10)), []).append(
            (word.x0, word.y0, word.x1, word.y1, word.text)
        )
    lines = []
    for run in grouped.values():
        run.sort(key=lambda w: w[0])
        lines.append([(w[4], w[0] / width, w[1] / height, w[2] / width, w[3] / height) for w in run])
    return lines


def _lines_from_image(image: Path) -> list[list[tuple[str, float, float, float, float]]]:
    """Each OCR line of the uploaded page as words in page fractions."""
    from app.services.word_source import group_lines_tolerant, words_from_image
    from PIL import Image

    width, height = Image.open(image).size
    lines = []
    for line in group_lines_tolerant(words_from_image(image, "eng")):
        ordered = sorted(line, key=lambda w: w.x0)
        lines.append(
            [(w.text, w.x0 / width, w.y0 / height, w.x1 / width, w.y1 / height) for w in ordered]
        )
    return lines


def locate(value: str, lines) -> tuple[float, float] | None:
    """Centre of the words spelling ``value``, or None if it is not there exactly once.

    Scanning consecutive-word windows rather than whole lines is what makes the two sides
    comparable. OCR reads the original as "Last Name: Okonkwo" on one line while
    regeneration draws "Okonkwo" alone, so keying whole lines finds the value on neither
    page. A value appearing twice locates nothing and is skipped rather than guessed at.
    """
    want = key(value)
    if len(want) < MIN_KEY_LENGTH:
        return None
    hits = []
    for line in lines:
        for i in range(len(line)):
            joined = ""
            for j in range(i, len(line)):
                joined += key(line[j][0])
                if len(joined) > len(want):
                    break
                if joined == want:
                    span = line[i : j + 1]
                    hits.append(
                        (
                            (min(w[1] for w in span) + max(w[3] for w in span)) / 2,
                            (min(w[2] for w in span) + max(w[4] for w in span)) / 2,
                        )
                    )
                    break
    return hits[0] if len(hits) == 1 else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    registry_before = _registered(args.api)

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
        try:
            _curl(
                "-o",
                str(out),
                f"{args.api}/api/v1/form-analyses/{first['analysis_id']}"
                f"/regenerate?template_id={derived['template_id']}",
            )
            after = _lines_from_pdf(out)
            before = _lines_from_image(image)
        finally:
            # Releasing only on the happy path is how strays accumulate: a template left
            # registered is matched against every later upload, and seventeen of them once
            # inflated this very number from 78.9% to 84.2% with nobody noticing. Measured
            # again 2026-09-11 -- this script really was leaking T50.
            _release(args.api, derived["template_id"])

        page = []
        for field in first["detected_fields"]:
            value = field["normalized_value"]
            if value in (None, "", False):
                continue
            there = locate(str(value), after)
            if there is None:
                continue  # value never reached the page; that is a round-trip miss, not placement
            was = locate(str(value), before)
            if was is None:
                continue  # cannot be located on the original either, so nothing to compare
            dx = (there[0] - was[0]) * PAGE_WIDTH_MM
            dy = (there[1] - was[1]) * PAGE_HEIGHT_MM
            distance = (dx * dx + dy * dy) ** 0.5
            # Keep the signed components, not only the magnitude. Two misses sitting just
            # over the line could be a systematic offset -- every value shifted the same
            # way by the same indent or baseline maths -- or unrelated per-field error, and
            # the magnitude alone cannot tell those apart.
            page.append((distance, field["label_text"], str(value), dx, dy))

        offsets += page
        if page:
            page.sort()
            within = sum(1 for d, *_ in page if d <= TARGET_MM)
            print(f"\n{name}  {len(page)} values placed, {within} within {TARGET_MM}mm")
            print(f"   median {page[len(page)//2][0]:.2f}mm   worst {page[-1][0]:.2f}mm")
            for d, label, value, dx, dy in page:
                if d > TARGET_MM:
                    print(f"      {d:6.2f}mm  dx={dx:+6.2f} dy={dy:+6.2f}  {label[:22]:24} {value[:24]!r}")
        else:
            print(f"\n{name}  no values survived to the page")

    if not offsets:
        print("\nNothing to measure.")
        return 1
    offsets.sort()
    within = sum(1 for d, *_ in offsets if d <= TARGET_MM)
    xs = sorted(o[3] for o in offsets)
    ys = sorted(o[4] for o in offsets)
    print(
        f"\nmedian offset dx={xs[len(xs)//2]:+.2f}mm dy={ys[len(ys)//2]:+.2f}mm "
        "(a systematic bias shows up here; scattered error does not)"
    )
    if _registered(args.api) != registry_before:
        print("!! templates leaked -- registry changed during this run, so "
              "every later measurement is against a polluted registry")
    print(f"\nPLACEMENT {within}/{len(offsets)} within {TARGET_MM}mm = {within/len(offsets)*100:.1f}%")
    print(f"median {offsets[len(offsets)//2][0]:.2f}mm   worst {offsets[-1][0]:.2f}mm")
    print("Gate 3 criterion: regenerated field text within ~2mm of target position")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
