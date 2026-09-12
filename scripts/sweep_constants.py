"""Is a threshold a general property, or was it fitted to our forms?

A rule that describes something real about how forms are laid out keeps working when its
threshold moves — text is still text at 0.8x and 1.2x the cutoff. A rule that was quietly
fitted to the handful of forms in `data/real-forms/` works at exactly the value it was
fitted at and falls over on either side of it.

So this sweeps every tunable constant in the extraction path across a wide range and
reports the score at each. Read the output as:

    a WIDE plateau  -> the constant is not doing the work; the rule is
    a NARROW spike  -> the constant *is* the rule, and it is fitted

This exists to be run by someone who does not want to take the author's word for it,
including after future changes. It scores the development set only, which is already
spoiled — the point here is threshold sensitivity, not accuracy.

    .venv312/bin/python scripts/sweep_constants.py [--quick]
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import field_layout, form_analyzer, word_source
from app.services.canonical_registry import match, norm
from app.services.capture import preprocess

FORMS = ROOT / "data/real-forms"
WORK = ROOT / "artifacts" / "_sweep"

# module, attribute, values to try. The value in use is marked in the output.
SWEEPS = [
    (word_source, "OCR_TARGET_WIDTH", [1275, 1600, 1800, 2400, 3000, 3600]),
    (field_layout, "CAPTION_REACH", [0.25, 0.4, 0.55, 0.7, 0.85, 1.0]),
    (field_layout, "CAPTION_GAP_RATIO", [0.6, 0.9, 1.2, 1.8, 2.4, 3.0]),
    (field_layout, "MAX_BOX_INK", [0.6, 0.7, 0.85, 0.95, 0.99]),
    (field_layout, "CHECKBOX_MIN_SIDE", [0.003, 0.006, 0.009, 0.012]),
    (field_layout, "CHECKBOX_MAX_SIDE", [0.02, 0.03, 0.045, 0.06]),
    (form_analyzer, "VALUE_GAP_RATIO", [3.0, 5.0, 8.0, 12.0, 20.0, 40.0]),
    (form_analyzer, "OPTION_GAP_RATIO", [3.0, 5.0, 8.0, 12.0, 20.0]),
    (form_analyzer, "MIN_MARK_STEP", [0.6, 1.0, 1.4, 1.8, 2.4]),
    (form_analyzer, "MIN_WRITTEN_ANSWER", [1, 2, 3, 5, 8]),
    (form_analyzer, "MARKED_INK_RATIO", [0.002, 0.004, 0.008, 0.016]),
]


def comparable(value) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def score() -> tuple[int, int]:
    """Dev-set fields read correctly, scored exactly as evaluate_real_forms.py does."""
    truth = json.loads((FORMS / "ground_truth.json").read_text())
    analyzer = form_analyzer.FormAnalyzer()
    correct = total = 0
    for name, expected in truth.items():
        if name.startswith("_"):
            continue
        path = FORMS / name
        if not path.exists():
            continue
        folder = WORK / name.replace(".", "_")
        if not folder.exists():
            folder.mkdir(parents=True)
            kind = "pdf" if path.suffix.lower() == ".pdf" else "image"
            if kind == "pdf":
                shutil.copy(path, folder / "source.pdf")
            preprocess(path, kind, folder)
        analysis = analyzer.analyze(name, folder)
        found: dict[str, object] = {}
        for field in analysis.detected_fields:
            if field.normalized_value in (None, "", False):
                continue
            for key in (norm(field.label_text), field.canonical_key):
                if key and key not in found:
                    found[key] = field.normalized_value
        for label, want in expected.items():
            total += 1
            mapped, _, _ = match(label)
            have = found.get(norm(label))
            if have is None and mapped:
                have = found.get(mapped.canonical_key)
            correct += have is not None and comparable(have) == comparable(want)
    return correct, total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="three values per constant")
    args = parser.parse_args()

    base_correct, total = score()
    print(f"baseline {base_correct}/{total}\n")
    print(f"{'constant':22} {'value':>8}  {'score':>7}   plateau")
    verdicts = []
    for module, attr, values in SWEEPS:
        original = getattr(module, attr)
        if args.quick:
            values = values[:: max(1, len(values) // 3)]
        results = []
        for value in values:
            setattr(module, attr, value)
            correct, _ = score()
            results.append((value, correct))
        setattr(module, attr, original)

        best = max(c for _, c in results)
        holds = [v for v, c in results if c >= base_correct]
        print()
        for value, correct in results:
            mark = " <- in use" if value == original else ""
            bar = "#" * correct
            print(f"{attr:22} {value!s:>8}  {correct:>3}/{total}   {bar}{mark}")
        # Fitted means: only the value in use holds the score.
        verdict = "FITTED — only one value holds" if len(holds) <= 1 else f"holds at {len(holds)}/{len(results)} values"
        verdicts.append((attr, verdict, best))
        print(f"{'':22} {'':>8}  -> {verdict}")

    print("\n--- summary ---")
    for attr, verdict, _ in verdicts:
        print(f"  {attr:22} {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
