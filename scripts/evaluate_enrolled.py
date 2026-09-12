"""Read benchmark forms the way a clinic would actually use them: enrolled first.

Every accuracy number this project reports is a *zero-shot* number. Only three templates
exist, for the dev forms, so TEST2 and TEST4 are read as "a form we have never seen". In
deployment that is the rare case: a clinic has a handful of intake forms and uses each one
thousands of times. The common case is a form we have seen four hundred times.

The benchmark data already contains the deployment situation -- every form is present as a
blank PDF and a filled copy, which is exactly what enrolment looks like: the clinic hands
over its blank form, it is learned once, and filled copies are read against it forever.

This enrols each blank, re-scores the filled copies, and then releases every template it
created. The cleanup is not optional: seventeen stray derived templates once inflated
placement from 78.9% to 84.2% and round-trip from 73.9% to 82.6% without anyone noticing.

    python scripts/evaluate_enrolled.py [--set TEST4] [--api ...]
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORMS = ROOT / "data" / "real-forms"


def api_json(args: list[str]) -> dict:
    out = subprocess.run(args, capture_output=True, text=True, check=False).stdout
    try:
        return json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        return {}


def templates(api: str) -> set[str]:
    return set(api_json(["curl", "-s", "--max-time", "20", f"{api}/api/v1/health"])
               .get("templates", []))


def enrol(api: str, blank: Path) -> tuple[str | None, str]:
    """Learn a template from a blank form, as a clinic's first upload would."""
    analysis = api_json(["curl", "-s", "--max-time", "600", "-X", "POST",
                         f"{api}/api/v1/form-analyses",
                         "-F", f"file=@{blank};type=application/pdf"])
    run_id = analysis.get("analysis_id")
    if not run_id:
        return None, "analysis failed"
    made = api_json(["curl", "-s", "--max-time", "600", "-X", "POST",
                     f"{api}/api/v1/form-analyses/{run_id}/derive-template"])
    if made.get("template_id"):
        pages = made.get("pages") or [{}]
        return made["template_id"], f"{len(pages[0].get('fields') or [])} slots"
    detail = made.get("detail")
    message = detail.get("message") if isinstance(detail, dict) else detail
    return None, str(message or "not derivable")[:70]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--set", dest="which", default="TEST4")
    args = parser.parse_args()

    folder = FORMS / args.which
    before = templates(args.api)
    blanks = [p for p in sorted(folder.glob("*.pdf")) if not p.stem.endswith("_FILLED")]
    print(f"enrolling {len(blanks)} blank forms from {args.which}")
    created = []
    for blank in blanks:
        template_id, note = enrol(args.api, blank)
        if template_id:
            created.append(template_id)
        print(f"  {blank.name:28} {template_id or '--':6} {note}")

    print(f"\n{len(created)} templates enrolled; re-scoring {args.which}\n")
    try:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "evaluate_real_forms.py"),
             "--api", args.api, "--truth", f"{args.which}/ground_truth_new.json"],
            check=False,
        )
    finally:
        # Release everything this run created, whatever happened above.
        for template_id in created:
            api_json(["curl", "-s", "--max-time", "30", "-X", "DELETE",
                      f"{args.api}/api/v1/templates/{template_id}"])
        after = templates(args.api)
        print(f"\ntemplates before {sorted(before)}")
        print(f"templates after  {sorted(after)}")
        restored = after == before
        if not restored:
            print("!! template registry not restored -- every later benchmark is suspect")
    return 0 if restored else 1


if __name__ == "__main__":
    raise SystemExit(main())
