"""Temporal leak, holdout, lesson lint, deterministic evolve, backtest cutoff."""
import json
from datetime import timedelta

import pytest

from swarmz.arms.runner import compute_aggregates, ARMS, AgentForecast
from swarmz.backtest import run_backtest
from swarmz.learning import selection
from swarmz.learning.lessons import lint
from swarmz.registry import asof, store
from swarmz.registry.db import connect, rows
from swarmz.timeutil import utc
from tests.conftest import BRIEF, T0, FakeLLM, add_q, fake_swarm

D = timedelta(days=1)


def _score(con, qid, pid, rel, arm="PANEL", method=None, prob=0.5, outcome=1):
    q = store.get_question(con, qid)
    run_id = f"run-{qid}-{arm}"
    if not rows(con, "SELECT 1 FROM runs WHERE run_id = ?", [run_id]):
        store.start_run(con, run_id=run_id, question_id=qid, arm=arm, generation=0, seed=0, config={},
                        model_versions={}, brief_sha256="x", started_at=q["freeze_ts"])
        store.finish_run(con, run_id, "complete")
    con.execute("INSERT INTO scores VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [qid, run_id, arm, pid, method, prob, outcome, 0.1, rel, q["category"], q["split"],
                 q["resolve_ts"], q["resolve_ts"]])


def _resolved(con, qid, freeze, resolve, split="train"):
    add_q(con, qid, freeze, split=split)
    store.set_outcome(con, qid, 1, resolve)


def test_temporal_leak_excluded(con):
    _resolved(con, "PAST", T0 - 10 * D, T0 - 5 * D)
    _resolved(con, "FUTURE", T0 - 10 * D, T0 + 5 * D)   # resolves after the current question's freeze
    add_q(con, "NOW", T0)
    _score(con, "PAST", "p1", -0.1)
    _score(con, "FUTURE", "p1", +0.9)
    con.execute("INSERT INTO lessons VALUES ('Lp', 'p1', 'PAST', 'past lesson', ?, 'active', NULL, NULL, ?)",
                [T0 - 5 * D, T0])
    con.execute("INSERT INTO lessons VALUES ('Lf', 'p1', 'FUTURE', 'future lesson', ?, 'active', NULL, NULL, ?)",
                [T0 + 5 * D, T0])
    con.execute("INSERT INTO calibration_fits VALUES ('f-old', 'PANEL', 'weighted', ?, ?, 50, ?)",
                [json.dumps({"a": 1.0, "b": 0.0}), T0 - 5 * D, T0])
    con.execute("INSERT INTO calibration_fits VALUES ('f-new', 'PANEL', 'weighted', ?, ?, 60, ?)",
                [json.dumps({"a": 0.5, "b": 0.0}), T0 + 5 * D, T0])
    freeze = store.get_question(con, "NOW")["freeze_ts"]
    tr = asof.track_records(con, "PANEL", freeze)
    assert tr["p1"].n == 1 and tr["p1"].mean_rel_brier == pytest.approx(-0.1)
    assert [l["lesson_id"] for l in asof.lessons_for(con, "p1", freeze)] == ["Lp"]
    assert asof.calibration_fit(con, "PANEL", "weighted", freeze)["fit_id"] == "f-old"
    assert [q["question_id"] for q in asof.resolved_train_questions(con, freeze)] == ["PAST"]


def test_holdout_never_in_learning_reads(con):
    _resolved(con, "TR", T0 - 10 * D, T0 - 5 * D, split="train")
    _resolved(con, "HO", T0 - 10 * D, T0 - 5 * D, split="holdout")
    _score(con, "TR", "p1", -0.1)
    _score(con, "HO", "p1", +0.5)
    _score(con, "HO", None, 0.0, method="weighted")
    con.execute("INSERT INTO lessons VALUES ('Lh', 'p1', 'HO', 'holdout lesson', ?, 'active', NULL, NULL, ?)",
                [T0 - 5 * D, T0])
    con.execute("INSERT INTO forecasts VALUES ('run-HO-PANEL', 'HO', 'p1', 0.5, 'r', ?)", [T0])
    con.execute("INSERT INTO personas (persona_id, population, generation, parent_id, status, base_prompt, model, created_at) "
                "VALUES ('p1', 'PANEL', 0, NULL, 'active', 'x', 'm', ?)", [T0])
    assert asof.track_records(con, "PANEL", T0)["p1"].n == 1
    assert asof.lessons_for(con, "p1", T0) == []
    assert asof.calibration_data(con, "PANEL", "weighted", T0) == []
    assert all(q["question_id"] != "HO" for q in asof.resolved_train_questions(con, T0))
    assert asof.lesson_inputs(con, "HO", "PANEL", T0) == []


QUESTION = "Will Jerome Powell announce a cut at the FOMC meeting on 17 September 2025 above 4.25%?"


@pytest.mark.parametrize("lesson", [
    "The betting odds were a better guide than my reasoning.",
    "I should have followed the market more closely.",
    "Consensus probabilities from traders deserved more weight.",
    "Powell tends to surprise doves, so lean hawkish.",
    "The FOMC rarely moves in September.",
    "Rates near 4.25 tend to stick.",
    "The cut happened, so my no forecast was wrong.",
    "In 2025 I was too slow to update.",
])
def test_lint_rejects(lesson):
    assert lint(lesson, QUESTION) is not None


@pytest.mark.parametrize("lesson", [
    "When officials signal data dependence, stay near the base rate for a change.",
    "I moved too far from 50% on thin, second-hand evidence.",
    "Scheduled procedural deadlines deserve more weight than commentary.",
])
def test_lint_accepts(lesson):
    assert lint(lesson, QUESTION) is None


