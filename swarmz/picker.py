"""Pick short-dated Kalshi markets and freeze briefs for them.

Selection: open binary markets expected to resolve in [min_hours, max_hours] (Kalshi's
expected_expiration_time, not close_time: markets often stay open after the event until data
arrives, and we must not pick events that already happened), combo (MVE) markets excluded,
one market per event (siblings resolve together), mid price inside [min_price, max_price] so the
outcome isn't already decided, enough volume to be a real market, and at most
``max_per_category`` per Kalshi category, filled round-robin across categories so sports
don't take every slot.

Briefs come from the unchanged brief builder (BriefPipeline, in the shoals-new folder), run in its
own Python (``BRIEF_BUILDER_PYTHON``) via scripts/build_brief.py; LLM calls it makes are recorded
under stage "brief" in the usage table.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from . import usage
from .config import ROOT
from .scoring.resolve import KALSHI_API_BASE

# The brief builder lives in its own project (folder still named shoals-new) with its own Python 3.11.
BRIEF_BUILDER_DIR = Path(os.environ.get("BRIEF_BUILDER_DIR", Path.home() / "Desktop/projects/shoals-new/backend"))
BRIEF_BUILDER_PYTHON = Path(os.environ.get("BRIEF_BUILDER_PYTHON", BRIEF_BUILDER_DIR / ".venv/bin/python"))
BRIEF_LOG = BRIEF_BUILDER_DIR / "data" / "shoals.db"     # the builder's prediction log (SQLite)
BRIEFS_DIR = BRIEF_BUILDER_DIR / "data" / "briefs_v2"
BUILD_SCRIPT = ROOT / "scripts" / "build_brief.py"


@dataclass
class Candidate:
    ticker: str
    event_ticker: str
    title: str
    close_time: str
    hours_left: float
    mid_cents: float
    volume: float
    category: str = ""
    series: str = ""
    raw: dict = field(default_factory=dict, repr=False)


def _num(m: dict, cents_key: str, dollars_key: str) -> float | None:
    v = m.get(cents_key)
    if v not in (None, 0, ""):
        return float(v)
    d = m.get(dollars_key)
    if d not in (None, "", "0", "0.0000"):
        try:
            return float(d) * 100
        except (TypeError, ValueError):
            return None
    return None


def mid_cents(m: dict) -> float | None:
    bid, ask = _num(m, "yes_bid", "yes_bid_dollars"), _num(m, "yes_ask", "yes_ask_dollars")
    if bid and ask and ask >= bid:
        return (bid + ask) / 2
    return _num(m, "last_price", "last_price_dollars")


def volume(m: dict) -> float:
    for k in ("volume", "volume_fp"):
        try:
            if m.get(k) not in (None, ""):
                return float(m[k])
        except (TypeError, ValueError):
            pass
    return 0.0


def fetch_markets(min_hours: float, max_hours: float, session=requests, max_pages: int = 150,
                  close_slack_hours: float = 36) -> list[dict]:
    """Open non-combo markets that close between min_hours and max_hours + close_slack_hours from now.
    The slack catches markets that stay open past their event while data arrives; candidates() then
    windows on expected resolution. Reads every page (sports props run to tens of thousands)."""
    import logging

    now = int(time.time())
    out, cursor = [], None
    for _ in range(max_pages):
        params = {"status": "open", "limit": 1000, "mve_filter": "exclude",
                  "min_close_ts": now + int(min_hours * 3600),
                  "max_close_ts": now + int((max_hours + close_slack_hours) * 3600)}
        if cursor:
            params["cursor"] = cursor
        r = session.get(f"{KALSHI_API_BASE}/markets", params=params, timeout=30)
        r.raise_for_status()
        j = r.json()
        out += j.get("markets") or []
        cursor = j.get("cursor")
        if not cursor:
            break
    else:
        logging.getLogger(__name__).warning(f"market listing truncated at {max_pages} pages; picks may miss markets")
    return [m for m in out if not str(m.get("ticker", "")).startswith("KXMVE")]


def resolves_at(m: dict):
    from .timeutil import utc
    return utc(m.get("expected_expiration_time") or m.get("close_time"))


def candidates(markets: list[dict], *, min_hours: float = 0, max_hours: float = 1e9, min_price: float = 10,
               max_price: float = 90, min_volume: float = 1000, exclude: set[str] = frozenset(),
               exclude_events: set[str] = frozenset()) -> list[Candidate]:
    """One liquid, undecided binary market per event, highest volume first."""
    from .timeutil import now, utc

    t0 = now()
    best: dict[str, Candidate] = {}
    for m in markets:
        if m.get("market_type", "binary") != "binary" or m.get("ticker") in exclude:
            continue
        ev_t = m.get("event_ticker") or ""
        if ev_t in exclude_events or occasion(ev_t) in exclude_events:
            continue   # a sibling of this event/game is already in the registry; they resolve together
        mid, vol = mid_cents(m), volume(m)
        if mid is None or not (min_price <= mid <= max_price) or vol < min_volume:
            continue
        ev = m.get("event_ticker") or m["ticker"]
        hours = (resolves_at(m) - t0).total_seconds() / 3600
        if not (min_hours <= hours <= max_hours):
            continue
        c = Candidate(m["ticker"], ev, m.get("title") or "", m["close_time"], round(hours, 1), round(mid, 1), vol, raw=m)
        # within an event prefer the most uncertain market (closest to 50c), then volume
        if ev not in best or (abs(c.mid_cents - 50), -c.volume) < (abs(best[ev].mid_cents - 50), -best[ev].volume):
            best[ev] = c
    return sorted(best.values(), key=lambda c: -c.volume)


def series_index(session=requests) -> dict[str, tuple[str, str]]:
    """series ticker -> (category, title), from one call to Kalshi's series list."""
    r = session.get(f"{KALSHI_API_BASE}/series", timeout=60)
    r.raise_for_status()
    return {s["ticker"]: (s.get("category") or "Unknown", s.get("title") or "")
            for s in r.json().get("series") or [] if s.get("ticker")}


