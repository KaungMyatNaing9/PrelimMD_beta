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
import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import NamedTuple

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont

from app.config import settings
from app.services.word_source import Word

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/chat"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Tried and rejected: qwen3-vl:4b, which the 2026 write-ups recommend for forms
# specifically (96.1 on DocVQA) and which is half the size. It answers a short question
# about a page correctly in five seconds, and returns an empty string for this module's
# prompt -- with the schema, without it, with and without options, taking 140-330s to
# return nothing each time. The long instruction is what breaks it. A better benchmark
# score does not survive contact with the prompt you actually send.
DEFAULT_OLLAMA_MODEL = "qwen2.5vl:7b"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_GROQ_MODEL = "qwen/qwen3.8-27b"

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

If the page contains a TABLE -- a grid where one caption heads a whole column of cells --
put it in "tables" instead of "fields". Give the column captions once, then one entry per
row. A column caption belongs to every cell beneath it.

Respond with JSON only:
{"fields": [{"label": ..., "value": ..., "handwritten": ...}],
 "tables": [{"columns": [...], "rows": [[...], [...]]}]}"""

SCHEMA = {
    "type": "object",
    "properties": {
        # A table is asked for as a grid, not as a flat list of pairs, because a flat list
        # cannot say that one caption heads six cells -- and measured on a FUNSD form with
        # 41 annotated pairs the model returned 6 fields and no table cell at all. 38.5% of
        # that split's links hang off a column header, where this pipeline scores 20.4%
        # against 70.7% on ordinary fields. The field has emitted tables as structured
        # sequences since 2023 (Docling, UniTabNet) for the same reason.
        "tables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "rows": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": ["string", "null"]}},
                    },
                },
                "required": ["columns", "rows"],
            },
        },
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


# A content-addressed cache of model readings, for measurement rather than production.
#
# Every pipeline experiment this week was judged through a non-deterministic model, so a
# five-point pipeline change and eight points of model noise looked the same. That is what
# left the letterhead rule unresolved. With the cache on, a baseline and a variant are shown
# *identical* model output, so any difference between them is the pipeline change and
# nothing else -- and a repeat run costs no model calls at all.
#
# Off by default: a cached reading is a stale reading, and production must always ask the
# model. Keyed by the image's bytes, the provider, the model and the prompt, so changing any
# of those is a different key rather than a wrong hit.
_LOG = logging.getLogger(__name__)

CACHE = os.getenv("VISION_CACHE", "") == "1"
CACHE_DIR = Path(os.getenv("VISION_CACHE_DIR", "") or (Path.home() / ".cache" / "prelimmd-vision"))


def _cache_key(image: Path, provider: str) -> str:
    material = hashlib.sha256(image.read_bytes()).hexdigest()
    model = settings.vision_model or ""
    recipe = f"{provider}|{model}|{TILE_GRID}|{TABLES}|{hashlib.sha256(PROMPT.encode()).hexdigest()[:16]}"
    return hashlib.sha256(f"{material}|{recipe}".encode()).hexdigest()


def _cache_read(image: Path, provider: str) -> dict | None:
    if not CACHE:
        return None
    key = _cache_key(image, provider)
    path = CACHE_DIR / f"{key}.json"
    try:
        found = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        _LOG.warning("vision cache MISS %s %s", key[:12], image.name)
        return None
    _LOG.warning("vision cache hit  %s %s", key[:12], image.name)
    return found


def _cache_write(image: Path, provider: str, raw: dict) -> None:
    if not CACHE or not raw:
        return
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / f"{_cache_key(image, provider)}.json").write_text(json.dumps(raw))
    except OSError:
        # A cache that cannot be written is a slow run, never a failed one.
        pass


def _fields_from(raw: dict) -> list[VisionField]:
    """The model's reply as fields, including a table's cells when those are asked for."""
    fields = [
        VisionField(
            label=str(f.get("label", "")).strip(),
            value=(str(f["value"]).strip() or None) if f.get("value") is not None else None,
            handwritten=bool(f.get("handwritten")),
        )
        for f in raw.get("fields", [])
        if str(f.get("label", "")).strip()
    ]
    if TABLES:
        fields += _cells_as_fields(raw.get("tables") or [])
    return fields


def extract(image: Path, expect_answers: bool = False) -> list[VisionField]:
    """Read label/value pairs off a form image using the configured provider.

    ``expect_answers`` says the caller has independent evidence that this page is filled
    in -- geometry found values on it. It is what separates the two ways an empty field
    list can arise: a page that genuinely has no answers, and a page that defeated the
    model. Without it, re-reading every empty page in pieces would charge five calls for
    each blank page of a multi-page packet, which is most of them.
    """
    provider = settings.vision_provider
    if provider in ("", "disabled"):
        raise HTTPException(503, "Vision extraction is disabled; set VISION_PROVIDER")
    if provider == "ollama":
        call = _call_ollama
    elif provider == "anthropic":
        call = _call_anthropic
    elif provider == "groq":
        call = _with_local_fallback
    else:
        raise HTTPException(503, f"Unknown VISION_PROVIDER {provider!r}")

    cached = _cache_read(image, provider)
    if cached is not None:
        if cached.get("__failed__"):
            # A page that defeated the model is replayed as having defeated it. Caching only
            # successes left every failing page calling the model on each run -- and each
            # failure also pays for a tiling retry -- which is what kept a "warm" TEST5 run
            # at six minutes when the successes cost nothing.
            raise HTTPException(502, str(cached.get("detail") or "Vision read failed"))
        return _fields_from(cached)

    try:
        raw = _read_whole(image, call, expect_answers)
    except HTTPException as exc:
        _cache_write(image, provider, {"__failed__": True, "detail": exc.detail})
        raise

    _cache_write(image, provider, raw)
    fields = _fields_from(raw)
    return _finish(raw, fields, image)


def _read_whole(image: Path, call, expect_answers: bool) -> dict:
    """The model's reply for a whole page, falling back to its parts when that fails."""
    if TILE_GRID > 1:
        # EXPERIMENT: read every page in enlarged pieces, not only the ones that failed.
        # A FUNSD scan is 1000px wide and can hold seventy-seven question/answer pairs, so
        # its text is tiny; it never trips the downscale threshold and the size-based
        # tiling divides 1000 by 1600 and returns one piece. Whatever the model does not
        # return from that single look is simply lost, and arrival -- not binding -- is
        # where 40-45% of FUNSD's answers go.
        #
        # Note this differs from the fallback below, whose pieces are crops at the page's
        # own resolution: those were measured as no better, and the reason given was lost
        # context. These are *enlarged*, which trades the same context for more pixels per
        # character, so the earlier result does not settle it.
        pieced = _read_in_pieces(image, call, grid=TILE_GRID)
        if pieced.get("fields"):
            raw = pieced
        else:
            raw = call(image)
    else:
        try:
            raw = call(image)
        except HTTPException:
        # The page defeated the model as a whole. Ask about its parts before giving up --
        # see ``_read_in_pieces``. Only a failed read gets here, so a page that answered
        # normally is never re-read and never costs a second call.
            raw = _read_in_pieces(image, call)
            if not raw:
                raise
        else:
        # An empty list is the other way a read fails, and it does not raise. The prompt
        # is explicit that an empty list means the page is completely unfilled, so a form
        # that came back with nothing has either no answers on it or defeated the model.
        # The densest of five photographed forms returned exactly this, every time, and
        # scored 0 of its 13 fields while four comparable forms scored well; split in
        # four, the same model read 9 of them.
            if expect_answers and not raw.get("fields"):
                raw = _read_in_pieces(image, call) or raw
    return raw


