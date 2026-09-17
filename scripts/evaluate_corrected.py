"""Enrol every blank, teach each template from one filled copy, score a second one.

The enrolled track's claim is that a form seen once reads better every time after. A blank
teaches where the form *offers* space; a reviewer correcting one filled copy teaches where
its answers *are*, and the stacked and sub-captioned rows that geometry keeps missing are
exactly what that adds. This measures it the only honest way: teach from the fill in one
set (a different invented patient per form) and score the fill in another (a different
patient again), so nothing memorised about one patient can count.

Three ways to place the teaching, `--positions`:

- ``none``     -- the blank alone (the enrolled arm `make bench` already runs).
- ``detected`` -- what today's review UI can confirm: the regions of fields the reader
                  already found whose value the reviewer agrees with.
- ``oracle``   -- where every true value actually sits on the teaching fill, from its
                  PDF text layer. What a reviewer who can also *draw* a region would teach.
                  The ceiling for this mechanism, and the case for that UI affordance.

    python scripts/evaluate_corrected.py --learn LARGE --score LARGE_B --positions oracle
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORMS = ROOT / "data" / "real-forms"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend"))
from app.services import pdf_backend
from evaluate_enrolled import api_json, enrol_run, templates

MAX_PAGES = 5


def comparable(value) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _region(rect: pdf_backend.Rect, page: pdf_backend.Page) -> dict:
    """A slot around where a value sits, with room to its right for a longer one.

    The next patient's surname is longer or shorter than this one's, so the region is
    extended rightwards by at least as much again -- but never past the next word on the
    line, which is the next caption or the next answer. Extended blindly, a two-letter
    state's region reached into the zip beside it and read "TN 37421", and that reading
    then outranked the page's correct "TN". A reviewer drawing the region would stop at
    the caption; so does this."""
    w, h = page.width, page.height
    # A wider left margin than before: the next patient writes a few pixels further left
    # and a region starting inside the first glyph read "Ingrid" as "rid". Less reach
    # above and below: a tall region caught the line beneath a one-line answer.
    x0 = max(0.0, rect.x0 / w - 0.015)
    x1 = min(0.98, rect.x1 / w + max(0.12, 1.5 * rect.width / w))
    beside = [
        word
        for word in page.words()
        if word.x0 >= rect.x1 - 1
        and min(word.y1, rect.y1) - max(word.y0, rect.y0) > 0.5 * rect.height
    ]
    if beside:
        x1 = min(x1, max(rect.x1 / w + 0.01, min(word.x0 for word in beside) / w - 0.005))
    y0 = max(0.0, rect.y0 / h - 0.2 * rect.height / h)
    y1 = min(0.995, rect.y1 / h + 0.2 * rect.height / h)
    return {"x": round(x0, 4), "y": round(y0, 4), "width": round(x1 - x0, 4),
            "height": round(y1 - y0, 4)}


def _nearest_to_caption(hits: list[pdf_backend.Rect], captions: list[pdf_backend.Rect]) -> pdf_backend.Rect:
    """Of several places a value occurs, the one written beside or beneath its caption.

    A patient and their emergency contact can share a surname, and the first occurrence
    on the page was the wrong one often enough to teach "Last Name" the contact's slot."""
    if len(hits) == 1 or not captions:
        return hits[0]

    def distance(hit: pdf_backend.Rect) -> float:
        best = float("inf")
        for cap in captions:
            dx, dy = hit.x0 - cap.x1, hit.y0 - cap.y0
            if dx < -cap.width and dy < -cap.height:
                continue  # above and left of the caption: not its answer
            best = min(best, abs(dx) + 2 * abs(dy))
        return best

    return min(hits, key=distance)


def _whole_word(hit: pdf_backend.Rect, page: pdf_backend.Page, value: str) -> bool:
    """Whether the words under a hit read as the value and nothing more: "PA" inside
    "Partner" and "Zephyr" inside the e-mail are not places a reviewer would put a slot."""
    under = [
        w for w in page.words()
        if pdf_backend.Rect(w.x0, w.y0, w.x1, w.y1).intersects(hit)
        and min(w.y1, hit.y1) - max(w.y0, hit.y0) > 0.5 * (w.y1 - w.y0)
    ]
    text = " ".join(w.text for w in sorted(under, key=lambda w: w.x0))
    return comparable(text) == comparable(value)