def _evolve_state(cfg):
    cfg["selection"]["min_n"] = 2
    c = connect(":memory:")
    store.seed_population(c, 10, "m", seed=3)
    pids = [p["persona_id"] for p in store.active_personas(c, "PANEL") if p["status"] != "system"]
    for i in range(3):
        _resolved(c, f"Q{i}", T0 + i * D, T0 + (i + 1) * D)
        for j, pid in enumerate(pids):
            _score(c, f"Q{i}", pid, (j - 5) / 100 + i / 1000)
    return c


def test_evolve_deterministic(cfg):
    results = []
    for _ in range(2):
        c = _evolve_state(cfg)
        p = selection.evolve(c, "PANEL", T0 + 10 * D, seed=7, llm=FakeLLM())
        assert p.skipped is None
        results.append((selection.population_events(c, "PANEL"),
                        rows(c, "SELECT persona_id, parent_id, status, base_prompt, generation FROM personas "
                                "WHERE population = 'PANEL' ORDER BY persona_id")))
    assert results[0] == results[1]
    events, personas = results[0]
    actions = [e["action"] for e in events]
    assert actions.count("cull") == 2 and actions.count("promote") == 2
    assert actions.count("mutate") + actions.count("spawn") == 2 and actions.count("spawn") == 1
    assert sum(p["status"] in ("active", "elite") for p in personas) == 10   # size constant
    assert sum(p["status"] == "retired" for p in personas) == 2               # kept, not deleted


def _write_jsonl(tmp_path, items):
    p = tmp_path / "bt.jsonl"
    p.write_text("\n".join(json.dumps(i) for i in items))
    return p


def test_backtest_refuses_pre_cutoff(con, llm, monkeypatch):
    from swarmz.config import settings
    monkeypatch.setattr(settings(), "training_cutoffs", {m: "2026-06-30" for m in
                        {settings().agent_model, settings().elicit_model, settings().mutate_model, settings().lesson_model}})
    store.seed_population(con, 6, "m")
    qs = [
        {"question_id": "OLD", "question_text": "q", "freeze_ts": "2026-05-01", "resolve_ts": "2026-06-01",
         "outcome": 1, "brief_path": str(BRIEF)},
        {"question_id": "NEW", "question_text": "q", "freeze_ts": "2026-07-01", "resolve_ts": "2026-08-01",
         "outcome": 0, "brief_path": str(BRIEF), "split": "train"},
    ]
    rep = run_backtest(con, qs, arms=["A0"], llm=llm, swarm=fake_swarm)
    assert "OLD" in rep.refused and store.get_question(con, "OLD") is None
    assert store.get_question(con, "NEW")["status"] == "resolved"


def test_backtest_unknown_model_refused(con, llm, monkeypatch):
    from swarmz.backtest import TrainingCutoffError
    from swarmz.config import settings
    monkeypatch.setattr(settings(), "training_cutoffs", {})
    with pytest.raises(TrainingCutoffError):
        run_backtest(con, [], arms=["A0"], llm=llm)


def test_backtest_time_order_and_learning(con, llm, cfg, monkeypatch):
    """Outcomes are hidden until resolve_ts, lessons get written, calibration kicks in once enough resolve."""
    from swarmz.config import settings
    monkeypatch.setattr(settings(), "training_cutoffs", {m: "2026-06-30" for m in
                        {settings().agent_model, settings().elicit_model, settings().mutate_model, settings().lesson_model}})
    cfg["calibration"]["min_fit_n"] = 3
    cfg["calibration"]["refit_every"] = 2
    cfg["selection"]["select_every"] = 3
    cfg["selection"]["min_n"] = 2
    store.seed_population(con, 6, "m")
    seen_outcomes = {}
    import swarmz.arms.runner as runner
    orig = runner.run_arm

    def spy(c, qid, arm, **kw):
        seen_outcomes[qid] = store.get_question(c, qid)["outcome"]
        return orig(c, qid, arm, **kw)
    monkeypatch.setattr("swarmz.backtest.run_arm", spy)
    qs = [{"question_id": f"B{i}", "question_text": f"Will event number {i} occur?", "category": "economy",
           "freeze_ts": f"2026-07-{1 + 2 * i:02d}", "resolve_ts": f"2026-07-{2 + 2 * i:02d}",
           "outcome": i % 2, "brief_path": str(BRIEF), "split": "train"} for i in range(8)]
    rep = run_backtest(con, qs, arms=["PANEL", "A3", "A4"], llm=llm, swarm=fake_swarm)
    assert not rep.refused and all(r.status == "complete" for r in rep.runs)
    assert all(v is None for v in seen_outcomes.values())          # never visible at freeze time
    assert con.execute("SELECT count(*) FROM lessons WHERE status = 'active'").fetchone()[0] > 0
    fits = rows(con, "SELECT arm, n_questions FROM calibration_fits ORDER BY arm, n_questions")
    assert {f["arm"] for f in fits} == {"PANEL", "A3", "A4"}
    # the last questions ran with a calibration fit whose window closed before their freeze
    last = rows(con, """SELECT a.prob AS cal, w.prob AS wtd FROM runs r
        JOIN aggregates a ON a.run_id = r.run_id AND a.method = 'calibrated'
        JOIN aggregates w ON w.run_id = r.run_id AND w.method = 'weighted'
        WHERE r.question_id = 'B7' AND r.arm = 'A3'""")[0]
    assert last["cal"] != pytest.approx(last["wtd"])
    assert rows(con, "SELECT count(*) AS n FROM selection_events")[0]["n"] > 0
