"""Count the mistakes a regenerated form can make, on every real blank we have.

Placement is measured elsewhere (`evaluate_placement.py`, millimetres) and so is whether a
value survives a round trip (`evaluate_roundtrip.py`). This asks a different question, the
one a clinic would ask: does the copy say anything the original did not?

Five mistakes, counted per form:

* **dropped** -- a value the reader found that is neither drawn nor reported as unplaced.
  A value missing from the copy without a word about it is the worst of these.
* **written twice / not written** -- the words on the copy are not the words it planned
  to write, one for one.
* **over printing** -- a drawn value covering a word the form prints, which hides the
  form's own text under an answer.
* **loose tick** -- an X drawn where the blank prints no box.
* **ticks not drawn** -- a tick the reader reported that no X marks, or an X too many.

Everything runs in this process against a scratch registry, so it needs no server:

    python scripts/evaluate_regeneration.py [--set LARGE] [--forms 30]
"""

import argparse
import inspect
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# ``--backend`` points the audit at another checkout's code, so the same measure can be
# taken of a change and of what it changed.
_backend = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--backend=")), None)
sys.path.insert(0, _backend or str(ROOT / "backend"))

SCRATCH = Path(tempfile.mkdtemp(prefix="regen-audit-"))
os.environ.setdefault("VISION_PROVIDER", "disabled")
os.environ.setdefault("SLOT_VISION", "disabled")
os.environ.setdefault("LAYOUT_CHOICE", "assign")
os.environ["DERIVED_TEMPLATES_DIR"] = str(SCRATCH / "templates")

from app.services import pdf_backend
from app.services import regeneration as R
from app.services.field_layout import detect_checkboxes
from app.services.form_analyzer import FormAnalyzer
from app.services.template_builder import derive_template
from app.services.templates import canonical_image_path, registry

FORMS = ROOT / "data/real-forms"
# How much of a printed word's height a drawn value has to reach across before it is
# hiding it rather than passing under it: lines of print nearly touch.
COVERED = 0.35
WHY = "--why" in sys.argv


# A rule a form draws with characters: "DOB:______________" is one word to a text layer,
# and the box it reports covers the whole line the answer is meant to go on.
RULE_RUN = re.compile(r"[_.\u00b7\u2022\u2500-\u257f-]{3,}")


def printed_boxes(
    blank: Path, width: float, height: float, template, index: int = 0
) -> list[tuple[float, float, float, float]]:
    """The box each word the blank prints occupies, in the regenerated page's units.

    Read here from the blank's own text layer rather than from the template, so that what
    this counts does not depend on what regeneration was told. Rules drawn as underscores
    are dropped: a value belongs on the line, and a word that carries one reports a box
    stretching the length of it.
    """
    try:
        page = pdf_backend.open_page(blank, index)
        try:
            words, w, h = page.words(), page.width, page.height
        finally:
            page.close()
    except Exception:  # noqa: BLE001 - an image blank has no text layer
        words, w, h = [], 0.0, 0.0
    if not words or not w or not h:
        # An image blank: the template's OCR of its render is the only reading there is.
        return [
            (sx0 * width, sy0 * height, sx1 * width, sy1 * height)
            for sx0, sy0, sx1, sy1 in getattr(template.pages[index], "printed_spans", [])
            if index < len(template.pages)
        ]
    boxes = []
    for word in words:
        kept = max(RULE_RUN.split(word.text), key=len, default="")
        # A word is something with a letter or a digit in it: a value written across the
        # "-" a form prints inside its SSN rule hides nothing.
        if not any(ch.isalnum() for ch in kept):
            continue
        start = word.text.index(kept)
        span = word.x1 - word.x0
        boxes.append(
            (
                (word.x0 + span * start / len(word.text)) * width / w,
                word.y0 * height / h,
                (word.x0 + span * (start + len(kept)) / len(word.text)) * width / w,
                word.y1 * height / h,
            )
        )
    return boxes


def comparable(value) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def words_of(text: str) -> list[str]:
    return [comparable(part) for part in str(text).split() if comparable(part)]


def analyse(source: Path, name: str) -> tuple[Path, object]:
    folder = SCRATCH / name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    shutil.copy(source, folder / ("source.pdf" if source.suffix == ".pdf" else "source.jpg"))
    return folder, FormAnalyzer().analyze(name, folder)


