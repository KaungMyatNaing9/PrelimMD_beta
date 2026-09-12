import pytest
from pydantic import ValidationError

from app.schemas.models import BoundingBox, ExtractedField, ExtractionSource, FieldType


def test_bbox_must_remain_inside_page():
    with pytest.raises(ValidationError):
        BoundingBox(x=0.9, y=0.1, width=0.2, height=0.1)


def test_confidence_bounds_are_enforced():
    with pytest.raises(ValidationError):
        ExtractedField(
            field_id="x",
            label="X",
            field_type=FieldType.DATE,
            bbox=BoundingBox(x=0, y=0, width=0.1, height=0.1),
            confidence=1.1,
            source=ExtractionSource.OCR,
            needs_review=False,
        )


def _heif(brand: bytes = b"ftypheic") -> bytes:
    """The first bytes of an ISO base-media file, which is what a HEIC photo is."""
    return b"\x00\x00\x00\x18" + brand + b"\x00" * 32


def test_an_iphone_photo_is_recognised_even_when_the_browser_does_not_label_it():
    """HEIC is the iPhone default, so it is the likeliest thing a patient photographing a
    form will send, and browsers do not always give it a content type."""
    from app.services.capture import _looks_like_heif, _validate

    assert _looks_like_heif(_heif())
    assert _looks_like_heif(_heif(b"ftypmif1"))
    assert not _looks_like_heif(b"\xff\xd8\xff" + b"\x00" * 32)
    assert _validate(_heif(), None) == "heic"
    assert _validate(_heif(), "image/heic") == "heic"


def test_a_file_claiming_to_be_heic_but_is_not_is_refused():
    from fastapi import HTTPException

    from app.services.capture import _validate

    with pytest.raises(HTTPException) as caught:
        _validate(b"\xff\xd8\xff" + b"\x00" * 32, "image/heic")
    assert caught.value.status_code == 415


def test_a_page_keeps_the_name_its_index_expects(tmp_path):
    """Page 0 keeps the unsuffixed names every stored run and the review UI already use;
    later pages follow the naming the PDF path established, so a form photographed a page
    at a time lands in the same shape as the same form scanned to PDF."""
    from PIL import Image

    from app.services.capture import preprocess

    for index in (0, 1):
        Image.new("RGB", (600, 800), "white").save(tmp_path / f"source_{index}.jpg")
        preprocess(tmp_path / f"source_{index}.jpg", "jpg", tmp_path, index)
    assert (tmp_path / "normalized_color.png").exists()
    assert (tmp_path / "ocr_gray.png").exists()
    assert (tmp_path / "page_1.png").exists()
    assert (tmp_path / "ocr_gray_1.png").exists()


def test_the_analyzer_reads_every_image_page_it_was_given(tmp_path):
    from PIL import Image

    from app.services.capture import preprocess
    from app.services.form_analyzer import _read_image

    for index in (0, 1):
        Image.new("RGB", (600, 800), "white").save(tmp_path / f"source_{index}.jpg")
        preprocess(tmp_path / f"source_{index}.jpg", "jpg", tmp_path, index)
    assert _read_image(tmp_path, 0) is not None
    assert _read_image(tmp_path, 1) is not None
    assert _read_image(tmp_path, 2) is None


def test_a_shadow_across_the_page_is_flattened_before_ocr():
    """A hand or head throwing a soft shadow is the commonest defect in a phone photo, and
    a global threshold cannot survive the gradient. Uniform dimming never could defeat it;
    the ramp could."""
    import numpy as np

    from app.services.capture import _flatten_illumination

    page = np.full((400, 600), 220, np.uint8)
    page[180:200, 100:500] = 40  # a line of text
    ramp = np.linspace(1.0, 0.35, 600)[None, :]
    shadowed = np.clip(page * ramp, 0, 255).astype(np.uint8)

    # Before: the lit and shadowed halves of the same paper differ enormously.
    assert int(shadowed[50, 50]) - int(shadowed[50, 550]) > 100
    flat = _flatten_illumination(shadowed)
    assert int(flat[50, 50]) - int(flat[50, 550]) < 30
    # And the text is still darker than the paper it sits on.
    assert int(flat[190, 300]) < int(flat[50, 300]) - 40


def test_a_derived_template_can_be_released_but_a_corpus_one_cannot(tmp_path, monkeypatch):
    """A registered template is matched against every later upload, so one learned to look
    at keeps changing what the extractor reads until it is removed. Surveying seven forms
    once left seventeen behind and inflated two Gate 3 figures."""
    from app.services import templates as T

    derived = tmp_path / "T50"
    derived.mkdir()
    (derived / "template.json").write_text("{}")
    monkeypatch.setattr(T, "DERIVED_ROOT", tmp_path)
    monkeypatch.setattr(T.registry, "load", lambda: None)

    assert T.release("T50") is True
    assert not derived.exists()
    # Absent, or a corpus id that was never under DERIVED_ROOT: nothing to remove.
    assert T.release("T50") is False
    assert T.release("T01") is False
