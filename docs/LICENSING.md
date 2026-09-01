# Dependency licensing

The project ships under **Apache 2.0** (Gate 1 deliverable, `LICENSE` at the repository
root). The faculty review asks that the dependency manifest stay current and that every
component be confirmed compatible with that release.

One component is not.

## The conflict

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

## The decision needed

1. **Migrate** to the permissive stack above, or
2. **Obtain an Artifex commercial licence** for PyMuPDF, or
3. **Relicense** PrelimMD, which contradicts the Gate 1 deliverable.

This is a licensing decision, not an engineering one, and it should be settled before Gate
3: the evidence package the faculty asks for includes a current manifest, and this is the
one entry that fails it.
