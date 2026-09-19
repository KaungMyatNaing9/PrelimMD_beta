"""PDF access through permissively licensed libraries.

The project ships under Apache 2.0 and PyMuPDF is AGPL, so nothing in the backend may
import it. What the reader needs from a PDF is small and all of it has a permissive
source: characters with their fonts and positions (pdfminer.six, MIT, through pdfplumber),
text widgets and font metrics (pypdf, BSD), a rendered page (pypdfium2, Apache/BSD), and,
for regeneration, a page composed of an image and some text (reportlab, BSD).

Fidelity to what the reader was measured against matters more than any one library's
conveniences, so three of PyMuPDF's habits are reproduced deliberately:

- **Words come in content-stream order, not sorted by position.** A filling tool writes
  a value as its own text object on top of the rule it belongs to; sorted by x, the value
  and the rule's underscores interleave into "Name__O__tt_il_ie", and the reader can do
  nothing with that. In stream order the value is its own run, as PyMuPDF reports it.
- **A word's vertical extent is the font's ascent to its descent**, read from the font
  descriptor (MuPDF's built-in figures for the base fourteen), not the font size. Every
  gap and tolerance in the reader is a multiple of the median word height, and a box a
  quarter shorter would have moved every one of them.
- **Blocks are runs of consecutive lines.** A mark stamped over a checkbox is a text
  object far away in the stream, so it lands in a block of its own, which is how
  ``word_source.pdf_marks`` tells a ticked box from the glyph that draws it.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from pypdf import PdfReader
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from app.services.word_source import Word

logger = logging.getLogger(__name__)
# pdfminer and pypdf narrate every odd object in a PDF at warning level; a form that
# reads fine is not worth a log line per font.
logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("pypdf").setLevel(logging.ERROR)

# MuPDF's metrics for the fonts a PDF need not embed, as fractions of the font size.
BASE14 = {
    "Helvetica": (1.075, -0.299),
    "Helvetica-Bold": (1.07, -0.307),
    "Helvetica-Oblique": (1.07, -0.284),
    "Helvetica-BoldOblique": (1.073, -0.309),
    "Times-Roman": (1.053, -0.281),
    "Times-Bold": (1.044, -0.341),
    "Times-Italic": (0.951, -0.27),
    "Times-BoldItalic": (0.972, -0.324),
    "Courier": (0.932, -0.317),
    "Courier-Bold": (1.007, -0.393),
    "Courier-Oblique": (0.92, -0.317),
    "Courier-BoldOblique": (0.997, -0.393),
    "Symbol": (1.01, -0.293),
    "ZapfDingbats": (0.819, -0.144),
}
# PyMuPDF's short names for the base fonts, kept so callers read as before.
FONT_NAMES = {"helv": "Helvetica", "tiro": "Times-Roman", "cour": "Courier"}
DEFAULT_METRICS = (1.0, -0.25)

# Within one line, a horizontal gap wider than this share of the font size is a space
# the writer did not type; a gap wider than LINE_GAP starts a new line even on the same
# baseline. Calibrated against PyMuPDF's words on the benchmark's PDFs.
WORD_GAP = 0.14
LINE_GAP = 1.0
# A line continues the block above it when its top is within this many font sizes of
# the previous line's bottom.
BLOCK_GAP = 1.5

_SUBSET = re.compile(r"^[A-Z]{6}\+")
_CID = re.compile(r"^\(cid:\d+\)$")
# PDFium keeps global state; every call into it holds this.
_PDFIUM = threading.Lock()


@dataclass(frozen=True)
class Char:
    text: str
    font: str
    x0: float
    y0: float
    x1: float
    y1: float
    block: int
    baseline: float
    size: float


@dataclass(frozen=True)
class Rect:
    """An axis-aligned box in the page's top-left coordinates, with the few operations
    the benchmark tooling used PyMuPDF's rectangle for."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def intersects(self, other: Rect) -> bool:
        return self.x0 < other.x1 and other.x0 < self.x1 and self.y0 < other.y1 and other.y0 < self.y1

    def __and__(self, other: Rect) -> Rect:
        return Rect(
            max(self.x0, other.x0), max(self.y0, other.y0), min(self.x1, other.x1), min(self.y1, other.y1)
        )

    def get_area(self) -> float:
        return max(0.0, self.x1 - self.x0) * max(0.0, self.y1 - self.y0)


