"""Run swarmz with no machine of its own: a scheduled function does the work, Postgres keeps the state.

A serverless function has no disk that lasts, so between runs the registry (the DuckDB file) and the
frozen briefs are kept as one compressed archive in a Postgres table. Each tick takes a lock, unpacks
the archive into a scratch folder, works on it exactly as the CLI would on a laptop, and packs it back.
One writer at a time, enforced by the lock; nothing else in swarmz had to change.

Tables (same database as the Supercast site):
  engine_files(name, data)   the archive, under the name ARCHIVE
  engine_lock                one row; whoever holds it until locked_until may write
  site_data(key, value)      the JSON the site is built from: app_data (public) and admin_costs
  queue                      the site's run queue; this module moves rows queued -> running -> done | failed

A tick (see tick()): run the oldest queued Kalshi market (brief, single call, panel), record what
happened on its queue row, settle and score markets that have resolved, publish the site data and ask
the site to rebuild. Every run costs money, so there is a cap per 24 hours (SWARMZ_QUEUE_MAX_PER_DAY).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path

ARCHIVE = "registry.tar.gz"
ARMS = "A0,PANEL"
LOCK_MINUTES = 20
STALE_MINUTES = 30        # a row left "running" this long belongs to a tick that died
SCHEMA = """
create table if not exists engine_files (name text primary key, data bytea not null, updated_at timestamptz not null default now());
create table if not exists engine_lock (id int primary key check (id = 1), locked_until timestamptz not null, holder text);
insert into engine_lock (id, locked_until) values (1, 'epoch') on conflict do nothing;
create table if not exists site_data (key text primary key, value text not null, updated_at timestamptz not null default now());
"""


def work_dir() -> Path:
    return Path(os.environ.get("SWARMZ_CLOUD_DIR", "/tmp/swarmz"))


def briefs_dir() -> Path:
    return work_dir() / "briefbuilder" / "briefs_v2"


def use_work_dir() -> None:
    """Point swarmz at the scratch folder. Call before anything imports swarmz.config or swarmz.picker."""
    w = work_dir()
    w.mkdir(parents=True, exist_ok=True)
    os.environ["SWARMZ_DB_PATH"] = str(w / "swarmz.duckdb")
    os.environ["BRIEF_DATA_DIR"] = str(w / "briefbuilder")
    os.environ.setdefault("HOME", "/tmp")       # DuckDB wants a home folder it can write to


def connect_pg():
    import psycopg
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg.connect(url, autocommit=True)


def ensure_schema(pg) -> None:
    pg.execute(SCHEMA)


# ------------------------------------------------------------------ lock

def lock(pg, holder: str, minutes: int = LOCK_MINUTES) -> bool:
    return pg.execute(
        "update engine_lock set locked_until = now() + make_interval(mins => %s), holder = %s "
        "where id = 1 and locked_until < now() returning 1", [minutes, holder]).fetchone() is not None


def unlock(pg, holder: str) -> None:
    pg.execute("update engine_lock set locked_until = 'epoch', holder = null where id = 1 and holder = %s", [holder])


# ------------------------------------------------------------------ the archive

def pack(db_path: Path, briefs: Path) -> bytes:
    """The registry file and the briefs as one .tar.gz. Checkpoints first so the file is whole."""
    import duckdb
    con = duckdb.connect(str(db_path))
    con.execute("CHECKPOINT")
    con.close()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
        tar.add(db_path, arcname="swarmz.duckdb")
        for f in sorted(briefs.glob("*.md")) if briefs.exists() else []:
            tar.add(f, arcname=f"briefs_v2/{f.name}")
    return buf.getvalue()


def unpack(data: bytes, db_path: Path, briefs: Path) -> int:
    """Write the archive's registry to db_path and its briefs into briefs. Returns how many briefs."""
    n = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            name = Path(m.name)
            if not m.isfile() or name.is_absolute() or ".." in name.parts:
                continue
            src = tar.extractfile(m)
            if m.name == "swarmz.duckdb":
                db_path.parent.mkdir(parents=True, exist_ok=True)
                db_path.write_bytes(src.read())
            elif name.parts[0] == "briefs_v2" and len(name.parts) == 2 and name.suffix == ".md":
                briefs.mkdir(parents=True, exist_ok=True)
                (briefs / name.name).write_bytes(src.read())
                n += 1
    return n


