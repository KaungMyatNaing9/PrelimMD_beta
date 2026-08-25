#!/usr/bin/env bash
# Show what changed between the Milestone 1 prototype and today, by running both
# versions of the analyzer against the same form.
#
# Usage: scripts/show_progress.sh [form.jpg] [baseline-commit]
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <form> [baseline-commit]" >&2
  echo "Name the form explicitly — this never picks a sample for you." >&2
  exit 2
fi
SAMPLE="$1"
BASELINE="${2:-aa9bda5}"
WORKTREE="${WORKTREE:-/tmp/prelimmd_m1}"
PY="${PY:-.venv312/bin/python}"

[ -f "$SAMPLE" ] || { echo "No such form: $SAMPLE"; exit 1; }

# A detached worktree lets the old code run unmodified beside the new one.
if [ ! -d "$WORKTREE" ]; then
  git worktree add -q --detach "$WORKTREE" "$BASELINE"
  trap 'git worktree remove --force "$WORKTREE" 2>/dev/null || true' EXIT
fi

runner=$(mktemp /tmp/prelimmd_runner.XXXXXX.py)
trap 'rm -f "$runner"' RETURN 2>/dev/null || true
cat > "$runner" <<'PYEOF'
import shutil, sys, tempfile
from pathlib import Path
label, root, sample = sys.argv[1], sys.argv[2], Path(sys.argv[3])
sys.path.insert(0, f"{root}/backend")
from app.services.capture import preprocess
from app.services.form_analyzer import FormAnalyzer

d = Path(tempfile.mkdtemp())
shutil.copy(sample, d / "source.jpg")
preprocess(d / "source.jpg", "jpg", d)
analysis = FormAnalyzer().analyze("x", d)
print(f"  fields detected: {len(analysis.detected_fields)}")
for w in analysis.warnings:
    print(f"  warning: {w}")
for f in analysis.detected_fields:
    print(f"    {f.label_text!r:24} = {str(f.normalized_value)[:38]!r}")
PYEOF

echo "Form: $SAMPLE"
echo
echo "=== Milestone 1 prototype (commit ${BASELINE}) ==="
"$PY" "$runner" before "$WORKTREE" "$SAMPLE"
echo
echo "=== Today ==="
"$PY" "$runner" after . "$SAMPLE"
echo
echo "Regeneration did not exist at Milestone 1. Today:"
echo "  scripts/demo_roundtrip.sh $SAMPLE"
