#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; py="${PYTHON_BIN:-$root/.venv312/bin/python}"; port="${SMOKE_PORT:-8010}"
"$py" "$root/scripts/verify_manifest.py"
(cd "$root/backend" && "$py" -m pytest && "$py" -m ruff check app tests)
(cd "$root/frontend" && npm run typecheck && npm run build)
(cd "$root/backend" && "$py" -m uvicorn app.main:app --host 127.0.0.1 --port "$port") & pid=$!
trap 'kill "$pid" 2>/dev/null || true' EXIT
for _ in {1..20}; do curl -fsS "http://127.0.0.1:$port/api/v1/health" >/dev/null && break; sleep 1; done
capture=$(curl -fsS -F "file=@$root/data/corpus/development/T01/T01_DEV_001/source.jpg;type=image/jpeg" -F template_id=T01 "http://127.0.0.1:$port/api/v1/capture")
run=$(printf '%s' "$capture" | "$py" -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
curl -fsS -X POST "http://127.0.0.1:$port/api/v1/runs/$run/extract" | "$py" -c 'import json,sys; r=json.load(sys.stdin); assert len(r["pages"][0]["fields"]) and any(f["needs_review"] for f in r["pages"][0]["fields"])'
curl -fsS "http://127.0.0.1:$port/api/v1/runs/$run/pages/0/image" >/dev/null
curl -fsS "http://127.0.0.1:$port/api/v1/runs/$run/pages/0/overlay" | grep -q '<svg'
echo "Smoke test passed: $run"
