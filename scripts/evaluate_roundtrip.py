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
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help=(
            "Resolution the regenerated PDF is rasterised at before being re-read. "
            "The default is what a middling scan gives; raise it to separate regeneration "
            "fidelity from the OCR floor."
        ),
    )
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    registry_before = _registered(args.api)

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
        try:
            _curl(
                "-o",
                str(out),
                f"{args.api}/api/v1/form-analyses/{first['analysis_id']}"
                f"/regenerate?template_id={template_id}",
            )
        # The regenerated PDF carries the blank form as a raster and the values as text,
        # so reading its text layer would see values with no labels to bind them to.
        # Rasterising first is also the fairer test: it is how a person would receive it.
        #
        # The resolution is a parameter because it decides what this script measures. At
        # 150 dpi most of the losses are single characters -- 15206 read back as 45206,
        # 09/12/1979 as 99/12/1979 -- which is this project's OCR floor rather than any
        # infidelity in the regeneration. Run it at both and the difference says which of
        # the two is being scored.
            subprocess.run(
                ["pdftoppm", "-jpeg", "-r", str(args.dpi), "-f", "1", "-l", "1",
                 str(out), str(out.with_suffix(""))],
                check=True,
            )
        finally:
            # Release before re-reading, not after. The template was derived *from this
            # very page*, and while it stays registered it is matched against the
            # regenerated copy and merged into the reading -- so the round trip would be
            # scoring the template's influence on its own re-read rather than the fidelity
            # of the regeneration. It is circular, and on one form it was actively worse:
            # the surname read `ee` with the template live and `Okonkwo` without it.
            #
            # In a `finally` because releasing only on the happy path leaks: any failure
            # above leaves the template registered for every later run. Measured
            # 2026-09-11, this script and `evaluate_placement.py` were both leaking.
            _release(args.api, template_id)
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

    if _registered(args.api) != registry_before:
        print("!! templates leaked -- registry changed during this run, so "
              "every later measurement is against a polluted registry")
    print(f"\nROUND-TRIP {kept}/{total} values survived = {kept / total * 100:.1f}%")
    print(f"Forms retaining at least half their values: {faithful}")
    print("Gate 3 criterion: >= 3 real layouts round-trip faithfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
