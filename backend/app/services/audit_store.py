"""A durable, append-only record of what was read, corrected and signed off.

The history this backs already worked without it, off `analysis.json` in each run folder.
That is a weak audit trail and the work plan says so: those folders are disposable
artifacts, they are rewritten in place by every correction, and nothing stops one being
deleted. An audit trail you can delete by clearing a cache is not an audit trail.

**Append-only by construction.** There is no update and no delete in this module, and the
table has no column that could be rewritten to change the past. A correction is a new row
that says what the value became; the row saying what it was before stays where it is. That
is the faculty review's rule -- never silently overwrite a patient-entered value -- made
structural rather than promised.

**Two backends, one set of rows.** With no ``DATABASE_URL`` the trail is a SQLite file
beside the artifacts: nothing to install, nothing to run, and durable on a laptop. Given a
PostgreSQL URL the same rows go to a server instead. The SQL was written plainly for this
-- no JSONB, no dialect features -- so the difference between the two is the placeholder
style and how the primary key counts, and nothing about what is stored.

Which one is in use is reported by ``status`` and therefore by ``/health``. An audit trail
whose location is a guess is not much of an audit trail.

**It must never break extraction.** A form that was read correctly must not fail because
an audit row could not be written. Every write is therefore guarded, and the failure is
reported through ``status()`` and `/health` rather than swallowed: a silent audit trail is
worse than none, because it looks like one.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id  TEXT    NOT NULL,
    patient_key  TEXT,
    at           TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    summary      TEXT    NOT NULL,
    field_id     TEXT,
    label        TEXT,
    was          TEXT,
    now          TEXT,
    by_whom      TEXT,
    recorded_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_by_analysis ON audit_events (analysis_id, id);
CREATE INDEX IF NOT EXISTS audit_by_patient  ON audit_events (patient_key, id);
"""

POSTGRES_SCHEMA = SQLITE_SCHEMA.replace(
    "id           INTEGER PRIMARY KEY AUTOINCREMENT",
    "id           BIGSERIAL PRIMARY KEY",
)


def _postgres_url() -> str | None:
    """The configured PostgreSQL URL, or None to use the SQLite file.

    ``DATABASE_URL`` is written in SQLAlchemy's form in ``.env.example``
    (``postgresql+psycopg://``) because that is what the rest of the world writes; psycopg
    wants the plain scheme, so the driver suffix is dropped here rather than asking anyone
    to keep two spellings of the same URL.
    """
    from app.config import settings

    url = (settings.database_url or "").strip()
    if not url.startswith("postgres"):
        return None
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


_lock = threading.Lock()
_last_error: str | None = None


def _path(root: Path) -> Path:
    return root / "audit.db"


def _connect(root: Path):
    """A connection to whichever store is configured, with the schema already applied."""
    url = _postgres_url()
    if url:
        import psycopg

        connection = psycopg.connect(url, connect_timeout=5)
        with connection.cursor() as cursor:
            for statement in POSTGRES_SCHEMA.strip().split(";"):
                if statement.strip():
                    cursor.execute(statement)
        connection.commit()
        return connection
    root.mkdir(parents=True, exist_ok=True)
    sqlite_connection = sqlite3.connect(_path(root), timeout=5)
    sqlite_connection.executescript(SQLITE_SCHEMA)
    return sqlite_connection


def _sql(statement: str) -> str:
    """The same statement in whichever placeholder style the backend speaks."""
    return statement.replace("?", "%s") if _postgres_url() else statement


def _text(value: Any) -> str | None:
    """Values are stored as text, including the structured ones.

    An audit row records *what was said*, so it is kept as written rather than re-typed:
    a phone number that was corrected from a list to a string must read back as both, and
    a column with a type would have to choose.
    """
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value, default=str)


def record(
    root: Path,
    analysis_id: str,
    kind: str,
    summary: str,
    *,
    patient_key: str | None = None,
    at: datetime | None = None,
    field_id: str | None = None,
    label: str | None = None,
    was: Any = None,
    now: Any = None,
    by: str | None = None,
) -> bool:
    """File one event. Returns whether it was stored; never raises."""
    global _last_error
    try:
        with _lock, _connect(root) as connection:
            connection.execute(
                _sql(
                    "INSERT INTO audit_events (analysis_id, patient_key, at, kind, summary,"
                    " field_id, label, was, now, by_whom, recorded_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                ),
                (
                    analysis_id,
                    patient_key,
                    (at or datetime.now(UTC)).isoformat(),
                    kind,
                    summary,
                    field_id,
                    label,
                    _text(was),
                    _text(now),
                    by,
                    datetime.now(UTC).isoformat(),
                ),
            )
        _last_error = None
        return True
    except Exception as exc:  # noqa: BLE001 - an audit write must not fail a reading
        _last_error = f"{type(exc).__name__}: {exc}"
        return False


def events_for(root: Path, analysis_id: str) -> list[dict]:
    """Every filed event for one form, in the order they were filed."""
    try:
        with _lock, _connect(root) as connection:
            if _postgres_url():
                from psycopg.rows import dict_row

                with connection.cursor(row_factory=dict_row) as cursor:
                    cursor.execute(
                        _sql("SELECT * FROM audit_events WHERE analysis_id = ? ORDER BY id"),
                        (analysis_id,),
                    )
                    return [dict(r) for r in cursor.fetchall()]
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE analysis_id = ? ORDER BY id", (analysis_id,)
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def status(root: Path) -> dict:
    """What `/health` should say about the audit trail.

    Reports the row count rather than just "ok": a store that is reachable and empty is a
    different problem from one that is working, and the difference is invisible otherwise.
    """
    url = _postgres_url()
    where = url.rsplit("@", 1)[-1] if url else str(_path(root))
    backend = "postgresql" if url else "sqlite"
    try:
        with _lock, _connect(root) as connection:
            if url:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM audit_events")
                    (count,) = cursor.fetchone()
            else:
                (count,) = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()
        return {"backend": backend, "path": where, "events": count, "last_error": _last_error}
    except Exception as exc:  # noqa: BLE001
        return {"backend": backend, "path": where, "events": None,
                "last_error": f"{type(exc).__name__}: {exc}"}