@dataclass(frozen=True)
class Widget:
    x0: float
    y0: float
    x1: float
    y1: float
    kind: str
    name: str
    value: str


@lru_cache(maxsize=8)
def _reader(path: str) -> PdfReader:
    return PdfReader(path)


def page_count(pdf: Path) -> int:
    return len(_reader(str(pdf)).pages)


def _font_dicts(resources, depth: int = 0):
    """Every font dictionary reachable from a resources dictionary: the page's own and
    those of the form XObjects drawn on it, where a filling tool's text often lives."""
    if resources is None or depth > 4:
        return
    try:
        resources = resources.get_object()
        yield from ((resources.get("/Font") or {}).values())
        for xobject in (resources.get("/XObject") or {}).values():
            xobject = xobject.get_object()
            if str(xobject.get("/Subtype")) == "/Form":
                yield from _font_dicts(xobject.get("/Resources"), depth + 1)
    except Exception:  # noqa: BLE001 - a broken resource tree is not a broken page
        return


def _metrics(page, fontname: str) -> tuple[float, float]:
    """Ascent and descent for a font on a page, as PyMuPDF reports them: the font
    descriptor's figures over a thousand, or MuPDF's own for a base font without one.

    A descent is always reported as negative. Several producers write it positive
    (Calibri and Wingdings at 250 and 205 in this corpus), and MuPDF reads that as
    below the baseline, which is the only sense it can have.
    """
    bare = _SUBSET.sub("", fontname)
    for font in _font_dicts(page.get("/Resources")):
        try:
            font = font.get_object()
            base = _SUBSET.sub("", str(font.get("/BaseFont") or "").lstrip("/"))
            if base != bare:
                continue
            descriptor = font.get("/FontDescriptor")
            if descriptor is None and font.get("/DescendantFonts"):
                descriptor = font["/DescendantFonts"][0].get_object().get("/FontDescriptor")
            if descriptor is None:
                break
            descriptor = descriptor.get_object()
            ascent, descent = descriptor.get("/Ascent"), descriptor.get("/Descent")
            if ascent is not None and descent is not None and float(ascent) != 0:
                return float(ascent) / 1000.0, -abs(float(descent)) / 1000.0
            break
        except Exception:  # noqa: BLE001 - a broken font dictionary is not a broken page
            break
    for name, metrics in BASE14.items():
        if bare == name or bare.replace(",", "-") == name:
            return metrics
    return DEFAULT_METRICS


