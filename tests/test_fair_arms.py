"""Arms differ only in what they are meant to: every arm's agents read the same brief when asked
for a probability, and a run started after the outcome was known is never scored."""

from datetime import timedelta

from swarmz.arms.runner import run_arm
from swarmz.registry import store
from swarmz.registry.db import rows
from swarmz.scoring.brier import score_question
from swarmz.scoring.report import arm_comparison
from tests.conftest import BRIEF, T0, FakeLLM, add_q, fake_swarm


class RecordingLLM(FakeLLM):
    def __init__(self):
        super().__init__()
        self.users = []

    def json(self, system, user, **kw):
        if system.startswith("You are one forecaster"):
            self.users.append(user)
        return super().json(system, user, **kw)


def test_every_arm_reads_the_brief_at_elicitation(con):
    store.seed_population(con, 6, "claude-haiku-5-5")
    add_q(con, "Q1", T0)
    brief = BRIEF.read_text()     # no fixed headings, so a panel member's part of it is all of it
    for arm in ("A0", "PANEL", "A3", "A4"):
        llm = RecordingLLM()
        run_arm(con, "Q1", arm, llm=llm, swarm=fake_swarm)
        assert llm.users, arm
        assert all(brief in u for u in llm.users), f"{arm} agents were asked without the brief"
    # panel members are asked twice: alone before the discussion, then with it
    llm = RecordingLLM()
    run_arm(con, "Q1", "PANEL", llm=llm, swarm=fake_swarm, workers=1)
    alone, after = llm.users[:6], llm.users[6:]
    assert len(after) == 6 and not any("THE PANEL'S DISCUSSION" in u for u in alone)
    assert all("THE PANEL'S DISCUSSION" in u and "the committee is data dependent" in u for u in after)


def _run(con, run_id, started_at, prob):
    store.start_run(con, run_id=run_id, question_id="Q1", arm="A3", generation=0, seed=0, config={},
                    model_versions={}, brief_sha256="x", started_at=started_at)
    store.finish_run(con, run_id, "complete")
    store.write_forecast(con, run_id=run_id, question_id="Q1", persona_id="a", prob=prob, rationale="", elicited_at=started_at)
    store.write_aggregate(con, run_id=run_id, question_id="Q1", method="equal", prob=prob, computed_at=started_at)


def test_runs_started_after_resolution_are_marked_late_and_not_scored(con):
    add_q(con, "Q1", T0)
    _run(con, "early", T0, 0.8)
    _run(con, "late", T0 + timedelta(days=3), 0.99)
    store.set_outcome(con, "Q1", 1, T0 + timedelta(days=1))
    score_question(con, "Q1")
    assert {r["run_id"] for r in rows(con, "SELECT DISTINCT run_id FROM scores")} == {"early"}
    assert dict(con.execute("SELECT run_id, status FROM runs").fetchall()) == {"early": "complete", "late": "late"}
    equal = [r for r in arm_comparison(con) if r["method"] == "equal"]
    assert len(equal) == 1 and abs(equal[0]["mean_brier"] - 0.04) < 1e-9   # the report uses the early run
    store.set_outcome(con, "Q1", 1, T0 + timedelta(days=9))                  # a corrected time puts it back
    score_question(con, "Q1")
    assert {r["run_id"] for r in rows(con, "SELECT DISTINCT run_id FROM scores")} == {"early", "late"}


def test_run_on_a_resolved_question_is_late_at_once(con, llm):
    store.seed_population(con, 6, "claude-haiku-5-5")
    add_q(con, "Q1", T0)
    store.set_outcome(con, "Q1", 1, T0 + timedelta(days=1))
    out = run_arm(con, "Q1", "A3", llm=llm, started_at=T0 + timedelta(days=2))
    assert dict(con.execute("SELECT run_id, status FROM runs").fetchall()) == {out.run_id: "late"}
    assert rows(con, "SELECT 1 FROM scores") == []


def test_every_round_is_live_for_every_agent(tmp_path):
    import json

    from swarmz.mirofish_bridge import every_round_live
    cfg = {"time_config": {"peak_hours": [19, 20], "off_peak_hours": [1, 2, 3], "agents_per_hour_min": 6},
           "agent_configs": [{"agent_id": 0, "active_hours": [9, 10], "activity_level": 0.6},
                             {"agent_id": 1, "active_hours": list(range(24)), "activity_level": 0.2}]}
    (tmp_path / "simulation_config.json").write_text(json.dumps(cfg))
    assert every_round_live(str(tmp_path)) == 1
    out = json.loads((tmp_path / "simulation_config.json").read_text())
    assert all(c["active_hours"] == list(range(24)) for c in out["agent_configs"])
    assert out["time_config"]["off_peak_hours"] == [] and out["time_config"]["peak_hours"] == []
    assert out["agent_configs"][0]["activity_level"] == 0.6 and out["time_config"]["agents_per_hour_min"] == 6