def _finish(raw: dict, fields: list[VisionField], image: Path) -> list[VisionField]:
    """Hand back the reading, refusing to let a truncated one pass as complete."""
    if raw.get("truncated"):
        raise VisionTruncated(
            f"Vision model output was cut off after {len(fields)} fields; the page has "
            "more. Raise num_predict or split the page.",
            fields,
        )
    return fields


# Beyond roughly this, a local model spends much longer per page for no accuracy gain —
# it downsamples internally anyway. Form text stays legible well below it.
MAX_EDGE_PIXELS = 1600

# EXPERIMENT (2026-09-12): force every page into a grid of enlarged pieces. 0 or 1 keeps
# the shipped behaviour, where pieces are only asked for after the whole page has failed.
TILE_GRID = int(os.getenv("VISION_TILE_GRID", "0") or 0)

# Whether a table's cells become fields. On FUNSD this is worth +10.7 points (41.5% ->
# 52.2%, table cells 20.4% -> 39.8%), but FUNSD is half tabular and intake forms are not,
# so it stays off until it is shown to help clinic forms -- medication lists and
# family-history grids are the cases that would.
TABLES = os.getenv("VISION_TABLES", "") == "1"


def _encoded(image: Path) -> str:
    """Base64 the page, shrinking an oversized scan first."""
    from io import BytesIO

    from PIL import Image

    page: Image.Image = Image.open(image)
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
    except TimeoutError as exc:
        # A socket timeout is not a URLError, so it used to escape this function as a bare
        # TimeoutError -- past every caller that handles a failed read, including the
        # fallback to reading the page in pieces. The densest of five photographed forms
        # timed out on every run and scored 0 of its 13 fields, silently, because nothing
        # recognised that as a failure it could do something about.
        raise HTTPException(
            504,
            f"The local model did not answer within {settings.vision_timeout_seconds}s. "
            "A dense page may need longer, a smaller model, or reading in pieces.",
        ) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(
            503,
            "Cannot reach Ollama at localhost:11434. Start it with `ollama serve` and "
            f"pull a vision model (`ollama pull {DEFAULT_OLLAMA_MODEL}`).",
        ) from exc

    return _parse_fields(body.get("message", {}).get("content", ""))