class Page:
    """One page of a PDF: its size, characters, words, text widgets and rendering."""

    def __init__(self, pdf: Path, index: int):
        self.path = Path(pdf)
        self.index = index
        self._pypdf = _reader(str(pdf)).pages[index]
        self._plumber_doc = pdfplumber.open(str(pdf))
        self._plumber = self._plumber_doc.pages[index]
        box = self._pypdf.cropbox
        w, h = float(box.width), float(box.height)
        self.rotation = int(self._pypdf.rotation or 0) % 360
        self.width, self.height = (h, w) if self.rotation in (90, 270) else (w, h)
        self._chars: list[Char] | None = None

    def close(self) -> None:
        self._plumber_doc.close()

    # -- characters and words --------------------------------------------------------

    def chars(self) -> list[Char]:
        if self._chars is not None:
            return self._chars
        # PyMuPDF measures from the crop box's top-left corner in absolute user space.
        # pdfplumber measures from the media box's origin, so a media box that does not
        # start at zero (several benchmark forms begin at y = 7.92) shifts everything it
        # reports by that much; both offsets are undone here.
        crop, media = self._pypdf.cropbox, self._pypdf.mediabox
        top_of_page = float(crop.top) - float(media.bottom)
        left_shift = float(media.left) - float(crop.left)
        metrics: dict[str, tuple[float, float]] = {}
        out: list[Char] = []
        block = -1
        previous: Char | None = None
        line_bottom = None
        for raw in self._plumber.chars:
            text = raw.get("text") or ""
            # pdfminer writes "(cid:123)" for a glyph it cannot map to a character; MuPDF
            # writes nothing readable there either, and one form with such a font grew
            # 164 junk fields out of the markers.
            if not text or _CID.match(text):
                continue
            font = str(raw.get("fontname") or "")
            if font not in metrics:
                metrics[font] = _metrics(self._pypdf, font)
            ascent, descent = metrics[font]
            size = float(raw.get("size") or 0.0)
            matrix = raw.get("matrix") or (1, 0, 0, 1, raw.get("x0", 0), 0)
            if self.rotation == 0 and raw.get("upright", True):
                baseline = top_of_page - float(matrix[5])
            else:
                # A rotated or sideways glyph: pdfminer's own box is the best there is.
                baseline = float(raw["bottom"]) + descent * size
            x0, x1 = float(raw["x0"]) + left_shift, float(raw["x1"]) + left_shift
            y0, y1 = baseline - ascent * size, baseline - descent * size
            # MuPDF reports nothing drawn outside the page box; pdfminer reports it all.
            # Text in a trimmed margin put a word's box past the page's edge and the
            # reader's normalised boxes past 1.0. What straddles the edge is clipped.
            if (x0 + x1) / 2 < 0 or (x0 + x1) / 2 > self.width or (y0 + y1) / 2 < 0 or (y0 + y1) / 2 > self.height:
                continue
            x0, x1 = max(0.0, x0), min(self.width, x1)
            y0, y1 = max(0.0, y0), min(self.height, y1)
            if x1 <= x0 or y1 <= y0:
                continue
            new_line = (
                previous is None
                or abs(baseline - previous.baseline) > 0.5 * max(size, 1e-6)
                or x0 < previous.x1 - size
                or x0 - previous.x1 > LINE_GAP * size
            )
            if new_line:
                continues_block = (
                    previous is not None
                    and line_bottom is not None
                    and 0 <= y0 - line_bottom <= BLOCK_GAP * size
                )
                if not continues_block:
                    block += 1
                line_bottom = y1
            else:
                line_bottom = max(line_bottom or y1, y1)
            char = Char(text, font, x0, y0, x1, y1, block, baseline, size)
            out.append(char)
            previous = char
        self._chars = out
        return out

    def words(self) -> list[Word]:
        """Words as PyMuPDF's ``get_text("words")`` gives them: stream order, split at
        whitespace and at untyped gaps, one block number per run of lines."""
        words: list[Word] = []
        current: list[Char] = []

        def flush() -> None:
            if current:
                words.append(
                    Word(
                        min(c.x0 for c in current),
                        min(c.y0 for c in current),
                        max(c.x1 for c in current),
                        max(c.y1 for c in current),
                        "".join(c.text for c in current),
                        current[0].block,
                        1.0,
                    )
                )
                current.clear()

        previous: Char | None = None
        for char in self.chars():
            if char.text.isspace():
                flush()
                previous = char
                continue
            if previous is not None and current and (
                char.block != previous.block
                or abs(char.baseline - previous.baseline) > 0.5 * max(char.size, 1e-6)
                or char.x0 - previous.x1 > WORD_GAP * char.size
                or char.x0 < previous.x1 - char.size
            ):
                flush()
            current.append(char)
            previous = char
        flush()
        return words

    def search(self, needle: str) -> list[Rect]:
        """Where ``needle`` occurs on the page, as boxes, as PyMuPDF's ``search_for`` gave
        them: case-insensitive, whitespace between words matched loosely, one box per
        occurrence spanning the matched characters."""
        target = " ".join(needle.lower().split())
        if not target:
            return []
        hits: list[Rect] = []
        lines: list[list[Char]] = []
        previous: Char | None = None
        for char in self.chars():
            if previous is None or char.block != previous.block or abs(char.baseline - previous.baseline) > 0.5 * max(char.size, 1e-6):
                lines.append([])
            lines[-1].append(char)
            previous = char
        for line in lines:
            text = ""
            spans: list[tuple[int, int, Char | None]] = []
            prior: Char | None = None
            for char in line:
                if char.text.isspace():
                    if text and not text.endswith(" "):
                        text += " "
                        spans.append((len(text) - 1, len(text), None))
                    prior = char
                    continue
                if prior is not None and not prior.text.isspace() and char.x0 - prior.x1 > WORD_GAP * char.size and text and not text.endswith(" "):
                    text += " "
                    spans.append((len(text) - 1, len(text), None))
                start = len(text)
                text += char.text.lower()
                spans.append((start, len(text), char))
                prior = char
            at = text.find(target)
            while at >= 0:
                matched = [c for s0, s1, c in spans if c is not None and s0 < at + len(target) and s1 > at]
                if matched:
                    hits.append(
                        Rect(min(c.x0 for c in matched), min(c.y0 for c in matched), max(c.x1 for c in matched), max(c.y1 for c in matched))
                    )
                at = text.find(target, at + 1)
        return hits

    # -- widgets -----------------------------------------------------------------------

    def widgets(self) -> list[Widget]:
        """Form widgets with their rectangles in the page's top-left coordinates."""
        box = self._pypdf.cropbox
        left, top = float(box.left), float(box.top)
        out: list[Widget] = []
        for annotation in self._pypdf.get("/Annots") or []:
            try:
                annotation = annotation.get_object()
                if str(annotation.get("/Subtype")) != "/Widget":
                    continue
                kind, name, value = annotation.get("/FT"), annotation.get("/T"), annotation.get("/V")
                parent = annotation.get("/Parent")
                while parent is not None and (kind is None or name is None):
                    parent = parent.get_object()
                    kind = kind if kind is not None else parent.get("/FT")
                    name = name if name is not None else parent.get("/T")
                    value = value if value is not None else parent.get("/V")
                    parent = parent.get("/Parent")
                rect = [float(v) for v in annotation["/Rect"]]
            except Exception as exc:  # noqa: BLE001 - one malformed annotation, not a broken page
                logger.debug("skipping a malformed widget annotation: %s", exc)
                continue
            x0, x1 = sorted((rect[0] - left, rect[2] - left))
            y0, y1 = sorted((top - rect[3], top - rect[1]))
            if self.rotation == 90:
                x0, y0, x1, y1 = self.width - y1, x0, self.width - y0, x1
            elif self.rotation == 180:
                x0, y0, x1, y1 = self.width - x1, self.height - y1, self.width - x0, self.height - y0
            elif self.rotation == 270:
                x0, y0, x1, y1 = y0, self.height - x1, y1, self.height - x0
            kinds = {"/Tx": "Text", "/Btn": "CheckBox", "/Ch": "Choice", "/Sig": "Signature"}
            out.append(
                Widget(x0, y0, x1, y1, kinds.get(str(kind), str(kind or "")), str(name or ""), str(value or ""))
            )
        return out

    # -- rendering ---------------------------------------------------------------------

    def render(self, scale: float, out_path: Path) -> None:
        """Render the page at ``scale`` times 72 dpi to a PNG, form fields drawn.

        PDFium is not thread-safe, and the API analyses uploads on a thread pool: two
        renders at once crashed the server with a segmentation fault (2026-09-16), taking
        every request in flight with it. One render at a time.
        """
        with _PDFIUM:
            document = pdfium.PdfDocument(str(self.path))
            try:
                document.init_forms()
                image = document[self.index].render(scale=scale, may_draw_forms=True).to_pil()
            finally:
                document.close()
        image.convert("RGB").save(out_path)


