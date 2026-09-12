"""Score the binding layer against FUNSD's own question -> answer annotations.

Everything this project has concluded about architecture came from twelve forms and 153
scored fields, which is too little to choose anything on. FUNSD is 199 real scanned forms
with entities labelled question/answer and, crucially, **explicit links between them** --
which is exactly the thing the failure census says costs us 55% of printed failures.

It also makes the numbers comparable. Published work reports F1 on this task: KVPFormer
82.23%, and ~90.86% when given gold entity labels. Those are layout-aware transformers
trained on the pairing itself. This measures a geometric binder on the same ground.

Scored as a linking task: of the question -> answer pairs the annotation asserts, how many
does the pipeline reproduce? Recall is what matters for intake -- a missed pair is a field
a person has to fill in by hand.

    python scripts/evaluate_funsd.py [--api ...] [--split testing_data] [--limit N]
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

DATA = ROOT / "data" / "dataset-scanned"


def flat(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def gold_links(annotation: Path) -> list[tuple[str, str]]:
    """Every question -> answer pair the annotation asserts."""
    entities = json.loads(annotation.read_text())["form"]
    by_id = {e["id"]: e for e in entities}
    pairs = []
    for entity in entities:
        if entity.get("label") != "question":
            continue
        for a, b in entity.get("linking", []):
            other = by_id.get(b if a == entity["id"] else a)
            if (
                other is not None
                and other.get("label") == "answer"
                and flat(entity["text"])
                and flat(other["text"])
            ):
                pairs.append((entity["text"], other["text"]))
    return pairs


def analyse(api: str, image: Path) -> dict:
    out = subprocess.run(
        ["curl", "-s", "--max-time", "600", "-X", "POST", f"{api}/api/v1/form-analyses",
         "-F", f"file=@{image};type=image/png"],
        capture_output=True, text=True, check=False,
    ).stdout
    return json.loads(out) if out.strip() else {"detected_fields": []}


def reproduced(pair: tuple[str, str], fields: list[dict]) -> bool:
    """Whether the pipeline bound this question to this answer.

    Both sides are matched by containment rather than equality: the annotation's text and
    our OCR of the same pixels rarely agree character for character, and scoring that
    disagreement would measure Tesseract rather than the binding.
    """
    question, answer = flat(pair[0]), flat(pair[1])
    for field in fields:
        label, value = flat(field.get("label_text")), flat(field.get("normalized_value"))
        if not label or not value:
            continue
        if (question in label or label in question) and (answer in value or value in answer):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--split", default="testing_data")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    annotations = sorted((DATA / args.split / "annotations").glob("*.json"))
    if args.limit:
        annotations = annotations[: args.limit]
    if not annotations:
        print(f"No annotations under {DATA / args.split}")
        return 1

    found = total = 0
    seen_answer, seen_question = [0], [0]
    for annotation in annotations:
        image = DATA / args.split / "images" / f"{annotation.stem}.png"
        if not image.exists():
            continue
        pairs = gold_links(annotation)
        if not pairs:
            continue
        fields = analyse(args.api, image).get("detected_fields", [])
        hit = sum(1 for p in pairs if reproduced(p, fields))
        # Decompose the loss. A pair can fail because the answer never reached us at all
        # (detection or OCR), or because it reached us and was bound to the wrong caption.
        # Those need completely different work, and the totals alone cannot separate them.
        for question, answer in pairs:
            q, a = flat(question), flat(answer)
            if any(a and (a in flat(f.get("normalized_value")) or flat(f.get("normalized_value")) in a)
                   for f in fields if flat(f.get("normalized_value"))):
                seen_answer[0] += 1
            if any(q and (q in flat(f.get("label_text")) or flat(f.get("label_text")) in q)
                   for f in fields if flat(f.get("label_text"))):
                seen_question[0] += 1
        found += hit
        total += len(pairs)
        if args.verbose:
            print(f"  {annotation.stem:16} {hit:3}/{len(pairs):<3} links, {len(fields)} fields read")

    print(f"\nFUNSD LINKING {found}/{total} = {found / total * 100:.1f}% of annotated "
          f"question -> answer pairs reproduced")
    print(f"  {len(annotations)} forms from {args.split}")
    print(f"  of those pairs, the answer text reached us at all in {seen_answer[0]}/{total} "
          f"= {seen_answer[0] / total * 100:.1f}%")
    print(f"  and the question text was found as a label in {seen_question[0]}/{total} "
          f"= {seen_question[0] / total * 100:.1f}%")
    print("  The first is the ceiling detection and OCR leave for binding; the gap between")
    print("  it and the linking score is what the binder itself loses.")
    print("  For scale: KVPFormer reports 82.23% F1 on FUNSD key-value pairing, and 90.86%")
    print("  when given gold entity labels. Those are trained on the pairing task.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
