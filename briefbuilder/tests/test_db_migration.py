import sqlite3

from app.storage.db import get_connection, init_db, insert_prediction, get_prediction
from app.storage.schemas import PredictionRow


def _row(qid="Q1"):
    return PredictionRow(
        question_id=qid, question_text="Q?", resolution_criteria="R",
        category="politics", freeze_ts="2026-07-04T12:00:00Z", brief_hash="a" * 64,
        brief_word_count=2000, market_snapshot_ts="2026-07-04T12:00:30Z", agent_count=50,
    )


def test_market_snapshot_ts_column_roundtrips(data_dir):
    conn = get_connection(str(data_dir / "shoals.db"))
    insert_prediction(conn, _row())
    got = get_prediction(conn, "Q1")
    assert got.market_snapshot_ts == "2026-07-04T12:00:30Z"


def test_ensure_columns_migrates_a_legacy_db(data_dir):
    # Simulate a pre-migration predictions table missing market_snapshot_ts.
    db_path = str(data_dir / "legacy.db")
    raw = sqlite3.connect(db_path)
    raw.execute(
        "CREATE TABLE predictions ("
        "question_id TEXT PRIMARY KEY, question_text TEXT, resolution_criteria TEXT, "
        "category TEXT, freeze_ts TEXT, brief_hash TEXT, brief_word_count INTEGER)"
    )
    raw.commit()
    raw.close()

    # Opening via get_connection runs init_db -> _ensure_columns, which should add the column.
    conn = get_connection(db_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)").fetchall()}
    assert "market_snapshot_ts" in cols


def test_ensure_columns_is_idempotent(data_dir):
    db_path = str(data_dir / "shoals.db")
    conn = get_connection(db_path)
    init_db(conn)  # second run must not raise
    init_db(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)").fetchall()}
    assert "market_snapshot_ts" in cols
