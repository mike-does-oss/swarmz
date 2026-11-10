"""Timestamps: stored as naive UTC everywhere."""

from __future__ import annotations

from datetime import datetime, timezone


def utc(ts: datetime | str | None) -> datetime | None:
    """Return a naive-UTC datetime (accepts ISO strings, aware or naive datetimes)."""
    if ts is None:
        return None
    if isinstance(ts, str):
        s = ts.strip().replace("Z", "+00:00")
        ts = datetime.fromisoformat(s)
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def aware(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
