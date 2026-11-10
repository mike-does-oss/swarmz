from app.storage.db import (
    get_connection, insert_prediction, get_prediction, update_prediction_run,
)
from app.storage.schemas import PredictionRow


def _row(qid="Q1"):
    return PredictionRow(
        question_id=qid, question_text="Q?", resolution_criteria="R",
        category="politics", freeze_ts="2026-07-04T12:00:00Z", brief_hash="a" * 64,
        brief_word_count=2000, market_mid=0.58,
    )


def test_update_prediction_run_fills_run_fields(data_dir):
    conn = get_connection(str(data_dir / "shoals.db"))
    insert_prediction(conn, _row("Q1"))
    update_prediction_run(
        conn, "Q1", mirofish_prob=0.61, baseline_prob=0.55,
        mirofish_cost_usd=0.12, mirofish_wall_clock_s=430, agent_count=50,
    )
    got = get_prediction(conn, "Q1")
    assert got.mirofish_prob == 0.61 and got.baseline_prob == 0.55
    assert got.mirofish_cost_usd == 0.12 and got.mirofish_wall_clock_s == 430
    assert got.agent_count == 50
    # frozen fields untouched
    assert got.market_mid == 0.58 and got.brief_hash == "a" * 64


def test_update_prediction_run_leaves_other_rows_untouched(data_dir):
    conn = get_connection(str(data_dir / "shoals.db"))
    insert_prediction(conn, _row("Q1"))
    insert_prediction(conn, _row("Q2"))
    update_prediction_run(conn, "Q1", mirofish_prob=0.7, baseline_prob=0.6)
    assert get_prediction(conn, "Q2").mirofish_prob is None


def test_update_prediction_run_partial_calls_do_not_clobber(data_dir):
    conn = get_connection(str(data_dir / "shoals.db"))
    insert_prediction(conn, _row("Q1"))
    update_prediction_run(conn, "Q1", mirofish_prob=0.61, agent_count=50)
    update_prediction_run(conn, "Q1", baseline_prob=0.55)  # must NOT wipe mirofish/agent_count
    got = get_prediction(conn, "Q1")
    assert got.mirofish_prob == 0.61
    assert got.agent_count == 50
    assert got.baseline_prob == 0.55
