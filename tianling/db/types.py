"""Custom SQLAlchemy column types."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator

from tianling.core.timeutil import as_utc


class UTCDateTime(TypeDecorator[datetime]):
    """A timezone-aware UTC datetime that survives SQLite's tz stripping.

    Values are normalized to UTC on the way in and re-attached to UTC on the
    way out, so application code never sees naive datetimes.
    """

    impl = DateTime
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(timezone=True)

    def process_bind_param(self, value: datetime | None, dialect):  # type: ignore[override]
        return as_utc(value)

    def process_result_value(self, value: datetime | None, dialect):  # type: ignore[override]
        return as_utc(value)
