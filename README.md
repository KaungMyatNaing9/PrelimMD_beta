# PrelimMD

PrelimMD is a research prototype that turns a medical intake form into reviewable structured data. It is designed for a narrow, reliable workflow: upload an approved JPG, PNG, or PDF; normalize the page; align it to a known blank template; extract known field regions; flag uncertain values; and let a staff member correct them.

It is **not** a clinical system, a diagnosis tool, or a HIPAA-compliant product. The repository uses deterministic synthetic forms only—never real patient data.

## What works in Sprint 1–2

- Validates JPG, PNG, and PDF uploads (size and file signature); creates internal run IDs.
- Applies EXIF orientation, creates normalized color and OCR-gray artifacts, and conservatively deskews a page.
- Registers three versioned synthetic blank templates (`T01`–`T03`) with normalized field coordinates.
- Uses template alignment and canonical crops for names, dates, phones, identifiers, numbers, checkboxes, medication text, notes, and signature presence.
- Returns Pydantic-validated JSON with raw and normalized values, confidence, source, bounding box, validation errors, and review flags.
- Provides a Next.js capture screen plus aligned SVG overlays and editable review fields.
- Includes a deterministic 36-sample synthetic corpus (24 development / 6 validation / 6 held-out test), a manifest verifier, and evaluation-report command.

## Quick start

Prerequisites: Python **3.12.9** (not 3.14), Node 20+, and Poppler (`pdftoppm`) for PDF inputs. Local printed OCR is optional but recommended: install Tesseract and ensure `tesseract` is on your path.

```bash
cp .env.example .env
make backend-install # creates .venv312 with Python 3.12
make frontend-install
make corpus
python scripts/verify_manifest.py
```

In separate terminals:

```bash
make backend-dev
make frontend-dev
```

Open `http://localhost:3000/capture`. Upload one of the generated `source.jpg` files under `data/corpus/validation/`, or use `T01` as the template hint. The API docs are available at `http://localhost:8000/docs`.

To run automated checks:

```bash
make test
make lint
make typecheck
make evaluate # scores saved extraction outputs in artifacts/evaluation/runs/
make smoke    # full local API smoke test; starts and stops its own API
```

Docker currently runs **PostgreSQL only**: `docker compose up -d postgres`, `docker compose logs -f postgres`, and `docker compose down`. The frontend and backend run locally.

### Troubleshooting

- Python 3.14: remove the accidental venv and run `make backend-install`; `.python-version` requires 3.12.9.
- OpenCV wheel failure: use Python 3.12 and install the prebuilt wheel with `.venv312/bin/python -m pip install 'opencv-python==4.10.0.84'`, then rerun installation.
- Port 8000 in use: run `SMOKE_PORT=8010 make smoke`, or stop the other local server.
- Frontend/API or CORS failure: set `NEXT_PUBLIC_API_URL=http://localhost:8000` before `npm run dev`; FastAPI allows the local frontend origin.
- No vision key: expected—the default provider is disabled and extraction falls back to local OCR/review flags.
- Unknown template/alignment failure: use a generated approved `T01`–`T03` sample and optionally supply its template hint.
- Docker volume permissions: `docker compose down -v` resets only the local PostgreSQL volume; generated artifacts remain local and can be reset with `rm -rf artifacts/`.

## How it works

```text
form upload → validate/rasterize → orient + deskew → template match + canonical alignment
           → known field crops → OCR / occupancy detection → normalization + confidence
           → review UI + versioned JSON
```

The system deliberately does not rediscover fields with a general-purpose model on each form. Field boxes are defined once per blank master template and stored as normalized coordinates, making outputs reproducible and overlays resolution-independent.

## Repository map

- `backend/` — FastAPI service, Pydantic schemas, preprocessing, template alignment, extraction, and tests.
- `frontend/` — Next.js staff capture/review UI.
- `data/corpus/` — generated synthetic-only templates, samples, expected labels, and manifest.
- `scripts/` — corpus generator, manifest verifier, and evaluator.
- `docs/` — architecture, API, demo, and progress notes.

## Evaluation and held-out data

The corpus is stratified by template: eight development, two validation, and two test samples per template. The test labels are not needed for ordinary development. Run `python scripts/evaluate_corpus.py --split test` only for the formal held-out demonstration. Reports are written to `artifacts/evaluation/`.

## Limitations and safety

The default vision provider is disabled. Local Tesseract is used when installed; if OCR is unavailable or uncertain, fields are preserved as blank/low-confidence for human review rather than guessed. Only the first PDF page is processed in the current demonstrator, and processing artifacts are local files, not a production database.

See [Prelim.md](Prelim.md), [architecture notes](docs/ARCHITECTURE.md), [API reference](docs/API.md), and the [demo script](docs/DEMO_SCRIPT.md) for the scoped implementation contract.
