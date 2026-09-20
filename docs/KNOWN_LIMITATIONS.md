# Known limitations

Every figure here is measured, and the script that produces it is named so it can be
re-run. Where a number would flatter the system, the caveat is given with it.

Last refreshed **2026-09-19** against `docs/PROGRESS.md`, which is the record that was
checked. If a figure anywhere else in the repository disagrees with PROGRESS, PROGRESS is
the one to believe.

## How accuracy is measured, and why the obvious number is not used

A synthetic corpus once read **96.9%**; every sample in it was a clean render of a layout
the extractor was tuned against, and on the day both were first measured real clinic forms
read 45%. It was deleted on 2026-09-13 so that number can no longer be produced, let alone
quoted.

Reported accuracy comes from real clinic forms downloaded from public clinic websites:
112 blank layouts, filled with invented patients, read back and scored field by field
(`scripts/evaluate_real_forms.py`, `scripts/evaluate_corrected.py`).

**Quote two numbers, never one.** Recall alone cannot fall when the extractor invents a
value, so recall without precision flatters it. Both are given below.

**The benchmark is biased toward easy fields.** Values are placed onto blank real forms
beside their printed label, and the filler skips any field where the space beside the label
is not already clear. So the fields being scored are the unambiguous ones, and a per-form
percentage is not comparable with another form's: "100%" on a form with four scored fields
is four easy fields, not a form read perfectly.

## Current measurements

Recall / precision, except where a count is given.

| What | Result | Script |
|---|---|---|
| **Zero-shot** — a layout never seen before (112 forms, 2,775 fields) | **67.4 / 78.2** | `evaluate_real_forms.py` |
| **Enrolled** — the blank learned once, nothing corrected | 68.8 / 67.9 | `evaluate_enrolled.py` |
| **Corrected** — blank learned, one filled copy corrected, scored on a *second* patient's copy | **91.8 / 94.1** | `evaluate_corrected.py` |
| **No blank anywhere** — a filled copy stands in for the blank | 90.2 / 94.0 | `evaluate_corrected.py --learn LARGE_SELF` |
| Held out, 21 unseen layouts, full system | **93.3 / 90.0** | locked set, totals only |
| Held out, 10 unseen layouts, no blank at all | **91.9 / 98.9** | locked set, totals only |
| **Tick boxes** — 417 ticked questions, enrolled | **86.1% read** (PDFs 84.7, photographs 88.6) | `evaluate_ticks.py` |
| Tick boxes, zero-shot | 75.5% read | `evaluate_ticks.py` |
| Tick boxes, held out | 79.4% read | locked set, totals only |
| **Handwriting** — 5 phone photos, 49 fields | **43–53%** (see the caveat below) | `evaluate_real_forms.py --truth HANDWRITTEN` |
| Round trip: read, learn, refill, read back | **20/23 values = 87.0%**, 3 of 3 layouts keep at least half | `evaluate_roundtrip.py` |
| Placement within 2mm of where the value sat on the original | **15/21**, median **1.29mm** | `evaluate_placement.py` |
| **Mistakes a regenerated copy makes** — 111 layouts, every page | **zero of every class** | `evaluate_regeneration.py` |

Gate 2 asks for **≥95%** field accuracy on printed text. No track reaches it on recall; the
best is 93.3% on a held-out set, and 91.8% on the benchmark. Precision has passed 90% on
three tracks.

## Where it fails, and why

**A value can bind to the wrong label.** This is the largest open problem and it blocks two
Gate 3 criteria at once. Four approaches were tried and reverted, each trading one benchmark
against another; the diagnosis is that the signals available (a value's shape, its type
validity) verify what a value *looks like*, and the broken thing is which label it *belongs*
to. For context, entity linking on the FUNSD benchmark tops out near 0.80 F1 with a trained
model, so this is not a gap a further hand-built rule is likely to close.

**Placement misses are extraction faults, but not the ones this file used to claim.** It
said the headline was dragged down by values printed into the wrong slot. Measured
2026-09-20 with `evaluate_placement.py` now classifying each value by whether its text
actually sits inside the answer region the reader chose: **all 21 do**. Nothing is in the
wrong slot, and the split leaves 15/21 within 2mm exactly as before.

