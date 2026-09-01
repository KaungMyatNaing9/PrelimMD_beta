"""Read a form image with the vision model and print what it found.

Usage:
    .venv312/bin/python scripts/try_vision.py <image>            # vision only
    .venv312/bin/python scripts/try_vision.py <image> --compare  # vs geometric + OCR

Requires a running vision provider. For the local one:
    ollama serve &
    ollama pull qwen2.5vl:7b
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("VISION_PROVIDER", "ollama")

from app.services.capture import preprocess
from app.services.vision_extractor import extract


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    compare = "--compare" in sys.argv
    if not args:
        print(__doc__)
        return 2

    source = Path(args[0])
    if not source.exists():
        print(f"No such file: {source}")
        return 1

    kind = "pdf" if source.suffix.lower() == ".pdf" else source.suffix.lower().lstrip(".")
    kind = {"jpeg": "jpg"}.get(kind, kind)

    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        shutil.copy(source, folder / f"source.{kind}")
        preprocess(folder / f"source.{kind}", kind, folder)
        page = folder / "normalized_color.png"

        if compare:
            from app.services.form_analyzer import FormAnalyzer

            analysis = FormAnalyzer().analyze("cmp", folder)
            print(f"\n─── geometric + OCR ─── {len(analysis.detected_fields)} fields")
            for f in analysis.detected_fields:
                print(f"   {f.label_text!r:34} = {str(f.normalized_value)[:46]!r}")

        provider = os.environ["VISION_PROVIDER"]
        print(f"\n─── vision model ({provider}) ───")
        started = time.monotonic()
        try:
            fields = extract(page)
        except Exception as exc:  # noqa: BLE001 - this is a CLI; report, don't traceback
            print(f"   failed: {exc}")
            return 1
        elapsed = time.monotonic() - started

        print(f"{len(fields)} fields in {elapsed:.0f}s")
        for f in fields:
            mark = "  [handwritten]" if f.handwritten else ""
            print(f"   {f.label!r:34} = {str(f.value)[:46]!r}{mark}")
        if not fields:
            print("   (nothing found — the model saw no labelled fields on this page)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