def _printed_there(hit: pdf_backend.Rect, blank: pdf_backend.Page | None, value: str) -> bool:
    """Whether the blank prints the same text in the same place -- the option label
    "Male" beside the box, as against the "Male" the patient wrote."""
    if blank is None:
        return False
    return any(
        printed.intersects(hit) and (printed & hit).get_area() > 0.5 * hit.get_area()
        for printed in blank.search(value)
    )


def _where_written(page: pdf_backend.Page, blank: pdf_backend.Page | None, value: str) -> list[pdf_backend.Rect]:
    """The places a reviewer might put the slot for a value, best kind first.

    Whole words the blank does not print are what a reviewer points at. Measured with
    only those allowed (2026-09-15), 150 of 1,489 slots went untaught and the set read
    worse: a patient who writes "Male" on top of the printed "Male" has still written it
    there, and the slot read it once the printing was subtracted. So the stricter kinds
    are preferred, not required -- except that a value shorter than four characters is
    never taught from inside another word, which is how a state got taught "Partner"."""
    hits = page.search(str(value))
    whole = [h for h in hits if _whole_word(h, page, value)]
    if not whole and len(comparable(value)) < 4:
        return []
    pool = whole or hits
    unprinted = [h for h in pool if not _printed_there(h, blank, value)]
    return unprinted or pool


def oracle_corrections(fill: Path, truth: dict[str, str]) -> list[dict]:
    blank_path = fill.with_name(fill.name.replace("_FILLED", ""))
    has_blank = blank_path != fill and blank_path.exists()
    count = min(pdf_backend.page_count(fill), MAX_PAGES)
    pages = [pdf_backend.open_page(fill, index) for index in range(count)]
    blanks = [
        pdf_backend.open_page(blank_path, index) if has_blank and index < pdf_backend.page_count(blank_path) else None
        for index in range(count)
    ]
    out = []
    for label, value in truth.items():
        for index in range(count):
            page, printed_page = pages[index], blanks[index]
            hits = _where_written(page, printed_page, str(value))
            if hits:
                hit = _nearest_to_caption(hits, page.search(label))
                out.append({"page_index": index, "label": label, "value": str(value),
                            "bbox": _region(hit, page)})
                break
    return out