What the six misses are, instead: three marginal (2.3–3.4mm on a date, an address and a
city) and three large — `Male[ ]Female` at 8.2mm, `1420 Bellefield Avenue A` at 15.3mm,
`Ngozi Okafor Sister 412-` at 34.2mm. Every one of the large ones is a *run-on*: the reader
returned several fields, or a row of printed options, as a single value. Where the value is
a whole row, its centre on the original is the centre of that row while the copy draws the
string from the region's left edge, so the millimetres measure the run-on rather than the
placement. The fix is in extraction, not in regeneration — but the criterion is not cleared
by saying so, and the number stands at 15/21.

The denominator grew (19 → 21) as regeneration learned to place values it used to leave off
the copy entirely, so the *share* within 2mm fell while the copy got better. There is no
systematic bias left: median offset is dx −0.15mm, dy −0.82mm.

**Handwriting is recognised, not read well.** 43–53% of 49 fields, on five phone
photographs, with a **10-point spread between identical runs** — the model is
non-deterministic and 11 of 23 failures are fields it never returned at all. No experiment
on a set this small can resolve an effect smaller than that spread, so *no run on it can
demonstrate a target*, including one that reaches it. Claude Opus 5 read the same photos
blind at 45/49 = 91.8%, which is the ceiling the ~73.5% working target is 80% of. A local
Apache-2.0 model (PaddleOCR-VL) reads 77.6% of these handwritten *values* against the
current model's 51%, but that is component-level recognition, it is not wired into the
shipped path, and it has never been measured end to end.

**Genuinely degraded scans are largely unread.** The last measurement on scanned documents
(FUNSD, 50 forms, 834 fields, 2026-09-13) read **53/834 = 6.4%**: 86% of expected values
never read at all, 8% read wrongly, and **79% of those wrong reads came back flagged**. On a
poor scan the system goes silent rather than confidently wrong, which is the behaviour that
matters clinically — but it reads very little. The bottleneck is association, not character
recognition: a second OCR engine sees 82.9% of the expected answer text where the current
one sees 41.2%, and swapping it moved the end-to-end score only 6.4% → 9.1%. That set is no
longer part of the regular benchmark.

Capture conditions that were measured and are *not* a problem: rotation (deskew recovers the
angle to within 0.01°), uniform dimming, and glare. One that was: a soft shadow gradient took
a real form from 7/8 fields to 1/8; flat-field correction restores it to 7/8, and to 5/8 at a
brutal 65% ramp.

**Character-level OCR errors are untouched by any of this.** `Daniel` reads as `Danie!`,
`812` as `B12`. No geometric fix addresses these.

**The vision model invents checkbox answers.** On a form where nothing is ticked it reports
`Sex='Female'`, `Marital Status='Single'`, `Race='White'`. An explicit prompt forbidding it
changed nothing. Tick marks are therefore read geometrically, by ink coverage. Any future use
of the model must take choice fields from the geometry and never from the model — inventing a
patient's sex or marital status is the worst failure this system could produce.

**Two layouts cannot be enrolled at all.** Enrolment refuses a page on which it can find no
answer region, and says so rather than deriving a template that would place values at
invented coordinates.

## Not implemented

- **FHIR beyond one resource.** `GET /api/v1/form-analyses/{id}/fhir` emits an R4
  `QuestionnaireResponse`; there is no LOINC coding of individual questions, and no other
  resource type.
- **Authentication.** Sign-off records who *claimed* to check a form. Nothing verifies it.
- **Template learning for every layout.** A template is derived from the geometric view, so
  it works where a form draws answer regions the detector can find.
- **Downsampled output.** A regenerated PDF embeds the full-resolution blank master, so a
  packet is several megabytes. Fine for a demo, wasteful for storage.

Implemented since this file last said otherwise: handwriting has a measured capability (poor,
see above); PostgreSQL is connected and verified end to end, selected by `DATABASE_URL` and
reported by `/health`; and the audit trail is append-only by construction — a correction is a
new row, the row it replaced stays, and there is no update or delete in the module.

## Licensing

**Resolved 2026-09-15.** Every dependency the product ships is compatible with its Apache 2.0
release. PyMuPDF (AGPL-3.0) was migrated out of `backend/app/` entirely and survives only in
the benchmark filler behind the optional `bench` extra, which is not part of what is
distributed. See [LICENSING.md](LICENSING.md) for the record and
`THIRD_PARTY_LICENSES.md` for the manifest.

## Data policy

No real patient data, and none of the benchmark material is committed. `data/real-forms/`
and `data/dataset-scanned/` are gitignored. The forms used for measurement are blank
documents published on clinics' own websites, filled with invented values.
