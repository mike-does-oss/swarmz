from app.storage.db import (
    get_connection,
    insert_prediction,
    get_prediction,
    list_predictions,
    update_outcome,
)
from app.storage.schemas import PredictionRow


def _row(qid="Q1", category="politics"):
    return PredictionRow(
        question_id=qid,
        question_text="Will X happen?",
        resolution_criteria="Resolves YES if ...",
        category=category,
        freeze_ts="2026-07-04T12:00:00Z",
        brief_hash="a" * 64,
        brief_word_count=2000,
        mirofish_prob=0.61,
        baseline_prob=0.55,
        market_mid=0.58,
        agent_count=50,
    )


def test_insert_and_get_prediction(data_dir):
    conn = get_connection(str(data_dir / "shoals.db"))
    insert_prediction(conn, _row("Q1"))
    got = get_prediction(conn, "Q1")
    assert got == _row("Q1")


def test_get_missing_returns_none(data_dir):
    conn = get_connection(str(data_dir / "shoals.db"))
    assert get_prediction(conn, "nope") is None


def test_list_predictions_filters_by_category(data_dir):
    conn = get_connection(str(data_dir / "shoals.db"))
    insert_prediction(conn, _row("Q1", "politics"))
    insert_prediction(conn, _row("Q2", "sports"))
    insert_prediction(conn, _row("Q3", "politics"))
    all_rows = list_predictions(conn)
    politics = list_predictions(conn, category="politics")
    assert {r.question_id for r in all_rows} == {"Q1", "Q2", "Q3"}
    assert {r.question_id for r in politics} == {"Q1", "Q3"}


def test_update_outcome(data_dir):
    conn = get_connection(str(data_dir / "shoals.db"))
    insert_prediction(conn, _row("Q1"))
    update_outcome(conn, "Q1", outcome=1, resolved_at="2026-08-01T00:00:00Z")
    got = get_prediction(conn, "Q1")
    assert got.outcome == 1
    assert got.resolved_at == "2026-08-01T00:00:00Z"
