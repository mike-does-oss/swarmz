"""swarmz CLI: health, personas, questions, run, resolve, learn, evolve, report, backtest."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from .config import settings
from .registry import store
from .registry.db import connect, rows
from .timeutil import now, utc

app = typer.Typer(no_args_is_help=True, add_completion=False, help="swarmz: a local forecasting swarm that learns from resolved questions.")
personas_app = typer.Typer(no_args_is_help=True, help="Persona registry.")
questions_app = typer.Typer(no_args_is_help=True, help="Questions and frozen briefs.")
app.add_typer(personas_app, name="personas")
app.add_typer(questions_app, name="questions")
console = Console()


def _llm():
    from .llm import default_llm
    return default_llm()


# ------------------------------------------------------------------ health

@app.command()
def health():
    """Check the DuckDB registry, the panel and the API key."""
    ok = True
    try:
        con = connect()
        n = con.execute("SELECT count(*) FROM personas").fetchone()[0]
        console.print(f"[green]ok[/]  DuckDB registry: {settings().db_path} ({n} personas)")
        panel = store.count_personas(con, "PANEL", ("active", "elite"))
        if panel:
            console.print(f"[green]ok[/]  panel: {panel} personas")
        else:
            console.print("[yellow]warn[/] panel: no personas yet; run `swarmz personas seed`")
    except Exception as e:
        ok = False
        console.print(f"[red]FAIL[/] DuckDB registry: {e}")
    present = bool(os.environ.get("ANTHROPIC_API_KEY"))
    ok &= present
    console.print(f"[{'green' if present else 'red'}]{'ok' if present else 'FAIL'}[/]  ANTHROPIC_API_KEY "
                  "(forecasts, discussion, lessons)")
    raise typer.Exit(0 if ok else 1)


@app.command()
def init():
    """Create or migrate the registry."""
    connect()
    console.print(f"registry ready at {settings().db_path}")


# ---------------------------------------------------------------- personas

@personas_app.command("seed")
def personas_seed(size: int = typer.Option(None, help="Personas per population (default SWARMZ_POPULATION_SIZE)"),
                  seed: int = 0, model: str = typer.Option(None)):
    """Create generation-0 personas for the panel (and A3, the same prompts forecasting alone) plus
    the A0/A4 baselines. A population that already has personas is left as it is."""
    s = settings()
    con = connect()
    todo = [p for p in store.POPULATIONS if not store.count_personas(con, p, ("active", "elite"))]
    if not todo:
        console.print("every population is already seeded")
        return
    out = store.seed_population(con, size or s.population_size, model or s.agent_model, seed, populations=todo)
    for pop, ids in out.items():
        console.print(f"{pop}: {len(ids)} personas")


@personas_app.command("list")
def personas_list(population: Optional[str] = None, all: bool = typer.Option(False, help="Include retired")):
    from .registry.names import ensure_names
    con = connect()
    ensure_names(con)
    where, params = [], []
    if population:
        where.append("population = ?"); params.append(population)
    if not all:
        where.append("status != 'retired'")
    sql = ("SELECT persona_id, display_name AS name, population, generation, status, parent_id, "
           "left(base_prompt, 80) AS prompt FROM personas")
    if where:
        sql += " WHERE " + " AND ".join(where)
    from .scoring.report import _table
    console.print(_table("Personas", rows(con, sql + " ORDER BY population, generation, persona_id", params)))


# --------------------------------------------------------------- questions

@questions_app.command("add")
def questions_add(question_id: str, question_text: str = typer.Option(..., "--text"),
                  freeze_ts: str = typer.Option(..., "--freeze-ts"), brief: Path = typer.Option(..., exists=True),
                  category: str = "other", split: Optional[str] = None, source: str = "manual",
                  criteria: str = typer.Option("", "--criteria")):
    """Register a question with its frozen brief file."""
    con = connect()
    q = store.add_question(con, question_id=question_id, question_text=question_text, freeze_ts=freeze_ts,
                           brief_path=brief, category=category, split=split, source=source,
                           resolution_criteria=criteria)
    console.print(f"added {q['question_id']} ({q['split']}, brief {q['brief_sha256'][:12]})")


@questions_app.command("import-briefs")
def questions_import_briefs(db: Optional[Path] = typer.Option(None, help="Brief builder prediction log (SQLite); default: its data/shoals.db"),
                            briefs_dir: Optional[Path] = typer.Option(None, help="Frozen briefs folder; default: its data/briefs_v2")):
    """Import frozen questions from the brief builder's prediction log (Kalshi tickers)."""
    from . import picker
    db, briefs_dir = db or picker.BRIEF_LOG, briefs_dir or picker.BRIEFS_DIR
    if not Path(db).exists():
        raise typer.BadParameter(f"prediction log not found: {db}")
    con = connect()
    src = sqlite3.connect(db)
    src.row_factory = sqlite3.Row
    n = sum(_import_brief_row(con, r, briefs_dir) for r in src.execute("SELECT * FROM predictions"))
    console.print(f"imported {n} questions")


