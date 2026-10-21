# PrelimMD

PrelimMD turns a filled-in intake form into structured, reviewable information. Upload a
PDF, scan or photograph of a form; it detects the fields, reads the answers, flags the ones
it is unsure of, shows them overlaid on the page for a human to correct, and can refill a
blank copy of the same form from what it read.

It is a research prototype: not a clinical system, not an EHR integration, not a diagnosis
tool, and not HIPAA-compliant. **Never upload real patient data.**

## Who it is for, and where it fits

**The user** is the front-desk or intake staff at an outpatient clinic: the person who, today,
retypes a patient's paper intake packet into the clinic's records by hand.

**The workflow.** A patient fills in the clinic's own intake packet, on paper or as a PDF.
Staff scan or photograph it. PrelimMD reads it and marks every answer it is unsure of. Staff
check the page on screen beside what was read, correct anything wrong, and sign it off. The
answers are stored, and a clean filled copy of the clinic's own form can be produced from them.
A clinic uses a handful of intake forms thousands of times, so each form is learned once, from
its blank, and every later copy is read through what was learned.

**What the software does:** reads printed and typed answers; reads tick boxes by measuring the
ink in each box; learns a form's layout from one blank copy; says how sure it is of each answer
and flags the doubtful ones for a person; and refills the clinic's own blank form from the
stored answers.

**What it does not do:** diagnose, triage, or advise on anything clinical; read what a signature
says (only whether one is there); read handwriting well (43-53% of fields on our handwritten
set); verify who signed off a form; or send a page anywhere off the machine, unless an optional
model is deliberately switched on. A person checks every form before it is used.

## What works today

- **PDFs, JPGs and PNGs**, through one shared understanding pipeline. The file format
  decides only where the words come from — a PDF's text layer or OCR over the page.
- **Multi-page PDFs.** Every page is read and each is reviewable; insurance sections
  usually live on page 2.
- **Two ways of reading a page**, scored against each other: fields drawn as boxes or ruled
  lines, and `label: value` on one row.
- **Tick boxes.** Marital status, Yes/No consents and similar are read as a *choice*, by
  measuring ink in the box rather than by reading text, so an untouched group correctly
  reads as unanswered.
- **Per-field confidence**, with low-confidence fields flagged for review rather than
  presented as fact.
- **Template learning.** Given a filled form no registered template matches, it derives a
  blank master and field coordinates from the upload itself.
- **Regeneration.** Stored values are drawn back onto a blank template, positioned from the
  template's own coordinates rather than from the upload, so output fidelity does not
  depend on capture quality. Every page of a packet is refilled, a value is never written
  over what the form prints or over another value, and a value too long for its space is set
  smaller rather than cut. Measured across 111 real layouts, the copy makes no such mistake
  (`scripts/evaluate_regeneration.py`).
- **An optional local vision model** (`qwen2.5-VL` via Ollama), off by default. Nothing
  leaves the machine.

Read [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) before relying on any of it.
The short version: a value can bind to the wrong label, genuinely degraded scans are largely
unread (and come back flagged rather than wrong), and handwriting reads at 43-53% where
printed text reads at 92%.

## Run it

Requires Python 3.12, Node 20+, and two system binaries:

```bash
brew install tesseract poppler     # OCR, and PDF rasterisation
```

```bash
cp .env.example .env
make backend-install
make frontend-install
```

Backend, in one terminal:

```bash
cd backend
../.venv312/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Drop `--reload` when showing the app to anybody: its file watcher sits at around 98% of a
core on this repository, which is a poor use of a laptop that is also running OCR.

Frontend, in a second:

```bash
cd frontend
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Then open <http://localhost:3000/capture> and upload a form. The API is at
<http://localhost:8000/docs>.

A form to try, included in the repository:

```text
backend/tests/fixtures/test_partial_form_robert.pdf
```

Real clinic forms are deliberately **not** committed — see the data policy below.

## Reviewing what it read

The review screen shows the page with each detected answer boxed, and the values beside it.
Fields it read come first in reading order; empty ones collapse behind a disclosure. A
multi-page upload gets one button per page, each showing how many fields were read there.
Corrections are saved against the analysis.

If a template matched, **Regenerate filled PDF** draws the values back onto a blank copy.
If none matched, **Learn template from this form** derives one from the upload.

## Measuring it

Accuracy is reported from real clinic forms only. The synthetic corpus that once served as
a regression check was removed on 2026-09-13: it read far higher than reality and nothing
measures against it any more. See
[docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) for why, and for the caveats that
belong with every figure.

```bash
python scripts/evaluate_real_forms.py                       # real forms
python scripts/evaluate_real_forms.py --truth ground_truth_heldout2.json
python scripts/evaluate_scans.py                            # real scans, and flagging
python scripts/evaluate_roundtrip.py                        # read, learn, refill, read back
python scripts/evaluate_placement.py                        # regeneration accuracy, in mm
python scripts/evaluate_vision.py                           # vision model, needs Ollama
```

These need the benchmark forms, which are not in the repository.

## Checks

```bash
make test
make lint
make typecheck
SMOKE_PORT=8011 make smoke
```

## Project layout

- `backend/app/services/` — the pipeline: `word_source` (words from any format),
  `field_layout` (boxes, rules, tick boxes), `form_analyzer` (understanding),
  `template_builder`, `regeneration`.
- `frontend/` — upload and review application.
- `scripts/` — the measurement harnesses listed above.
- `docs/` — architecture, API, licensing, known limitations.

## Licensing

The project ships under Apache 2.0, and every dependency it ships is compatible with that
(`THIRD_PARTY_LICENSES.md`). PyMuPDF, which is AGPL-3.0, was taken out of the product on
2026-09-15 and survives only in the benchmark filler behind the optional `bench` extra. See
[docs/LICENSING.md](docs/LICENSING.md) for the record.

## Data policy

Synthetic or public data only. No real patient information, ever. The forms used for
measurement are blank documents published on clinics' own websites, filled with invented
values, and are gitignored rather than committed.
