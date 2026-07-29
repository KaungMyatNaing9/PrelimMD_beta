import json
from collections import Counter
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "data/corpus"
records = [json.loads(x) for x in (root / "manifest.jsonl").read_text().splitlines()]
counts = Counter(r["split"] for r in records)
templates = Counter(r["template_id"] for r in records)
assert counts == {"development": 24, "validation": 6, "test": 6}, counts
assert templates == {"T01": 12, "T02": 12, "T03": 12}, templates
assert len({r["sample_id"] for r in records}) == 36
for r in records:
    assert (root / r["source_path"]).exists() and (root / r["expected_path"]).exists()
print("Manifest valid: 24 dev / 6 validation / 6 test; 12 samples per template")
