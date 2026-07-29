# Sprint 1–2 demo

1. Run `make corpus`, `make backend-dev`, and `make frontend-dev` in separate terminals.
2. Visit `http://localhost:3000/capture`.
3. Upload `data/corpus/validation/T01/T01_VAL_001/source.jpg` and choose `T01` if auto-detection is not installed/configured.
4. Show the normalized page, template match, and SVG field overlays.
5. Filter to **Needs review**, edit a field, save it, and refresh the page.
6. Expand the Structured JSON panel to show raw value, normalized value, source, confidence, box, and review status.
7. Run `make evaluate` for the validation report. Use `--split test` only for the formal held-out demo.

Known limitations: no production persistence/authentication, no handwriting-specific cloud provider by default, PDF rendering needs local Poppler, and template match supports only approved templates.
