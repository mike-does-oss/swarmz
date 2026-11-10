"""Question tags for analysis: which personas and arms do well on which kinds of question.

Kalshi questions are tagged automatically from the market, its event and its series:
  category   Kalshi category (Economics, Sports, ...); also copied into questions.category
  topic      Kalshi series tags (Inflation, Football, AI, ...); can be several
  series     series title (CPI, NFL Game, ...)
  frequency  series frequency (monthly, one_off, ...)
  horizon    days from freeze to market close, bucketed
  strike_type  Kalshi strike type (greater, less, between, ...)
and, for brief-builder imports, ``market_bucket`` (the market mid price at freeze, bucketed).

Tags are reporting metadata only. They are never shown to agents, used in lessons, or read by
learning (see tests/test_tags.py), so the market-price tag can't leak odds into forecasts.
"""

from __future__ import annotations

import requests

from .registry.db import rows
from .scoring.resolve import KALSHI_API_BASE
from .timeutil import now, utc

ANALYSIS_ONLY = {"market_bucket"}
HORIZON_BUCKETS = [(3, "0-3d"), (7, "4-7d"), (14, "8-14d"), (60, "15-60d")]
MARKET_BUCKETS = [(10, "0-10c"), (30, "10-30c"), (70, "30-70c"), (90, "70-90c")]


def horizon_bucket(freeze_ts, close_ts) -> str | None:
    f, c = utc(freeze_ts), utc(close_ts)
    if f is None or c is None:
        return None
    days = (c - f).total_seconds() / 86400
    for limit, label in HORIZON_BUCKETS:
        if days <= limit:
            return label
    return "60d+"


def market_bucket(mid) -> str | None:
    """Mid price in cents (0-100) or dollars (0-1) -> bucket."""
    if mid is None:
        return None
    cents = float(mid) * 100 if float(mid) <= 1 else float(mid)
    for limit, label in MARKET_BUCKETS:
        if cents < limit:
            return label
    return "90-100c"


def _get(session, path: str) -> dict:
    r = session.get(f"{KALSHI_API_BASE}{path}", timeout=20)
    r.raise_for_status()
    return r.json()


def kalshi_tags(ticker: str, freeze_ts, session=requests) -> tuple[str | None, list[tuple[str, str]]]:
    """(category, [(key, value), ...]) for a Kalshi market ticker."""
    m = _get(session, f"/markets/{ticker}").get("market") or {}
    ev = (_get(session, f"/events/{m['event_ticker']}").get("event") or {}) if m.get("event_ticker") else {}
    s = (_get(session, f"/series/{ev['series_ticker']}").get("series") or {}) if ev.get("series_ticker") else {}
    category = s.get("category") or ev.get("category")
    tags: list[tuple[str, str]] = []
    if category:
        tags.append(("category", category))
    tags += [("topic", t) for t in (s.get("tags") or []) if t]
    for key, value in (("series", s.get("title")), ("frequency", s.get("frequency")),
                       ("strike_type", m.get("strike_type")),
                       ("horizon", horizon_bucket(freeze_ts, m.get("close_time")))):
        if value:
            tags.append((key, str(value)))
    return category, tags


def set_tags(con, question_id: str, tags: list[tuple[str, str]], source: str, *, replace_keys: bool = False) -> int:
    """Add tags. With replace_keys, existing values for the same keys are dropped first."""
    at = now()
    if replace_keys:
        for key in {k for k, _ in tags}:
            con.execute("DELETE FROM question_tags WHERE question_id = ? AND key = ?", [question_id, key])
    for key, value in tags:
        con.execute("INSERT OR REPLACE INTO question_tags VALUES (?, ?, ?, ?, ?)",
                    [question_id, key.strip(), str(value).strip(), source, at])
    return len(tags)


def remove_tag(con, question_id: str, key: str, value: str | None = None) -> None:
    if value is None:
        con.execute("DELETE FROM question_tags WHERE question_id = ? AND key = ?", [question_id, key])
    else:
        con.execute("DELETE FROM question_tags WHERE question_id = ? AND key = ? AND value = ?",
                    [question_id, key, value])


def tags_for(con, question_id: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in rows(con, "SELECT key, value FROM question_tags WHERE question_id = ? ORDER BY key, value",
                  [question_id]):
        out.setdefault(r["key"], []).append(r["value"])
    return out


def tag_from_kalshi(con, question_id: str, session=requests) -> dict[str, list[str]]:
    """Auto-tag one Kalshi question and set questions.category from Kalshi if it is still 'other'."""
    from .registry import store
    from .scoring.brier import score_question

    q = store.get_question(con, question_id)
    category, tags = kalshi_tags(question_id, q["freeze_ts"], session)
    set_tags(con, question_id, tags, "kalshi", replace_keys=True)
    if category and q["category"] in ("", "other"):
        con.execute("UPDATE questions SET category = ? WHERE question_id = ?", [category, question_id])
        if q["status"] != "open":
            score_question(con, question_id)   # scores carry the category; rescoring is idempotent
    return tags_for(con, question_id)


def parse_pairs(pairs: list[str]) -> list[tuple[str, str]]:
    out = []
    for p in pairs:
        key, sep, value = p.partition("=")
        if not sep or not key.strip() or not value.strip():
            raise ValueError(f"tags must look like key=value, got {p!r}")
        out.append((key.strip(), value.strip()))
    return out
