"""Two page-level signals that sound like they should separate captions from answers.

Neither does. Both are recorded because both are the kind of idea that gets reinvented:
label detection is lexical, 93.3% of legible answers are lost when the vocabulary cannot
name a caption (`funsd_builder_loss.py`), and the obvious escape is to find some geometric
property of the page that says "this run is an answer" without consulting any word list.

**Column alignment.** A form repeats its layout down the page, so answers might share a few
left edges that captions do not. Measured on FUNSD: answers sit on the top three left edges
69.7% of the time, questions 71.9% -- questions are *more* aligned, and answers are the more
aligned of the two in only 17 of 44 forms.

**Text height.** Captions are printed with the form; answers are added later, often on a
different machine. Measured: the median answer is 6.7% taller than the median question, and
a threshold between the two medians separates 70% of boxes on only 15 of 44 forms.

Both are also available to the live pipeline -- OCR supplies word boxes -- so if either had
worked it would have been cheap. Neither is worth building on.

    python scripts/funsd_signal_probes.py [--split testing_data]
"""

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "dataset-scanned"

# Two boxes within this many pixels of each other count as sharing a left edge.
EDGE_TOLERANCE = 12
# A signal has to classify at least this share of boxes to be worth considering.
USEFUL_SHARE = 0.70


def boxes(entities: list[dict], label: str) -> list[list[int]]:
    return [
        e["box"]
        for e in entities
        if e.get("label") == label and str(e.get("text", "")).strip()
    ]


def edge_concentration(items: list[list[int]]) -> float:
    """Share of boxes sitting on the three most popular left edges."""
    if not items:
        return 0.0
    buckets = Counter(round(b[0] / EDGE_TOLERANCE) for b in items)
    return sum(count for _, count in buckets.most_common(3)) / len(items)


def height_separates(question: list[list[int]], answer: list[list[int]]) -> bool:
    """Whether a cut between the two median heights would classify most boxes correctly."""
    qh = [b[3] - b[1] for b in question if b[3] > b[1]]
    ah = [b[3] - b[1] for b in answer if b[3] > b[1]]
    if not qh or not ah:
        return False
    mq, ma = statistics.median(qh), statistics.median(ah)
    if mq == ma:
        return False
    cut = (mq + ma) / 2
    high, low = (ah, qh) if ma > mq else (qh, ah)
    correct = sum(1 for h in high if h > cut) + sum(1 for h in low if h <= cut)
    return correct / (len(high) + len(low)) >= USEFUL_SHARE


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="testing_data")
    args = parser.parse_args()

    q_edges, a_edges, separable, forms = [], [], 0, 0
    for annotation in sorted((DATA / args.split / "annotations").glob("*.json")):
        entities = json.loads(annotation.read_text())["form"]
        question, answer = boxes(entities, "question"), boxes(entities, "answer")
        if len(question) < 5 or len(answer) < 5:
            continue
        forms += 1
        q_edges.append(edge_concentration(question))
        a_edges.append(edge_concentration(answer))
        separable += height_separates(question, answer)

    if not forms:
        print(f"No annotated forms under {DATA / args.split}")
        return 1
    better = sum(1 for q, a in zip(q_edges, a_edges, strict=True) if a > q)
    print(f"forms with at least five of each: {forms}\n")
    print("COLUMN ALIGNMENT")
    print(f"  questions on their top three left edges: {sum(q_edges) / forms * 100:5.1f}%")
    print(f"  answers   on their top three left edges: {sum(a_edges) / forms * 100:5.1f}%")
    print(f"  forms where answers align more tightly than questions: {better}/{forms}")
    print("\nTEXT HEIGHT")
    print(f"  forms where a height cut classifies >={USEFUL_SHARE:.0%} of boxes: "
          f"{separable}/{forms}")
    print("\nNeither signal separates captions from answers. Recorded so the idea is not "
          "rebuilt from scratch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
