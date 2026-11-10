import math
from datetime import timedelta

import pytest

from swarmz.capture import aggregate as agg
from swarmz.learning.calibration import fit_platt
from swarmz.registry import store
from swarmz.registry.db import migrate, rows
from swarmz.scoring.brier import score_question
from tests.conftest import T0, add_q


def test_migrations_idempotent(con):
    assert migrate(con) == 0
    from swarmz.registry.db import _migrations
    assert con.execute("SELECT max(version) FROM schema_version").fetchone()[0] == _migrations()[-1][0]


def test_seed_population_identical_prompts(con):
    out = store.seed_population(con, 6, "m", seed=1)
    panel = [p["base_prompt"] for p in store.active_personas(con, "PANEL")]
    a3 = [p["base_prompt"] for p in store.active_personas(con, "A3")]
    assert len(out["PANEL"]) == 6 and sorted(panel) == sorted(a3)
    assert store.active_personas(con, "A4")[0]["status"] == "system"
    with pytest.raises(ValueError):
        store.seed_population(con, 6, "m", seed=1)


def test_scoring_matches_hand_computed(con):
    add_q(con, "Q1", T0)
    store.start_run(con, run_id="r1", question_id="Q1", arm="A3", generation=0, seed=0, config={},
                    model_versions={}, brief_sha256="x", started_at=T0)
    store.finish_run(con, "r1", "complete")
    for pid, p in [("a", 0.9), ("b", 0.6), ("c", 0.2)]:
        store.write_forecast(con, run_id="r1", question_id="Q1", persona_id=pid, prob=p, rationale="", elicited_at=T0)
    store.write_aggregate(con, run_id="r1", question_id="Q1", method="equal", prob=0.7, computed_at=T0)
    store.write_aggregate(con, run_id="r1", question_id="Q1", method="native", prob=None, computed_at=T0)
    store.set_outcome(con, "Q1", 1, T0 + timedelta(days=5))
    assert score_question(con, "Q1") == 4  # 3 personas + 1 non-null aggregate
    s = {r["persona_id"] or r["method"]: r for r in rows(con, "SELECT * FROM scores")}
    # Brier: a=0.01, b=0.16, c=0.64; median=0.16
    assert s["a"]["brier"] == pytest.approx(0.01) and s["a"]["rel_brier"] == pytest.approx(-0.15)
    assert s["b"]["rel_brier"] == pytest.approx(0.0)
    assert s["c"]["brier"] == pytest.approx(0.64) and s["c"]["rel_brier"] == pytest.approx(0.48)
    assert s["equal"]["brier"] == pytest.approx(0.09) and s["equal"]["rel_brier"] == pytest.approx(-0.07)


def test_void_questions_not_scored(con):
    add_q(con, "QV", T0)
    store.set_outcome(con, "QV", None, T0 + timedelta(days=1), void=True)
    assert score_question(con, "QV") == 0


def test_aggregation_math():
    assert agg.equal([0.5, 0.5]) == pytest.approx(0.5)
    # log-odds mean of 0.9 and 0.1 is 0
    assert agg.equal([0.9, 0.1]) == pytest.approx(0.5)
    assert agg.weighted([0.9, 0.1], [3, 1]) == pytest.approx(agg.logistic(0.5 * math.log(9)))
    assert agg.weighted([0.7], [1], a=2.0) > 0.7  # extremizing pushes away from 0.5


def test_track_record_weights_median_fill():
    class R:  # minimal TrackRecord
        def __init__(self, n, rel): self.n, self.mean_rel_brier = n, rel
    w = agg.track_record_weights(["a", "b", "new"], {"a": R(40, -0.05), "b": R(40, 0.05), "new": R(3, -0.5)},
                                 min_n=30, k=10)
    assert w[0] == pytest.approx(math.e ** 0.5) and w[1] == pytest.approx(math.e ** -0.5)
    assert w[2] == pytest.approx((w[0] + w[1]) / 2)
    assert agg.track_record_weights(["x"], {}, 30, 10) == [1.0]


def test_platt_recovers_overconfidence():
    import random
    rng = random.Random(0)
    probs, ys = [], []
    for _ in range(4000):
        true = rng.uniform(0.05, 0.95)
        reported = agg.logistic(2.0 * agg.logit(true))  # forecaster is overconfident
        probs.append(reported)
        ys.append(1 if rng.random() < true else 0)
    fit = fit_platt(probs, ys)
    assert fit["a"] == pytest.approx(0.5, abs=0.08) and abs(fit["b"]) < 0.1