def detected_corrections(api: str, fill: Path, truth: dict[str, str]) -> list[dict]:
    analysis = api_json(["curl", "-s", "--max-time", "600", "-X", "POST",
                         f"{api}/api/v1/form-analyses",
                         "-F", f"file=@{fill};type=application/pdf"])
    run_id = analysis.get("analysis_id")
    if not run_id:
        return []
    analysis = api_json(["curl", "-s", "--max-time", "60",
                         f"{api}/api/v1/form-analyses/{run_id}"])
    wanted = {comparable(v): label for label, v in truth.items()}
    out, seen = [], set()
    for field in analysis.get("detected_fields", []):
        label = wanted.get(comparable(field.get("normalized_value") or ""))
        if label and label not in seen:
            seen.add(label)
            out.append({"page_index": field["page_index"], "label": label,
                        "value": field["normalized_value"], "bbox": field["answer_bbox"]})
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--learn", nargs="+", default=["LARGE"],
                        help="sets whose fills teach; the first set's blanks are enrolled")
    parser.add_argument("--score", default="LARGE_B")
    parser.add_argument("--positions", choices=["none", "detected", "oracle"], default="oracle")
    parser.add_argument("--dump", default=None)
    parser.add_argument("--parallel", type=int, default=1, help="scoring workers")
    parser.add_argument("--no-same-copy", action="store_true",
                        help="diagnostic: do not tell the server a correction is of the enrolled copy")
    parser.add_argument(
        "--templates-dir", default=None,
        help="the folder the backend was started with as DERIVED_TEMPLATES_DIR; with "
        "--snapshot, the taught templates are copied there after enrolment, and reused "
        "from there next time instead of enrolling again",
    )
    parser.add_argument(
        "--snapshot", default=None,
        help="folder to keep (or reuse) the taught templates in; enrolment and teaching "
        "produce the same templates whenever only the reading side changed, and cost "
        "half an hour a run",
    )
    args = parser.parse_args()

    learn = FORMS / args.learn[0]
    truths = {name: json.loads((FORMS / name / "ground_truth_new.json").read_text())
              for name in args.learn}
    before = templates(args.api)
    if before:
        # A template already registered is read through by every upload scored here: a
        # server stopped mid-enrolment finished writing one into a freshly emptied folder
        # and one unseen form was read through it for a whole run (2026-09-16, recall
        # 93.3 -> 86.7 on a set that had not changed). A score is only a score of the
        # templates this run creates.
        print(f"!! the backend already holds templates {sorted(before)}; refusing to score")
        return 1
    snapshot = Path(args.snapshot) if args.snapshot else None
    if snapshot and args.templates_dir and snapshot.is_dir() and any(snapshot.iterdir()):
        # A backend started on an empty folder loads its registry at first use, so the
        # copied templates are live for the scoring below without a restart.
        if before:
            print("!! the backend already holds templates; reuse needs an empty registry")
            return 1
        import shutil

        for folder in snapshot.iterdir():
            shutil.copytree(folder, Path(args.templates_dir) / folder.name, dirs_exist_ok=True)
        created = sorted(p.name for p in snapshot.iterdir())
        print(f"reusing {len(created)} taught templates from {snapshot}; scoring {args.score}\n")
        blanks = []
    else:
        blanks = [p for p in sorted(learn.glob("*.pdf")) if not p.stem.endswith("_FILLED")]
    if blanks:
        print(f"enrolling {len(blanks)} blanks from {args.learn[0]}, teaching from "
              f"{', '.join(args.learn)} with --positions {args.positions}, scoring {args.score}")
        created = []
    taught = 0
    for blank in blanks:
        template_id, note, enrolled_run = enrol_run(args.api, blank)
        if not template_id:
            print(f"  {blank.name:28} --     {note}")
            continue
        created.append(template_id)
        corrections, expected = [], {}
        for name in args.learn:
            fill = FORMS / name / f"{blank.stem}_FILLED.pdf"
            expected = truths[name].get(fill.name, {})
            if args.positions == "oracle" and fill.exists():
                found = oracle_corrections(fill, expected)
            elif args.positions == "detected" and fill.exists():
                found = detected_corrections(args.api, fill, expected)
            else:
                found = []
            # A set with no blank enrols a filled copy in the blank's place; a reviewer
            # correcting that copy is correcting the analysis the template came from, and
            # says so, as the review page does.
            if found and not args.no_same_copy and blank.read_bytes() == fill.read_bytes():
                for correction in found:
                    correction["analysis_id"] = enrolled_run
            corrections += found
        if corrections:
            made = api_json(["curl", "-s", "--max-time", "600", "-X", "POST",
                             f"{args.api}/api/v1/templates/{template_id}/corrections",
                             "-H", "Content-Type: application/json",
                             "-d", json.dumps(corrections)])
            taught += len(corrections)
            note += f"; taught {len(corrections)} of {len(expected)} -> {made.get('field_count', '?')} slots"
        print(f"  {blank.name:28} {template_id:6} {note}")

    if blanks:
        print(f"\n{len(created)} templates enrolled, {taught} corrections taught; scoring {args.score}\n")
        if snapshot and args.templates_dir:
            import shutil

            shutil.rmtree(snapshot, ignore_errors=True)
            shutil.copytree(args.templates_dir, snapshot)
            print(f"taught templates kept in {snapshot}")
    try:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "evaluate_real_forms.py"),
             "--api", args.api, "--truth", f"{args.score}/ground_truth_new.json",
             *(["--dump", args.dump] if args.dump else []),
             *(["--parallel", str(args.parallel)] if args.parallel > 1 else [])],
            check=False,
        )
    finally:
        for template_id in created:
            api_json(["curl", "-s", "--max-time", "30", "-X", "DELETE",
                      f"{args.api}/api/v1/templates/{template_id}"])
        after = templates(args.api)
        print(f"\ntemplates before {sorted(before)}\ntemplates after  {sorted(after)}")
        restored = after == before
        if not restored:
            print("!! template registry not restored -- every later benchmark is suspect")
    return 0 if restored else 1


if __name__ == "__main__":
    raise SystemExit(main())
