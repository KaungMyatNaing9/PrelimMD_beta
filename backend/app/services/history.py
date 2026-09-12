"""What happened to a form, and what happened to a patient across their forms.

Two different questions, and the faculty review asks for both. Per-field history already
exists -- `FieldRevision` files the value a correction replaced, append-only, so a chart
review can tell an extraction error from a patient changing their mind. What it cannot
show is the shape of things: everything that happened to one form in order, or how a
patient's own answers moved between the form they filled in March and the one in
September.

Neither is derived from anything new. The revisions are already on disk; this reads them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from app.schemas.form_understanding import FormAnalysis

# The keys that identify a person. A form that carries none of them belongs to nobody this
# module can name, and is reported that way rather than guessed at.
NAME_KEYS = ("patient.full_name", "patient.last_name", "patient.first_name")
BIRTH_KEY = "patient.date_of_birth"


def _text(value: Any) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _values(analysis: FormAnalysis) -> dict[str, Any]:
    """The answered fields of an analysis, keyed canonically.

    Fields the vocabulary could not map are left out: without a canonical key there is no
    way to say that a value on one form is *the same field* as a value on another, and
    comparing them by printed caption is what put a patient's name into a subscriber
    section once already.
    """
    found: dict[str, Any] = {}
    for field in analysis.detected_fields:
        key = field.canonical_key
        if not key or field.normalized_value in (None, "", False):
            continue
        found.setdefault(key, field.normalized_value)
    return found


def patient_key(analysis: FormAnalysis) -> str | None:
    """A stable identifier for whoever this form is about, or None.

    Name plus date of birth, which is what a front desk uses and what the data actually
    contains. It is deliberately not a medical record number: the forms do not carry one,
    and inventing an identity for a form that cannot supply one would let two people merge.

    Returning None is a real answer. A form whose name was never read belongs to an
    unidentified patient, and saying so is better than filing it under a near-miss.
    """
    values = _values(analysis)
    birth = values.get(BIRTH_KEY)
    names = [_text(values[k]) for k in NAME_KEYS if values.get(k)]
    if not names or not birth:
        return None
    return f"{'-'.join(sorted(names))}|{_text(birth)}"


class Event(NamedTuple):
    at: datetime
    kind: str
    summary: str
    field_id: str | None = None
    label: str | None = None
    was: Any = None
    now: Any = None
    by: str | None = None


def form_timeline(analysis: FormAnalysis, stored_at: datetime) -> list[Event]:
    """Everything that has happened to one form, oldest first.

    The extraction itself is an event: it is the moment the machine asserted values, and a
    timeline that began at the first human correction would imply the values before it
    came from nowhere.
    """
    events = [
        Event(
            at=stored_at,
            kind="extracted",
            summary=(
                f"Read {len(analysis.detected_fields)} fields, "
                f"{sum(f.needs_review for f in analysis.detected_fields)} flagged for review"
            ),
        )
    ]
    for field in analysis.detected_fields:
        for revision in field.history:
            events.append(
                Event(
                    at=revision.superseded_at,
                    kind="corrected",
                    summary=f"{field.label_text} corrected",
                    field_id=field.field_id,
                    label=field.label_text,
                    was=revision.value,
                    now=field.normalized_value,
                    by=revision.superseded_by,
                )
            )
    for attestation in getattr(analysis, "attestations", []) or []:
        events.append(
            Event(
                at=attestation.signed_at,
                kind="signed-off",
                summary=f"Signed off by {attestation.signed_by}",
                by=attestation.signed_by,
            )
        )
    return sorted(events, key=lambda e: e.at)


class Change(NamedTuple):
    key: str
    was: Any
    now: Any


def diff(earlier: FormAnalysis, later: FormAnalysis) -> list[Change]:
    """How a patient's answers moved between two of their forms.

    Only fields both forms answered are compared. A key present on one form and absent from
    the other says nothing about the patient -- that form may simply not have asked -- and
    reporting it as a change would turn a difference between two *questionnaires* into a
    claim about a person.
    """
    before, after = _values(earlier), _values(later)
    return [
        Change(key=key, was=before[key], now=after[key])
        for key in sorted(before.keys() & after.keys())
        if _text(before[key]) != _text(after[key])
    ]


def read_at(folder: Path) -> datetime:
    """When this form was read, as opposed to when its record was last written.

    ``analysis.json`` is rewritten by every correction and every sign-off, so its mtime is
    the time of the *last* change -- which put the extraction after the corrections that
    followed it on the timeline, an order that cannot happen. The uploaded source is
    written once and never touched again, so it dates the reading.
    """
    for name in ("source.pdf", "source.jpg", "source.png", "source.jpeg"):
        source = folder / name
        if source.is_file():
            return datetime.fromtimestamp(source.stat().st_mtime, tz=UTC)
    return datetime.fromtimestamp((folder / "analysis.json").stat().st_mtime, tz=UTC)


def stored_analyses(root: Path) -> list[tuple[FormAnalysis, datetime]]:
    """Every analysis on disk with the time it was read, oldest first."""
    found: list[tuple[FormAnalysis, datetime]] = []
    if not root.is_dir():
        return found
    for folder in root.iterdir():
        record = folder / "analysis.json"
        if not record.is_file():
            continue
        try:
            analysis = FormAnalysis.model_validate_json(record.read_text())
        except ValueError:
            continue  # a partial or older record must not break the history
        found.append((analysis, read_at(folder)))
    return sorted(found, key=lambda pair: pair[1])
