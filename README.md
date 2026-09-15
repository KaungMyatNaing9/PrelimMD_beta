# PrelimMD

PrelimMD turns a filled-in intake form into structured, reviewable information. Upload a
PDF, scan or photograph of a form; it detects the fields, reads the answers, flags the ones
it is unsure of, shows them overlaid on the page for a human to correct, and can refill a
blank copy of the same form from what it read.

It is a research prototype: not a clinical system, not an EHR integration, not a diagnosis
tool, and not HIPAA-compliant. **Never upload real patient data.**

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
  depend on capture quality.
- **An optional local vision model** (`qwen2.5-VL` via Ollama), off by default. Nothing
  leaves the machine.

Read [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) before relying on any of it.
The short version: real scans are largely unread, values can bind to the wrong label, and
handwriting is not supported.

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

The project ships under Apache 2.0. **One dependency currently conflicts with that**:
PyMuPDF is AGPL-3.0. See [docs/LICENSING.md](docs/LICENSING.md) for the costed options.

## Data policy

Synthetic or public data only. No real patient information, ever. The forms used for
measurement are blank documents published on clinics' own websites, filled with invented
values, and are gitignored rather than committed.
