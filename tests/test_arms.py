import shutil

import pytest

from swarmz.arms.runner import run_arm
from swarmz.registry import store
from swarmz.registry.db import rows
from tests.conftest import BRIEF, T0, add_q, fake_swarm

ARMS = ["A0", "PANEL", "A3", "A4"]
METHODS = {"equal", "weighted", "calibrated", "native"}


def test_full_run_all_arms(con, llm):
    store.seed_population(con, 6, "m")
    add_q(con, "Q1", T0)
    for arm in ARMS:
        out = run_arm(con, "Q1", arm, llm=llm, swarm=fake_swarm, started_at=T0)
        assert out.status == "complete", out.error
        fc = rows(con, "SELECT persona_id FROM forecasts WHERE run_id = ?", [out.run_id])
        expected = {p["persona_id"] for p in store.active_personas(con, arm)
                    if arm in ("A0", "A4") or p["status"] != "system"}
        assert {f["persona_id"] for f in fc} == expected
        methods = {r["method"] for r in rows(con, "SELECT method FROM aggregates WHERE run_id = ?", [out.run_id])}
        assert methods == (METHODS | {"prior", "public"} if arm == "PANEL" else METHODS)
        assert all(0.01 <= r["prob"] <= 0.99 for r in rows(con, "SELECT prob FROM forecasts"))
    runs = rows(con, "SELECT DISTINCT brief_sha256 FROM runs")
    assert len(runs) == 1 and runs[0]["brief_sha256"] == store.get_question(con, "Q1")["brief_sha256"]


def test_arm_refuses_changed_brief(con, llm, tmp_path):
    store.seed_population(con, 6, "m")
    brief = tmp_path / "b.md"
    shutil.copy(BRIEF, brief)
    store.add_question(con, question_id="Q2", question_text="q", freeze_ts=T0, brief_path=brief)
    brief.write_text("tampered")
    with pytest.raises(ValueError, match="sha256"):
        run_arm(con, "Q2", "A0", llm=llm, started_at=T0)
    assert rows(con, "SELECT * FROM runs") == []


def test_lessons_injected_only_before_freeze(con, llm):
    store.seed_population(con, 6, "m")
    add_q(con, "Q1", T0)
    pid = store.active_personas(con, "A3")[0]["persona_id"]
    con.execute("INSERT INTO lessons VALUES ('L1', ?, NULL, 'old habit', ?, 'active', NULL, NULL, ?)",
                [pid, T0.replace(year=2026), T0])
    con.execute("INSERT INTO lessons VALUES ('L2', ?, NULL, 'future habit', ?, 'active', NULL, NULL, ?)",
                [pid, T0.replace(year=2028), T0])
    seen = []
    orig = llm.json
    llm.json = lambda s, u, **k: (seen.append(u), orig(s, u, **k))[1]
    run_arm(con, "Q1", "A3", llm=llm, started_at=T0, workers=1)
    mine = [u for u in seen if "old habit" in u]
    assert len(mine) == 1 and not any("future habit" in u for u in seen)
