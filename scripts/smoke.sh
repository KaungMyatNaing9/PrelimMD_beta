#!/usr/bin/env bash
# Smoke test: tests, lint, typecheck, build, then one real upload through the pipeline the
# product actually uses (/api/v1/form-analyses). The upload needs a real form on disk:
# real forms are gitignored by policy, so set SMOKE_FORM or have data/real-forms/LARGE
# populated; without either the upload half is skipped and says so.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; py="${PYTHON_BIN:-$root/.venv312/bin/python}"; port="${SMOKE_PORT:-8010}"
(cd "$root/backend" && "$py" -m pytest && "$py" -m ruff check app tests)
(cd "$root/frontend" && npm run typecheck && npm run build)
form="${SMOKE_FORM:-$(ls "$root"/data/real-forms/LARGE/*_FILLED.pdf 2>/dev/null | head -1 || true)}"
if [ -z "$form" ] || [ ! -f "$form" ]; then
  echo "Smoke test: checks passed; no real form available for the upload check (set SMOKE_FORM=/path/to/filled.pdf)"; exit 0
fi
case "$form" in *.pdf|*.PDF) mime=application/pdf;; *) mime=image/jpeg;; esac
# Started from the repo root, like the dev server: ARTIFACT_ROOT is relative, and a server
# started inside backend/ writes a second artifacts tree there.
# A server already on the port would be tested instead of this checkout -- and a stale
# one once passed a smoke test for code it did not contain. Refuse rather than run.
if lsof -ti:"$port" >/dev/null 2>&1; then echo "Smoke test: port $port is in use (pid $(lsof -ti:"$port" | tr '\n' ' ')); stop it or set SMOKE_PORT"; exit 1; fi
# Geometry-only unless SMOKE_VISION says otherwise: a smoke test must not depend on a
# hosted model answering, and the local model adds a minute a page.
(cd "$root" && VISION_PROVIDER="${SMOKE_VISION:-disabled}" "$py" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port "$port") & pid=$!
# Kill by port, not just by the subshell pid: uvicorn is that shell's child and outlived it.
trap 'kill "$pid" 2>/dev/null || true; lsof -ti:"$port" 2>/dev/null | xargs kill 2>/dev/null || true' EXIT
for _ in {1..30}; do curl -fsS "http://127.0.0.1:$port/api/v1/health" >/dev/null 2>&1 && break; sleep 1; done
analysis=$(curl -fsS -F "file=@$form;type=$mime" "http://127.0.0.1:$port/api/v1/form-analyses")
id=$(printf '%s' "$analysis" | "$py" -c 'import json,sys; a=json.load(sys.stdin); assert a["detected_fields"], "no fields detected"; print(a["analysis_id"])')
curl -fsS "http://127.0.0.1:$port/api/v1/form-analyses/$id" | "$py" -c 'import json,sys; a=json.load(sys.stdin); assert a["pages"], "no pages"'
curl -fsS "http://127.0.0.1:$port/api/v1/form-analyses/$id/pages/0/image" >/dev/null
echo "Smoke test passed: $id ($(basename "$form"))"
