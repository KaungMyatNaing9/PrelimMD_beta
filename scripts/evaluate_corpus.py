"""Score saved extraction runs against the requested corpus split.

Run extraction first (through the API or `run_extraction.py`) and place each
`<sample_id>.json` result in the results directory. Test labels are only read
when `--split test` is explicitly requested.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path


def ratio(correct: int, total: int) -> float:
    return round(correct / total, 3) if total else 0.0


parser = argparse.ArgumentParser()
parser.add_argument("--split", default="validation", choices=["development", "validation", "test"])
parser.add_argument("--results-dir", type=Path, default=Path("artifacts/evaluation/runs"))
parser.add_argument("--output", type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1] / "data/corpus"
records = [json.loads(line) for line in (root / "manifest.jsonl").read_text().splitlines()]
records = [record for record in records if record["split"] == args.split]
totals = defaultdict(int); correct = defaultdict(int); failures = []; reviewed = incorrect = caught = fields = 0
for record in records:
    expected = json.loads((root / record["expected_path"]).read_text())
    result_path = args.results_dir / f"{record['sample_id']}.json"
    if not result_path.exists():
        failures.append({"sample_id": record["sample_id"], "message": "missing extraction result"})
        continue
    predicted = json.loads(result_path.read_text())
    values = {field["field_id"]: field for page in predicted["pages"] for field in page["fields"]}
    for label in expected["fields"]:
        field = values.get(label["field_id"])
        if field is None:
            failures.append({"sample_id": record["sample_id"], "field_id": label["field_id"], "message": "missing field"})
            continue
        fields += 1; key = field["field_type"]; template = record["template_id"]
        actual = label["normalized_value"]
        # Keep comparison case/whitespace tolerant for text while retaining exact boolean/list matching.
        normalize = lambda value: " ".join(str(value).casefold().split())
        ok = normalize(field.get("normalized_value")) == normalize(actual)
        totals["overall"] += 1; totals[key] += 1; totals[template] += 1
        if label.get("content_type") == "printed": totals["printed"] += 1
        else: totals["handwritten"] += 1
        if ok:
            correct["overall"] += 1; correct[key] += 1; correct[template] += 1
            if label.get("content_type") == "printed": correct["printed"] += 1
            else: correct["handwritten"] += 1
        else:
            incorrect += 1; caught += int(field["needs_review"])
            failures.append({"sample_id": record["sample_id"], "field_id": label["field_id"], "expected": actual, "actual": field.get("normalized_value")})
        reviewed += int(field["needs_review"])
report = {"schema_version":"1.0","split":args.split,"sample_count":len(records),"field_count":fields,"overall_accuracy":ratio(correct["overall"],totals["overall"]),"printed_accuracy":ratio(correct["printed"],totals["printed"]),"handwritten_accuracy":ratio(correct["handwritten"],totals["handwritten"]),"checkbox_accuracy":ratio(correct["checkbox"],totals["checkbox"]),"signature_presence_accuracy":ratio(correct["signature_presence"],totals["signature_presence"]),"review_rate":ratio(reviewed,fields),"error_capture_rate":ratio(caught,incorrect),"by_template":{key:ratio(correct[key],totals[key]) for key in ("T01","T02","T03")},"by_field_type":{key:ratio(correct[key],totals[key]) for key in totals if key not in {"overall","printed","handwritten","T01","T02","T03"}},"failures":failures}
out = args.output or Path("artifacts/evaluation") / f"{args.split}.json"; out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(report, indent=2))
summary = f"Split: {args.split} | Samples: {len(records)} | Fields: {fields}\nOverall: {report['overall_accuracy']:.1%}\nReview rate: {report['review_rate']:.1%}\nIncorrect fields caught by review flag: {report['error_capture_rate']:.1%}\n"
out.with_suffix(".md").write_text(summary); print(summary, end="")
