"""Per-form totals of a scorer's output, and nothing else.

    python scripts/unseen_summary.py OUT.txt [OUT2.txt ...]

The unseen set (`data/real-forms/UNSEEN/`) stays unseen only if nobody reads which field
failed on which form. `evaluate_real_forms.py` and `evaluate_corrected.py` print a FAIL line
per field; this prints the per-form counts and the totals, so a run on the unseen set can
be quoted without being looked at.
"""
import re
import sys
from pathlib import Path

FORM = re.compile(r"^(\S+)  (\d+)/(\d+) = \d+%   precision (\d+)/(\d+)$")


def summarise(path: str) -> None:
    got = total = good = valued = 0
    rows = []
    for line in Path(path).read_text().splitlines():
        m = FORM.match(line)
        if not m:
            continue
        name, g, t, p, v = m.group(1), *map(int, m.groups()[1:])
        rows.append((name, g, t, p, v))
        got += g; total += t; good += p; valued += v
    print(f"{path}: {len(rows)} artefacts")
    for name, g, t, p, v in rows:
        print(f"  {name[:64]:64} {g:3d}/{t:<3d}  precision {p:3d}/{v:<3d}")
    if total:
        kinds = {"PDF": [r for r in rows if r[0].endswith(".pdf")], "JPG": [r for r in rows if r[0].endswith(".jpg")]}
        for kind, sub in kinds.items():
            g, t = sum(r[1] for r in sub), sum(r[2] for r in sub)
            p, v = sum(r[3] for r in sub), sum(r[4] for r in sub)
            if t:
                print(f"  {kind}: recall {g}/{t} = {g / t * 100:.1f}%   precision {p}/{max(v, 1)} = {p / max(v, 1) * 100:.1f}%")
        print(f"  ALL: recall {got}/{total} = {got / total * 100:.1f}%   precision {good}/{max(valued, 1)} = {good / max(valued, 1) * 100:.1f}%")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        summarise(arg)
