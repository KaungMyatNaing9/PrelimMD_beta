.PHONY: backend-install backend-dev frontend-install frontend-dev test lint typecheck db-up corpus evaluate smoke

PYTHON ?= python3.12
VENV ?= .venv312
PY := $(VENV)/bin/python

backend-install:
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e './backend[dev]'
backend-dev:
	cd backend && ../$(PY) -m uvicorn app.main:app --reload --port 8000
frontend-install:
	cd frontend && npm install
frontend-dev:
	cd frontend && npm run dev
test:
	cd backend && ../$(PY) -m pytest
lint:
	cd backend && ../$(PY) -m ruff check app tests
	cd frontend && npm run lint
typecheck:
	cd backend && ../$(PY) -m mypy app
	cd frontend && npm run typecheck
db-up:
	docker compose up -d postgres
corpus:
	$(PY) scripts/create_synthetic_sample.py
evaluate:
	$(PY) scripts/evaluate_corpus.py --split validation
smoke:
	./scripts/smoke.sh
