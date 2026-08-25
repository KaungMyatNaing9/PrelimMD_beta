"""Deriving a reusable template from a single filled form."""

import numpy as np
import pytest

from app.schemas.models import BoundingBox
from app.services.regeneration import label_key, values_from_analysis
from app.services.template_builder import _slug, blank_from_filled


def test_value_pixels_are_erased_but_the_rule_line_survives():
    page = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    # A box outline at 100..300, with "value" ink filling its interior.
    page[100, 100:300] = page[300, 100:300] = 0
    page[100:300, 100] = page[100:300, 300] = 0
    page[150:250, 150:250] = 0

    blank = blank_from_filled(page, [BoundingBox(x=0.1, y=0.1, width=0.2, height=0.2)])

    assert (blank[200, 200] == 255).all(), "interior ink should be erased"
    assert (blank[100, 200] == 0).all(), "the box's own rule line must survive"


def test_ink_flush_against_the_top_edge_is_still_erased():
    """Values sit at the top of tall fields; a size-scaled inset used to spare them."""
    page = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    page[105:130, 150:400] = 0  # text just inside a tall box starting at y=100

    # A plausible multi-line field: wide, but not so tall it becomes a section frame.
    blank = blank_from_filled(page, [BoundingBox(x=0.1, y=0.1, width=0.6, height=0.15)])

    assert blank[105:130, 150:400].all(), "text near the top edge should be gone"


def test_erasing_leaves_content_outside_the_box_untouched():
    page = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    page[50:70, 100:300] = 0  # a label above the box

    blank = blank_from_filled(page, [BoundingBox(x=0.1, y=0.1, width=0.2, height=0.2)])

    assert (blank[50:70, 100:300] == 0).all(), "labels are template, not values"


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Patient name", "patient_name"),
        ("Date of Birth", "date_of_birth"),
        ("Insurance ID #", "insurance_id"),
        ("", "field"),
        ("123 Numeric", "f_123_numeric"),
    ],
)
def test_slug_matches_the_field_id_pattern(label, expected):
    assert _slug(label, set()) == expected


def test_slug_disambiguates_repeated_labels():
    used = set()
    first = _slug("Phone", used)
    used.add(first)
    assert _slug("Phone", used) == "phone_2"


@pytest.mark.parametrize(
    "label", ["Surname", "Given names", "Policy number", "Blood group", "Reason for visit"]
)
def test_derived_field_ids_match_the_keys_regeneration_looks_up(label):
    """The contract linking the two halves.

    A derived template names unmapped fields by slug; regeneration keys their values the
    same way. If these ever diverge, an unknown form regenerates almost entirely blank,
    because most of its fields have no canonical mapping.
    """
    assert _slug(label, set()) == label_key(label)


def test_values_are_offered_under_both_canonical_and_label_keys():
    class Field:
        def __init__(self, key, label, value):
            self.canonical_key, self.label_text, self.normalized_value = key, label, value

    class Analysis:
        def __init__(self):
            self.detected_fields = [
                Field("patient.email", "Email", "a@b.com"),
                Field(None, "Blood group", "O+"),
            ]

    values = values_from_analysis(Analysis())
    assert values["patient.email"] == "a@b.com"
    assert values["email"] == "a@b.com", "canonical fields still need their label key"
    assert values["blood_group"] == "O+", "unmapped fields resolve by label alone"
