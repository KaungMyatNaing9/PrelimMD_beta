"""A local language model links captions to values; every answer is grounded or dropped.

The geometric readers bind a value to a caption by where it sits, and the stacked and
sub-captioned rows of real intake forms -- "CITY  STATE  ZIP" printed under one rule, the
value on the line above or below -- are where they fail most. A language model reading the
page's text *with positions* can resolve those, which is LMDX's finding (arXiv 2309.10952),
and the one property that makes a model admissible here at all is LMDX's grounding rule:
the model must name the line its answer came from, the answer must be an exact substring of
that line, and anything else is discarded. An ungrounded answer becomes a miss, never a
wrong value. Earlier vision work here was abandoned because models invented content and
prompt wording did not stop them; grounding is a mechanism, not a request.

It runs only over captions the page prints and no reader answered, so the readers stay
authoritative where they fire, and it is off unless ``RELINK_PROVIDER=ollama``.
"""

import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from app.config import settings
from app.services.canonical_registry import norm
from app.services.word_source import Word, group_lines_tolerant

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "qwen2.5:7b-instruct"
# One call per page, so a page with more questions than this asks about the first ones.
MAX_QUESTIONS = 10
CACHE = os.getenv("RELINK_CACHE", "") == "1"
CACHE_DIR = Path(os.getenv("RELINK_CACHE_DIR", "") or (Path.home() / ".cache" / "prelimmd-relink"))

PROMPT = """You are reading a patient's completed intake form. Below, each line of the form is
followed by its position as XX|YY: percent across the page, then percent down. A patient's
answer is usually written to the right of its caption, or on the line below it.

For each caption in QUESTIONS, find the value the patient wrote for it. Reply with JSON:
{"answers": [{"caption": "...", "value": "...", "line": "XX|YY"}]}
"value" must be copied exactly from one line, character for character, and "line" is that
line's position. Never answer with the form's own printed words. If nothing was written for
a caption, leave it out.

QUESTIONS: {questions}

FORM:
{lines}
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "caption": {"type": "string"},
                    "value": {"type": "string"},
                    "line": {"type": "string"},
                },
                "required": ["caption", "value", "line"],
            },
        }
    },
    "required": ["answers"],
}


class Line:
    def __init__(self, ident: str, words: list[Word]) -> None:
        self.ident = ident
        self.words = words
        self.text = " ".join(z.text for z in words)


def available() -> bool:
    return settings.relink_provider == "ollama" and _reachable()


def serialise(words: list[Word], w: float, h: float) -> list[Line]:
    """The page's lines, each with a position token that doubles as its identifier."""
    lines: list[Line] = []
    taken: set[str] = set()
    for row in group_lines_tolerant(words):
        cx = sum((z.x0 + z.x1) / 2 for z in row) / len(row) / w * 100
        cy = sum((z.y0 + z.y1) / 2 for z in row) / len(row) / h * 100
        ident = f"{min(99, max(0, int(cx))):02d}|{min(99, max(0, int(cy))):02d}"
        # Two lines can quantise to one cell; the identifier has to stay unique.
        suffix, unique = 0, ident
        while unique in taken:
            suffix += 1
            unique = f"{ident}{'abcdefghij'[suffix % 10]}"
        taken.add(unique)
        lines.append(Line(unique, row))
    return lines


def ground(answers: list[dict], lines: list[Line], questions: list[str]) -> dict[str, tuple[str, list[Word]]]:
    """Keep only answers that are an exact substring of the line they claim.

    Returns caption -> (value, the words carrying it). A value that spans the words only
    partially still grounds -- the value is the substring; its region is the words that
    contain it.
    """
    by_id = {line.ident: line for line in lines}
    wanted = {norm(q): q for q in questions}
    found: dict[str, tuple[str, list[Word]]] = {}
    for answer in answers:
        caption = wanted.get(norm(str(answer.get("caption", ""))))
        value = str(answer.get("value", "")).strip()
        line = by_id.get(str(answer.get("line", "")).strip())
        if not caption or not value or line is None or caption in found:
            continue
        if value not in line.text:
            continue
        # The model copies the line from its caption on -- "MARITAL STATUS Married" -- and
        # the caption is the question, not the answer. A line that is nothing but the
        # caption answered nothing.
        value = _without_caption_prefix(value, caption)
        if not value or _is_choice_row(line.text, value):
            continue
        carrying = _words_carrying(line, value)
        if carrying:
            found[caption] = (value, carrying)
    return found


