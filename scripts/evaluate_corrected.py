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

import fitz

ROOT = Path(__file__).resolve().parents[1]
FORMS = ROOT / "data" / "real-forms"
sys.path.insert(0, str(ROOT / "scripts"))
from evaluate_enrolled import api_json, enrol, templates

MAX_PAGES = 5


def comparable(value) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _region(rect: fitz.Rect, page: fitz.Page) -> dict:
    """A slot around where a value sits, with room to its right for a longer one.

    The next patient's surname is longer or shorter than this one's, so the region is
    extended rightwards by at least as much again -- but never past the next word on the
    line, which is the next caption or the next answer. Extended blindly, a two-letter
    state's region reached into the zip beside it and read "TN 37421", and that reading
    then outranked the page's correct "TN". A reviewer drawing the region would stop at
    the caption; so does this."""
    w, h = page.rect.width, page.rect.height
    x0 = max(0.0, rect.x0 / w - 0.005)
    x1 = min(0.98, rect.x1 / w + max(0.12, 1.5 * rect.width / w))
    beside = [
        word
        for word in page.get_text("words")
        if word[0] >= rect.x1 - 1
        and min(word[3], rect.y1) - max(word[1], rect.y0) > 0.5 * rect.height
    ]
    if beside:
        x1 = min(x1, max(rect.x1 / w + 0.01, min(word[0] for word in beside) / w - 0.005))
    y0 = max(0.0, rect.y0 / h - 0.3 * rect.height / h)
    y1 = min(0.995, rect.y1 / h + 0.3 * rect.height / h)
    return {"x": round(x0, 4), "y": round(y0, 4), "width": round(x1 - x0, 4),
            "height": round(y1 - y0, 4)}


def _nearest_to_caption(hits: list[fitz.Rect], captions: list[fitz.Rect]) -> fitz.Rect:
    """Of several places a value occurs, the one written beside or beneath its caption.

    A patient and their emergency contact can share a surname, and the first occurrence
    on the page was the wrong one often enough to teach "Last Name" the contact's slot."""
    if len(hits) == 1 or not captions:
        return hits[0]

    def distance(hit: fitz.Rect) -> float:
        best = float("inf")
        for cap in captions:
            dx, dy = hit.x0 - cap.x1, hit.y0 - cap.y0
            if dx < -cap.width and dy < -cap.height:
                continue  # above and left of the caption: not its answer
            best = min(best, abs(dx) + 2 * abs(dy))
        return best

    return min(hits, key=distance)


def oracle_corrections(fill: Path, truth: dict[str, str]) -> list[dict]:
    doc = fitz.open(fill)
    out = []
    for label, value in truth.items():
        for index in range(min(doc.page_count, MAX_PAGES)):
            page = doc[index]
            hits = page.search_for(str(value))
            if hits:
                hit = _nearest_to_caption(hits, page.search_for(label))
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
    args = parser.parse_args()

    learn = FORMS / args.learn[0]
    truths = {name: json.loads((FORMS / name / "ground_truth_new.json").read_text())
              for name in args.learn}
    before = templates(args.api)
    blanks = [p for p in sorted(learn.glob("*.pdf")) if not p.stem.endswith("_FILLED")]
    print(f"enrolling {len(blanks)} blanks from {args.learn[0]}, teaching from "
          f"{', '.join(args.learn)} with --positions {args.positions}, scoring {args.score}")
    created, taught = [], 0
    for blank in blanks:
        template_id, note = enrol(args.api, blank)
        if not template_id:
            print(f"  {blank.name:28} --     {note}")
            continue
        created.append(template_id)
        corrections, expected = [], {}
        for name in args.learn:
            fill = FORMS / name / f"{blank.stem}_FILLED.pdf"
            expected = truths[name].get(fill.name, {})
            if args.positions == "oracle" and fill.exists():
                corrections += oracle_corrections(fill, expected)
            elif args.positions == "detected" and fill.exists():
                corrections += detected_corrections(args.api, fill, expected)
        if corrections:
            made = api_json(["curl", "-s", "--max-time", "600", "-X", "POST",
                             f"{args.api}/api/v1/templates/{template_id}/corrections",
                             "-H", "Content-Type: application/json",
                             "-d", json.dumps(corrections)])
            taught += len(corrections)
            note += f"; taught {len(corrections)} of {len(expected)} -> {made.get('field_count', '?')} slots"
        print(f"  {blank.name:28} {template_id:6} {note}")

    print(f"\n{len(created)} templates enrolled, {taught} corrections taught; scoring {args.score}\n")
    try:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "evaluate_real_forms.py"),
             "--api", args.api, "--truth", f"{args.score}/ground_truth_new.json",
             *(["--dump", args.dump] if args.dump else [])],
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