# A page is split this finely when reading it whole fails: enough overlap that a value
# straddling a seam is whole inside at least one piece.
TILE_OVERLAP = 0.12


def _tiles(image: Path, into: Path, grid: int = 0) -> list[Path]:
    """Cut a page into pieces that each already fit inside ``MAX_EDGE_PIXELS``.

    Sized so no piece is downsampled on the way out, which is the point: the whole page
    has to shrink to fit, a piece does not.

    ``grid`` forces a division the page's size alone would not ask for, and each piece is
    then *enlarged* to fill ``MAX_EDGE_PIXELS``. That is for a page that is small in pixels
    and dense in fields -- a FUNSD scan is 1000px wide and can carry seventy-seven
    question/answer pairs, so its text is tiny, the page never trips the downscale
    threshold, and the existing tiling divides 1000 by 1600 and returns a single piece.
    The model is asked to read the whole dense page at once, every time, and what it does
    not return is simply lost.
    """
    from PIL import Image

    with Image.open(image) as im:
        page = im.convert("L")
        width, height = page.size
        if grid > 1:
            across = down = grid
        else:
            across = max(1, -(-width // MAX_EDGE_PIXELS))
            down = max(1, -(-height // MAX_EDGE_PIXELS))
        if across == 1 and down == 1:
            return []
        step_x, step_y = width / across, height / down
        made = []
        for row in range(down):
            for col in range(across):
                piece = page.crop((
                    max(0, int(col * step_x - step_x * TILE_OVERLAP)),
                    max(0, int(row * step_y - step_y * TILE_OVERLAP)),
                    min(width, int((col + 1) * step_x + step_x * TILE_OVERLAP)),
                    min(height, int((row + 1) * step_y + step_y * TILE_OVERLAP)),
                ))
                if grid > 1 and max(piece.size) < MAX_EDGE_PIXELS:
                    # Enlarge, because the point of forcing the grid is to put more pixels
                    # on each character than the whole page could afford.
                    factor = MAX_EDGE_PIXELS / max(piece.size)
                    piece = piece.resize(
                        (round(piece.width * factor), round(piece.height * factor)),
                        Image.LANCZOS,
                    )
                out = into / f"{image.stem}_r{row}c{col}.png"
                piece.save(out)
                made.append(out)
    return made


def _read_in_pieces(image: Path, call, grid: int = 0) -> dict:
    """Ask about parts of a page when asking about the whole of it failed.

    A dense form can defeat a small model outright: on one photographed intake form the
    reply came back as unparseable JSON every time, and the page scored 0 of its 13
    fields while four comparable forms scored well. Split into four, the same model and
    the same prompt read 9 of the 13.

    So this is a robustness fallback, not an accuracy trick -- measured head to head on
    pages the model *can* answer about whole, tiling is no better and sometimes slightly
    worse, because a piece has lost the context of the page around it. It is used only
    after the whole page has already failed.

    A piece that fails in turn is skipped rather than fatal: three quarters of a form is
    worth more than none of it. If every piece fails, the original failure stands.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        pieces = _tiles(image, Path(folder), grid=grid)
        if not pieces:
            return {}
        merged: list[dict] = []
        seen: set[str] = set()
        failures = 0
        for piece in pieces:
            try:
                part = call(piece)
            except Exception:  # noqa: BLE001 - a bad piece must not lose the good ones
                failures += 1
                continue
            for entry in part.get("fields", []):
                label = str(entry.get("label", "")).strip()
                # Overlap means a field can appear in two pieces; the first wins.
                if label and _normalize(label) not in seen:
                    seen.add(_normalize(label))
                    merged.append(entry)
        if failures == len(pieces):
            return {}
        return {"fields": merged}


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


# Groq's free tier meters *input* tokens per minute, and a page image is most of one
# request's cost. Measured on this account: a 7000 ITPM ceiling against roughly 2500
# tokens per page, so about two and a half pages a minute. Reading several forms in a row
# -- which is exactly what a review session or an evaluation run does -- goes straight
# through it.
GROQ_TOKENS_PER_MINUTE = 7000
GROQ_TOKENS_PER_PAGE = 2500
_recent_calls: list[float] = []


def _wait_for_budget() -> None:
    """Hold a request until the last minute has room for it.

    Waiting on purpose beats being refused: a 429 that arrives mid-run is retried once
    and then gives up, and the caller's blanket except-and-warn turns that into a page
    silently read without the model at all. Pacing makes the cost visible as time rather
    than as fields that quietly went missing.
    """
    per_minute = max(1, GROQ_TOKENS_PER_MINUTE // GROQ_TOKENS_PER_PAGE)
    while True:
        cutoff = time.monotonic() - 60
        _recent_calls[:] = [t for t in _recent_calls if t > cutoff]
        if len(_recent_calls) < per_minute:
            _recent_calls.append(time.monotonic())
            return
        time.sleep(max(0.5, _recent_calls[0] + 60 - time.monotonic()))


def _with_local_fallback(image: Path) -> dict:
    """Read through Groq, and through the local model when Groq will not answer.

    The hosted model is faster and reads better, but it is metered: a rolling budget of
    roughly seventy-five pages a day on the free tier, and a per-minute ceiling on top of
    that. Running out is not an error in the page -- the page is fine, the allowance is
    gone -- so falling back to the model on this machine keeps a review session working
    rather than dropping every field the hosted reader would have found.

    Only availability failures fall back. A page the hosted model genuinely could not
    parse should be reported, not quietly re-asked of a weaker reader whose answer would
    then look authoritative.
    """
    try:
        return _call_groq(image)
    except HTTPException as exc:
        if exc.status_code not in (429, 502, 503):
            raise
        if not _ollama_reachable():
            raise
        logger.warning(
            "Groq unavailable (%s); falling back to the local model for this page",
            str(exc.detail)[:120],
        )
        return _call_ollama(image)


def _ollama_reachable() -> bool:
    """Whether a local model is actually there, before promising to use one."""
    try:
        request = urllib.request.Request(OLLAMA_URL.replace("/api/chat", "/api/tags"))
        with urllib.request.urlopen(request, timeout=2):
            return True
    except Exception:  # noqa: BLE001 - any failure here means "no local model"
        return False


def _call_groq(image: Path) -> dict:
    """Llama 4 Scout on Groq. Free tier, no card, no training on inputs even unpaid --
    the strongest privacy terms of the three hosted options measured. Page data still
    leaves the machine, same as any hosted API; that is what distinguishes it from
    the Ollama path, not this call itself.

    Scout does not support Groq's strict JSON-schema output (only a few OpenAI/Qwen
    models there do), so this asks for basic JSON-object mode and leans on the same
    schema spelled out in ``PROMPT`` plus ``_parse_fields``'s salvage path -- exactly
    the safety net the Ollama call already needed for the same reason.
    """
    if not settings.vision_api_key:
        raise HTTPException(503, "VISION_API_KEY is required for VISION_PROVIDER=groq")
    suffix = image.suffix.lower()
    media_type = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
    payload = {
        "model": settings.vision_model or DEFAULT_GROQ_MODEL,
        "temperature": 0,
        # Groq's free tier enforces a per-minute *output* token ceiling far below what a
        # dense form needs in one call (measured: 1000 OTPM on this account, against an
        # 8192 request that a form with 40+ fields can legitimately need). Asking for
        # more than the ceiling is rejected outright before generation starts, so this
        # has to sit under it; a form that still doesn't fit comes back ``truncated``
        # via the same salvage path already built for Ollama's output cap.
        "max_completion_tokens": 900,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{_encoded(image)}"},
                    },
                ],
            }
        ],
    }
    _wait_for_budget()
    request = urllib.request.Request(
        GROQ_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.vision_api_key}",
            # Groq sits behind Cloudflare, which blocks urllib's default User-Agent
            # ("Python-urllib/3.x") outright with a 403 before the request ever reaches
            # Groq's own API -- this has nothing to do with the key or the payload.
            "User-Agent": "PrelimMD/1.0",
        },
    )
    # The free tier's per-minute token ceiling is tight enough that two or three page
    # reads in quick succession exhaust it (measured: 7000 ITPM, ~2500 tokens per page),
    # and a 429 here was previously swallowed by the caller's blanket except-and-warn --
    # a field the model would have read correctly just silently went missing, with
    # nothing in the response saying why. One retry after the wait Groq itself reports
    # covers the common case without hiding a persistent failure.
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=settings.vision_timeout_seconds) as r:
                body = json.loads(r.read())
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            if exc.code == 429:
                # Two different ceilings wear the same status code and mean very
                # different things. A per-minute limit is worth waiting out in-process; a
                # per-day one is not -- nothing this request can do will clear it, and a
                # caller that keeps retrying just burns the retry budget to reach the same
                # answer several seconds later.
                if "per day" in detail or "(TPD)" in detail:
                    raise HTTPException(
                        503,
                        "Groq's free daily token allowance is spent (about 75 pages). It "
                        "resets on its own; until then set VISION_PROVIDER=disabled to "
                        "read geometrically, or use a paid tier.",
                    ) from exc
                if attempt == 0:
                    wait = re.search(r"try again in ([\d.]+)s", detail)
                    time.sleep(float(wait.group(1)) + 0.5 if wait else 15)
                    continue
            raise HTTPException(502, f"Groq vision request failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise HTTPException(503, "Cannot reach Groq's API") from exc

    return _parse_fields(body["choices"][0]["message"]["content"])


def _best_cluster(candidates: list[Word], wanted: str) -> list[Word] | None:
    """The tightest group of nearby candidate words that together cover ``wanted``.

    Same fixed-anchor grouping as ``word_source.group_lines_tolerant``: a cluster's band
    is set by the word that opens it and never slides, so a chain of near-misses cannot
    drift across the page and pull unrelated words in.
    """
    ordered = sorted(candidates, key=lambda w: (w.y0, w.x0))
    heights = sorted(w.y1 - w.y0 for w in ordered)
    band = max(1.0, heights[len(heights) // 2] * 1.5)

    clusters: list[list[Word]] = []
    anchors: list[float] = []
    for word in ordered:
        centre = (word.y0 + word.y1) / 2
        for i, anchor in enumerate(anchors):
            if abs(centre - anchor) <= band:
                clusters[i].append(word)
                break
        else:
            clusters.append([word])
            anchors.append(centre)

    threshold = len(wanted) * 0.6
    best: list[Word] | None = None
    best_coverage = 0
    for cluster in clusters:
        coverage = sum(len(_normalize(w.text)) for w in cluster)
        if coverage >= threshold and coverage > best_coverage:
            best, best_coverage = cluster, coverage
    return best


def _normalize(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalnum())


def _cells_as_fields(tables: list[dict]) -> list[VisionField]:
    """Flatten a table into one field per filled cell, captioned by its column.

    A column header owns every cell beneath it, so each cell becomes its own field under
    that caption -- six cells under `VOLUME` are six fields labelled `VOLUME`, not one. The
    rest of the pipeline keys readings by position rather than by caption, so repeats no
    longer collide.

    Blank cells are skipped: the contract of the read is that a listed field was answered,
    and a table's empty cells would otherwise flood the page with fields nobody filled in.
    """
    made: list[VisionField] = []
    for table in tables:
        columns = [str(c).strip() for c in (table.get("columns") or [])]
        if not columns:
            continue
        for row in table.get("rows") or []:
            if not isinstance(row, list):
                continue
            for column, cell in zip(columns, row, strict=False):
                text = "" if cell is None else str(cell).strip()
                if not column or not text:
                    continue
                made.append(VisionField(label=column, value=text, handwritten=False))
    return made


def locate_values(
    fields: list[VisionField], words: list[Word], page_w: float, page_h: float
) -> dict[int, tuple[float, float, float, float]]:
    """Recover a normalized bounding box for each value by matching it to OCR words.

    The model reads *what* the value says; OCR knows *where* it sits. Matching one onto
    the other keeps regeneration accurate without trusting the model for coordinates.
    Values OCR never saw — unreadable handwriting, for instance — get no box here; see
    ``locate_by_label`` for what happens to those.

    **Keyed by the reading's position, not by its label.** A table column repeats one
    caption down a row of cells -- `VOLUME` over six figures -- and keying by label
    collapsed all six into a single entry, so five correct readings were lost before
    fusion ever saw them. Measured on FUNSD, 38.5% of annotated links hang off a caption
    that owns more than one answer, and this pipeline scored 20.4% on those against 70.7%
    on ordinary one-caption-one-answer fields.
    """
    boxes: dict[int, tuple[float, float, float, float]] = {}
    for position, field in enumerate(fields):
        if not field.value:
            continue
        wanted = _normalize(field.value)
        if not wanted:
            continue
        # A minimum fragment length matters twice over: without it, every punctuation-only
        # token on the page (a lone "@", "#", "(") normalizes to the empty string, and
        # Python's `in` is vacuously true for "" -- every one of them "matched" any value,
        # anywhere on the page. On one real form this put "Greensboro"'s box at 77% of the
        # page's width and 90% of its height, which then triggered the *unrelated* guard
        # against inventing tick-box answers and silently dropped the field.
        candidates = [
            w for w in words if w.text and len(_normalize(w.text)) >= 2 and _normalize(w.text) in wanted
        ]
        if not candidates:
            continue
        # A single word that alone covers most of the value is corroborated on its own,
        # and is who the value actually is -- "Greensboro" needs nothing else. Combining
        # it with an unrelated word elsewhere on the page that merely shares a couple of
        # letters ("or") only enlarges the box away from where the value really sits.
        # Multiple words are combined only when none alone is enough, which is the real
        # case this exists for: OCR splits "1420 Bellefield Avenue" into several tokens
        # that individually cover a fraction of the value and only add up together.
        best = max(candidates, key=lambda w: len(_normalize(w.text)))
        matched: list[Word] | None
        if len(_normalize(best.text)) >= len(wanted) * 0.6:
            matched = [best]
        else:
            # No single token covers enough alone -- the real case this is for is OCR
            # splitting one written value into several tokens ("812", "Sycamore",
            # "Ridge", "Road"), which sit on one line together. A short fragment sharing
            # a couple of letters with the value by coincidence sits wherever it happens
            # to sit on the page, so clustering by proximity separates the two: the
            # genuine tokens cluster together and clear the coverage bar as a group; a
            # stray "or" or "Mo" three lines away forms its own tiny cluster and does not.
            matched = _best_cluster(candidates, wanted)
            if matched is None:
                continue
        boxes[position] = (
            min(w.x0 for w in matched) / page_w,
            min(w.y0 for w in matched) / page_h,
            (max(w.x1 for w in matched) - min(w.x0 for w in matched)) / page_w,
            (max(w.y1 for w in matched) - min(w.y0 for w in matched)) / page_h,
        )
    return boxes


# How far right of its label an answer space is assumed to run when nothing bounds it.
LABEL_ANSWER_WIDTH = 0.95


def locate_by_label(
    fields: list[VisionField], words: list[Word], page_w: float, page_h: float
) -> dict[int, tuple[float, float, float, float]]:
    """Where each value *probably* sits, judged from its label rather than its text.

    ``locate_values`` matches a model reading onto the OCR words that spell it, which is
    exact and is what regeneration wants. On handwriting it finds nothing at all: OCR
    returns "Vv \\ \\ ANAC VA" where a person wrote "Villanueva", so the correct model
    reading matches no word on the page and the caller drops the field. Measured on five
    photographed forms the model alone read 51% of values and the pipeline delivered
    16.3% -- two thirds of what it got right was discarded for want of coordinates.

    But a handwritten form is only handwritten in its *answers*. The captions are printed,
    and OCR reads them as well as on any other form. So when the value cannot be found,
    the label can, and the answer space is what follows it: from the end of the label to
    the start of the next label on that line, or to the right edge of the page.

    This is an estimate and is marked as one by its caller. It cannot serve regeneration,
    which places text from template coordinates anyway -- it exists so a value that was
    read correctly is shown to a human instead of being thrown away.
    """
    from app.services.field_layout import split_runs
    from app.services.word_source import group_lines_tolerant

    lines = group_lines_tolerant(words)
    boxes: dict[int, tuple[float, float, float, float]] = {}
    for position, field in enumerate(fields):
        if not field.value:
            continue
        wanted = _normalize(field.label)
        if len(wanted) < 3:
            continue
        best: tuple[float, list[Word], list[list[Word]]] | None = None
        for line in lines:
            for run in split_runs(line):
                text = _normalize(" ".join(w.text for w in run))
                if not text:
                    continue
                # The label as printed and the label as the model repeated it rarely
                # match character for character, so score by containment either way.
                if text in wanted or wanted in text:
                    score = min(len(text), len(wanted)) / max(len(text), len(wanted))
                    if best is None or score > best[0]:
                        best = (score, run, split_runs(line))
        if best is None or best[0] < 0.5:
            continue
        _, run, runs = best
        right = max(w.x1 for w in run)
        after = [r for r in runs if min(w.x0 for w in r) > right]
        limit = min(w.x0 for w in after[0]) if after else page_w * LABEL_ANSWER_WIDTH
        if limit <= right:
            continue
        top = min(w.y0 for w in run)
        bottom = max(w.y1 for w in run)
        boxes[position] = (
            right / page_w,
            top / page_h,
            (limit - right) / page_w,
            (bottom - top) / page_h,
        )
    return boxes


TRANSCRIBE_PROMPT = (
    "This image stacks {n} numbered strips cut from one filled-in medical form; each strip "
    "is one field. For every strip, transcribe exactly what a person wrote or typed in it, "
    "character for character, including digits and punctuation. Ignore printed labels, "
    "box outlines and rule lines. Answer with exactly {n} lines, one per strip, in the "
    "form `<number>: <text>`, and write `<number>: EMPTY` where nothing was written."
)
# One image costs the local model the same whatever its size -- Ollama resizes every
# image to about 1,100 tokens, measured 24 s for a 200x40 crop and for a full page alike
# -- so a page's slots are read together, as strips on one sheet, not one call each.
# A strip is enlarged so one line of the page's text stands STRIP_HEIGHT tall; a region
# several lines tall keeps that scale, so its writing stays as legible as a single line's.
STRIP_HEIGHT = 64
STRIP_MAX_HEIGHT = 4 * STRIP_HEIGHT
SHEET_WIDTH = 1100
SHEET_HEIGHT = 1150
SHEET_GUTTER = 72
STRIP_GAP = 10
_STRIP_LINE = re.compile(r"^\s*[`*]*\s*(\d+)\s*[:.)\-\u2013]\s*[`*]*\s*(.*?)\s*[`*]*\s*$")


def slot_vision_available() -> bool:
    return settings.slot_vision == "ollama" and _ollama_reachable()


def transcribe_strips(
    image: Path, regions: list[tuple[float, float, float, float]], line_height: float
) -> list[str]:
    """What the local vision model reads in each region of a page image.

    Every region is cut out, enlarged so a line of the page's text is one height, and
    stacked with its number on a sheet; the model transcribes each sheet line by line.
    The geometry already says where each answer lives, so the model is asked only to
    read, never to find. Cached by the sheet's bytes, the prompt and the model, so a
    measurement replays and a re-upload of the same photograph costs nothing. A region
    the model did not answer reads as "".
    """
    page = Image.open(image).convert("RGB")
    strips = [_strip(page, region, line_height) for region in regions]
    out: list[str] = [""] * len(regions)
    first = 0
    while first < len(strips):
        last, height = first, STRIP_GAP
        while last < len(strips) and (last == first or height + strips[last].height + STRIP_GAP <= SHEET_HEIGHT):
            height += strips[last].height + STRIP_GAP
            last += 1
        batch = strips[first:last]
        for offset, text in _read_sheet(_sheet(batch), len(batch)).items():
            if 0 <= offset < len(batch):
                out[first + offset] = text
        first = last
    return out


def _strip(page: Image.Image, region: tuple[float, float, float, float], line_height: float) -> Image.Image:
    x0, y0, x1, y1 = region
    left, top = max(0, int(x0)), max(0, int(y0))
    right, bottom = min(page.width, int(x1) + 1), min(page.height, int(y1) + 1)
    if right - left < 4 or bottom - top < 4:
        return Image.new("RGB", (4, STRIP_HEIGHT), "white")
    crop = page.crop((left, top, right, bottom))
    factor = STRIP_HEIGHT / max(line_height, 1.0, min(crop.height, line_height))
    factor = min(factor, (SHEET_WIDTH - SHEET_GUTTER) / crop.width, STRIP_MAX_HEIGHT / crop.height)
    factor = max(factor, 0.25)
    return crop.resize((max(4, int(crop.width * factor)), max(4, int(crop.height * factor))), Image.LANCZOS)


def _sheet(strips: list[Image.Image]) -> bytes:
    height = STRIP_GAP + sum(strip.height + STRIP_GAP for strip in strips)
    sheet = Image.new("RGB", (SHEET_WIDTH, height), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=30)
    top = STRIP_GAP
    for index, strip in enumerate(strips):
        draw.text((8, top + max(0, (strip.height - 30) // 2)), f"{index + 1}:", fill="black", font=font)
        sheet.paste(strip, (SHEET_GUTTER, top))
        rule = top + strip.height + STRIP_GAP // 2
        draw.line([(0, rule), (SHEET_WIDTH, rule)], fill=(160, 160, 160), width=2)
        top += strip.height + STRIP_GAP
    buffer = BytesIO()
    sheet.save(buffer, format="PNG")
    return buffer.getvalue()


def _read_sheet(sheet: bytes, count: int) -> dict[int, str]:
    prompt = TRANSCRIBE_PROMPT.format(n=count)
    model = settings.vision_model or DEFAULT_OLLAMA_MODEL
    key = hashlib.sha256(sheet + prompt.encode() + model.encode()).hexdigest()
    cached = CACHE_DIR / f"sheet-{key}.txt"
    if CACHE and cached.exists():
        return _parse_strips(cached.read_text())
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt, "images": [base64.b64encode(sheet).decode()]}
        ],
        "stream": False,
        "options": {"temperature": 0, "num_predict": 40 * count + 64},
    }
    request = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=settings.vision_timeout_seconds) as r:
        text = str(json.loads(r.read())["message"]["content"])
    if CACHE:
        # The sheet is kept beside the answer so a measurement can show what the model
        # was looking at when it read a strip wrong.
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached.with_suffix(".png").write_bytes(sheet)
        cached.write_text(text)
    return _parse_strips(text)


def _parse_strips(text: str) -> dict[int, str]:
    """``{strip offset: transcription}`` from the model's numbered lines; EMPTY is ""."""
    found: dict[int, str] = {}
    for line in text.splitlines():
        m = _STRIP_LINE.match(line)
        if not m:
            continue
        number, value = int(m.group(1)), " ".join(m.group(2).split())
        if number < 1 or number - 1 in found:
            continue
        found[number - 1] = "" if value.upper().strip("`*. ") == "EMPTY" else value
    return found
