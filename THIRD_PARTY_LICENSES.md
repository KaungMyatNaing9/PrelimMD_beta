# Third-party dependencies

| Dependency | Version range | Purpose | License |
| --- | --- | --- | --- |
| FastAPI | >=0.115,<1 | HTTP API | MIT |
| Pydantic | >=2.9,<3 | Versioned validation schemas | MIT |
| Pillow | >=10.4,<12 | Image loading, synthetic corpus, PDF rasterization | HPND |
| NumPy | >=2.0,<3 | Image arrays | BSD-3-Clause |
| OpenCV Python | >=4.10,<5 | Perspective correction, registration, deskew | Apache-2.0 |
| pytesseract | >=0.3.13,<1 | Optional local OCR adapter | Apache-2.0 |
| Uvicorn | >=0.30,<1 | Development ASGI server | BSD-3-Clause |
| pytest | >=8,<9 | Tests | MIT |
| Ruff | >=0.6,<1 | Linting/formatting | MIT |
| Next.js | 15.x | Web application | MIT |
| React | 19.x | Web UI | MIT |
| TypeScript | 5.x | Typed frontend | Apache-2.0 |

The prototype deliberately has no cloud OCR or vision dependency. `VISION_PROVIDER=disabled` is the safe default.
