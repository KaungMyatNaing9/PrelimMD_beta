# Dependency licensing

The project ships under **Apache 2.0** (Gate 1 deliverable, `LICENSE` at the repository
root). The faculty review asks that the dependency manifest stay current and that every
component be confirmed compatible with that release.

**Every component now is. Resolved 2026-09-15 by option 1 below: the product migrated to a
permissive PDF stack.** PyMuPDF is no longer imported anywhere under `backend/app/` --
everything the product does with a PDF goes through `app/services/pdf_backend.py`, built on
pdfplumber/pdfminer.six (MIT), pypdf (BSD), pypdfium2 (Apache/BSD) and reportlab (BSD). The
migration was verified word for word against PyMuPDF on every benchmark page and on both
accuracy tracks (`docs/PROGRESS.md`, 2026-09-15 night). PyMuPDF remains only in
`scripts/fill_forms.py`, the benchmark filler, behind the optional `bench` extra; it never
runs in the product and is not part of what is distributed as PrelimMD.

**Rule for contributors: do not add a PyMuPDF import to `backend/app/`. Extend
`pdf_backend.py` instead.**

The rest of this document is the record of the conflict and the evidence that decided it.

## The conflict (as it stood before 2026-09-15)

**PyMuPDF** (`pymupdf`, imported as `fitz`) is dual-licensed **AGPL-3.0 or Artifex
Commercial**. AGPL-3.0 is a strong copyleft licence: distributing an Apache 2.0 project
that depends on it is not compatible without either an Artifex commercial licence or
relicensing the project.

It is load-bearing in two places:

| Capability | Where |
|---|---|
| Read a PDF's words with their bounding boxes | `services/form_analyzer.py` |
| Render a PDF page to an image, so rules can be detected | `services/form_analyzer.py` |
| Compose the regenerated PDF: place the blank master, draw values | `services/regeneration.py` |
| Font descender metric, so a value sits on its rule rather than through it | `services/regeneration.py` |

Every other dependency checked so far is permissive. The OCR engines considered during the
extraction work — `paddleocr`, `paddlepaddle`, `easyocr`, `rapidocr-onnxruntime` — are all
Apache 2.0.

## Replacements, and evidence they fit

| Need | Candidate | Licence |
|---|---|---|
| Words with bounding boxes | `pdfplumber` | MIT |
| Render a page to an image | `pypdfium2` | BSD-3-Clause / Apache-2.0 |
| Compose a PDF, place image and text | `reportlab` | BSD |

The risky part is coordinates: regeneration places values from stored template geometry, so
a replacement that disagreed about where a word sits would move every filled value.

Measured on a real filled form (ECU Vidant, 116 unambiguously matched words):

```
max dx 0.00pt   max dy 3.84pt
median dx 0.00pt   median dy 0.09pt
```

Sub-point agreement. Against a ~2mm (5.7pt) placement criterion, that is comfortable.

`pdfplumber` tokenises differently from PyMuPDF, most visibly around runs of underscores —
it returns 367 words where PyMuPDF returns 422 for the same page. That is a wash rather
than a problem: the pipeline already splits rule runs off adjacent text and discards them
(`word_source._split_rules_from_text`), so PyMuPDF's extra tokens are ones we remove.

## What the swap costs

Four capabilities across two files, not a rewrite of the pipeline. Reading is close to a
like-for-like substitution. Composition is the larger piece: `reportlab` builds a page
rather than editing one, so `regeneration.regenerate` would be restructured, and the
placement tests (`test_regeneration.py`) are what would prove it landed correctly — they
already assert that a value sits inside its field and on the rule at its foot.

The candidate packages were installed, measured, and uninstalled again so the manifest
still reflects what the project actually ships.

## The decision, and what was chosen

1. **Migrate** to the permissive stack above -- **chosen, done 2026-09-15**.
2. Obtain an Artifex commercial licence for PyMuPDF -- not pursued.
3. Relicense PrelimMD, which contradicts the Gate 1 deliverable -- not pursued.

The manifest (`THIRD_PARTY_LICENSES.md`) has no failing entry. Two further licence
decisions were taken on the same evidence: Chandra OCR 2, the most accurate handwriting
model surveyed, was rejected for being OpenRAIL-M with a field-of-use restriction rather
than on accuracy; and the optional extras are recorded with their own terms (`postgres`:
psycopg, LGPL-3.0, used over its public API; `heic`: pillow-heif, BSD-3-Clause, dynamically
linking libheif, LGPL-3.0; `handwriting`: PaddlePaddle/PaddleOCR/PaddleX, all Apache-2.0,
not installed by default).