def _import_brief_row(con, r, briefs_dir: Path) -> bool:
    if store.get_question(con, r["question_id"]):
        return False
    path = Path(briefs_dir) / f"{r['brief_hash']}.md"
    if not path.exists():
        console.print(f"[yellow]skip[/] {r['question_id']}: brief {path.name} missing")
        return False
    store.add_question(con, question_id=r["question_id"], question_text=r["question_text"],
                       resolution_criteria=r["resolution_criteria"], category=r["category"],
                       freeze_ts=r["freeze_ts"], brief_path=path, source="kalshi")
    _auto_tag(con, r["question_id"], market_mid=r["market_mid"] if "market_mid" in r.keys() else None)
    _record_market(con, r["question_id"])
    return True


def _record_market(con, question_id: str) -> float | None:
    """Store the market's price for YES at the question's freeze time (a scoring baseline)."""
    from . import picker
    q = store.get_question(con, question_id)
    try:
        got = picker.market_prob_at(question_id, q["freeze_ts"])
    except Exception as e:  # a baseline, never a reason to block an import
        console.print(f"  [yellow]no market price for {question_id}[/]: {e}")
        return None
    if got is None:
        console.print(f"  [yellow]no market price for {question_id}[/]: no quote before the freeze")
        return None
    store.set_market_prob(con, question_id, got[0], got[1])
    console.print(f"  market at freeze for {question_id}: {got[0]:.2f}")
    return got[0]


@questions_app.command("market")
def questions_market(question_id: Optional[str] = typer.Argument(None, help="One question (default: every Kalshi question without a price)")):
    """Record the market's price at freeze time, read from Kalshi's price history. Used only to score against."""
    con = connect()
    qids = [question_id] if question_id else [r["question_id"] for r in rows(
        con, "SELECT question_id FROM questions WHERE source = 'kalshi' AND market_prob IS NULL ORDER BY freeze_ts")]
    for qid in qids:
        _record_market(con, qid)


@questions_app.command("pick")
def questions_pick(min_hours: float = typer.Option(4, help="Earliest expected resolution, hours from now (games under ~4h may be under way)"),
                   max_hours: float = typer.Option(36, help="Latest expected resolution, hours from now"),
                   n: int = typer.Option(5, help="How many questions"),
                   max_per_category: int = typer.Option(2, help="Cap per Kalshi category"),
                   min_price: float = 10, max_price: float = 90, min_volume: float = 1000,
                   build: bool = typer.Option(False, help="Freeze a brief for each pick and import it"),
                   min_brief_words: int = typer.Option(800, help="Minimum brief length (builder default 1500; short-dated questions have less news)"),
                   run_arms: str = typer.Option("", "--run", help="Then run these arms, e.g. A0,PANEL")):
    """Pick short-dated Kalshi markets (one per event, mixed categories); optionally brief, import and run them."""
    from . import picker, usage
    from .scoring.report import _table

    con = connect()
    brief_log = picker.BRIEF_LOG
    exclude = {r["question_id"] for r in rows(con, "SELECT question_id FROM questions")}
    # also skip other markets of the same event or game as any question already registered
    exclude_events = {picker.event_of(q) for q in exclude} | {picker.occasion(picker.event_of(q)) for q in exclude}
    if brief_log.exists():
        exclude |= {r[0] for r in sqlite3.connect(brief_log).execute("SELECT question_id FROM predictions")}
    cands = picker.candidates(picker.fetch_markets(min_hours, max_hours), min_hours=min_hours, max_hours=max_hours,
                              min_price=min_price, max_price=max_price, min_volume=min_volume, exclude=exclude,
                              exclude_events=exclude_events)
    chosen = picker.pick(cands, n, max_per_category, min_hours=min_hours)
    console.print(f"{len(cands)} eligible events resolving in {min_hours:g}-{max_hours:g}h; picked {len(chosen)}")
    console.print(_table("Picks", [{"ticker": c.ticker, "category": c.category, "series": c.series,
                                    "resolves_in_h": c.hours_left, "title": c.title[:60]} for c in chosen]))
    if not build:
        return
    frozen = []
    for c in chosen:
        console.print(f"[bold]brief[/] {c.ticker} ...")
        result, tracker = picker.build_brief(c.ticker, min_words=min_brief_words)
        cost = usage.save(con, tracker, context="brief", question_id=c.ticker)
        if result.get("status") != "frozen":
            console.print(f"  [yellow]{result.get('status')}[/]: {result.get('reject_reason')}  (cost ${cost:.4f})")
            continue
        src = sqlite3.connect(brief_log)
        src.row_factory = sqlite3.Row
        row = src.execute("SELECT * FROM predictions WHERE question_id = ?", [c.ticker]).fetchone()
        if row is None or not _import_brief_row(con, row, picker.BRIEFS_DIR):
            console.print(f"  [red]frozen but not imported[/]")
            continue
        console.print(f"  [green]frozen[/] {result.get('word_count')} words; cost ${cost:.4f}")
        frozen.append(c.ticker)
    if run_arms and frozen:
        con.close()
        for qid in frozen:
            run(qid, arms=run_arms, seed=0, workers=8)


