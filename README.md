# PrelimMD

PrelimMD is a synthetic-data research prototype for turning intake forms into structured, reviewable information. Upload a PDF, scan, or image; PrelimMD preserves the page, detects available form fields, extracts existing answers, flags uncertain results, and shows a visual overlay for human review.

It is not a clinical system, EHR integration, diagnosis tool, patient portal, or HIPAA-compliant service. Never upload real patient or sensitive personal data.

## What is implemented

- FastAPI backend and Next.js review UI.
- JPG, PNG, and PDF validation with safe local artifacts.
- PDF digital-text form analysis for the synthetic Robert fixture.
- PDF word coordinates, label/value detection for colon-delimited rows, normalized coordinates, extracted values, canonical mappings, confidence, and review flags.
- Complete-page PNG and SVG overlay endpoints.
- Editable form-analysis corrections that persist in the active analysis.
- Legacy template-aware `/api/v1/capture` path retained for compatibility.
- Synthetic corpus: 3 layouts, 24 development, 6 validation, and 6 held-out test samples.

Current limitation: generic scan/image OCR and general-purpose unknown-layout detection are still in progress. The bounded digital-PDF workflow is the current demonstration path.

## Run locally

Requirements: Python 3.12, Node.js 20+, and optionally Docker Desktop for PostgreSQL. 

```bash
cd /Users/kaungmyatnaing/GitRepo/PrelimMD_beta
cp .env.example .env
make backend-install
make frontend-install
make corpus
```

Start the backend in one terminal:

```bash
cd backend
../.venv312/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8002
```

Start the frontend in a second terminal:

```bash
cd frontend
NEXT_PUBLIC_API_URL=http://localhost:8002 npm run dev -- --hostname 127.0.0.1 --port 3002
```

Open:

- App: http://localhost:3002/capture
- API health: http://localhost:8002/api/v1/health
- API docs: http://localhost:8002/docs

Upload the synthetic digital-PDF fixture:

```text
backend/tests/fixtures/test_partial_form_robert.pdf
```

## Checks

```bash
make test
make lint
make typecheck
python scripts/verify_manifest.py
SMOKE_PORT=8011 make smoke
```

Docker currently starts PostgreSQL only:

```bash
docker compose up -d postgres
docker compose logs -f postgres
docker compose down
```

## Project layout

- `backend/` — API, schemas, preprocessing, form analysis, tests.
- `frontend/` — upload and review application.
- `data/corpus/` — deterministic synthetic corpus and labels.
- `backend/tests/fixtures/` — regression PDFs, including the Robert digital form.
- `docs/` — architecture, API, progress, and demo notes.
