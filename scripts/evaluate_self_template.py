"""Two passes over an unseen form: learn its structure, then read it through that.

A clinic's forms repeat, and reading a form the system has already learned scores far
better than reading it cold -- `evaluate_enrolled.py` measures +12.7 points on TEST4. But
that needs the blank form in advance. This asks whether the system can do it *unaided* on a
form it has never seen: analyse the page once to learn where its answer regions are, then
read the same page again through the template that produced.

It is not circular, and the distinction matters. `derive_template` builds from the page's
**printed geometry** -- the rules, boxes and captions the form was printed with -- and
explicitly not from the values the first pass read ("Always the geometric view, never the
analysis's own fields"). Structure and content are independent, and the ground truth comes
from where `fill_forms.py` physically placed each value beside its printed label, never
from this pipeline. So a gain here is a real gain in reading, not a measurement artefact.

Every template it derives is released again, and the registry is checked: a stray derived
template is matched against every later upload, and seventeen of them once inflated two
Gate 3 figures unnoticed.

    python scripts/evaluate_self_template.py [--set TEST4] [--api ...]
"""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORMS = ROOT / "data" / "real-forms"


def curl(*args: str) -> str:
    return subprocess.run(["curl", "-s", "--max-time", "900", *args],
                          capture_output=True, text=True, check=False).stdout


def as_json(text: str) -> dict:
    try:
        return json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return {}


def analyse(api: str, image: Path, template_id: str | None = None) -> dict:
    kind = "application/pdf" if image.suffix.lower() == ".pdf" else "image/jpeg"
    args = ["-X", "POST", f"{api}/api/v1/form-analyses", "-F", f"file=@{image};type={kind}"]
    if template_id:
        args += ["-F", f"template_id={template_id}"]
    return as_json(curl(*args))


def templates(api: str) -> list[str]:
    return sorted(as_json(curl(f"{api}/api/v1/health")).get("templates", []))


def scored(analysis: dict, expected: dict) -> tuple[int, int]:
    """Correct fields against the values actually written on the page."""
    read = {}
    for field in analysis.get("detected_fields", []):
        value = str(field.get("normalized_value") or "").strip()
        if value:
            read.setdefault(str(field.get("label_text") or "").strip().lower(), value)
    hit = 0
    for want in expected.values():
        want = str(want).strip().lower()
        if any(want == v.lower() or want in v.lower() for v in read.values()):
            hit += 1
    return hit, len(expected)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--set", dest="which", default="TEST4")
    args = parser.parse_args()

    folder = FORMS / args.which
    truth = json.loads((folder / "ground_truth_new.json").read_text())
    before = templates(args.api)
    cold_hit = cold_all = warm_hit = warm_all = 0
    created: list[str] = []

    print(f"{'form':30} {'cold':>9} {'self-taught':>12}")
    try:
        for name, expected in sorted(truth.items()):
            if name.startswith("_") or not isinstance(expected, dict):
                continue
            image = folder / name
            if not image.exists():
                continue

            first = analyse(args.api, image)
            if not first.get("analysis_id"):
                continue
            hit, total = scored(first, expected)
            cold_hit += hit
            cold_all += total

            learned = as_json(curl(
                "-X", "POST",
                f"{args.api}/api/v1/form-analyses/{first['analysis_id']}/derive-template",
            ))
            template_id = learned.get("template_id")
            if not template_id:
                # Nothing to learn from this page, so the second pass is the first.
                warm_hit += hit
                warm_all += total
                print(f"{name[:30]:30} {hit:4}/{total:<4} {'(no template)':>12}")
                continue
            created.append(template_id)

            second = analyse(args.api, image, template_id)
            warm, warm_total = scored(second, expected)
            warm_hit += warm
            warm_all += warm_total
            flag = "  <-- worse" if warm < hit else ("  <-- better" if warm > hit else "")
            print(f"{name[:30]:30} {hit:4}/{total:<4} {warm:7}/{warm_total:<4}{flag}")
    finally:
        for template_id in created:
            curl("-X", "DELETE", f"{args.api}/api/v1/templates/{template_id}")
        after = templates(args.api)
        if after != before:
            print(f"\n!! registry not restored: {before} -> {after}")

    if cold_all:
        print(f"\ncold          {cold_hit}/{cold_all} = {cold_hit / cold_all * 100:.1f}%")
        print(f"self-taught   {warm_hit}/{warm_all} = {warm_hit / warm_all * 100:.1f}%")
        print(f"{len(created)} templates learned and released")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