def _auto_tag(con, question_id: str, market_mid=None) -> None:
    from . import tags
    try:
        got = tags.tag_from_kalshi(con, question_id)
        console.print(f"  tagged {question_id}: " + "; ".join(f"{k}={','.join(v)}" for k, v in got.items()))
    except Exception as e:  # tagging is metadata; never block an import on it
        console.print(f"  [yellow]could not tag {question_id} from Kalshi[/]: {e}")
    bucket = tags.market_bucket(market_mid)
    if bucket:
        tags.set_tags(con, question_id, [("market_bucket", bucket)], "brief_builder", replace_keys=True)


@questions_app.command("tag")
def questions_tag(question_id: str, pairs: list[str] = typer.Argument(None, help="key=value tags to add"),
                  remove: list[str] = typer.Option([], "--remove", help="key or key=value to remove (repeatable)")):
    """Add or remove tags on a question; with no arguments, show its tags."""
    from . import tags
    con = connect()
    if store.get_question(con, question_id) is None:
        raise typer.BadParameter(f"unknown question {question_id}")
    for item in remove:
        key, _, value = item.partition("=")
        tags.remove_tag(con, question_id, key, value or None)
    if pairs:
        tags.set_tags(con, question_id, tags.parse_pairs(pairs), "manual")
    for k, v in tags.tags_for(con, question_id).items():
        console.print(f"{k}: {', '.join(v)}")


@questions_app.command("retag")
def questions_retag(question_id: Optional[str] = typer.Argument(None, help="One question (default: all Kalshi questions)")):
    """Re-fetch Kalshi tags (category, topic, series, frequency, horizon, strike type)."""
    con = connect()
    ids = [question_id] if question_id else [r["question_id"] for r in rows(
        con, "SELECT question_id FROM questions WHERE source = 'kalshi' ORDER BY question_id")]
    for qid in ids:
        _auto_tag(con, qid)


@questions_app.command("exclude")
def questions_exclude(question_ids: list[str] = typer.Argument(..., help="Questions to set aside"),
                      reason: str = typer.Option(..., help="Why, e.g. 'pilot: swarm arms did not see the brief'")):
    """Set questions aside: results stay on record, but nothing learns from them and they count in
    neither train nor holdout results. Their lessons are retired."""
    con = connect()
    for qid in question_ids:
        if store.get_question(con, qid) is None:
            raise typer.BadParameter(f"unknown question {qid}")
    for qid in question_ids:
        out = store.exclude_question(con, qid, reason)
        console.print(f"{qid}: {out['was']} to excluded; {out['lessons_retired']} lessons retired, "
                      f"{out['fits_dropped']} calibration fits dropped")


@questions_app.command("list")
def questions_list():
    con = connect()
    from .scoring.report import _table
    console.print(_table("Questions", rows(con, """SELECT question_id, category, split, status, outcome,
        freeze_ts, resolve_ts, left(question_text, 60) AS text FROM questions ORDER BY freeze_ts""")))


# --------------------------------------------------------------------- run

