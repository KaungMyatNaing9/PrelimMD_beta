"""A model's answer counts only if it is an exact substring of the line it names."""

from app.services.relinker import ground, serialise
from app.services.word_source import Word


def _line(y: float, *texts: str) -> list[Word]:
    return [Word(x0=100 + 60 * i, y0=y, x1=150 + 60 * i, y1=y + 10, text=t, block=0, confidence=0.9)
            for i, t in enumerate(texts)]


def test_lines_get_position_identifiers_that_stay_unique():
    words = _line(100, "CITY", "STATE", "ZIP") + _line(120, "Pittsburgh", "PA", "15206")
    lines = serialise(words, 1000, 1000)
    assert [line.text for line in lines] == ["CITY STATE ZIP", "Pittsburgh PA 15206"]
    assert len({line.ident for line in lines}) == 2
    assert all("|" in line.ident for line in lines)


def test_an_answer_grounds_only_as_an_exact_substring_of_its_line():
    words = _line(100, "CITY", "STATE", "ZIP") + _line(120, "Pittsburgh", "PA", "15206")
    lines = serialise(words, 1000, 1000)
    value_line = lines[1].ident
    found = ground(
        [
            {"caption": "City", "value": "Pittsburgh", "line": value_line},
            {"caption": "State", "value": "Pennsylvania", "line": value_line},  # invented
            {"caption": "Zip", "value": "15206", "line": "00|00"},  # wrong line
        ],
        lines,
        ["City", "State", "Zip"],
    )
    assert set(found) == {"City"}
    value, carrying = found["City"]
    assert value == "Pittsburgh" and [w.text for w in carrying] == ["Pittsburgh"]


def test_a_caption_not_asked_about_is_ignored():
    words = _line(120, "Pittsburgh")
    lines = serialise(words, 1000, 1000)
    assert ground([{"caption": "Town", "value": "Pittsburgh", "line": lines[0].ident}], lines, ["City"]) == {}


def test_the_caption_the_model_copied_is_not_part_of_the_answer():
    from app.services.relinker import _without_caption_prefix

    assert _without_caption_prefix("MARITAL STATUS Married", "marital status") == "Married"
    assert _without_caption_prefix("EMERGENCY CONTACT: NAME:", "emergency contact name") == ""
    assert _without_caption_prefix("noor.sandoval@example.com", "email address") == "noor.sandoval@example.com"


def test_only_the_lines_around_asked_captions_are_shown():
    from app.services.relinker import _around

    words = []
    for i in range(30):
        words += _line(100 + 20 * i, f"line{i}")
    words += _line(100 + 20 * 15 + 5, "CITY")  # same row as line15
    lines = serialise(words, 1000, 1000)
    shown = _around(lines, ["City"])
    assert 4 <= len(shown) <= 8 and any("CITY" in line.text for line in shown)


def test_an_option_picked_off_a_tick_row_is_not_an_answer():
    from app.services.relinker import _is_choice_row

    assert _is_choice_row("☐ Single ☐ Married ☐ Divorced", "Single")
    assert _is_choice_row("Single Married Divorced Widowed Partnered", "Single")
    assert not _is_choice_row("MARITAL STATUS Married", "Married")
    assert not _is_choice_row("Rafferty O Vandermeer", "Rafferty O Vandermeer")
    assert not _is_choice_row("Date of Birth 08/04/1945", "08/04/1945")
