#!/usr/bin/env bash
# Full pipeline smoke test: upload -> extract -> regenerate.
# Usage: scripts/demo_roundtrip.sh [path/to/form.jpg] [api-url]
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <form.jpg|form.png|form.pdf> [api-url]" >&2
  echo "Name the form explicitly — this never picks a sample for you." >&2
  exit 2
fi
FILE="$1"
API="${2:-http://127.0.0.1:8000}"
PY="${PY:-.venv312/bin/python}"

echo "→ uploading $FILE"
RESP=$(curl -sf -X POST "$API/api/v1/form-analyses" -F "file=@$FILE")
ID=$("$PY" -c 'import json,sys; print(json.loads(sys.argv[1])["analysis_id"])' "$RESP")

"$PY" - "$RESP" <<'PYEOF'
import json, sys
a = json.loads(sys.argv[1])
p = a["pages"][0]
print(f'  analysis   : {a["analysis_id"]}')
print(f'  source     : {a["source_type"]}  layout={p.get("layout","-")}  template={a["matched_known_template"]}')
print(f'  ocr        : {p.get("ocr_word_count","-")} words @ {p.get("mean_ocr_confidence","-")} mean confidence')
print(f'  fields     : {len(a["detected_fields"])}')
for f in a["detected_fields"]:
    flag = "  <-- REVIEW" if f["needs_review"] else ""
    print(f'    {f["label_text"]:22} = {f["normalized_value"]!r}{flag}')
if a["warnings"]:
    print(f'  warnings   : {a["warnings"]}')
PYEOF

OUT="/tmp/${ID}.pdf"
if curl -sf "$API/api/v1/form-analyses/$ID/regenerate" -o "$OUT"; then
  echo "→ regenerated: $OUT ($(wc -c <"$OUT" | tr -d ' ') bytes)"
  command -v open >/dev/null && open "$OUT"
else
  echo "→ no regeneration (no template matched — expected for unknown forms)"
fi