def event_of(ticker: str) -> str:
    """Market ticker -> its event ticker (Kalshi markets are <event>-<outcome>), e.g.
    KXCBDECISIONMEXICO-26NOV05-C25 -> KXCBDECISIONMEXICO-26NOV05."""
    return ticker.rsplit("-", 1)[0] if ticker.count("-") >= 2 else ticker


def occasion(event_ticker: str) -> str:
    """The shared part of sibling events, e.g. KXNFLGAME-26NOV05BALCIN and KXNFLTOTAL-26NOV05BALCIN
    are the same game. Questions about one occasion resolve together, so we keep one."""
    return event_ticker.split("-", 1)[1] if "-" in event_ticker else event_ticker


def _strike_passed(session, event_ticker: str, min_hours: float = 0) -> bool:
    """True if the event's own date (e.g. the day a rain market is about) ends within min_hours:
    by then most of the outcome has already been observed."""
    from datetime import timedelta

    from .timeutil import now, utc
    try:
        ev = session.get(f"{KALSHI_API_BASE}/events/{event_ticker}", timeout=20).json().get("event") or {}
    except Exception:
        return False
    strike = ev.get("strike_date")
    return bool(strike) and utc(strike) <= now() + timedelta(hours=min_hours)


def pick(cands: list[Candidate], n: int, max_per_category: int, session=requests,
         series: dict | None = None, min_hours: float = 4) -> list[Candidate]:
    """Round-robin across categories (by each category's top volume), at most max_per_category each,
    one per occasion, preferring series not yet chosen. Skips events whose date has already passed."""
    series = series if series is not None else series_index(session)
    by_cat: dict[str, list[Candidate]] = {}
    for c in cands:
        c.category, c.series = series.get(c.event_ticker.split("-")[0], ("Unknown", ""))
        by_cat.setdefault(c.category, []).append(c)
    order = sorted(by_cat, key=lambda k: -by_cat[k][0].volume)
    chosen: list[Candidate] = []
    seen_series: set[str] = set()
    seen_occasions: set[str] = set()
    for _ in range(max_per_category):
        for cat in order:
            if len(chosen) >= n:
                break
            pool = sorted((c for c in by_cat[cat] if c not in chosen and occasion(c.event_ticker) not in seen_occasions),
                          key=lambda c: (c.series in seen_series, -c.volume))
            for c in pool:
                if _strike_passed(session, c.event_ticker, min_hours):
                    continue
                chosen.append(c)
                seen_series.add(c.series)
                seen_occasions.add(occasion(c.event_ticker))
                break
        if len(chosen) >= n:
            break
    return chosen


def _dollars(d: dict | None, key: str) -> float | None:
    try:
        return float((d or {}).get(key))
    except (TypeError, ValueError):
        return None


def market_prob_at(ticker: str, ts, session=requests, lookback_hours: float = 6) -> tuple[float, object] | None:
    """The market's price for YES at ``ts``, from Kalshi's one-minute price history: the middle of
    the last bid and ask quoted at or before ``ts``, or the last trade if a side was missing.
    Returns (probability, time of that quote) or None if the market had no quote by then.

    A baseline to score against. It is read after the brief is frozen and never shown to an agent.
    """
    from datetime import datetime, timezone

    from .timeutil import aware, utc
    end = int(aware(utc(ts)).timestamp())
    series = ticker.split("-")[0]
    r = session.get(f"{KALSHI_API_BASE}/series/{series}/markets/{ticker}/candlesticks",
                    params={"start_ts": end - int(lookback_hours * 3600), "end_ts": end + 60, "period_interval": 1},
                    timeout=30)
    r.raise_for_status()
    for c in reversed(r.json().get("candlesticks") or []):
        if c.get("end_period_ts", 0) > end + 60:
            continue
        bid, ask = _dollars(c.get("yes_bid"), "close_dollars"), _dollars(c.get("yes_ask"), "close_dollars")
        last = _dollars(c.get("price"), "close_dollars") or _dollars(c.get("price"), "previous_dollars")
        # an empty side of the book is quoted as 0 or 1: that is not a price
        p = (bid + ask) / 2 if bid and ask and 0 < bid <= ask < 1 else last
        if p and 0 < p < 1:
            return round(p, 4), datetime.fromtimestamp(c["end_period_ts"], timezone.utc).replace(tzinfo=None)
    return None


def build_brief(ticker: str, timeout: float = 1800, min_words: int | None = None) -> tuple[dict, usage.Tracker]:
    """Freeze a brief for one ticker. Returns (result, usage tracker for the brief build)."""
    if not BRIEF_BUILDER_PYTHON.exists():
        raise FileNotFoundError(f"brief builder Python not found at {BRIEF_BUILDER_PYTHON} (set BRIEF_BUILDER_PYTHON)")
    with tempfile.TemporaryDirectory() as tmp:
        usage_file = Path(tmp) / "usage.jsonl"
        env = {**os.environ, **usage.subprocess_env(usage_file, stage="brief")}
        for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL_NAME"):
            env.pop(k, None)   # let the brief builder use its own .env, not swarmz's
        cmd = [str(BRIEF_BUILDER_PYTHON), str(BUILD_SCRIPT), ticker] + ([str(min_words)] if min_words else [])
        proc = subprocess.run(cmd, cwd=BRIEF_BUILDER_DIR, env=env,
                              capture_output=True, text=True, timeout=timeout)
        lines = [l for l in proc.stdout.strip().splitlines() if l.startswith("{")]
        try:
            result = json.loads(lines[-1])
        except (IndexError, json.JSONDecodeError):
            result = {"ticker": ticker, "status": "error",
                      "reject_reason": (proc.stderr or proc.stdout)[-800:] or f"exit {proc.returncode}"}
        with usage.track() as t:
            usage.merge_file(usage_file)
    return result, t
