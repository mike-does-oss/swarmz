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
