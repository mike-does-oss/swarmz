"""Build and freeze a v2 brief for one Kalshi ticker. Runs inside the brief builder's own Python.

    <brief builder python> scripts/build_brief.py TICKER [MIN_WORDS]   (cwd = the brief builder's backend/,
    today ~/Desktop/projects/shoals-new/backend)

Calls the unchanged brief builder (BriefPipeline: (decompose -> retrieve -> triage -> assemble -> scrub -> freeze),
which writes the brief to data/briefs_v2 and a row to its prediction log (data/shoals.db). Prints one JSON line:
{"ticker", "status": "frozen" | "rejected" | "error", "reject_reason", "brief_hash", "word_count", "freeze_ts",
 "latest_reading"}
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.getcwd())


def main(ticker: str, min_words: int | None = None) -> dict:
    from app.services.brief_v2.pipeline import BriefPipeline
    from app.services.kalshi_client import KalshiClient

    market = KalshiClient().get_market(ticker)
    if not market or not market.get("ticker"):
        return {"ticker": ticker, "status": "error", "reject_reason": "market_not_found"}
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    kw = {"min_words": min_words} if min_words else {}   # builder default is 1500; short-dated questions have less news
    r = BriefPipeline(**kw).build(market, now=now)
    return {"ticker": ticker, "status": r.status, "reject_reason": r.reject_reason,
            "brief_hash": r.brief_hash, "word_count": r.word_count, "freeze_ts": r.freeze_ts,
            # where the thing the question resolves on stands, as the builder read it: kind "value" for a
            # measured quantity, "state" for an event or decision. A price or index question with no value
            # from the last 4 days is rejected; the state of an event is never judged for age.
            "latest_reading": getattr(r, "latest_reading", None)}


if __name__ == "__main__":
    try:
        out = main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else None)
    except Exception as e:
        out = {"ticker": sys.argv[1] if len(sys.argv) > 1 else None, "status": "error",
               "reject_reason": f"{type(e).__name__}: {e}"[:500]}
    print(json.dumps(out))
