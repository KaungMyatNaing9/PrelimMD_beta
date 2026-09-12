"""Separate the two ways a FUNSD answer can fail to reach the binder.

`evaluate_funsd.py` reports that the answer text arrives as a field value in only 33.4% of
annotated pairs, so on scanned paper reading -- not binding -- is the first ceiling. That
number alone does not say what to fix: an answer can be missing because Tesseract never
read those characters off the page, or because it read them and the field builder never
turned them into a value. The first is a reader problem, the second is a structure problem,
and they take completely different work.

This runs the pipeline's own image preparation and OCR, then asks only whether the
annotated answer string is present in the page's words at all. Comparing that ceiling with
the 33.4% splits the loss.

    python scripts/funsd_read_ceiling.py [--split testing_data] [--limit N]
"""

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings
from app.services.capture import preprocess
from app.services.word_source import words_from_image

DATA = ROOT / "data" / "dataset-scanned"


def flat(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def gold_links(annotation: Path) -> list[tuple[str, str]]:
    entities = json.loads(annotation.read_text())["form"]
    by_id = {e["id"]: e for e in entities}
    pairs = []
    for entity in entities:
        if entity.get("label") != "question":
            continue
        for a, b in entity.get("linking", []):
            other = by_id.get(b if a == entity["id"] else a)
            if (other is not None and other.get("label") == "answer"
                    and flat(entity["text"]) and flat(other["text"])):
                pairs.append((entity["text"], other["text"]))
    return pairs


def page_text(image: Path, run_dir: Path) -> str:
    """The words the pipeline's reader actually gets, flattened to one string.

    Flattening lets a multi-word answer match even though OCR split or joined it
    differently -- the question here is whether the characters were recovered, not whether
    the tokenisation agrees.
    """
    preprocess(image, "image", run_dir)
    prepared = run_dir / "ocr_gray.png"
    if not prepared.exists():
        prepared = run_dir / "normalized_color.png"
    return "".join(flat(w.text) for w in words_from_image(prepared, settings.ocr_language))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="testing_data")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--engine",
        default=None,
        help="override OCR_ENGINE for this run (tesseract, rapidocr) to compare readers",
    )
    args = parser.parse_args()
    if args.engine:
        settings.ocr_engine = args.engine
    print(f"reader: {settings.ocr_engine}")

    annotations = sorted((DATA / args.split / "annotations").glob("*.json"))
    if args.limit:
        annotations = annotations[: args.limit]

    read = total = 0
    read_q = 0
    with tempfile.TemporaryDirectory() as tmp:
        for n, annotation in enumerate(annotations, 1):
            image = DATA / args.split / "images" / f"{annotation.stem}.png"
            pairs = gold_links(annotation)
            if not image.exists() or not pairs:
                continue
            run_dir = Path(tmp) / annotation.stem
            run_dir.mkdir(parents=True, exist_ok=True)
            text = page_text(image, run_dir)
            for question, answer in pairs:
                total += 1
                read += flat(answer) in text
                read_q += flat(question) in text
            print(f"  {n:3}/{len(annotations)} {annotation.stem:16} "
                  f"{sum(flat(a) in text for _, a in pairs):3}/{len(pairs):<3} answers legible",
                  flush=True)

    print(f"\nFUNSD READ CEILING [{settings.ocr_engine}] {read}/{total} = "
          f"{read / total * 100:.1f}% of annotated answers are present in the page's OCR at all")
    print(f"  questions legible {read_q}/{total} = {read_q / total * 100:.1f}%")
    print("  Compare with evaluate_funsd.py: answers arriving as a field value 33.4%,")
    print("  pairs bound 20.4%. The gap between this number and 33.4% is text the reader")
    print("  recovered and the field builder threw away; the rest is the reader itself.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
