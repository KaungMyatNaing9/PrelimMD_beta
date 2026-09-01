"""Score extraction on real scanned forms, and check that failures are flagged.

Gate 3 asks for graceful behaviour on poor scans. Graceful has two halves and both are
measured here: how much is read, and -- more important for a clinical tool -- whether what
is *not* read comes back flagged for review rather than silently wrong.

The data is FUNSD's testing split: 50 genuinely scanned forms with question/answer
annotations, so the ground truth is the dataset's, not ours. These are noisy real scans,
not clean renders, which is exactly the condition the criterion is about.

    python scripts/evaluate_scans.py [--limit 50] [--api http://127.0.0.1:8000]
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.canonical_registry import norm

SCANS = ROOT / "data/dataset-scanned/testing_data"


def comparable(value) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def expected_pairs(annotation: Path) -> list[tuple[str, str]]:
    """Question/answer pairs the dataset itself asserts, via its linking edges."""
    form = json.loads(annotation.read_text())["form"]
    by_id = {entry["id"]: entry for entry in form}
    pairs = []
    for entry in form:
        if entry["label"] != "question" or not entry["text"].strip():
            continue
        for source, target in entry["linking"]:
            other = by_id.get(target if source == entry["id"] else source)
            if other and other["label"] == "answer" and other["text"].strip():
                pairs.append((entry["text"].strip(), other["text"].strip()))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    images = sorted((SCANS / "images").glob("*.png"))[: args.limit]
    if not images:
        print(f"No scans under {SCANS}. Is the dataset present?")
        return 1

    total = correct = 0
    unread = wrong_flagged = wrong_total = 0
    per_form = []
    for image in images:
        annotation = SCANS / "annotations" / f"{image.stem}.json"
        if not annotation.exists():
            continue
        pairs = expected_pairs(annotation)
        if not pairs:
            continue

        out = subprocess.run(
            ["curl", "-s", "-X", "POST", f"{args.api}/api/v1/form-analyses",
             "-F", f"file=@{image};type=image/png"],
            capture_output=True, text=True, check=False,
        ).stdout
        if not out.strip():
            raise SystemExit(f"No response from {args.api}. Is the backend running?")
        analysis = json.loads(out)

        read: dict[str, dict] = {}
        for field in analysis["detected_fields"]:
            key = norm(field["label_text"])
            if key and key not in read:
                read[key] = field

        good = 0
        for question, answer in pairs:
            total += 1
            field = read.get(norm(question))
            value = field["normalized_value"] if field else None
            if value not in (None, "", False) and comparable(value) == comparable(answer):
                correct += 1
                good += 1
            elif field is None or value in (None, "", False):
                # No field at all. Nothing wrong is asserted, but nothing is offered
                # either -- the distinction that matters when a clinician reviews it.
                unread += 1
            else:
                wrong_total += 1
                wrong_flagged += 1 if field["needs_review"] else 0
        per_form.append((good / len(pairs), image.stem, good, len(pairs)))

    if not total:
        print("No annotated pairs found.")
        return 1
    per_form.sort()
    print(f"\nscans scored: {len(per_form)}")
    print(f"  worst  {per_form[0][1]}  {per_form[0][2]}/{per_form[0][3]}")
    print(f"  median {per_form[len(per_form)//2][1]}  "
          f"{per_form[len(per_form)//2][2]}/{per_form[len(per_form)//2][3]}")
    print(f"  best   {per_form[-1][1]}  {per_form[-1][2]}/{per_form[-1][3]}")
    print(f"\nSCAN ACCURACY {correct}/{total} = {correct / total * 100:.1f}%")
    print(
        f"  read correctly {correct}   read wrongly {wrong_total}   "
        f"never read {unread} ({unread / total * 100:.0f}%)"
    )
    if wrong_total:
        print(
            f"GRACEFUL: of {wrong_total} values read incorrectly, "
            f"{wrong_flagged} ({wrong_flagged / wrong_total * 100:.0f}%) were flagged for review"
        )
    else:
        print("GRACEFUL: no incorrect values were produced")
    print("Gate 3 criterion: graceful behaviour on poor scans")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
