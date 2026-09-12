# Third-party dependencies

| Dependency | Version range | Purpose | License |
| --- | --- | --- | --- |
| FastAPI | >=0.115,<1 | HTTP API | MIT |
| Pydantic | >=2.9,<3 | Versioned validation schemas | MIT |
| Pillow | >=10.4,<13 | Image loading, synthetic corpus, PDF rasterization | HPND |
| NumPy | >=2.0,<3 | Image arrays | BSD-3-Clause |
| OpenCV Python | >=4.10,<5 | Perspective correction, registration, deskew | Apache-2.0 |
| pytesseract | >=0.3.13,<1 | Optional local OCR adapter | Apache-2.0 |
| Uvicorn | >=0.30,<1 | Development ASGI server | BSD-3-Clause |
| pytest | >=8,<9 | Tests | MIT |
| Ruff | >=0.6,<1 | Linting/formatting | MIT |
| Next.js | 15.x | Web application | MIT |
| React | 19.x | Web UI | MIT |
| TypeScript | 5.x | Typed frontend | Apache-2.0 |

## Optional extra: `postgres`

| Dependency | Version range | Purpose | License |
| --- | --- | --- | --- |
| psycopg (binary) | >=3.1,<4 | PostgreSQL driver for the audit trail | LGPL-3.0 |

psycopg is LGPL-3.0, used as a library over its public API, which is compatible with
distributing this project under Apache 2.0. Recorded rather than left implicit, since the
licence manifest is a faculty-review item. Without the extra the audit trail runs on
SQLite from the standard library and nothing else changes.

## Optional extra: `heic`

| Dependency | Version range | Purpose | License |
| --- | --- | --- | --- |
| pillow-heif | >=1.0,<2 | Decode iPhone HEIC/HEIF photos | BSD-3-Clause |

The wheel bundles **libheif (LGPL-3.0)**, dynamically linked. That arrangement is
compatible with distributing this project under Apache 2.0, but it is an LGPL component
and is recorded here for that reason rather than left implicit. Without the extra, HEIC
uploads are refused with an actionable message and nothing else changes.

## Optional extra: `handwriting`

Not installed by default, and nothing in the shipped path imports it.

| Dependency | Version range | Purpose | License |
| --- | --- | --- | --- |
| PaddlePaddle | >=3.3,<4 | Inference runtime for PaddleOCR-VL | Apache-2.0 |
| PaddleOCR | >=3.7,<4 | PaddleOCR-VL pipeline: layout detection + handwriting recognition | Apache-2.0 |
| PaddleX | >=3.7,<4 (`[ocr]`) | Pipeline plumbing PaddleOCR requires | Apache-2.0 |

All three are Apache-2.0, so this extra raises no licensing conflict with the Apache 2.0
deliverable — unlike PyMuPDF (see `docs/LICENSING.md`) and unlike Chandra OCR 2, the most
accurate handwriting model surveyed, which is OpenRAIL-M with a field-of-use restriction
and was rejected for that reason rather than on accuracy.

Installing it upgrades Pillow past 12. The full test suite and the geometric accuracy of
all three real-form sets were re-measured on Pillow 12.3 and are unchanged, so the `<12`
pin has been widened to `<13`; it was a guess rather than a measurement.

The prototype deliberately has no cloud OCR or vision dependency. `VISION_PROVIDER=disabled` is the safe default.
