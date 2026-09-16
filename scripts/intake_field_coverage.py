"""The FUNSD coverage measure, run on the distribution the product actually serves.

On FUNSD the field builder proposes a value-carrying field for only 45.9% of annotated
pairs, and that shortfall -- not OCR, not binding -- is the largest bucket of loss we
control. But FUNSD is tobacco-industry memos and dense tables, not patient intake. Before
betting an architecture change on that number, the same measure has to be taken here.

Coverage is deliberately *not* accuracy: it asks whether the builder proposed a
value-carrying candidate at all, not whether it read the value correctly. A builder that
proposes a field per ground-truth answer and misreads half of them has a reading problem;
one that proposes half the fields has a shape problem, and no reader fixes that.

    python scripts/intake_field_coverage.py [--api ...] [--set TEST2 --set TEST4]
"""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORMS = ROOT / "data" / "real-forms"


def truth_for(folder: Path) -> dict[str, dict]:
    for name in ("ground_truth_new.json", "ground_truth.json"):
        if (folder / name).exists():
            data = json.loads((folder / name).read_text())
            return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}
    return {}


def analyse(api: str, image: Path) -> dict:
    kind = "application/pdf" if image.suffix.lower() == ".pdf" else "image/jpeg"
    out = subprocess.run(
        ["curl", "-s", "--max-time", "600", "-X", "POST", f"{api}/api/v1/form-analyses",
         "-F", f"file=@{image};type={kind}"],
        capture_output=True, text=True, check=False,
    ).stdout
    return json.loads(out) if out.strip() else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--set", action="append", dest="sets", default=None)
    args = parser.parse_args()

    grand_truth = grand_valued = grand_good = 0
    for name in args.sets or ["TEST2", "TEST4", "TEST5"]:
        folder = FORMS / name
        truth = truth_for(folder)
        if not truth:
            print(f"{name}: no ground truth found")
            continue
        print(f"\n== {name}")
        print(f"{'form':28} {'fields':>6} {'valued':>6} {'truth':>6} {'coverage':>9}")
        set_truth = set_valued = set_good = 0
        for form, expected in sorted(truth.items()):
            image = folder / form
            if not image.exists():
                continue
            result = analyse(args.api, image)
            fields = result.get("detected_fields", [])
            valued = [f for f in fields if str(f.get("normalized_value") or "").strip()]
            want = len([v for v in expected.values() if str(v).strip()])
            set_truth += want
            set_valued += len(valued)
            wanted = {str(v).strip().lower() for v in expected.values() if str(v).strip()}
            # A proposed value is vouched for if it is one of the answers actually written
            # on the page. Everything else is printed furniture the builder mistook for an
            # answer -- and `evaluate_real_forms.py` cannot see those, because it scores
            # only labels the ground truth names.
            # Separators collapsed on both sides, as `evaluate_real_forms.py` does now: the
            # truth writes a phone four ways and the reader one, and raw comparison
            # counted three of the four as junk.
            def _cmp(v):
                return "".join(ch for ch in str(v).lower() if ch.isalnum())
            wanted_c = {_cmp(w) for w in wanted} - {""}
            good = sum(1 for f in valued if _cmp(f.get("normalized_value")) and any(
                _cmp(f.get("normalized_value")) in w
                or w in _cmp(f.get("normalized_value")) for w in wanted_c))
            set_good += good
            print(f"{form[:28]:28} {len(fields):6} {len(valued):6} {want:6} "
                  f"{len(valued) / want * 100 if want else 0:8.1f}%", flush=True)
        grand_truth += set_truth
        grand_valued += set_valued
        grand_good += set_good
        if set_truth:
            print(f"{name} proposed {set_valued} values for {set_truth} real answers; "
                  f"{set_good} match an answer ({set_good / set_valued * 100:.1f}% precision)")

    if grand_truth:
        print(f"\nINTAKE {grand_valued} values proposed for {grand_truth} real answers "
              f"= {grand_valued / grand_truth * 100:.0f}% of the page invented")
        print(f"  of those, {grand_good} match an answer actually written on the form "
              f"= {grand_good / grand_valued * 100:.1f}% precision")
        print("  FUNSD, same measure: 45.9% -- the builder is starved there and noisy here.")
        print("  Nothing else in the project measures this: evaluate_real_forms.py scores")
        print("  only labels the ground truth names, so an invented field is invisible to it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
