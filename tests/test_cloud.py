"""The archive the cloud runner keeps in Postgres: what goes in comes back out, and nothing else does."""
import io
import tarfile

import duckdb

from swarmz import cloud


def test_registry_and_briefs_round_trip(tmp_path):
    db, briefs = tmp_path / "a" / "swarmz.duckdb", tmp_path / "a" / "briefs_v2"
    briefs.mkdir(parents=True)
    con = duckdb.connect(str(db))
    con.execute("create table t (x int)")
    con.execute("insert into t values (41), (1)")
    con.close()
    (briefs / "abc.md").write_text("a frozen brief")
    (briefs / "notes.txt").write_text("not a brief")

    data = cloud.pack(db, briefs)
    db2, briefs2 = tmp_path / "b" / "swarmz.duckdb", tmp_path / "b" / "briefs_v2"
    assert cloud.unpack(data, db2, briefs2) == 1
    assert duckdb.connect(str(db2), read_only=True).execute("select sum(x) from t").fetchone()[0] == 42
    assert [p.name for p in briefs2.iterdir()] == ["abc.md"]
    assert (briefs2 / "abc.md").read_text() == "a frozen brief"


def test_unpack_writes_only_the_registry_and_briefs(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in ("../escape.md", "briefs_v2/../../escape2.md", "briefs_v2/sub/deep.md", "other/file.md", "briefs_v2/ok.md"):
            body = b"x"
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    out = tmp_path / "out"
    assert cloud.unpack(buf.getvalue(), out / "swarmz.duckdb", out / "briefs_v2") == 1
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()) == ["out/briefs_v2/ok.md"]


class FakePg:
    def __init__(self, queued):
        self.queued, self.done = list(queued), []

    def execute(self, sql, args=()):
        if sql.startswith("select id from queue"):
            return type("R", (), {"fetchall": lambda s: [(t,) for t in self.queued]})()
        assert sql.startswith("update queue set status = 'done'")
        self.done.append(args[0])


def test_next_queued_skips_and_marks_markets_the_registry_already_finished():
    pg = FakePg(["KXOLD-T1", "KXNEW-T1", "KXNEW-T2"])
    assert cloud.next_queued(pg, {"KXOLD-T1"}) == ("KXNEW-T1", 1)
    assert pg.done == ["KXOLD-T1"]
    assert cloud.next_queued(FakePg([]), set()) == (None, 0)


class LivePg:
    def __init__(self):
        self.saved = []

    def execute(self, sql, args=()):
        import json
        self.saved.append(json.loads(args[0]))


def test_live_run_follows_a_run_in_the_shape_the_site_replays():
    pg = LivePg()
    live = cloud.LiveRun(pg, "KXT-1", "Will it?")
    assert (pg.saved[-1]["stage"], pg.saved[-1]["run"], pg.saved[-1]["active"]) == ("brief", None, True)
    live("panel", {"question_id": "KXT-1", "question": "Will it?", "market": 0.4, "rounds": 2,
                   "agents": [{"id": "p2", "name": "", "method": "m2", "challenger": True, "lenses": ["facts"]},
                              {"id": "p1", "name": "", "method": "m1", "challenger": False, "lenses": ["numbers"]}]})
    thread = [{"round": 0, "persona_id": "p1", "name": "Sober Newt", "prob": 0.3, "text": "alone"},
              {"round": 0, "persona_id": "p2", "name": "Calm Vole", "prob": 0.6, "text": "alone too"}]
    live("thread", {"round": 0, "rounds": 2, "thread": thread})
    run = pg.saved[-1]["run"]
    assert [(a["id"], a["name"], a["alone"]) for a in run["agents"]] == [("p1", "Sober Newt", 0.3), ("p2", "Calm Vole", 0.6)]
    assert (run["live_done"], len(run["posts"])) == (0, 2)
    thread = thread + [{"round": 1, "persona_id": "p2", "name": "Calm Vole", "prob": 0.5, "text": "Newt has a point", "likes": ["p1"]}]
    live("thread", {"round": 1, "rounds": 2, "thread": thread})
    post = pg.saved[-1]["run"]["posts"][-1]
    assert (post["answers"], post["likes"], pg.saved[-1]["run"]["live_done"]) == (["p1"], ["p1"], 1)
    live("final", {"forecasts": [{"id": "p1", "prob": 0.4, "why": "a"}, {"id": "p2", "prob": 0.5, "why": "b"}]})
    run = pg.saved[-1]["run"]
    assert (run["live_done"], round(run["aggregates"]["equal"], 3), run["agents"][0]["final"]) == (3, 0.45, 0.4)
    live.close(None)
    assert (pg.saved[-1]["active"], pg.saved[-1]["stage"]) == (False, "done")
    assert [d["rev"] for d in pg.saved] == list(range(1, len(pg.saved) + 1))


