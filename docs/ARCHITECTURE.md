# Architecture

PrelimMD is a template-aware extraction prototype. It accepts a synthetic or approved form, creates color and OCR image artifacts, matches the page against a blank master, warps the form to canonical coordinates, crops known field boxes, applies local OCR/occupancy detection, normalizes values, and returns a versioned review payload.

```text
Next.js capture/review UI → FastAPI → validation/preprocessing → template registry/alignment
                                              ↓
                                   canonical field crops → OCR/normalization/confidence
                                              ↓
                                  local artifact JSON + overlay/review response
```

Coordinates are normalized (`0.0–1.0`) in the template’s canonical page coordinate system. This is why SVG overlays remain aligned at any display size.

The runtime route is template-aware, not generic document discovery. Unknown templates fail clearly. Cloud vision is deliberately disabled by default; `VISION_PROVIDER` is only an interface boundary for a later approved provider adapter.
