"""Where does legible text die before becoming a field value?

On FUNSD the reader recovers an answer's text 58.6% of the time and the field builder emits
it as a value 32.3% of the time. That ~26-point gap is the largest thing this project
controls, and "the builder is not looking" is not an actionable description of it.

This walks the same page the builder walks and classifies each legible-but-unemitted answer
by the first thing that would stop it:

- **no line**: the words were never grouped onto a line with anything else
- **no label**: its line has no colon and nothing the vocabulary recognises, so the line is
  skipped entirely
- **blocked by a rule**: one of the answer-evidence rules rejected it -- these are ours and
  were added for intake precision, so their cost on scans belongs on the record
- **reached the builder**: nothing here explains it, look further downstream

    python scripts/funsd_builder_loss.py [--limit N]
"""

import argparse
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings
from app.services import form_analyzer as fa
from app.services.capture import preprocess
from app.services.word_source import group_lines_tolerant, words_from_image

DATA = ROOT / "data" / "dataset-scanned"


def flat(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def gold_answers(annotation: Path) -> list[str]:
    entities = json.loads(annotation.read_text())["form"]
    by_id = {e["id"]: e for e in entities}
    out = []
    for entity in entities:
        if entity.get("label") != "question":
            continue
        for a, b in entity.get("linking", []):
            other = by_id.get(b if a == entity["id"] else a)
            if other is not None and other.get("label") == "answer" and flat(other["text"]):
                out.append(other["text"])
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="testing_data")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    annotations = sorted((DATA / args.split / "annotations").glob("*.json"))[: args.limit]
    verdicts: Counter[str] = Counter()
    blocked_examples: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        for n, annotation in enumerate(annotations, 1):
            image = DATA / args.split / "images" / f"{annotation.stem}.png"
            answers = gold_answers(annotation)
            if not image.exists() or not answers:
                continue
            run = Path(tmp) / annotation.stem
            run.mkdir(parents=True, exist_ok=True)
            preprocess(image, "image", run)
            prepared = run / "ocr_gray.png"
            if not prepared.exists():
                prepared = run / "normalized_color.png"
            words = words_from_image(prepared, settings.ocr_language)
            lines = group_lines_tolerant(words)
            # Real page dimensions: the builder normalises boxes by them, and passing 1.0
            # makes every bounding box fail validation.
            with Image.open(prepared) as page:
                page_w, page_h = float(page.width), float(page.height)
            emitted = {
                flat(f.normalized_value)
                for _, f in fa._fields_with_lines(lines, page_w, page_h, "ocr")
                if f.normalized_value not in (None, "", False)
            }
            for answer in answers:
                want = flat(answer)
                if not want:
                    continue
                if any(want in e or e in want for e in emitted if e):
                    continue
                # Only text the reader actually recovered can be blamed on the builder.
                on_page = [ln for ln in lines if want in "".join(flat(w.text) for w in ln)]
                if not on_page:
                    continue
                line = on_page[0]
                texts = [w.text for w in line]
                if any(":" in t for t in texts):
                    verdicts["reached the builder (line has a colon)"] += 1
                    continue
                without = [w for w in line if not fa.is_blank_marker(w.text)]
                ends = fa._caps_label_ends(line) or fa._known_label_ends(line)
                if not ends and len(without) < len(line):
                    ends = fa._caps_label_ends(without) or fa._known_label_ends(without)
                if not ends:
                    verdicts["no label: line has no colon and no known caption"] += 1
                    continue
                typical = fa._typical_gap(lines)
                if not [c for c in ends if fa._has_answer_space(line, c, typical)]:
                    verdicts["blocked: no answer space after the label"] += 1
                    if len(blocked_examples) < 6:
                        blocked_examples.append(" ".join(texts)[:70])
                    continue
                if fa._is_printed_prose(answer):
                    verdicts["blocked: read as printed prose"] += 1
                    continue
                if fa.is_blank_marker(answer):
                    verdicts["blocked: read as blank"] += 1
                    continue
                verdicts["reached the builder, lost downstream"] += 1
            print(f"  {n:3}/{len(annotations)} {annotation.stem}", flush=True)

    total = sum(verdicts.values())
    print(f"\nLEGIBLE ANSWERS THAT NEVER BECAME A VALUE: {total}\n")
    for reason, count in verdicts.most_common():
        print(f"  {count:4}  {count / total * 100:5.1f}%  {reason}")
    if blocked_examples:
        print("\nlines where the answer-space rule refused:")
        for line in blocked_examples:
            print(f"    {line!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