@app.command()
def run(question_id: str, arms: str = typer.Option("A0,PANEL", help="Comma-separated arms"),
        seed: int = 0, workers: int = 8):
    """Run arms on one question with its frozen brief."""
    from .arms.runner import ARMS, run_arm
    con = connect(wait_s=1800)   # the resolve watcher can hold the registry for minutes while it writes lessons
    for arm in [a.strip() for a in arms.split(",") if a.strip()]:
        if arm not in ARMS:
            raise typer.BadParameter(f"unknown arm {arm}; choose from {', '.join(ARMS)}")
        console.print(f"[bold]{arm}[/] on {question_id} ...")
        out = run_arm(con, question_id, arm, llm=_llm(), seed=seed, workers=workers)
        if out.status == "complete":
            aggs = ", ".join(f"{k}={v:.3f}" for k, v in out.aggregates.items() if v is not None)
            console.print(f"  [green]complete[/] run {out.run_id}: {out.n_forecasts} forecasts; {aggs}")
            sp = out.spread or {}
            if sp.get("n", 0) > 1:
                before = f"; sd before the discussion {sp['prior_sd']:.3f}" if "prior_sd" in sp else ""
                console.print(f"  spread: {sp['low']:.2f} to {sp['high']:.2f}, sd {sp['sd']:.3f}; "
                              f"{sp['yes_side']} above 50%, {sp['no_side']} below{before}")
        else:
            console.print(f"  [red]failed[/] run {out.run_id}: {out.error}")
        console.print(f"  cost ${out.cost_usd or 0:.4f}")


# ----------------------------------------------------------------- resolve

@app.command()
def resolve(set_: list[str] = typer.Option([], "--set", help="Manual outcome QID=1|0|void (repeatable)"),
            at: Optional[str] = typer.Option(None, help="Resolution timestamp for --set (default now)"),
            learn: bool = typer.Option(True, help="Run lessons / calibration / selection afterwards"),
            seed: int = 0,
            watch: bool = typer.Option(False, help="Keep polling Kalshi until every open question has resolved"),
            every: int = typer.Option(600, help="Seconds between polls with --watch"),
            forever: bool = typer.Option(False, help="With --watch, keep running even when nothing is open")):
    """Record outcomes (Kalshi for kalshi questions, or --set), score, then learn."""
    from .scoring.resolve import Resolution, apply, resolve_open
    if watch:
        return _watch_resolve(every, forever, learn, seed)
    con = connect()
    if set_:
        for item in set_:
            qid, _, val = item.partition("=")
            void = val.strip().lower() == "void"
            res = Resolution(qid, None if void else int(val), utc(at) if at else now(), void=void)
            n = apply(con, res)
            console.print(f"{qid}: {'void' if void else val} ({n} scores)")
    else:
        for r in resolve_open(con):
            console.print(f"{r.question_id}: {'void' if r.void else r.outcome} at {r.resolve_ts}")
    if learn:
        learn_cmd(seed=seed)


def _watch_resolve(every: int, forever: bool, learn: bool, seed: int) -> None:
    """Poll Kalshi; on each settlement score and learn. The registry is opened only briefly per poll,
    so `swarmz run` can use it in between (DuckDB allows one writer process at a time)."""
    import time

    import duckdb

    from .learning.hooks import run_learning
    from .scoring.resolve import resolve_open

    console.print(f"watching Kalshi every {every}s (Ctrl-C to stop)")
    while True:
        try:
            con = connect()
        except duckdb.IOException as e:  # another swarmz process holds the lock; try next poll
            console.print(f"{now():%H:%M} registry busy, retrying later ({str(e)[:80]})")
            time.sleep(every)
            continue
        try:
            done = resolve_open(con)
            for r in done:
                console.print(f"{now():%H:%M} {r.question_id}: {'void' if r.void else r.outcome} at {r.resolve_ts}")
            if done and learn:
                rep = run_learning(con, now(), _llm(), seed=seed)
                console.print(f"  learned: {len(rep['lessons'])} lesson sets, fits={list(rep['fits'])}, "
                              f"evolve={rep['evolve']}, cost ${rep.get('cost_usd', 0):.4f}")
            open_n = con.execute("SELECT count(*) FROM questions WHERE status = 'open' AND source = 'kalshi'"
                                 ).fetchone()[0]
        finally:
            con.close()
        if open_n == 0 and not forever:
            console.print("no open Kalshi questions left; stopping")
            return
        console.print(f"{now():%H:%M} {open_n} still open")
        time.sleep(every)


@app.command("learn")
def learn_cmd(seed: int = 0):
    """Run post-resolution learning now (lessons, calibration refits, selection when due)."""
    from .learning.hooks import run_learning
    con = connect()
    rep = run_learning(con, now(), _llm(), seed=seed)
    console.print_json(json.dumps(rep, default=str))


@app.command()
def evolve(population: str = typer.Option("PANEL"), seed: int = 0, dry_run: bool = False):
    """Selection + mutation for one population (ignores the select_every schedule)."""
    from .learning.selection import evolve as do_evolve
    from . import usage
    con = connect()
    with usage.track() as u:
        p = do_evolve(con, population, now(), seed, _llm() if not dry_run else None, dry_run=dry_run)
    usage.save(con, u, context="evolve")
    if p.skipped:
        console.print(f"[yellow]skipped[/]: {p.skipped}")
        return
    for e in p.events:
        console.print(f"  {e['action']:8} {e['persona_id']}  {e['reason']}")