def audit(blank: Path, fill: Path) -> Counter:
    """Every mistake on one form's regenerated copy, on every page of it."""
    found: Counter = Counter()
    blank_dir, blank_read = analyse(blank, blank.stem + "-blank")
    template = derive_template(blank_read, blank_dir / "normalized_color.png", blank=True)
    registry.load()
    _, filled = analyse(fill, fill.stem + "-filled")
    if filled.matched_known_template != template.template_id:
        found["form not matched to its own blank"] += 1
        return found

    values = R.values_from_analysis(filled, template)
    own = template.derived_from == filled.analysis_id
    indices = [page.page_index for page in template.pages]
    loose = {index: (R.loose_values(filled, template, index) if own else []) for index in indices}
    ticks = {index: R.ticks_from_analysis(filled, index) for index in indices}
    # Code that cannot say what it planned to write is still audited for what it wrote.
    planned = getattr(R, "plan", None)
    plans = {
        index: (planned(template, values, index, loose[index]) if planned else ([], []))
        for index in indices
    }
    out = SCRATCH / f"{blank.stem}.pdf"
    out.write_bytes(regenerated(template, values, ticks, loose))
    if not planned:
        found["plan not measured"] += 1

    written: set[str] = set()
    for index in range(pdf_backend.page_count(out)):
        page = pdf_backend.open_page(out, index)
        try:
            drawn = [w for w in page.words() if w.text.strip()]
            width, height = page.width, page.height
        finally:
            page.close()
        texts, boxes = plans.get(index, ([], []))
        written |= {comparable(t.text) for t in texts} | {comparable(b.text) for b in boxes}
        written |= {comparable(t) for _, t, _ in loose.get(index, [])}

        # What the page carries, word for word, is what it planned to write: anything
        # missing was not written, anything over is written twice or was never asked for.
        # Counted as words rather than whole values, because one value can be a part of
        # another ("523 Oakhaven" inside "523 Oakhaven Circle").
        if planned:
            wanted = Counter(tok for t in texts for tok in words_of(t.text))
            wanted += Counter(tok for b in boxes for tok in words_of(b.text))
            carried = Counter(tok for w in drawn if w.text.strip() != "X" for tok in words_of(w.text))
            found["not written"] += sum((wanted - carried).values())
            found["written twice"] += sum((carried - wanted).values())
            if WHY and (wanted - carried or carried - wanted):
                print(
                    f"    p{index} planned not carried: {dict(wanted - carried)} /"
                    f" carried not planned: {dict(carried - wanted)}",
                    flush=True,
                )

        # A written value over a word the form prints.
        printed = printed_boxes(blank, width, height, template, index)
        for word in drawn:
            if word.text.strip() == "X":
                continue
            over = [
                (sx0, sy0)
                for sx0, sy0, sx1, sy1 in printed
                if min(word.x1, sx1) - max(word.x0, sx0) > 0
                and min(word.y1, sy1) - max(word.y0, sy0) > COVERED * (sy1 - sy0)
            ]
            if over:
                found["over printing"] += 1
                if WHY:
                    print(
                        f"    p{index} over printing: {word.text!r} at x {word.x0:.0f}-{word.x1:.0f}"
                        f" covers {over}",
                        flush=True,
                    )

        # Every X inside a box the blank prints, and no more marks than ticks reported.
        master = canonical_image_path(template.template_id, index)
        import cv2

        grey = cv2.imread(str(master), cv2.IMREAD_GRAYSCALE)
        scale = width / grey.shape[1] if grey is not None else 1.0
        tick_boxes = detect_checkboxes(master) if grey is not None else []
        marks = [w for w in drawn if w.text.strip() == "X"]
        for mark in marks:
            cx, cy = (mark.x0 + mark.x1) / 2 / scale, (mark.y0 + mark.y1) / 2 / scale
            if not any(b.x0 <= cx <= b.x1 and b.y0 <= cy <= b.y1 for b in tick_boxes):
                found["loose tick"] += 1
        reported = ticks.get(index, ())
        if len(marks) != len(reported):
            found["ticks not drawn"] += abs(len(reported) - len(marks))
        # And each X is on the box the reader named, not the one beside it: a tick is
        # snapped to the blank's own box, and the wrong neighbour is a different answer.
        for box in reported:
            cx, cy = (box.x + box.width / 2) * width, (box.y + box.height / 2) * height
            near = min(
                (max(abs((m.x0 + m.x1) / 2 - cx), abs((m.y0 + m.y1) / 2 - cy)) for m in marks),
                default=None,
            )
            if near is not None and near > box.width * width:
                found["tick moved"] += 1
                if WHY:
                    print(f"    p{index} tick moved {near:.0f} units from where it was read", flush=True)

    # Every value it was given is either written somewhere or reported as unplaced.
    slots = {
        R.CORPUS_KEY_ALIASES.get(f.field_id, f.field_id)
        for page in template.pages
        for f in page.fields
    }
    # A signature is never synthesised -- "presence and location, not recognition" -- so a
    # slot for one is not a value the copy is meant to carry.
    signatures = {
        R.CORPUS_KEY_ALIASES.get(f.field_id, f.field_id)
        for page in template.pages
        for f in page.fields
        if str(getattr(f.field_type, "value", f.field_type)) == "signature_presence"
    }
    # Reported by text, not by key: the same value sits in ``values`` under its canonical
    # key and under its caption's, and the report names one of the two.
    said = {comparable(values[key]) for key in set(R.unplaced(template, values)) if key in values}
    for key, value in values.items():
        if not planned:
            break
        # A yes/no answer is drawn as a mark, or not at all when it is "no"; ticks are
        # counted on their own above.
        if isinstance(value, bool) or key in signatures:
            continue
        text = comparable(value)
        if not text or text in written or text in said:
            continue
        found["dropped" if key in slots else "dropped without a word"] += 1
        if WHY:
            print(f"    dropped: {key} = {value!r}", flush=True)
    return found


