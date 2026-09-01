"""Run both extraction paths against the same form and show them side by side.

    .venv312/bin/python scripts/compare_extractors.py <form>          # both
    .venv312/bin/python scripts/compare_extractors.py <form> --geo    # geometric only
    .venv312/bin/python scripts/compare_extractors.py --all           # every real form

Version 1 (geometric + OCR) is what the app uses today: fast, exact coordinates, strong
on boxed layouts, weak on dense tables and rotated pages.

Version 2 (vision model) reads the page like a person: strong on tables, rotated scans
and handwriting, slower, and it gives no coordinates. Needs `ollama serve` and
`ollama pull qwen2.5vl:7b`.
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("VISION_PROVIDER", "ollama")
os.environ.setdefault("VISION_TIMEOUT_SECONDS", "300")

from app.services.capture import preprocess

REAL_FORMS = Path(__file__).resolve().parents[1] / "data" / "real-forms"


def show(form: Path, run_geo: bool, run_vision: bool) -> None:
    kind = "pdf" if form.suffix.lower() == ".pdf" else form.suffix.lower().lstrip(".")
    kind = {"jpeg": "jpg"}.get(kind, kind)

    print(f"\n{'=' * 74}\n{form.name}\n{'=' * 74}")
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        shutil.copy(form, folder / f"source.{kind}")
        preprocess(folder / f"source.{kind}", kind, folder)

        if run_geo:
            from app.services.form_analyzer import FormAnalyzer

            started = time.monotonic()
            analysis = FormAnalyzer().analyze("cmp", folder)
            print(
                f"\n-- v1 geometric + OCR -- {len(analysis.detected_fields)} fields "
                f"in {time.monotonic() - started:.0f}s"
            )
            for f in analysis.detected_fields[:15]:
                print(f"   {f.label_text[:32]!r:34} = {str(f.normalized_value)[:30]!r}")
            if len(analysis.detected_fields) > 15:
                print(f"   ... {len(analysis.detected_fields) - 15} more")

        if run_vision:
            from app.services.vision_extractor import extract

            started = time.monotonic()
            try:
                fields = extract(folder / "normalized_color.png")
            except Exception as exc:  # noqa: BLE001 - CLI: report, don't traceback
                print(f"\n-- v2 vision model -- failed: {exc}")
                return
            print(
                f"\n-- v2 vision model -- {len(fields)} fields "
                f"in {time.monotonic() - started:.0f}s"
            )
            for f in fields[:15]:
                mark = "  [handwritten]" if f.handwritten else ""
                print(f"   {f.label[:32]!r:34} = {str(f.value)[:30]!r}{mark}")
            if len(fields) > 15:
                print(f"   ... {len(fields) - 15} more")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    run_geo = "--vision" not in flags
    run_vision = "--geo" not in flags

    if "--all" in flags:
        forms = sorted(REAL_FORMS.glob("*.pdf"))
        if not forms:
            print(f"No forms in {REAL_FORMS}")
            return 1
    elif args:
        forms = [Path(args[0])]
    else:
        print(__doc__)
        return 2

    for form in forms:
        if not form.exists():
            print(f"No such file: {form}")
            return 1
        show(form, run_geo, run_vision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
