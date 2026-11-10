"""Run the next queued Kalshi market from the Supercast queue, then refresh the site.

    .venv/bin/python scripts/run_queue.py [--force] [--dry-run] [--publish-only]

Meant to be started every hour by launchd (see scripts/com.swarmz.queue.plist). It acts on even UTC
hours only, so runs land on the two-hourly slots the site shows; --force skips that check.

One slot, one market: the oldest queued Kalshi ticker that has no finished panel run and has not
failed before. It freezes a brief, runs A0 and PANEL, exports the registry to web/src, rebuilds the
page and pushes, which deploys it. Own questions are skipped: they need a hand-built brief.

Every run costs money (about $0.31 measured on the first one), so there is a daily cap
(SWARMZ_QUEUE_MAX_PER_DAY, default 12) and a market that fails is never retried on its own:
delete its entry from data/queue_state.json to try again.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

SITE = os.environ.get("SUPERCAST_URL", "https://supercast-two.vercel.app").rstrip("/")
MAX_PER_DAY = int(os.environ.get("SWARMZ_QUEUE_MAX_PER_DAY", "12"))
MIN_BRIEF_WORDS = int(os.environ.get("SWARMZ_QUEUE_MIN_BRIEF_WORDS", "800"))
ARMS = "A0,PANEL"
STATE = ROOT / "data" / "queue_state.json"
LOCK = ROOT / "data" / "queue.lock"
TICKER = re.compile(r"^[A-Z0-9]{2,}(-[A-Z0-9.]+){1,4}$")
WEB_FILES = ["web/src/app_data.json", "web/src/admin_costs.json", "web/public/index.html", "web/lib/generated.js"]


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z  {msg}", flush=True)


def load_state() -> dict:
    try:
        return {"failed": {}, "started": [], **json.loads(STATE.read_text())}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"failed": {}, "started": []}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1))


def queued_tickers() -> list[str]:
    """Queued Kalshi tickers, oldest first, as the public API lists them. Rows there were typed by people: check them."""
    r = requests.get(f"{SITE}/api/state", timeout=30)
    r.raise_for_status()
    rows = (r.json().get("state") or {}).get("queued") or []
    rows = [q for q in rows if isinstance(q, dict) and q.get("kind") == "k" and isinstance(q.get("id"), str)
            and len(q["id"]) <= 64 and TICKER.match(q["id"])]
    return [q["id"] for q in sorted(rows, key=lambda q: str(q.get("at") or ""))]


def finished(con) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT DISTINCT question_id FROM runs WHERE arm = 'PANEL' AND status = 'complete'").fetchall()}


def freeze(con, ticker: str) -> str | None:
    """Brief the market and register it. Returns why it could not, or None."""
    from swarmz import cli, picker, usage
    from swarmz.registry import store

    if store.get_question(con, ticker):
        return None
    result, tracker = picker.build_brief(ticker, min_words=MIN_BRIEF_WORDS)
    cost = usage.save(con, tracker, context="brief", question_id=ticker)
    log(f"brief {ticker}: {result.get('status')}, {result.get('word_count')} words, ${cost:.4f}")
    if result.get("status") != "frozen":
        return f"brief {result.get('status')}: {str(result.get('reject_reason'))[:300]}"
    src = sqlite3.connect(picker.BRIEF_LOG)
    src.row_factory = sqlite3.Row
    row = src.execute("SELECT * FROM predictions WHERE question_id = ?", [ticker]).fetchone()
    if row is None or not cli._import_brief_row(con, row, picker.BRIEFS_DIR):
        return "brief frozen but not imported"
    return None


def sh(*cmd: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, **kw)


def publish(note: str) -> bool:
    """Export the registry into web/src, rebuild the page, and push if anything but the timestamp changed."""
    data, costs = ROOT / "web/src/app_data.json", ROOT / "web/src/admin_costs.json"
    old = (data.read_text(), costs.read_text()) if data.exists() and costs.exists() else None
    out = sh(sys.executable, "scripts/export_web.py", str(costs) + ".new")
    if out.returncode:
        log(f"export failed: {out.stderr[-500:]}")
        return False
    new_costs = Path(str(costs) + ".new")
    strip = lambda text: {k: v for k, v in json.loads(text).items() if k != "generated"}
    if old and strip(old[0]) == strip(out.stdout) and strip(old[1]) == strip(new_costs.read_text()):
        new_costs.unlink()
        log("site data unchanged")
        return False
    data.write_text(out.stdout)
    new_costs.replace(costs)
    for cmd in (["node", "web/build.mjs"], ["git", "add", "--", *WEB_FILES],
                ["git", "commit", "-q", "-m", f"Supercast data: {note}", "--", *WEB_FILES], ["git", "push", "-q", "origin", "HEAD:main"]):
        done = sh(*cmd)
        if done.returncode:
            log(f"{' '.join(cmd[:2])} failed: {(done.stderr or done.stdout)[-500:]}")
            return False
    log(f"site data pushed ({note})")
    return True


def main(argv: list[str]) -> int:
    force, dry, publish_only = "--force" in argv, "--dry-run" in argv, "--publish-only" in argv
    now = datetime.now(timezone.utc)
    if not (force or dry or publish_only) and now.hour % 2:
        return 0
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another queue run is still going")
        return 0
    if publish_only:
        publish("refresh")
        return 0

    from swarmz import cli
    from swarmz.registry.db import connect

    state = load_state()
    day_ago = (now - timedelta(hours=24)).isoformat()
    state["started"] = [t for t in state["started"] if t > day_ago]
    con = connect(wait_s=600)
    done = finished(con)
    todo = [t for t in queued_tickers() if t not in done and t not in state["failed"]]
    if not todo:
        con.close()
        log("nothing to run")
        if not dry:
            publish("refresh")      # outcomes may have settled since the last push
        return 0
    ticker = todo[0]
    if dry:
        log(f"would run {ticker}; {len(todo) - 1} more waiting; {len(state['started'])} started in the last 24h")
        return 0
    if len(state["started"]) >= MAX_PER_DAY:
        log(f"daily cap of {MAX_PER_DAY} reached; {ticker} waits")
        return 0
    state["started"].append(now.isoformat())
    save_state(state)
    log(f"running {ticker} ({len(todo) - 1} more waiting)")
    try:
        why = freeze(con, ticker)
        con.close()
        if why is None:
            cli.run(ticker, arms=ARMS, seed=0, workers=8)
            con = connect(wait_s=600)
            why = None if ticker in finished(con) else "run did not complete"
            con.close()
    except Exception as e:  # keep the schedule alive; the market is set aside below
        why = f"{type(e).__name__}: {str(e)[:300]}"
    if why:
        state["failed"][ticker] = {"at": now.isoformat(), "why": why}
        save_state(state)
        log(f"set aside {ticker}: {why}")
    publish(ticker if not why else "refresh")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