_TICK_GLYPHS = "☐□☑☒○◯◻◼●©®\uf06f\uf0a8\uf0fe"


def _is_choice_row(line_text: str, value: str) -> bool:
    """Whether the model picked an option caption off a row of tick boxes.

    Measured on the first sample, 23 of 39 adopted values were "Single", "English",
    "White", "☐ Parent": the printed options of a choice, not what the patient chose. A
    tick row is read from ink by the choice stage; a value drawn from one is never an
    answer here. A row is a choice when the line carries tick glyphs, or when the value is
    a single word on a line of several capitalised words with no digits in it.
    """
    if any(glyph in line_text for glyph in _TICK_GLYPHS) or any(glyph in value for glyph in _TICK_GLYPHS):
        return True
    tokens = line_text.split()
    if len(value.split()) == 1 and len(tokens) >= 4 and not any(ch.isdigit() for ch in line_text):
        capitalised = sum(1 for t in tokens if t[:1].isupper())
        return capitalised >= 0.75 * len(tokens)
    return False


def _without_caption_prefix(value: str, caption: str) -> str:
    tokens = value.split()
    target = norm(caption)
    for k in range(len(tokens), 0, -1):
        if norm(" ".join(tokens[:k])) == target:
            return " ".join(tokens[k:]).lstrip(":-– ").strip()
    return value


def _words_carrying(line: Line, value: str) -> list[Word]:
    """The shortest run of the line's words whose joined text contains the value."""
    texts = [z.text for z in line.words]
    best: list[Word] = []
    for i in range(len(texts)):
        for j in range(i, len(texts)):
            if value in " ".join(texts[i : j + 1]):
                run = line.words[i : j + 1]
                if not best or len(run) < len(best):
                    best = run
                break
    return best


def relink(words: list[Word], w: float, h: float, questions: list[str]) -> dict[str, tuple[str, list[Word]]]:
    """Ask the model which lines answer ``questions``; return only grounded answers."""
    questions = questions[:MAX_QUESTIONS]
    lines = serialise(words, w, h)
    if not lines or not questions:
        return {}
    shown = _around(lines, questions)
    prompt = PROMPT.replace("{questions}", json.dumps(questions)).replace(
        "{lines}", "\n".join(f"{line.text}  {line.ident}" for line in shown)
    )
    body = _cached(prompt)
    if body is None:
        body = _call(prompt)
        if CACHE:
            _store(prompt, body)
    try:
        answers = json.loads(body).get("answers", [])
    except (json.JSONDecodeError, AttributeError):
        return {}
    return ground(answers if isinstance(answers, list) else [], lines, questions)


# How many lines above and below a caption's line are shown to the model. A page has
# eighty-odd lines and the model reads them at roughly two seconds each on this machine;
# an answer sits within a few lines of its caption, and the rest of the page is the rest
# of the page.
WINDOW = 3


def _around(lines: list[Line], questions: list[str]) -> list[Line]:
    """The lines near any asked caption, in reading order; the whole page if none match."""
    wanted = [norm(q) for q in questions]
    keep: set[int] = set()
    for i, line in enumerate(lines):
        text = " " + norm(line.text) + " "
        if any(f" {q} " in text for q in wanted):
            keep.update(range(max(0, i - WINDOW), min(len(lines), i + WINDOW + 1)))
    return [line for i, line in enumerate(lines) if i in keep] if keep else lines


def _call(prompt: str) -> str:
    payload = {
        "model": settings.relink_model or DEFAULT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": SCHEMA,
        "options": {"temperature": 0, "seed": 7, "num_predict": 1024, "num_ctx": 4096},
    }
    request = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.relink_timeout_seconds) as r:
            return str(json.loads(r.read())["message"]["content"])
    except (TimeoutError, urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"relinker call failed: {type(exc).__name__}: {exc}") from exc


def _reachable() -> bool:
    try:
        with urllib.request.urlopen(OLLAMA_URL.replace("/api/chat", "/api/tags"), timeout=2):
            return True
    except Exception:  # noqa: BLE001 - any failure means "no local model"
        return False


def _key(prompt: str) -> Path:
    model = settings.relink_model or DEFAULT_MODEL
    return CACHE_DIR / (hashlib.sha256(f"{model}|{prompt}".encode()).hexdigest() + ".json")


def _cached(prompt: str) -> str | None:
    if not CACHE:
        return None
    path = _key(prompt)
    return path.read_text() if path.exists() else None


def _store(prompt: str, body: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _key(prompt).write_text(body)