def open_page(pdf: Path, index: int = 0) -> Page:
    return Page(pdf, index)


# -- fonts -----------------------------------------------------------------------------


def _font(fontname: str) -> str:
    return FONT_NAMES.get(fontname, fontname)


def text_length(text: str, fontname: str = "helv", fontsize: float = 1.0) -> float:
    """Width of ``text`` set in a base font, as PyMuPDF's ``get_text_length`` measured it."""
    return float(pdfmetrics.stringWidth(text, _font(fontname), fontsize))


def font_descender(fontname: str = "helv") -> float:
    """The base font's descent as a positive fraction of the size."""
    return abs(BASE14.get(_font(fontname), DEFAULT_METRICS)[1])


def font_ascender(fontname: str = "helv") -> float:
    return BASE14.get(_font(fontname), DEFAULT_METRICS)[0]


# -- regeneration ----------------------------------------------------------------------


@dataclass(frozen=True)
class Text:
    x: float
    baseline: float
    text: str
    size: float


@dataclass(frozen=True)
class TextBox:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    size: float


@dataclass(frozen=True)
class PageDraw:
    """One page of a composed PDF: the image behind it and what is drawn on top."""

    image: Path
    width: float
    height: float
    texts: list[Text]
    boxes: list[TextBox]


def compose(
    image: Path,
    width: float,
    height: float,
    texts: list[Text],
    boxes: list[TextBox],
    fontname: str = "helv",
    color: tuple[float, float, float] = (0.05, 0.05, 0.15),
) -> bytes:
    """A one-page PDF of ``image`` filling the page with text drawn over it."""
    return compose_pages([PageDraw(image, width, height, texts, boxes)], fontname, color)


