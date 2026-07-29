from app.schemas.models import FieldType
from app.services.extraction import normalize


def test_phone_normalizes_to_digits():
    assert normalize("(312) 555-0101", FieldType.PHONE)[0] == "3125550101"


def test_ambiguous_date_is_flagged():
    assert normalize("03/04/26", FieldType.DATE)[1][0].code == "AMBIGUOUS_DATE"


def test_identifier_uses_pattern():
    assert normalize("ab 123456", FieldType.INSURANCE_ID, "[A-Z]{2}\\d{6}")[0] == "AB123456"
