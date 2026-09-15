# Known limitations

Every figure here is measured, and the script that produces it is named so it can be
re-run. Where a number would flatter the system, the caveat is given with it.

## How accuracy is measured, and why the obvious number is not used

A synthetic corpus once read **96.9%**; every sample in it was a clean render of a layout
the extractor was tuned against, and on the day both were first measured real clinic forms
read 45%. It was deleted on 2026-09-13 so that number can no longer be produced, let alone
quoted.

Reported accuracy comes from real clinic forms downloaded from public clinic websites
(`scripts/evaluate_real_forms.py`).

**That benchmark is itself biased toward easy fields.** Values are placed onto blank real
forms beside their printed label, and the filler skips any field where the space beside the
label is not already clear — 13 to 19 values per form. So the fields being scored are the
unambiguous ones. Forms carry between 4 and 11 scored fields each, which makes per-form
percentages incomparable: "100% on Bauman" means four easy fields, not a form read
perfectly.

Say **"47 of 60 on a favourable slice"**, not a bare percentage.

## Current measurements

| Benchmark | Result | Script |
|---|---|---|
| Real forms, development set (5 forms, 45 fields) | 32/45 | `evaluate_real_forms.py` |
| Held-out set A (3 unseen forms, 11 fields) | 11/11 | `--truth ground_truth_heldout.json` |
| Held-out set B (3 unseen forms, 60 fields) | 47/60 | `--truth ground_truth_heldout2.json` |
| Real scanned forms (FUNSD, 50 forms, 834 fields) | 53/834 | `evaluate_scans.py` |
| Round trip: read, learn, refill, read back | 16/34 | `evaluate_roundtrip.py` |
| Vision model (qwen2.5-VL 7B, local) | 30/34 at ~53s/page | `evaluate_vision.py` |

Held-out A is small and biased easy — 11 fields, all of them ones the filler could place
cleanly. It is not evidence of general accuracy.

Held-out set B was used to diagnose a PDF baseline bug, so it is training data now. A clean
generalisation figure needs forms neither the system nor its author has seen.

## Where it fails, and why

**Real scans are largely unread.** 6.4% on FUNSD. The shape matters more than the number:
86% of expected values are never read at all and 8% are read wrongly, of which **79% come
back flagged for review**. On a poor scan the system goes silent rather than confidently
wrong, which is the behaviour that matters clinically — but it reads very little.

The bottleneck there is *association*, not character recognition. A second OCR engine sees
82.9% of the expected answer text where the current one sees 41.2%, and swapping it moves
the end-to-end score only 6.4% → 9.1%. Doubling what the reader can see buys under three
points, because the difficulty is binding a value to its label.

**A value can bind to the wrong label.** This is the largest open problem and it blocks two
Gate 3 criteria at once. Four approaches were tried and reverted, each trading one benchmark
against another; the diagnosis is that the signals available (a value's shape, its type
validity) verify what a value *looks like*, and the broken thing is which label it *belongs*
to. For context, entity linking on the FUNSD benchmark tops out near 0.80 F1 with a trained
model, so this is not a gap a further hand-built rule is likely to close.

**Regeneration placement is accurate; slot selection is not.** A value bound to the right
field lands sub-0.5mm from where it sat on the original. The headline placement figure is
much worse only because values printed into the wrong slot are counted as placement error.

**Character-level OCR errors are untouched by any of this.** `Daniel` reads as `Danie!`,
`812` as `B12`. No geometric fix addresses these.

**The vision model invents checkbox answers.** On a form where nothing is ticked it reports
`Sex='Female'`, `Marital Status='Single'`, `Race='White'`. An explicit prompt forbidding it
changed nothing. Tick marks are therefore read geometrically, by ink coverage, which
measured **zero false positives across five forms**. Any future use of the model must take
choice fields from the geometry and never from the model — inventing a patient's sex or
marital status is the worst failure this system could produce.

## Not implemented

- **Handwriting.** No capability, and the Gate 3 target of 80% is above the current state
  of the art: a 2026 benchmark of 17 models on 49 real handwritten medical forms puts the
  best frontier model at 0.78, while open-source models comparable to ours scored 0.48 with
  a 36% hallucination rate and were excluded from the real-data evaluation.
- **Storage.** Persistence is durable JSON under `artifact_root`. `docker-compose.yml`
  defines PostgreSQL; nothing connects to it.
- **FHIR / LOINC output.** The faculty review's first technical recommendation.
  `canonical_registry.py` is already a controlled vocabulary with types and aliases, so this
  is a mapping layer rather than a rewrite, but it does not exist yet.
- **Field-level audit trail.** Per-field confidence exists; the immutable diff the faculty
  asked for — source document, extraction confidence, last modifier, never silently
  overwriting a patient-entered value — does not.
- **Template learning for every layout.** A template is derived from the geometric view, so
  it works where a form draws answer regions the detector can find.

## Licensing

**PyMuPDF is AGPL-3.0**, which is incompatible with this project's Apache 2.0 release, and
it backs PDF reading and regeneration. See [LICENSING.md](LICENSING.md) for the costed
options. This is unresolved and is the one entry in the dependency manifest that fails.

## Data policy

No real patient data, and none of the benchmark material is committed. `data/real-forms/`
and `data/dataset-scanned/` are gitignored. The forms used for measurement are blank
documents published on clinics' own websites, filled with invented values.