@app.command()
def report(section: list[str] = typer.Option(["leaderboard", "arms", "baselines", "spread", "extremize",
                                              "calibration", "curve", "tags", "cost"]),
           population: Optional[str] = None,
           tag: str = typer.Option("category", help="Tag key for the 'tags' section (category, topic, horizon, ...)")):
    """Leaderboard, arm comparison, baselines (always 50%, one call, the market), spread per question,
    extremizing looked at backwards, calibration bins, learning curve, results by tag, cost."""
    from .scoring.report import print_report
    print_report(connect(), tuple(section), population, console, tag=tag)


@app.command()
def backtest(file: Path = typer.Argument(..., exists=True), arms: str = "A0,PANEL", seed: int = 0):
    """Replay historical resolved questions (JSONL) in time order."""
    from .backtest import load, run_backtest
    con = connect()
    rep = run_backtest(con, load(file), arms=[a.strip() for a in arms.split(",")], llm=_llm(), seed=seed,
                       on_event=console.print)
    for qid, why in rep.refused.items():
        console.print(f"[yellow]refused[/] {qid}: {why}")
    console.print(f"{len(rep.runs)} runs, {len(rep.refused)} refused")



# ------------------------------------------------------------------- cloud

cloud_app = typer.Typer(no_args_is_help=True, help="The copy of the registry kept in Postgres for the scheduled cloud runs.")
app.add_typer(cloud_app, name="cloud")


def _local_briefs() -> Path:
    from . import cloud
    return cloud.briefs_dir()   # briefs are recorded by absolute path, so they live at the cloud's path here too


@cloud_app.command("status")
def cloud_status():
    """What is stored, who holds the lock, and the queue."""
    from . import cloud
    pg = cloud.connect_pg()
    cloud.ensure_schema(pg)
    f = pg.execute("select octet_length(data), updated_at from engine_files where name = %s", [cloud.ARCHIVE]).fetchone()
    console.print(f"registry: {f[0] / 1e6:.1f} MB, saved {f[1]:%Y-%m-%d %H:%M} UTC" if f else "registry: nothing stored")
    lk = pg.execute("select locked_until > now(), locked_until, holder from engine_lock where id = 1").fetchone()
    console.print(f"lock: held by {lk[2]} until {lk[1]:%H:%M:%S} UTC" if lk and lk[0] else "lock: free")
    for status, n in pg.execute("select status, count(*) from queue group by 1 order by 1").fetchall():
        console.print(f"queue {status}: {n}")
    for key, at in pg.execute("select key, updated_at from site_data order by 1").fetchall():
        console.print(f"site data {key}: stored {at:%Y-%m-%d %H:%M} UTC")


@cloud_app.command("push")
def cloud_push(yes: bool = typer.Option(False, "--yes", help="Replace the stored registry without asking")):
    """Store this machine's registry as the cloud's. Replaces what is there."""
    from . import cloud
    pg = cloud.connect_pg()
    cloud.ensure_schema(pg)
    if not yes and pg.execute("select 1 from engine_files where name = %s", [cloud.ARCHIVE]).fetchone():
        typer.confirm("A registry is already stored. Replace it with this machine's?", abort=True)
    if not cloud.lock(pg, "cli-push"):
        raise typer.BadParameter("a cloud run holds the lock; try again when it has finished")
    try:
        n = cloud.push(pg, settings().db_path, _local_briefs())
    finally:
        cloud.unlock(pg, "cli-push")
    console.print(f"stored {n / 1e6:.1f} MB")


@cloud_app.command("pull")
def cloud_pull():
    """Replace this machine's registry with the cloud's. The old file is kept beside it as .before-pull."""
    import shutil
    from . import cloud
    pg = cloud.connect_pg()
    db = settings().db_path
    if db.exists():
        shutil.copy2(db, str(db) + ".before-pull")
    got = cloud.pull(pg, db, _local_briefs())
    if got is None:
        raise typer.BadParameter("nothing is stored yet")
    console.print(f"pulled {got['bytes'] / 1e6:.1f} MB and {got['briefs']} briefs, saved {got['updated_at']}")


@cloud_app.command("tick")
def cloud_tick(dry_run: bool = typer.Option(False, help="Say what would run, and change nothing")):
    """Do one scheduled pass from this machine, in the scratch folder. Costs money if a market is queued."""
    from . import cloud
    console.print(cloud.tick(run=not dry_run, log=console.print))


if __name__ == "__main__":
    app()