def compose_pages(
    pages: Sequence[PageDraw],
    fontname: str = "helv",
    color: tuple[float, float, float] = (0.05, 0.05, 0.15),
) -> bytes:
    """A PDF of every page given, each its own image with text drawn over it.

    Coordinates are the page's top-left system, as everywhere else in the reader; a
    ``Text`` is placed by its baseline, a ``TextBox`` is filled from its top edge, its
    lines wrapped to its width, as PyMuPDF's ``insert_text`` and ``insert_textbox`` did.
    Pages need not be the same size: a packet can mix letter and legal.
    """
    font = _font(fontname)
    buffer = BytesIO()
    first = pages[0] if pages else None
    canvas_ = canvas.Canvas(buffer, pagesize=(first.width, first.height) if first else (612, 792))
    ascent, descent = BASE14.get(font, DEFAULT_METRICS)
    for page in pages:
        canvas_.setPageSize((page.width, page.height))
        canvas_.drawImage(ImageReader(str(page.image)), 0, 0, width=page.width, height=page.height)
        canvas_.setFillColorRGB(*color)
        for item in page.texts:
            canvas_.setFont(font, item.size)
            canvas_.drawString(item.x, page.height - item.baseline, item.text)
        for box in page.boxes:
            canvas_.setFont(font, box.size)
            line_height = box.size * (ascent - descent)
            baseline = box.y0 + ascent * box.size
            for line in _wrapped(box.text, font, box.size, box.x1 - box.x0):
                if baseline > box.y1 + 0.01:
                    break
                canvas_.drawString(box.x0, page.height - baseline, line)
                baseline += line_height
        canvas_.showPage()
    canvas_.save()
    return buffer.getvalue()


def _wrapped(text: str, font: str, size: float, width: float) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}".strip()
            if current and pdfmetrics.stringWidth(candidate, font, size) > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines
