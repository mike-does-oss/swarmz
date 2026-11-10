"""Resolution job: record outcomes for open questions, then score them.

Outcome source is an open decision. Default: Kalshi public market data (the
imported questions are Kalshi tickers). Manual outcomes always work.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

import requests

from ..registry import store
from ..registry.db import rows
from ..timeutil import now, utc
from .brier import score_question

KALSHI_API_BASE = os.environ.get("KALSHI_API_BASE", "https://external-api.kalshi.com/trade-api/v2")
_SETTLED = {"settled", "finalized", "determined"}


@dataclass
class Resolution:
    question_id: str
    outcome: int | None     # None with void=False means still open
    resolve_ts: datetime | None
    void: bool = False


def kalshi_resolution(ticker: str, session=requests) -> Resolution:
    r = session.get(f"{KALSHI_API_BASE}/markets/{ticker}", timeout=20)
    r.raise_for_status()
    m = r.json().get("market", {})
    status = (m.get("status") or "").lower()
    result = (m.get("result") or "").lower()
    if status not in _SETTLED or not result:
        return Resolution(ticker, None, None)
    ts = _resolved_at(m)
    if result == "yes":
        return Resolution(ticker, 1, ts)
    if result == "no":
        return Resolution(ticker, 0, ts)
    return Resolution(ticker, None, ts, void=True)   # void / scalar / anything ambiguous


def _resolved_at(m: dict) -> datetime:
    """When the outcome became known. Kalshi fills settlement_ts some minutes after it reports a
    result; until then use the close time, and never a time in the future. (expiration_time is a
    backstop a week out: using it kept two questions out of learning until that date.)"""
    if m.get("settlement_ts"):
        return utc(m["settlement_ts"])
    known = [utc(m[k]) for k in ("close_time", "expiration_time") if m.get(k)]
    return min([now()] + known[:1])

def apply(con, res: Resolution) -> int:
    """Write a resolution and score the question. Returns score rows written."""
    if res.outcome is None and not res.void:
        return 0
    store.set_outcome(con, res.question_id, res.outcome, res.resolve_ts, void=res.void)
    return score_question(con, res.question_id)


def resolve_open(con, source=kalshi_resolution) -> list[Resolution]:
    done = []
    for q in rows(con, "SELECT question_id, source FROM questions WHERE status = 'open' ORDER BY freeze_ts"):
        if q["source"] != "kalshi":
            continue
        try:
            res = source(q["question_id"])
        except Exception as e:  # keep going; one bad ticker shouldn't stop the job
            print(f"  {q['question_id']}: lookup failed ({e})")
            continue
        if apply(con, res) or res.void:
            done.append(res)
    return done
