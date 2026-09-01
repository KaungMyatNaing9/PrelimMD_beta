"""Measure the Gate 3 criterion: how faithfully a real form round-trips.

A round trip is the whole product in one line: read a filled form, learn its blank
template, fill that template from the values read, and read the result back. A value that
survives all four steps was extracted correctly, stored under the right field, and printed
in the right place -- which is what "faithful" has to mean if it is to be measurable.

Synthetic corpus templates are deliberately excluded; only layouts downloaded from real
clinics count.

    python scripts/evaluate_roundtrip.py [--api http://127.0.0.1:8000]
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.canonical_registry import match, norm

FORMS = ROOT / "data/real-forms"


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


def comparable(value) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def readable(analysis: dict) -> dict:
    found = {}
    for field in analysis["detected_fields"]:
        if field["normalized_value"] in (None, "", False):
            continue
        for key in (norm(field["label_text"]), field.get("canonical_key")):
            if key and key not in found:
                found[key] = field["normalized_value"]
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    truth = json.loads((FORMS / "ground_truth.json").read_text())
    kept = total = 0
    faithful = 0
    for name, expected in truth.items():
        if name.startswith("_") or name.endswith(".pdf"):
            continue
        image = FORMS / name
        if not image.exists():
            continue

        first = analyse(args.api, image)
        # Learn this form's own blank master, then fill it from what was just read.
        derived = json.loads(
            _curl(
                "-X",
                "POST",
                f"{args.api}/api/v1/form-analyses/{first['analysis_id']}"
                f"/derive-template?template_name={image.stem}",
            )
        )
        template_id = derived.get("template_id")
        if not template_id:
            print(f"\n{name}: template could not be derived -- {str(derived)[:80]}")
            continue

        out = ROOT / "artifacts" / f"roundtrip_{image.stem}.pdf"
        out.parent.mkdir(parents=True, exist_ok=True)
        _curl(
            "-o",
            str(out),
            f"{args.api}/api/v1/form-analyses/{first['analysis_id']}"
            f"/regenerate?template_id={template_id}",
        )
        # The regenerated PDF carries the blank form as a raster and the values as text,
        # so reading its text layer would see values with no labels to bind them to.
        # Rasterising first is also the fairer test: it is how a person would receive it.
        subprocess.run(
            ["pdftoppm", "-jpeg", "-r", "150", "-f", "1", "-l", "1", str(out), str(out.with_suffix(""))],
            check=True,
        )
        again = readable(analyse(args.api, out.with_name(out.stem + "-1.jpg")))

        survived = []
        for label, want in expected.items():
            total += 1
            mapped, _, _ = match(label)
            have = again.get(norm(label))
            if have is None and mapped:
                have = again.get(mapped.canonical_key)
            ok = have is not None and comparable(have) == comparable(want)
            kept += ok
            survived.append((ok, label, want, have))

        good = sum(1 for s in survived if s[0])
        share = good / len(survived) if survived else 0
        faithful += share >= 0.5
        print(f"\n{name}  ->  {template_id}  {good}/{len(survived)} survived = {share*100:.0f}%")
        for ok, label, want, have in survived:
            mark = "ok  " if ok else "lost"
            print(f"   {mark} {label:22} {want!r:34} -> {have!r}")

    print(f"\nROUND-TRIP {kept}/{total} values survived = {kept / total * 100:.1f}%")
    print(f"Forms retaining at least half their values: {faithful}")
    print("Gate 3 criterion: >= 3 real layouts round-trip faithfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
