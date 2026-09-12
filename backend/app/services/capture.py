import subprocess
import uuid
from pathlib import Path

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore[assignment]
import numpy as np
from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings
from app.schemas.models import PageQuality, PreprocessingResult

ALLOWED = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "application/pdf": "pdf",
    "image/heic": "heic",
    "image/heif": "heic",
}
SIGNATURES = {"jpg": (b"\xff\xd8\xff",), "png": (b"\x89PNG\r\n\x1a\n",), "pdf": (b"%PDF-",)}
# HEIC is an ISO base-media container, so its first four bytes are a box length and the
# brand sits at offset 4. These are the brands Apple writes; a still photo is `heic` or
# `heix`, a burst or edited image can arrive as `mif1`/`msf1`, and `hevc`/`hevx` appear on
# some exports. Matched at offset 4 rather than 0, which is why they are kept apart from
# the run-of-the-mill signatures above.
HEIF_BRANDS = (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypmsf1", b"ftyphevc", b"ftyphevx")


def _looks_like_heif(data: bytes) -> bool:
    return len(data) > 12 and data[4:12] in HEIF_BRANDS


def heif_ready() -> bool:
    """Whether this installation can decode HEIC at all.

    Kept optional rather than required: the decoder pulls in libheif, and a deployment
    that never sees an iPhone photo should not have to carry it. What is *not* optional is
    saying so clearly -- see ``_validate``.
    """
    try:
        import pillow_heif
    except ImportError:
        return False
    pillow_heif.register_heif_opener()
    return True


def _validate(data: bytes, content_type: str | None) -> str:
    if not data:
        raise HTTPException(422, "File is empty")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "File exceeds upload limit")
    kind = ALLOWED.get(content_type or "")
    # A browser does not always label a HEIC upload, and an unlabelled one used to be
    # refused as though the file were corrupt. HEIC is the iPhone default, so this is the
    # single most likely thing a patient photographing a form will send.
    if kind is None and _looks_like_heif(data):
        kind = "heic"
    if kind == "heic":
        if not _looks_like_heif(data):
            raise HTTPException(415, "File is labelled HEIC but is not a HEIF image")
        if not heif_ready():
            raise HTTPException(
                415,
                "This is an iPhone HEIC photo and HEIC support is not installed here. "
                "Install it with `pip install pillow-heif`, or on the iPhone set "
                "Settings > Camera > Formats to 'Most Compatible' and take the photo "
                "again, which saves JPEG.",
            )
        return kind
    if not kind or not any(data.startswith(s) for s in SIGNATURES[kind]):
        raise HTTPException(415, "Only valid JPG, PNG, HEIC, and PDF files are accepted")
    return kind


async def save_upload(upload: UploadFile, run_dir: Path, page_index: int = 0) -> tuple[Path, str]:
    """Store one uploaded page. Page 0 keeps the plain name every other reader expects."""
    data = await upload.read()
    kind = _validate(data, upload.content_type)
    stem = "source" if page_index == 0 else f"source_{page_index}"
    source = run_dir / f"{stem}.{kind}"
    source.write_bytes(data)
    return source, kind


