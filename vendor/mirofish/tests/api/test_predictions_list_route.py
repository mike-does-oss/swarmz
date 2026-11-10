import pytest
from flask import Flask

import app.api.brief_v2 as brief_v2_routes
from app.api import brief_v2_bp
from app.storage.db import get_connection, insert_prediction
from app.storage.schemas import PredictionRow


def _row(qid, category="politics", mirofish=0.61):
    return PredictionRow(
        question_id=qid, question_text=f"Q {qid}?", resolution_criteria="R",
        category=category, freeze_ts="2026-07-04T12:00:00Z", brief_hash="a" * 64,
        brief_word_count=2000, mirofish_prob=mirofish, baseline_prob=0.55, market_mid=0.58,
    )


@pytest.fixture
def client(monkeypatch, tmp_path):
    db_path = str(tmp_path / "shoals.db")
    conn = get_connection(db_path)
    insert_prediction(conn, _row("Q1", "politics"))
    insert_prediction(conn, _row("Q2", "sports"))
    conn.close()
    monkeypatch.setattr(brief_v2_routes.Config, "SHOALS_DB_PATH", db_path)
    app = Flask(__name__)
    app.register_blueprint(brief_v2_bp, url_prefix="/api/brief/v2")
    return app.test_client()


def test_list_predictions_returns_all(client):
    resp = client.get("/api/brief/v2/predictions")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["count"] == 2
    assert {p["question_id"] for p in body["data"]["predictions"]} == {"Q1", "Q2"}
    assert body["data"]["predictions"][0]["mirofish_prob"] is not None


def test_list_predictions_filters_by_category(client):
    resp = client.get("/api/brief/v2/predictions?category=sports")
    body = resp.get_json()
    assert body["data"]["count"] == 1
    assert body["data"]["predictions"][0]["question_id"] == "Q2"
