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
    template_id, note, _ = enrol_run(api, blank)
    return template_id, note


def enrol_run(api: str, blank: Path) -> tuple[str | None, str, str | None]:
    """``enrol``, also returning the analysis the template was learned from."""
    analysis = api_json(["curl", "-s", "--max-time", "600", "-X", "POST",
                         f"{api}/api/v1/form-analyses",
                         "-F", f"file=@{blank};type=application/pdf"])
    run_id = analysis.get("analysis_id")
    if not run_id:
        return None, "analysis failed", None
    made = api_json(["curl", "-s", "--max-time", "600", "-X", "POST",
                     f"{api}/api/v1/form-analyses/{run_id}/derive-template"])
    if not made:
        # A crash answers with a plain-text 500, not JSON. Reporting that as "not
        # derivable" hid a hard cap of fifty derived ids for a whole day.
        return None, "server error (see the backend log)", run_id
    if made.get("template_id"):
        # The route returns a summary (template_id, field_count, field ids), not the
        # template's pages. Reading pages[0].fields here printed "0 slots" for every
        # enrolment, which read as "the template is empty" when it was not.
        return made["template_id"], f"{made.get('field_count', '?')} slots", run_id
    detail = made.get("detail")
    message = detail.get("message") if isinstance(detail, dict) else detail
    return None, str(message or "not derivable")[:70], run_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--set", dest="which", default="TEST4")
    parser.add_argument("--dump", default=None, help="passed through to evaluate_real_forms.py")
    parser.add_argument("--parallel", type=int, default=1, help="scoring workers")
    args = parser.parse_args()

    folder = FORMS / args.which
    before = templates(args.api)
    if before:
        # A template already registered is read through by every upload scored here: a
        # server stopped mid-enrolment finished writing one into a freshly emptied folder
        # and one unseen form was read through it for a whole run (2026-09-16, recall
        # 93.3 -> 86.7 on a set that had not changed). A score is only a score of the
        # templates this run creates.
        print(f"!! the backend already holds templates {sorted(before)}; refusing to score")
        return 1
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
             "--api", args.api, "--truth", f"{args.which}/ground_truth_new.json",
             *(["--dump", args.dump] if args.dump else []),
             *(["--parallel", str(args.parallel)] if args.parallel > 1 else [])],
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
