"""Fill a blank benchmark form with known values, one value per cell.

Why this exists
---------------
The St. Clair fixture was originally filled by hand, by inserting one text span per
row and padding it with spaces to push the later values rightward:

    'Pittsburgh                        PA                15206'

Space padding does not line up with a form's columns, and it cannot, because the
column walls are set by where the printed captions sit, not by character widths. The
result was a fixture no reader could satisfy:

  * every value floated roughly one row *above* the rule it belongs on, so
    '412-555-0184' overlapped the printed 'CITY' caption and the emergency contact
    sat on the *Employer* rule;
  * 'PA' landed inside the CITY column and '15206' inside the STATE column.

Scoring an extractor against that measures nothing. Two attempts to build
captioned-column reading were reverted because they were being judged by it.

The rules this script follows are the two a person filling the form obeys:

  1. a value sits *on* its rule -- baseline just above the line, not floating a row up;
  2. a value sits inside the column its caption heads, left-aligned with a small indent.

Column walls are computed from the captions themselves (midpoints between caption
centres), so the placement is derived from the form rather than guessed.

Nothing here reads the form -- positions come from the blank original's own geometry.
Filling a benchmark with our own detector and then scoring that detector would be
circular; this script never imports the extractor.

Usage:
    python scripts/fill_fixture.py            # rewrites the St. Clair fixture + its JPG
    python scripts/fill_fixture.py --check    # report placement only, write nothing
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
FORMS = ROOT / "data" / "real-forms"

# The render the image path is scored on. 150dpi reproduces the existing JPG exactly.
JPG_DPI = 150

INK = (0.051, 0.051, 0.353)  # the dark blue the original fill used
FONT = "helv"
SIZE = 11.0
# Helvetica's descender is 0.212 em. Sitting the baseline this far above the rule puts
# the glyph bottoms on the line without the descenders crossing it.
BASELINE_ABOVE_RULE = 2.5


@dataclass(frozen=True)
class Placement:
    """One value, on the rule at `rule_y`, in the column headed by `caption`."""

    text: str
    rule_y: float
    caption: str | None = None  # None means the rule carries a single value
    indent: float = 4.0


# St. Clair page 1. Rule y-coordinates are read back from the blank and asserted below,
# so a different edition of the form fails loudly instead of filling the wrong lines.
STCLAIR = [
    Placement("Okafor Amara C", 133.6),
    Placement("04 / 17 / 1986", 166.1),
    Placement("1420 Bellefield Avenue", 294.4, "STREET"),
    Placement("Apt 3B", 294.4, "APT"),
    Placement("Pittsburgh", 331.1, "CITY"),
    Placement("PA", 331.1, "STATE"),
    Placement("15206", 331.1, "ZIP"),
    Placement("412-555-0184", 356.9, "HOME"),
    Placement("amara.okafor@example.com", 385.2),
    Placement("Riverside Logistics", 449.8),
    Placement("Operations Manager", 450.5),
    Placement("Ngozi Okafor", 468.4, "Name"),
    Placement("Sister", 468.4, "Relation"),
    Placement("412-555-0199", 468.4, "Phone"),
]


def rules_on(page: fitz.Page, limit: float = 520.0) -> list[fitz.Rect]:
    """Horizontal rules a value can be written on, top to bottom."""
    found = [
        d["rect"]
        for d in page.get_drawings()
        if d["rect"].height < 3 and d["rect"].width > 40 and d["rect"].y0 < limit
    ]
    return sorted(found, key=lambda r: (r.y0, r.x0))


def columns_under(page: fitz.Page, rule: fitz.Rect) -> dict[str, tuple[float, float]]:
    """Column extents for a rule captioned from beneath.

    The captions sit centred under their columns, so the wall between two columns is
    the midpoint between their centres. The first column starts where the rule starts
    and the last ends where it ends.
    """
    caps: list[tuple[float, str]] = []
    for word in page.get_text("words"):
        x0, y0, x1, _, text = word[0], word[1], word[2], word[3], word[4]
        if rule.y0 < y0 < rule.y0 + 14 and rule.x0 - 20 < x0 and x1 < rule.x1 + 20:
            caps.append(((x0 + x1) / 2, text))
    caps.sort()
    # A caption of several words ('ZIP CODE', 'Phone Number') is one column; merge any
    # centre that sits within a few points of the previous one.
    merged: list[tuple[float, str]] = []
    for centre, text in caps:
        if merged and centre - merged[-1][0] < 40:
            continue
        merged.append((centre, text))
    out: dict[str, tuple[float, float]] = {}
    for i, (centre, text) in enumerate(merged):
        left = rule.x0 if i == 0 else (merged[i - 1][0] + centre) / 2
        right = rule.x1 if i == len(merged) - 1 else (centre + merged[i + 1][0]) / 2
        out[text] = (left, right)
    return out


def apply(page: fitz.Page, plan: list[Placement], *, check: bool) -> list[str]:
    report = []
    known = {round(r.y0, 1): r for r in rules_on(page)}
    for p in plan:
        rule = known.get(round(p.rule_y, 1))
        if rule is None:
            raise SystemExit(f"no rule at y={p.rule_y} -- form edition changed, refusing to fill")
        if p.caption is None:
            left = rule.x0
            where = "whole rule"
        else:
            cols = columns_under(page, rule)
            match = next((k for k in cols if k.upper().startswith(p.caption.upper())), None)
            if match is None:
                raise SystemExit(f"no column {p.caption!r} under rule y={p.rule_y}: {list(cols)}")
            left, _right = cols[match]
            where = f"column {match!r} [{left:.0f}..{_right:.0f}]"
        x = left + p.indent
        y = rule.y0 - BASELINE_ABOVE_RULE
        report.append(f"  {p.text[:30]:32} rule y={rule.y0:6.1f}  x={x:6.1f}  {where}")
        if not check:
            page.insert_text((x, y), p.text, fontname=FONT, fontsize=SIZE, color=INK)
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report placement, write nothing")
    args = ap.parse_args()

    blank = FORMS / "stclair_newpatient.pdf"
    out_pdf = FORMS / "stclair_FILLED.pdf"
    out_jpg = FORMS / "stclair_FILLED_page1.jpg"

    doc = fitz.open(blank)
    report = apply(doc[0], STCLAIR, check=args.check)
    print(f"St. Clair page 1 <- {blank.name}")
    print("\n".join(report))
    if args.check:
        print("\n--check: nothing written")
        return 0
    doc.save(out_pdf)
    doc[0].get_pixmap(dpi=JPG_DPI).pil_save(out_jpg, quality=92)
    print(f"\nwrote {out_pdf.relative_to(ROOT)} and {out_jpg.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