def regenerated(template, values, ticks: dict, loose: dict) -> bytes:
    """The copy, from whichever regeneration this checkout has: every page, or the first."""
    if "page_index" in inspect.signature(R.regenerate).parameters:
        return R.regenerate(template, values, ticks=ticks.get(0, ()), loose=loose.get(0, []))
    return R.regenerate(template, values, ticks=ticks, loose=loose)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", dest="which", default="LARGE", help="the folder holding the blanks")
    parser.add_argument(
        "--fills",
        default="",
        help="the folder holding the filled copies, when they are not beside the blanks "
        "(LARGE_TICK_B holds the ticked copies of LARGE's blanks)",
    )
    parser.add_argument("--forms", type=int, default=0, help="stop after this many forms")
    parser.add_argument("--why", action="store_true", help="name each mistake as it is found")
    parser.add_argument("--only", default="", help="audit just the forms whose name contains this")
    parser.add_argument("--backend", default="", help="another checkout's backend to audit")
    parser.add_argument("--shard", default="", help="\"2/3\": audit every third form, from the second")
    args = parser.parse_args()
    folder = FORMS / args.which
    fills = FORMS / (args.fills or args.which)
    blanks = [p for p in sorted(folder.glob("*.pdf")) if not p.stem.endswith("_FILLED")]
    if args.only:
        blanks = [p for p in blanks if args.only in p.stem]
    if args.shard:
        part, of = (int(n) for n in args.shard.split("/"))
        blanks = blanks[part - 1 :: of]
    if args.forms:
        blanks = blanks[: args.forms]
    totals: Counter = Counter()
    scored = 0
    for blank in blanks:
        fill = fills / f"{blank.stem}_FILLED.pdf"
        if not fill.exists():
            continue
        try:
            found = audit(blank, fill)
        except ValueError as exc:
            # A form no template can be derived from is not a regeneration mistake: it is
            # the enrolment refusing, which it says in its own words.
            found = Counter({"no template could be derived": 1})
            if WHY:
                print(f"    {exc}", flush=True)
        except Exception as exc:  # noqa: BLE001 - a form that cannot be audited is reported
            found = Counter({f"audit failed: {type(exc).__name__}": 1})
        scored += 1
        totals.update(found)
        print(f"{blank.stem[:52]:54} {dict(found) or 'clean'}", flush=True)
    print(f"\n{scored} forms regenerated from their own blank")
    for kind, n in totals.most_common():
        print(f"  {kind:34} {n}")
    if not totals:
        print("  no mistakes")
    shutil.rmtree(SCRATCH, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
