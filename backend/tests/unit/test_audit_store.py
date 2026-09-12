"""The audit trail: durable, append-only, and never able to fail a reading."""

from datetime import UTC, datetime

import pytest

from app.services import audit_store

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def sqlite_backend(monkeypatch):
    """These exercise the file backend, so they must not follow a configured DATABASE_URL.

    Without this they quietly wrote to whatever PostgreSQL the developer happened to have
    running and ignored their own tmp_path, which is both a failing test and a test that
    could have passed for the wrong reason.
    """
    monkeypatch.setattr(audit_store, "_postgres_url", lambda: None)


def test_an_event_survives_being_written_and_read_back(tmp_path):
    assert audit_store.record(tmp_path, "run_1", "extracted", "Read 12 fields", at=NOW)
    (event,) = audit_store.events_for(tmp_path, "run_1")
    assert event["kind"] == "extracted"
    assert event["summary"] == "Read 12 fields"
    assert event["at"] == NOW.isoformat()


def test_a_correction_keeps_the_value_it_replaced(tmp_path):
    """The point of the trail: what the machine said before a person changed it."""
    audit_store.record(tmp_path, "run_1", "corrected", "Home Phone corrected",
                       field_id="f1", was="312-555-0109", now="312-555-0200", by="a reviewer")
    (event,) = audit_store.events_for(tmp_path, "run_1")
    assert (event["was"], event["now"]) == ("312-555-0109", "312-555-0200")
    assert event["by_whom"] == "a reviewer"


def test_history_only_ever_grows(tmp_path):
    """There is no update and no delete in this module. Two corrections to one field are
    two rows, and the first is still the first."""
    audit_store.record(tmp_path, "run_1", "corrected", "x", field_id="f1", was="a", now="b")
    audit_store.record(tmp_path, "run_1", "corrected", "x", field_id="f1", was="b", now="c")
    assert [(e["was"], e["now"]) for e in audit_store.events_for(tmp_path, "run_1")] == [
        ("a", "b"), ("b", "c")
    ]


def test_a_structured_value_reads_back_as_what_was_said(tmp_path):
    audit_store.record(tmp_path, "run_1", "corrected", "x", was=["one", "two"], now="three")
    (event,) = audit_store.events_for(tmp_path, "run_1")
    assert event["was"] == '["one", "two"]'


def test_events_are_scoped_to_their_own_form(tmp_path):
    audit_store.record(tmp_path, "run_1", "extracted", "a")
    audit_store.record(tmp_path, "run_2", "extracted", "b")
    assert len(audit_store.events_for(tmp_path, "run_1")) == 1


def test_a_store_that_cannot_be_written_does_not_raise(tmp_path):
    """A form read correctly must not fail because an audit row could not be filed. The
    failure is reported, not swallowed -- see ``status``."""
    blocked = tmp_path / "audit.db"
    blocked.mkdir()  # a directory where the database file must go
    assert audit_store.record(tmp_path, "run_1", "extracted", "a") is False
    assert audit_store.status(tmp_path)["last_error"]


def test_status_distinguishes_empty_from_broken(tmp_path):
    """A reachable, empty store is a different problem from a working one."""
    assert audit_store.status(tmp_path)["events"] == 0
    audit_store.record(tmp_path, "run_1", "extracted", "a")
    assert audit_store.status(tmp_path)["events"] == 1



def _postgres_or_skip():
    """A live PostgreSQL, or skip -- this suite must pass on a laptop with no server."""
    url = audit_store._postgres_url()
    if not url:
        pytest.skip("DATABASE_URL is not a PostgreSQL URL")
    try:
        import psycopg

        psycopg.connect(url, connect_timeout=3).close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not reachable: {type(exc).__name__}")
    return url


def test_the_same_rows_survive_a_round_trip_through_postgresql(monkeypatch, tmp_path):
    """The point of the second backend is that nothing about the record changes -- only
    where it is kept. Skipped where no server is running, which is most laptops."""
    monkeypatch.undo()  # let this one see the real DATABASE_URL
    _postgres_or_skip()
    analysis = f"run_pg_{datetime.now(UTC).timestamp()}"

    assert audit_store.record(tmp_path, analysis, "extracted", "Read 12 fields", at=NOW)
    assert audit_store.record(
        tmp_path, analysis, "corrected", "Home Phone corrected",
        field_id="f1", was="312-555-0109", now="312-555-0200", by="a reviewer",
    )
    events = audit_store.events_for(tmp_path, analysis)
    assert [e["kind"] for e in events] == ["extracted", "corrected"]
    assert (events[1]["was"], events[1]["now"]) == ("312-555-0109", "312-555-0200")
    assert events[1]["by_whom"] == "a reviewer"
    assert audit_store.status(tmp_path)["backend"] == "postgresql"