def test_an_own_question_is_registered_from_its_brief_with_no_market(tmp_path, monkeypatch):
    import sqlite3
    from swarmz import picker, usage
    from swarmz.registry import store
    from swarmz.registry.db import connect

    briefs = tmp_path / "briefs_v2"
    briefs.mkdir()
    (briefs / "abc123.md").write_text("a frozen brief about the question")
    log = tmp_path / "shoals.db"
    src = sqlite3.connect(log)
    src.execute("create table predictions (question_id text, brief_hash text, freeze_ts text)")
    src.execute("insert into predictions values ('q-0123456789abcd', 'abc123', '2026-11-10T12:00:00Z')")
    src.commit()
    monkeypatch.setattr(picker, "BRIEF_LOG", log)
    monkeypatch.setattr(picker, "BRIEFS_DIR", briefs)
    asked = {}

    def build_brief(ticker, min_words=None, own=None):
        asked.update(ticker=ticker, own=own)
        return {"status": "frozen", "word_count": 6}, usage.Tracker()
    monkeypatch.setattr(picker, "build_brief", build_brief)

    con = connect(":memory:")
    own = {"text": "Will it happen by spring?", "resolves": "2027-03-01", "criteria": "The ministry says so in writing."}
    assert cloud.freeze(con, "q-0123456789abcd", log=lambda m: None, own=own) is None
    assert asked == {"ticker": "q-0123456789abcd", "own": own}
    q = store.get_question(con, "q-0123456789abcd")
    assert (q["question_text"], q["resolution_criteria"], q["source"], q["market_prob"]) == \
        ("Will it happen by spring?", "The ministry says so in writing.", "manual", None)
    assert store.read_brief(q) == b"a frozen brief about the question"


class _Kalshi:
    def __init__(self, market):
        self.market = market

    def get(self, url, timeout=None):
        market = self.market
        return type("R", (), {"json": lambda self: {"market": market}})()


def test_a_market_that_has_closed_or_settled_is_not_run():
    from datetime import datetime, timezone
    at = datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc)
    open_ = {"status": "active", "result": "", "close_time": "2026-11-11T03:00:00Z"}
    assert cloud.closed_reason("KXA-26NOV10-B", _Kalshi(open_), at) is None
    assert "settled (YES)" in cloud.closed_reason("KXA-26NOV10-B", _Kalshi({**open_, "status": "finalized", "result": "yes"}), at)
    assert "closed" in cloud.closed_reason("KXA-26NOV10-B", _Kalshi({**open_, "status": "closed"}), at)
    assert "closed" in cloud.closed_reason("KXA-26NOV10-B", _Kalshi({**open_, "close_time": "2026-11-10T18:01:27Z"}), at)


def test_an_own_question_or_an_unreachable_kalshi_does_not_stop_a_run():
    class Down:
        def get(self, url, timeout=None):
            raise OSError("no network")
    assert cloud.closed_reason("q-0123456789abcd", Down()) is None
    assert cloud.closed_reason("KXA-26NOV10-B", Down()) is None


def test_a_run_that_failed_because_a_provider_did_is_held_not_failed():
    held = cloud.provider_trouble
    assert "search provider has reached its usage limit" in held(
        "brief rejected: provider_error:search:HTTPError: 402 Client Error: Payment Required for url:")
    assert "plans the searches is out of credit" in held(
        "brief rejected: provider_error:planner:BadRequestError: Your credit balance is too low to access the API")
    assert "forecasting model is out of credit" in held("BadRequestError: Error code: 400 - Your credit balance is too low")
    assert "could not be reached" in held("brief rejected: provider_error:search:ConnectionError: timed out")
    # a fault of the question or the brief still fails the run
    for why in (None, "brief rejected: empty_query_set", "brief rejected: no_claims:3_docs",
                "brief rejected: word_count_out_of_range:3018", "run did not complete"):
        assert held(why) is None


def test_the_cloud_writes_reports_to_its_scratch_folder(tmp_path, monkeypatch):
    for name in ("SWARMZ_DB_PATH", "BRIEF_DATA_DIR", "SWARMZ_REPORTS_DIR", "HOME"):
        monkeypatch.setenv(name, "unset")
    monkeypatch.setenv("SWARMZ_CLOUD_DIR", str(tmp_path / "work"))
    cloud.use_work_dir()
    import os
    assert os.environ["SWARMZ_REPORTS_DIR"] == str(tmp_path / "work" / "reports")
