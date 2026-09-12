"""Why does legible text fail to become a field value on FUNSD scans?

The loss chain on the test split is 58.6% of answers legible to OCR, 33.4% arriving as a
field value, 20.4% correctly linked. The middle step is the one made of our own code: a
quarter of all annotated answers are characters the reader recovered and the field builder
then threw away. This asks whether that is a *coverage* failure -- the builder never
proposing a candidate for that region -- or a precision one.

Per form it reports what the page offered against what came back, so the two are
distinguishable: a page with thirty gold pairs that yields six fields is not binding badly,
it is not looking.

    python scripts/funsd_field_coverage.py [--api ...] [--limit N]
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


def analyse(api: str, image: Path) -> dict:
    out = subprocess.run(
        ["curl", "-s", "--max-time", "600", "-X", "POST", f"{api}/api/v1/form-analyses",
         "-F", f"file=@{image};type=image/png"],
        capture_output=True, text=True, check=False,
    ).stdout
    return json.loads(out) if out.strip() else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--split", default="testing_data")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    annotations = sorted((DATA / args.split / "annotations").glob("*.json"))[: args.limit]
    gold = fields_total = valued_total = 0
    layouts: dict[str, int] = {}
    print(f"{'form':16} {'layout':7} {'boxes':>5} {'fields':>6} {'valued':>6} {'gold':>5}")
    for annotation in annotations:
        image = DATA / args.split / "images" / f"{annotation.stem}.png"
        pairs = gold_links(annotation)
        if not image.exists() or not pairs:
            continue
        result = analyse(args.api, image)
        fields = result.get("detected_fields", [])
        page = (result.get("pages") or [{}])[0]
        valued = [f for f in fields if flat(f.get("normalized_value"))]
        layout = str(page.get("layout") or "-")
        layouts[layout] = layouts.get(layout, 0) + 1
        gold += len(pairs)
        fields_total += len(fields)
        valued_total += len(valued)
        print(f"{annotation.stem:16} {layout:7} {page.get('boxes_detected', 0):5} "
              f"{len(fields):6} {len(valued):6} {len(pairs):5}", flush=True)

    print(f"\n{fields_total} fields proposed, {valued_total} carrying a value, "
          f"against {gold} annotated pairs")
    print(f"  candidate coverage {valued_total / gold * 100:.1f}% -- a value-carrying field "
          f"per annotated pair")
    print(f"  layouts chosen: {layouts}")
    print("  Coverage far below 100% means the builder is not proposing candidates, which")
    print("  no amount of better binding can recover.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