def pull(pg, db_path: Path, briefs: Path) -> dict | None:
    row = pg.execute("select data, updated_at from engine_files where name = %s", [ARCHIVE]).fetchone()
    if row is None:
        return None
    for stale in (db_path, Path(str(db_path) + ".wal")):
        stale.unlink(missing_ok=True)
    return {"bytes": len(row[0]), "briefs": unpack(bytes(row[0]), db_path, briefs), "updated_at": str(row[1])}


def push(pg, db_path: Path, briefs: Path) -> int:
    data = pack(db_path, briefs)
    pg.execute("insert into engine_files (name, data) values (%s, %s) "
               "on conflict (name) do update set data = excluded.data, updated_at = now()", [ARCHIVE, data])
    return len(data)


# ------------------------------------------------------------------ the site's data

def _child_env() -> dict:
    return {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in [os.environ.get("PYTHONPATH", ""), *sys.path] if p)}


def publish(pg, log=print) -> bool:
    """Export the registry for the site. If anything but the timestamp changed, store it and ask the site to rebuild."""
    from .config import ROOT
    costs_file = work_dir() / "admin_costs.json"
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_web.py"), str(costs_file)],
                         cwd=ROOT, env=_child_env(), text=True, capture_output=True)
    if out.returncode:
        log(f"export failed: {out.stderr[-600:]}")
        return False
    new = {"app_data": out.stdout, "admin_costs": costs_file.read_text()}
    old = dict(pg.execute("select key, value from site_data where key in ('app_data', 'admin_costs')").fetchall())
    body = lambda text: {k: v for k, v in json.loads(text).items() if k != "generated"}
    if all(k in old and body(old[k]) == body(new[k]) for k in new):
        log("site data unchanged")
        return False
    for k, v in new.items():
        pg.execute("insert into site_data (key, value) values (%s, %s) "
                   "on conflict (key) do update set value = excluded.value, updated_at = now()", [k, v])
    hook = os.environ.get("SITE_DEPLOY_HOOK_URL")
    if hook:
        import requests
        r = requests.post(hook, timeout=30)
        log(f"site data stored; rebuild asked for ({r.status_code})")
    else:
        log("site data stored; SITE_DEPLOY_HOOK_URL is not set, so the site was not rebuilt")
    return True


# ------------------------------------------------------------------ watching a run on the site