def _load_first_page(source: Path, kind: str) -> Image.Image:
    if kind != "pdf":
        if kind == "heic":
            heif_ready()  # registers the opener; validation already proved it is there
        try:
            return ImageOps.exif_transpose(Image.open(source)).convert("RGB")
        except UnidentifiedImageError as exc:
            raise HTTPException(422, "Corrupted image") from exc
    output_prefix = source.parent / "raster"
    try:
        subprocess.run(
            [
                "pdftoppm",
                "-f",
                "1",
                "-l",
                "1",
                "-r",
                "275",
                "-png",
                str(source),
                str(output_prefix),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(
            422, "PDF rasterization requires the local Poppler pdftoppm tool"
        ) from exc
    # pdftoppm zero-pads the page number to the page count's width, so a 10+ page
    # document yields "raster-01.png" rather than "raster-1.png". Real intake packets
    # routinely run past ten pages, so match whatever it actually wrote.
    pages = sorted(output_prefix.parent.glob(f"{output_prefix.name}-*.png"))
    if not pages:
        raise HTTPException(422, "PDF produced no rasterized page")
    return Image.open(pages[0]).convert("RGB")


# The blur that estimates the page's lighting. It has to be far wider than any glyph, so
# that text averages away and only the illumination is left; a fraction of the page's own
# width keeps that true whatever resolution the photo arrived at.
ILLUMINATION_BLUR = 0.08


def _flatten_illumination(gray: np.ndarray) -> np.ndarray:
    """Even out lighting across the page before OCR reads it.

    A phone photo is rarely lit evenly: a hand or a head throws a soft shadow across part
    of the sheet. Measured on a real form with a shadow ramp over a third of its width,
    field accuracy fell from 7/8 to 1/8 and the reader found 16 regions where it had found
    27. Uniform dimming and even glare cost nothing at all -- so what defeats it is the
    *gradient*, not the darkness. That is exactly what a global threshold cannot survive
    and what dividing by the local background removes.

    The page is divided by a heavily blurred copy of itself. Blurred that wide the text
    disappears and what remains is the lighting, so the quotient is the page as it would
    have looked lit evenly. Standard flat-field correction, and it is blind to what the
    page says -- it cannot be fitted to any particular form.
    """
    radius = max(3, int(min(gray.shape) * ILLUMINATION_BLUR) | 1)
    background = cv2.GaussianBlur(gray, (radius, radius), 0)
    # Guard the divide: a genuinely black region would otherwise blow up to white.
    flattened = cv2.divide(gray, np.maximum(background, 1), scale=255)
    return np.clip(flattened, 0, 255).astype(np.uint8)


def preprocess(source: Path, kind: str, run_dir: Path, page_index: int = 0) -> PreprocessingResult:
    """Straighten and enhance one page, writing it under the name its index expects.

    Page 0 is ``normalized_color.png`` / ``ocr_gray.png``, which is what every existing
    reader, the review UI and the stored runs already look for. Later pages follow the
    naming the PDF path established -- ``page_1.png`` and so on -- so a form photographed
    a page at a time lands in exactly the same shape as the same form scanned to PDF.
    """
    if cv2 is None:
        raise HTTPException(503, "OpenCV preprocessing dependency is unavailable")
    pil = _load_first_page(source, kind)
    original = np.array(pil)[:, :, ::-1]
    original_h, original_w = original.shape[:2]
    gray = cv2.cvtColor(original, cv2.COLOR_BGR2GRAY)
    # Conservative deskew from long edge lines; no speculative right-angle rotation.
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, 100, minLineLength=max(80, original_w // 5), maxLineGap=15
    )
    angles: list[float] = []
    if lines is not None:
        for x1, y1, x2, y2 in lines[:, 0]:
            angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
            if abs(angle) < 12:
                angles.append(float(angle))
    angle = float(np.median(angles)) if angles else 0.0
    if abs(angle) > 0.2:
        matrix = cv2.getRotationMatrix2D((original_w / 2, original_h / 2), angle, 1)
        normalized = cv2.warpAffine(
            original, matrix, (original_w, original_h), borderValue=(255, 255, 255)
        )
    else:
        normalized = original
    color_path = run_dir / ("normalized_color.png" if page_index == 0 else f"page_{page_index}.png")
    gray_path = run_dir / ("ocr_gray.png" if page_index == 0 else f"ocr_gray_{page_index}.png")
    cv2.imwrite(str(color_path), normalized)
    ocr = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(
        _flatten_illumination(cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY))
    )
    cv2.imwrite(str(gray_path), ocr)
    variance = cv2.Laplacian(gray, cv2.CV_64F).var()
    contrast = float(np.std(gray) / 64)
    quality = PageQuality(
        blur_score=float(min(1, variance / 250)),
        contrast_score=min(1, contrast),
        glare_score=0,
        overall=float(min(1, (min(1, variance / 250) + min(1, contrast)) / 2)),
    )
    return PreprocessingResult(
        page_index=page_index,
        original_width=original_w,
        original_height=original_h,
        normalized_width=original_w,
        normalized_height=original_h,
        deskew_angle_degrees=-angle,
        quality=quality,
        artifacts={"normalized_color": str(color_path), "ocr_gray": str(gray_path)},
    )


def new_run_dir() -> tuple[str, Path]:
    run_id = f"run_{uuid.uuid4().hex}"
    root = settings.artifact_root.resolve()
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_id, run_dir
