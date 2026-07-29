# Progress

## 2026-07-28

### Repository state

The repository started with the approved `Prelim.md` build specification only. No application, corpus, or runtime configuration existed.

### Sprint 1

- [x] Repository scaffold, versioned schemas, API skeleton, and local artifacts.
- [x] Synthetic three-template corpus generator and manifest verifier.
- [x] Upload validation, image/PDF rasterization boundary, orientation, deskew, and canonical template alignment path.
- [x] Template-aware crops, local OCR adapter boundary, capture/extraction API, and overlay UI.

### Sprint 2

- [x] Type-specific normalization, confidence/review rules, correction persistence, and evaluation CLI.
- [x] Review UI with editable fields and JSON output.
- [x] Generated and validated the synthetic 36-sample corpus; backend schema/normalization tests and frontend production build pass.

### Current work

Runtime validation completed on Python 3.12.9: health, upload, preprocessing, alignment, extraction, PNG/SVG artifacts, correction persistence, backend tests, lint/type check, and frontend production build.

### Blockers

No approved real blank forms are present; the committed workflow uses deterministic synthetic forms only. Tesseract/Poppler are absent in this environment, so PDF and printed-text OCR need those optional local tools for full quality.

### Next concrete step

Run `make smoke` before a demo; start PostgreSQL with `make db-up` only when needed.
