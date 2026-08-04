"""Geometric field detection and the OCR line grouping it depends on."""

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.schemas.models import FieldType
from app.services.field_layout import Box, detect_boxes, ink_ratio, label_for_box, partition
from app.services.form_analyzer import normalize
from app.services.word_source import Word, group_lines_tolerant


def word(text, x0, y0, x1=None, y1=None, confidence=0.9):
    return Word(x0, y0, x1 if x1 is not None else x0 + 40, y1 if y1 is not None else y0 + 20,
                text, 0, confidence)


@pytest.fixture
def boxed_page(tmp_path):
    """A page with two labelled boxes side by side and one below."""
    image = Image.new("RGB", (800, 600), "white")
    draw = ImageDraw.Draw(image)
    for rect in ((60, 100, 340, 150), (420, 100, 700, 150), (60, 300, 700, 400)):
        draw.rectangle(rect, outline="#222", width=2)
    path = tmp_path / "page.png"
    image.save(path)
    return path


def test_detect_boxes_finds_each_rule_bounded_field(boxed_page):
    # A rectangle yields an outer and inner contour; both must collapse to one box.
    assert len(detect_boxes(boxed_page)) == 3


def test_detect_boxes_ignores_the_page_border(tmp_path):
    image = Image.new("RGB", (800, 600), "white")
    ImageDraw.Draw(image).rectangle((10, 10, 790, 590), outline="#222", width=4)
    path = tmp_path / "border.png"
    image.save(path)
    assert detect_boxes(path) == []


def test_label_binds_to_the_caption_above_it():
    box = Box(60, 100, 340, 150)
    above = [word("Patient", 60, 70), word("name", 105, 70)]
    elsewhere = [word("Unrelated", 60, 400)]
    assert label_for_box(box, [above, elsewhere]) is above


def test_label_ignores_a_caption_in_a_neighbouring_column():
    # Vertically plausible but with no horizontal overlap, so it must not be stolen.
    box = Box(60, 100, 340, 150)
    other_column = [word("Phone", 500, 70, 560, 90)]
    assert label_for_box(box, [other_column]) is None


def test_ink_ratio_separates_a_marked_box_from_an_empty_one():
    empty = np.full((200, 200), 255, dtype=np.uint8)
    marked = empty.copy()
    marked[80:120, 80:120] = 0
    box = Box(50, 50, 150, 150)
    assert ink_ratio(empty, box) == 0.0
    assert ink_ratio(marked, box) > 0.004


def test_ink_ratio_excludes_the_box_border():
    """An empty box must read as empty even though its own rule lines are dark."""
    page = np.full((200, 200), 255, dtype=np.uint8)
    page[50, 50:150] = page[150, 50:150] = 0
    page[50:150, 50] = page[50:150, 150] = 0
    assert ink_ratio(page, Box(50, 50, 150, 150)) == 0.0


def test_partition_splits_values_inside_boxes_from_free_labels():
    boxes = [Box(0, 0, 100, 50)]
    inside, outside = word("value", 10, 10), word("Label", 10, 200)
    contents, free = partition(boxes, [inside, outside])
    assert contents == [[inside]] and free == [outside]


def test_columns_on_one_row_do_not_fuse_into_a_single_label():
    row = [word("Date", 60, 70), word("of", 105, 70), word("birth", 130, 70),
           word("Phone", 500, 70)]
    assert [[z.text for z in line] for line in group_lines_tolerant(row)] == [
        ["Date", "of", "birth"],
        ["Phone"],
    ]


def test_a_colon_keeps_a_tab_aligned_value_with_its_label():
    """Wide gaps after a colon are alignment, not a column break."""
    row = [word("Name:", 60, 70), word("Robert", 500, 70)]
    assert len(group_lines_tolerant(row)) == 1


def test_phone_normalizes_to_the_stored_hyphenated_contract():
    assert normalize("(312) 555-0101", FieldType.PHONE) == "312-555-0101"
    assert normalize("1 312 555 0101", FieldType.PHONE) == "312-555-0101"


def test_phone_passes_through_when_it_is_not_ten_digits():
    assert normalize("555-01", FieldType.PHONE) == "555-01"


def test_multiline_values_keep_their_line_breaks():
    assert normalize("Metformin  500 mg\nVitamin D", FieldType.MEDICATION_LIST) == (
        "Metformin 500 mg\nVitamin D"
    )
