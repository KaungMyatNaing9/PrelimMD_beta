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

ALLOWED = {"image/jpeg": "jpg", "image/png": "png", "application/pdf": "pdf"}
SIGNATURES = {"jpg": (b"\xff\xd8\xff",), "png": (b"\x89PNG\r\n\x1a\n",), "pdf": (b"%PDF-",)}


def _validate(data: bytes, content_type: str | None) -> str:
    if not data:
        raise HTTPException(422, "File is empty")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "File exceeds upload limit")
    kind = ALLOWED.get(content_type or "")
    if not kind or not any(data.startswith(s) for s in SIGNATURES[kind]):
        raise HTTPException(415, "Only valid JPG, PNG, and PDF files are accepted")
    return kind


async def save_upload(upload: UploadFile, run_dir: Path) -> tuple[Path, str]:
    data = await upload.read()
    kind = _validate(data, upload.content_type)
    source = run_dir / f"source.{kind}"
    source.write_bytes(data)
    return source, kind


def _load_first_page(source: Path, kind: str) -> Image.Image:
    if kind != "pdf":
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


def preprocess(source: Path, kind: str, run_dir: Path) -> PreprocessingResult:
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
    color_path = run_dir / "normalized_color.png"
    gray_path = run_dir / "ocr_gray.png"
    cv2.imwrite(str(color_path), normalized)
    ocr = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(
        cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY)
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
        page_index=0,
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
