"""Score the vision model against the same real forms and ground truth as the OCR path.

``compare_extractors.py`` shows the two readings side by side but scores neither, so there
has never been a vision number comparable to the geometric one. This produces both halves
of the trade the project actually has to decide: accuracy against seconds per page.

Fields are matched on the label the model reports, resolved through the canonical registry,
exactly as ``evaluate_real_forms.py`` does -- otherwise the two numbers would not be
comparable, which is the whole point.

    ollama serve && ollama pull qwen2.5vl:7b
    .venv312/bin/python scripts/evaluate_vision.py [--truth ground_truth.json]
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("VISION_PROVIDER", "ollama")
os.environ.setdefault("VISION_TIMEOUT_SECONDS", "300")

from app.services.canonical_registry import match, norm
from app.services.vision_extractor import VisionTruncated, extract

FORMS = ROOT / "data/real-forms"


def comparable(value) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", default="ground_truth.json")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    truth = json.loads((FORMS / args.truth).read_text())
    correct = total = 0
    elapsed: list[float] = []
    for name, expected in truth.items():
        if name.startswith("_") or name.endswith(".pdf"):
            continue
        image = FORMS / name
        if not image.exists():
            continue

        started = time.monotonic()
        truncated = ""
        try:
            fields = extract(image)
        except VisionTruncated as exc:
            # A cut-off list is still a real reading of everything before the cut, so it is
            # scored rather than discarded -- silently dropping it would flatter the model.
            fields = getattr(exc, "fields", []) or []
            truncated = "  (TRUNCATED)"
        except Exception as exc:  # noqa: BLE001 - report and keep going
            print(f"\n{name}: vision failed -- {type(exc).__name__}: {exc}")
            continue
        took = time.monotonic() - started
        elapsed.append(took)

        found: dict[str, str] = {}
        for field in fields:
            if not field.value:
                continue
            mapped, _, _ = match(field.label)
            for key in (norm(field.label), mapped.canonical_key if mapped else None):
                if key and key not in found:
                    found[key] = field.value

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

        good = sum(1 for h in hits if h[0])
        print(
            f"\n{name}  {good}/{len(hits)} = {good / len(hits) * 100:.0f}%"
            f"  [{took:.0f}s, {len(fields)} fields read]{truncated}"
        )
        for ok, label, want, have in hits:
            if not ok or args.verbose:
                print(f"   {'ok  ' if ok else 'FAIL'} {label:22} want {want!r:32} got {have!r}")

    if not elapsed:
        print("\nNo pages were read. Is `ollama serve` running?")
        return 1
    print(f"\nVISION ACCURACY {correct}/{total} = {correct / total * 100:.1f}%")
    print(f"Speed: {sum(elapsed) / len(elapsed):.1f}s per page mean, {max(elapsed):.0f}s worst")
    print("Compare: scripts/evaluate_real_forms.py for the geometric + OCR path")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
