"""How well tick boxes are read, from a scorer's dump of a --tick set.

    python scripts/evaluate_ticks.py --truth LARGE_TICK_B --dump DIR

Every question `fill_forms.py --tick` answered with an X is looked up the way
`evaluate_real_forms.py` looks up any field -- by the question's caption, then by its
canonical key -- and counts as read when the value names exactly the ticked option.
A choice value reported under a caption no tick answered is counted too: a question
nobody ticked that reads as ticked is a false mark.

The filler records a long question by its last few words ("you ever had abnormal pap
smears?") while a reader reports the whole printed question ("Have you ever had abnormal
pap smears?"), and a reader drops a question's numbering the filler keeps. So after the
exact caption and the canonical key, a label whose words end the question's words, or
the other way round, names the same question.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.canonical_registry import match, norm


def comparable(value) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def same_question(asked: str, label: str) -> bool:
    """One label ends the other: the same printed question, cut differently.

    Compared word by word, and on letters and digits alone, because OCR also joins or
    splits the words of a question ("athome", "yourown") without reading it differently,
    while a PDF whose font maps letters oddly drops characters inside words instead.
    """
    words_a, words_b = (re.findall(r"[a-z0-9]+", text.lower()) for text in (asked, label))
    short_w, long_w = sorted((words_a, words_b), key=len)
    if short_w and len("".join(short_w)) >= 3 and long_w[len(long_w) - len(short_w):] == short_w:
        return True
    a, b = comparable(asked), comparable(label)
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 3 and long_.endswith(short)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", required=True, help="set folder under data/real-forms")
    parser.add_argument("--dump", required=True)
    args = parser.parse_args()
    truth = json.loads((ROOT / "data/real-forms" / args.truth / "ground_truth_new.json").read_text())
    ticked = truth.get("_ticked", {})
    outcome: Counter[str] = Counter()
    by_kind: Counter[tuple[str, str]] = Counter()
    false_marks = 0
    for name, questions in sorted(ticked.items()):
        path = Path(args.dump) / f"{name}.json"
        if not path.exists() or not questions:
            continue
        fields = json.loads(path.read_text()).get("detected_fields", [])
        found: dict[str, object] = {}
        answered = [f for f in fields if f.get("normalized_value") not in (None, "", False)]
        for field in answered:
            for key in (norm(field["label_text"]), field.get("canonical_key")):
                if key and key not in found:
                    found[key] = field["normalized_value"]
        kind = "photo" if name.endswith(".jpg") else "pdf"
        for question in questions:
            want = truth[name][question]
            mapped, _, _ = match(question)
            have = found.get(norm(question))
            if have is None and mapped:
                have = found.get(mapped.canonical_key)
            if have is None:
                have = next((f["normalized_value"] for f in answered if same_question(question, f["label_text"])), None)
            if have is None:
                result = "not read"
            elif comparable(have) == comparable(want):
                result = "read"
            elif comparable(want) in comparable(have):
                result = "read, with other options"
            else:
                result = "wrong option or text"
            outcome[result] += 1
            by_kind[(kind, result)] += 1
        false_marks += sum(
            1 for f in fields
            if f.get("field_type") == "multiple_choice" and f.get("normalized_value")
            and not any(norm(f["label_text"]) == norm(q) or same_question(q, f["label_text"]) for q in questions)
        )
    total = sum(outcome.values())
    print(f"{total} ticked questions in {args.truth}")
    for result, count in outcome.most_common():
        print(f"  {result:26} {count:4}  {count / total * 100:5.1f}%")
    for kind in ("pdf", "photo"):
        n = sum(v for (k, _), v in by_kind.items() if k == kind)
        if n:
            print(f"  {kind:5} read {by_kind[(kind, 'read')]}/{n} = {by_kind[(kind, 'read')] / n * 100:.1f}%")
    print(f"choice values under questions nobody ticked: {false_marks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