class LiveRun:
    """Keeps site_data['live'] up to date while a market runs, in the shape the site replays a finished run from."""

    def __init__(self, pg, ticker: str, title: str | None):
        self.pg, self.rev = pg, 0
        self.doc = {"active": True, "stage": "brief", "ticker": ticker, "title": title or ticker,
                    "started": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), "run": None}
        self.save()

    def save(self) -> None:
        self.rev += 1
        self.doc["rev"] = self.rev
        self.pg.execute("insert into site_data (key, value) values ('live', %s) "
                        "on conflict (key) do update set value = excluded.value, updated_at = now()", [json.dumps(self.doc)])

    def __call__(self, kind: str, data: dict) -> None:
        import re
        run = self.doc["run"]
        if kind == "panel":
            now_ = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            self.doc["stage"] = "panel"
            self.doc["run"] = {
                "run_id": "live", "question_id": data["question_id"], "question": data["question"], "split": "",
                "market": data.get("market"), "outcome": None, "brier": None, "rounds": data["rounds"], "silent": 0,
                "a0": None, "aggregates": {}, "usage": [], "has_likes": True, "started": now_, "finished": now_,
                "secs": 0, "sd_alone": None, "sd_final": None, "posts": [], "live_done": -1,
                "agents": [{**a, "alone": None, "final": None, "why": ""} for a in data["agents"]]}
        elif kind == "thread" and run:
            names = {e["persona_id"]: e["name"] for e in data["thread"]}
            hit = lambda nm, text: bool(re.search(r"\b" + re.escape(nm) + r"\b", text))
            run["posts"] = [{"i": i, "round": e["round"], "agent": e["persona_id"], "prob": e["prob"], "text": e["text"],
                             "answers": [pid for pid, nm in names.items() if pid != e["persona_id"]
                                         and (hit(nm, e["text"]) or hit(nm.split()[-1], e["text"]))],
                             "likes": e.get("likes", [])} for i, e in enumerate(data["thread"])]
            opening = {e["persona_id"]: e["prob"] for e in data["thread"] if e["round"] == 0}
            order = [e["persona_id"] for e in data["thread"] if e["round"] == 0]
            for a in run["agents"]:
                a["name"], a["alone"] = names.get(a["id"]) or a["name"], opening.get(a["id"])
            run["agents"].sort(key=lambda a: order.index(a["id"]) if a["id"] in order else len(order))
            run["live_done"] = data["round"]             # the opening is step 0; round r is step r
        elif kind == "final" and run:
            got = {f["id"]: f for f in data["forecasts"]}
            for a in run["agents"]:
                f = got.get(a["id"])
                if f:
                    a["final"], a["why"] = f["prob"], f.get("why") or ""
            probs = [a["final"] for a in run["agents"] if a["final"] is not None]
            run["aggregates"] = {"equal": sum(probs) / len(probs)} if probs else {}
            run["live_done"] = run["rounds"] + 1
            run["finished"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        else:
            return
        self.save()

    def close(self, why: str | None) -> None:
        self.doc.update({"active": False, "stage": "failed" if why else "done", "failed": why})
        self.save()


# ------------------------------------------------------------------ one market

def finished(con) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT DISTINCT question_id FROM runs WHERE arm = 'PANEL' AND status = 'complete'").fetchall()}


def freeze(con, ticker: str, log=print, own: dict | None = None) -> str | None:
    """Brief the market and register it. Returns why it could not, or None. ``own`` is an own question's
    text, resolves date and criteria: it has no Kalshi market, so no market price, tags or automatic outcome."""
    import sqlite3
    from . import cli, picker, usage
    from .registry import store

    if store.get_question(con, ticker):
        return None
    result, tracker = picker.build_brief(ticker, min_words=int(os.environ.get("SWARMZ_QUEUE_MIN_BRIEF_WORDS", "800")), own=own)
    cost = usage.save(con, tracker, context="brief", question_id=ticker)
    log(f"brief {ticker}: {result.get('status')}, {result.get('word_count')} words, ${cost:.4f}")
    if result.get("status") != "frozen":
        return f"brief {result.get('status')}: {str(result.get('reject_reason'))[:300]}"
    src = sqlite3.connect(picker.BRIEF_LOG)
    src.row_factory = sqlite3.Row
    row = src.execute("SELECT * FROM predictions WHERE question_id = ?", [ticker]).fetchone()
    if own and row is not None:
        path = Path(picker.BRIEFS_DIR) / f"{row['brief_hash']}.md"
        if not path.exists():
            return "brief frozen but its file is missing"
        store.add_question(con, question_id=ticker, question_text=own["text"], resolution_criteria=own["criteria"],
                           category="other", freeze_ts=row["freeze_ts"], brief_path=path, source="manual")
        return None
    if row is None or not cli._import_brief_row(con, row, picker.BRIEFS_DIR):
        return "brief frozen but not imported"
    return None


def run_market(ticker: str, log=print, own: dict | None = None) -> str | None:
    """Brief, single call and panel for one Kalshi ticker or one own question. Returns why it failed, or None."""
    from . import cli
    from .registry.db import connect
    try:
        con = connect()
        why = freeze(con, ticker, log, own)
        con.close()
        if why:
            return why
        cli.run(ticker, arms=ARMS, seed=0, workers=8)
        con = connect()
        ok = ticker in finished(con)
        con.close()
        return None if ok else "run did not complete"
    except Exception as e:  # the tick must go on to save what was done
        return f"{type(e).__name__}: {str(e)[:300]}"


def next_queued(pg, done: set[str]) -> tuple[str | None, int]:
    """The oldest queued market or question, and how many more wait behind it. Marks rows the registry already finished."""
    rows = pg.execute("select id from queue where status = 'queued' order by at").fetchall()
    ids = [r[0] for r in rows]
    for t in [t for t in ids if t in done]:
        pg.execute("update queue set status = 'done', note = null where id = %s", [t])
    todo = [t for t in ids if t not in done]
    return (todo[0], len(todo) - 1) if todo else (None, 0)


# ------------------------------------------------------------------ the tick

def tick(*, run: bool = True, settle: bool = True, budget_s: float = 780, only: str | None = None, log=print) -> dict:
    """One scheduled pass. Returns a summary of what it did. With ``only``, the market to run is that queued
    ticker and no other (the admin view's Run now), not the oldest in the queue."""
    started, holder, out = time.monotonic(), uuid.uuid4().hex[:12], {"ran": None, "failed": None, "published": False}
    left = lambda: budget_s - (time.monotonic() - started)
    use_work_dir()
    from .config import settings
    db_path = settings().db_path
    pg = connect_pg()
    ensure_schema(pg)
    if not lock(pg, holder):
        log("another tick holds the lock")
        return {**out, "skipped": "locked"}
    try:
        pg.execute("update queue set status = 'failed', note = 'the run was cut off before it finished' "
                   "where status = 'running' and ran_at < now() - make_interval(mins => %s)", [STALE_MINUTES])
        got = pull(pg, db_path, briefs_dir())
        if got is None:
            log("no registry stored yet: run `swarmz cloud push` from a machine that has one")
            return {**out, "skipped": "no registry"}
        log(f"registry unpacked: {got['bytes']} bytes, {got['briefs']} briefs, saved {got['updated_at']}")
        from .registry.db import connect
        con = connect()
        done = finished(con)
        con.close()

        ticker, waiting = next_queued(pg, done)
        if only:
            ok = pg.execute("select 1 from queue where id = %s and status = 'queued'", [only]).fetchone()
            if not ok or only in done:
                log(f"{only} is not waiting in the queue")
            ticker, waiting = (only, max(0, waiting)) if ok and only not in done else (None, 0)
        cap = int(os.environ.get("SWARMZ_QUEUE_MAX_PER_DAY", "12"))
        recent = pg.execute("select count(*) from queue where ran_at > now() - interval '24 hours'").fetchone()[0]
        if not ticker:
            log("nothing queued")
        elif not run:
            log(f"would run {ticker}; {waiting} more waiting; {recent} started in the last 24h")
        elif recent >= cap:
            log(f"daily cap of {cap} reached; {ticker} waits")
        else:
            log(f"running {ticker} ({waiting} more waiting)")
            title = pg.execute("update queue set status = 'running', ran_at = now(), note = null where id = %s "
                               "returning text, kind, resolves, criteria", [ticker]).fetchone()
            own = {"text": title[0], "resolves": title[2], "criteria": title[3]} if title and title[1] == "q" else None
            from . import progress
            live = None
            try:                                 # showing the run on the site is optional; the run is not
                live = LiveRun(pg, ticker, title[0] if title else None)
                progress.set_sink(live)
            except Exception as e:
                log(f"live view unavailable: {type(e).__name__}: {str(e)[:200]}")
            try:
                why = run_market(ticker, log, own)
            finally:
                progress.set_sink(None)
            try:
                if live:
                    live.close(why)
            except Exception as e:
                log(f"live view not closed: {type(e).__name__}: {str(e)[:200]}")
            pg.execute("update queue set status = %s, note = %s where id = %s", ["failed" if why else "done", why, ticker])
            out["failed" if why else "ran"] = ticker
            if why:
                log(f"set aside {ticker}: {why}")
            log(f"registry saved: {push(pg, db_path, briefs_dir())} bytes")   # before anything else can go wrong

        if run and settle and left() > 240:      # settle what has resolved, score it, and learn from it
            from . import cli
            for name, step in (("settling", lambda: cli.resolve(set_=[], at=None, learn=False, seed=0, watch=False, every=600, forever=False)),
                               ("learning", lambda: cli.learn_cmd(seed=0))):
                try:                             # outcomes are saved even if learning from them then fails
                    step()
                    log(f"{name} done; registry saved: {push(pg, db_path, briefs_dir())} bytes")
                except Exception as e:
                    log(f"{name} failed: {type(e).__name__}: {str(e)[:300]}")
        if run:
            out["published"] = publish(pg, log)
        return out
    finally:
        unlock(pg, holder)
        pg.close()
