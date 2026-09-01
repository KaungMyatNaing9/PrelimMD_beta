"""Read a form with a vision model, where geometry and OCR alone cannot.

Geometric detection binds a value to a label by where they sit on the page. That works
when a form draws boxes, and fails on real scans: measured against FUNSD, 43% of values
extracted for a correctly-identified label were unrelated text. A model that *sees* the
page reads the association directly instead of inferring it from layout.

This deliberately does not ask the model for coordinates. Vision models are unreliable at
pixel geometry, and regeneration fidelity depends on exact boxes, so positions still come
from OCR — the model supplies comprehension, Tesseract supplies geometry, and
``locate_values`` matches one onto the other.

Providers are pluggable so a local model can do the work for free with no data leaving the
machine, which matters more than cost for anything resembling patient data.
"""

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

from fastapi import HTTPException

from app.config import settings
from app.services.word_source import Word

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_OLLAMA_MODEL = "qwen2.5vl:7b"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"

PROMPT = """You are reading a form. List every field that HAS BEEN ANSWERED.

A field is a printed caption with somewhere to answer — a box, a ruled line, a blank
space, or a checkbox.

For each ANSWERED field return:
- "label": the printed caption exactly as it appears
- "value": what a person wrote or typed there
- "handwritten": true if the value is handwritten rather than printed

Rules:
- REPORT EVERY ANSWERED FIELD. Work down the whole page and do not stop early: a form
  with twelve answers must return twelve. Missing one is the worst thing you can do.
- Skip a field only when its answer space is genuinely blank. The caller already knows
  where the empty fields are, so listing them is wasted effort — but when in doubt about
  whether something was filled in, report it.
- TABLE CELLS ARE FIELDS. Many forms lay their fields out in a grid or table. A cell
  with a caption is a field, and each cell in a row is a SEPARATE field. Never merge
  several captions into one.
- A printed rule used for writing on ("______" or "......") is not a value. That field
  is empty, so its value is null.
- Checkboxes: value is true if ticked, false if empty. Report the option label.
- Do not report page headings, logos, page numbers, or paragraphs of consent text.
- Transcribe values exactly. Do not correct spelling or reformat.
- Join a multi-line value's lines with \\n.
- If you cannot read a value, use null rather than guessing.
- Only return an empty list if the page is completely unfilled. If you can see even one
  answer, the list must not be empty.

Respond with JSON only: {"fields": [{"label": ..., "value": ..., "handwritten": ...}]}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": ["string", "null"]},
                    "handwritten": {"type": "boolean"},
                },
                "required": ["label", "value", "handwritten"],
            },
        }
    },
    "required": ["fields"],
}


class VisionField(NamedTuple):
    label: str
    value: str | None
    handwritten: bool


class VisionTruncated(Exception):
    """The model's field list was cut off — what was read is real, but incomplete.

    Raised rather than returned so a partial page can never be silently reported as a
    whole one. Callers that genuinely want the partial read can catch this and use
    ``.fields``.
    """

    def __init__(self, message: str, fields: list[VisionField]):
        super().__init__(message)
        self.fields = fields


def available() -> bool:
    return settings.vision_provider not in ("", "disabled")


def extract(image: Path) -> list[VisionField]:
    """Read label/value pairs off a form image using the configured provider."""
    provider = settings.vision_provider
    if provider in ("", "disabled"):
        raise HTTPException(503, "Vision extraction is disabled; set VISION_PROVIDER")
    if provider == "ollama":
        raw = _call_ollama(image)
    elif provider == "anthropic":
        raw = _call_anthropic(image)
    else:
        raise HTTPException(503, f"Unknown VISION_PROVIDER {provider!r}")

    fields = [
        VisionField(
            label=str(f.get("label", "")).strip(),
            value=(str(f["value"]).strip() or None) if f.get("value") is not None else None,
            handwritten=bool(f.get("handwritten")),
        )
        for f in raw.get("fields", [])
        if str(f.get("label", "")).strip()
    ]
    if raw.get("truncated"):
        # A partial read must not look like a complete one.
        raise VisionTruncated(
            f"Vision model output was cut off after {len(fields)} fields; the page has "
            "more. Raise num_predict or split the page.",
            fields,
        )
    return fields


# Beyond roughly this, a local model spends much longer per page for no accuracy gain —
# it downsamples internally anyway. Form text stays legible well below it.
MAX_EDGE_PIXELS = 1600


def _encoded(image: Path) -> str:
    """Base64 the page, shrinking an oversized scan first."""
    from io import BytesIO

    from PIL import Image

    page = Image.open(image)
    if max(page.size) > MAX_EDGE_PIXELS:
        scale = MAX_EDGE_PIXELS / max(page.size)
        page = page.convert("RGB").resize(
            (round(page.width * scale), round(page.height * scale)), Image.LANCZOS
        )
        buffer = BytesIO()
        page.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode()
    return base64.b64encode(image.read_bytes()).decode()


def _call_ollama(image: Path) -> dict:
    """Local model via Ollama. Free, and no page data leaves the machine."""
    payload = {
        "model": settings.vision_model or DEFAULT_OLLAMA_MODEL,
        "messages": [{"role": "user", "content": PROMPT, "images": [_encoded(image)]}],
        "stream": False,
        "format": SCHEMA,
        "options": {
            "temperature": 0,
            # A dense form can carry 40+ fields; the default output cap truncates the
            # JSON mid-array, and a page that long also needs the wider context window.
            "num_predict": 8192,
            "num_ctx": 8192,
        },
    }
    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.vision_timeout_seconds) as r:
            body = json.loads(r.read())
    except urllib.error.URLError as exc:
        raise HTTPException(
            503,
            "Cannot reach Ollama at localhost:11434. Start it with `ollama serve` and "
            f"pull a vision model (`ollama pull {DEFAULT_OLLAMA_MODEL}`).",
        ) from exc

    return _parse_fields(body.get("message", {}).get("content", ""))


def _parse_fields(content: str) -> dict:
    """Parse the model's JSON, salvaging a response that was cut off mid-array.

    A long form can exhaust the output budget partway through the field list. The
    fields already emitted are still good, so recover them — but mark the result
    ``truncated`` so a partial read is never mistaken for a complete one.
    """
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Keep whole objects only — a half-written final entry has unreliable values.
    complete = content.rfind("},")
    if complete != -1:
        try:
            salvaged = json.loads(content[: complete + 1] + "]}")
            salvaged["truncated"] = True
            return salvaged
        except json.JSONDecodeError:
            pass
    raise HTTPException(502, f"Vision model returned unparseable JSON: {content[:200]}")


def _call_anthropic(image: Path) -> dict:
    """Claude vision. Higher accuracy, costs per call, page data leaves the machine."""
    try:
        import anthropic
    except ImportError as exc:
        raise HTTPException(503, "pip install anthropic to use VISION_PROVIDER=anthropic") from exc

    client = anthropic.Anthropic(api_key=settings.vision_api_key or None)
    suffix = image.suffix.lower()
    media_type = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
    response = client.messages.create(
        model=settings.vision_model or DEFAULT_ANTHROPIC_MODEL,
        max_tokens=8192,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": _encoded(image),
                        },
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    )
    if response.stop_reason == "refusal":
        raise HTTPException(502, "Vision model declined to read this page")
    return _parse_fields(next((b.text for b in response.content if b.type == "text"), ""))


def _normalize(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalnum())


def locate_values(
    fields: list[VisionField], words: list[Word], page_w: float, page_h: float
) -> dict[str, tuple[float, float, float, float]]:
    """Recover a normalized bounding box for each value by matching it to OCR words.

    The model reads *what* the value says; OCR knows *where* it sits. Matching one onto
    the other keeps regeneration accurate without trusting the model for coordinates.
    Values OCR never saw — unreadable handwriting, for instance — simply get no box.
    """
    boxes: dict[str, tuple[float, float, float, float]] = {}
    for field in fields:
        if not field.value:
            continue
        wanted = _normalize(field.value)
        if not wanted:
            continue
        matched = [w for w in words if w.text and _normalize(w.text) in wanted]
        # A single short token can match by accident; require real coverage of the value.
        if not matched or sum(len(_normalize(w.text)) for w in matched) < len(wanted) * 0.6:
            continue
        boxes[field.label] = (
            min(w.x0 for w in matched) / page_w,
            min(w.y0 for w in matched) / page_h,
            (max(w.x1 for w in matched) - min(w.x0 for w in matched)) / page_w,
            (max(w.y1 for w in matched) - min(w.y0 for w in matched)) / page_h,
        )
    return boxes
