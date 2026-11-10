from app.storage.db import (
    get_connection, insert_prediction, insert_provenance, get_prediction, get_provenance,
)
from app.storage.schemas import PredictionRow, BriefProvenance


def _row(qid="Q1"):
    return PredictionRow(
        question_id=qid, question_text="Will X happen?", resolution_criteria="R",
        category="politics", freeze_ts="2026-07-04T12:00:00Z", brief_hash="a" * 64,
        brief_word_count=2000, agent_count=50,
    )


def _prov(bh="b" * 64):
    return BriefProvenance(brief_hash=bh, question_id="Q1", freeze_ts="2026-07-04T12:00:00Z")


def test_uncommitted_writes_roll_back(data_dir):
    conn = get_connection(str(data_dir / "shoals.db"))
    insert_provenance(conn, _prov(), commit=False)
    insert_prediction(conn, _row(), commit=False)
    conn.rollback()
    assert get_prediction(conn, "Q1") is None
    assert get_provenance(conn, "b" * 64) is None


def test_caller_can_commit_multiple_writes_atomically(data_dir):
    conn = get_connection(str(data_dir / "shoals.db"))
    insert_provenance(conn, _prov(), commit=False)
    insert_prediction(conn, _row(), commit=False)
    conn.commit()
    assert get_prediction(conn, "Q1") == _row()
    assert get_provenance(conn, "b" * 64) == _prov()
