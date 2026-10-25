"""Timezone helpers.  All timestamps in Tianling are timezone-aware UTC."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Normalize a datetime to aware UTC.

    SQLite drops tzinfo on read, so naive values are assumed to already be UTC.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def discord_ts(value: datetime | None, style: str = "R") -> str:
    """Format a datetime as a Discord timestamp markdown token."""
    normalized = as_utc(value)
    if normalized is None:
        return "-"
    return f"<t:{int(normalized.timestamp())}:{style}>"
