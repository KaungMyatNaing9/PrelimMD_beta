"""Score extraction against hand-labelled real clinic forms.

The synthetic corpus cannot evidence the work plan's accuracy criterion on its own: every
sample there is a clean PIL render of a layout the system was tuned against. This scores
the same pipeline against forms downloaded from public clinic websites, which the system
has never seen.

A field counts as correct only when the value is bound to the right label, so a value read
perfectly but attached to the neighbouring caption still fails. That is the failure mode
real multi-column forms actually produce.

    python scripts/evaluate_real_forms.py [--api http://127.0.0.1:8000] [--verbose]
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


def _mime(path: Path) -> str:
    return "application/pdf" if path.suffix.lower() == ".pdf" else "image/jpeg"


def analyse(api: str, image: Path) -> dict:
    out = subprocess.run(
        ["curl", "-s", "-X", "POST", f"{api}/api/v1/form-analyses",
         "-F", f"file=@{image};type={_mime(image)}"],
        capture_output=True, text=True, check=False,
    ).stdout
    if not out.strip():
        raise SystemExit(f"No response from {api}. Is the backend running?")
    return json.loads(out)


def comparable(value) -> str:
    """Collapse the differences that are not extraction errors: case, spacing, and the
    separators a phone or date may legitimately be written with."""
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--truth", default="ground_truth.json")
    args = parser.parse_args()

    truth = json.loads((FORMS / args.truth).read_text())
    total = correct = 0
    for name, expected in truth.items():
        if name.startswith("_"):
            continue
        image = FORMS / name
        if not image.exists():
            print(f"!! missing {name}")
            continue
        analysis = analyse(args.api, image)
        found = {}
        for field in analysis["detected_fields"]:
            if field["normalized_value"] in (None, "", False):
                continue
            # Index by the label as printed *and* by canonical key. Clinics word the same
            # field differently ("Street address", "Mailing Address"), and the canonical
            # key is what the pipeline is contracted to emit, so a value found under the
            # form's own wording is a success, not a miss.
            for key in (norm(field["label_text"]), field.get("canonical_key")):
                # Keep the first non-empty read: real forms repeat captions per section
                # and the later copies are the blank insurance ones.
                if key and key not in found:
                    found[key] = field["normalized_value"]

        hits = []
        for label, want in expected.items():
            total += 1
            mapped, _, _ = match(label)
            have = found.get(norm(label))
            if have is None and mapped:
                have = found.get(mapped.canonical_key)
            ok = have is not None and comparable(have) == comparable(want)
            correct += ok
            hits.append((ok, label, want, have))

        got = sum(1 for h in hits if h[0])
        print(f"\n{name}  {got}/{len(hits)} = {got / len(hits) * 100:.0f}%")
        for ok, label, want, have in hits:
            if not ok or args.verbose:
                mark = "ok  " if ok else "FAIL"
                detail = "not read" if have is None else f"read {have!r}"
                print(f"   {mark} {label:22} want {want!r:34} {detail}")

    print(f"\nREAL-FORM ACCURACY {correct}/{total} = {correct / total * 100:.1f}%")
    print("Gate 2 criterion: >= 95% on printed text")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