def _scored_question_with_a_lesson(con, qid="Q1"):
    add_q(con, qid, T0, split="train")
    store.start_run(con, run_id=f"r-{qid}", question_id=qid, arm="A3", generation=0, seed=0, config={},
                    model_versions={}, brief_sha256="x", started_at=T0)
    store.finish_run(con, f"r-{qid}", "complete")
    for pid, p in [("a", 0.9), ("b", 0.2)]:
        store.write_forecast(con, run_id=f"r-{qid}", question_id=qid, persona_id=pid, prob=p, rationale="",
                             elicited_at=T0)
    store.write_aggregate(con, run_id=f"r-{qid}", question_id=qid, method="equal", prob=0.6, computed_at=T0)
    resolved = T0 + timedelta(days=1)
    store.set_outcome(con, qid, 1, resolved)
    score_question(con, qid)
    con.execute("INSERT INTO lessons VALUES (?, 'a', ?, 'Trust the base rate.', ?, 'active', NULL, NULL, ?)",
                [f"L-{qid}", qid, resolved, resolved])
    con.execute("INSERT INTO learning_log VALUES (?, 'A3', 'lessons', ?)", [qid, resolved])
    return resolved


def test_excluded_question_stays_on_record_but_feeds_no_learning(con):
    from swarmz.registry import asof
    from swarmz.scoring import report

    resolved = _scored_question_with_a_lesson(con)
    later = resolved + timedelta(days=1)
    assert asof.track_records(con, "A3", later) and asof.lessons_for(con, "a", later)

    out = store.exclude_question(con, "Q1", "pilot")

    assert out == {"question_id": "Q1", "was": "train", "lessons_retired": 1, "fits_dropped": 0}
    assert asof.track_records(con, "A3", later) == {}
    assert asof.lessons_for(con, "a", later) == []
    assert asof.resolved_train_questions(con, later) == []
    assert asof.calibration_data(con, "A3", "equal", later) == []
    assert rows(con, "SELECT status, lint_reason FROM lessons") == [{"status": "retired", "lint_reason": "excluded: pilot"}]
    assert rows(con, "SELECT * FROM learning_log") == []
    # the record is kept: scores are still there, under their own split
    assert {r["split"] for r in rows(con, "SELECT split FROM scores")} == {"excluded"}
    assert [r["split"] for r in report.arm_comparison(con)] == ["excluded"]
    assert report.calibration_bins(con, "equal") == [] and report.leaderboard(con) == []


def test_excluding_a_question_retires_lessons_merged_from_it(con):
    resolved = _scored_question_with_a_lesson(con)
    con.execute("INSERT INTO lessons VALUES ('M1', 'a', NULL, 'Merged lesson.', ?, 'active', NULL, NULL, ?)",
                [resolved, resolved])
    con.execute("UPDATE lessons SET status = 'merged', merged_into = 'M1' WHERE lesson_id = 'L-Q1'")

    assert store.exclude_question(con, "Q1", "pilot")["lessons_retired"] == 1
    assert rows(con, "SELECT status FROM lessons WHERE lesson_id = 'M1'") == [{"status": "retired"}]


def test_rescoring_an_excluded_question_keeps_it_excluded(con):
    _scored_question_with_a_lesson(con)
    store.exclude_question(con, "Q1", "pilot")
    score_question(con, "Q1")
    assert {r["split"] for r in rows(con, "SELECT split FROM scores")} == {"excluded"}


class _Kalshi:
    def __init__(self, market):
        self.market = market

    def get(self, url, timeout=None):
        return self

    def raise_for_status(self):
        pass

    def json(self):
        return {"market": self.market}


def test_resolution_time_is_the_settlement_time_and_never_in_the_future():
    from swarmz.scoring.resolve import kalshi_resolution
    from swarmz.timeutil import now, utc

    def iso(delta):
        return (now() + delta).isoformat() + "Z"

    past, settle, future = iso(timedelta(hours=-2)), iso(timedelta(hours=-1)), iso(timedelta(days=7))
    settled = kalshi_resolution("T", _Kalshi({"status": "finalized", "result": "yes", "close_time": past,
                                              "expiration_time": future, "settlement_ts": settle}))
    assert settled.outcome == 1 and settled.resolve_ts == utc(settle)
    # a result is reported before settlement_ts is filled in: use the close time, not the far expiration
    early = kalshi_resolution("T", _Kalshi({"status": "determined", "result": "no", "close_time": past,
                                            "expiration_time": future}))
    assert early.outcome == 0 and early.resolve_ts == utc(past)
    odd = kalshi_resolution("T", _Kalshi({"status": "determined", "result": "no", "close_time": future,
                                          "expiration_time": future}))
    assert odd.resolve_ts <= now()
