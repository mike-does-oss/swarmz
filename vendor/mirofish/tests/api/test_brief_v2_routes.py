import pytest
from flask import Flask

import app.api.brief_v2 as brief_v2_routes
from app.api import brief_v2_bp
from app.services.brief_v2.pipeline import BuildResult


class FakeKalshi:
    def __init__(self, *a, **k):
        pass

    def get_market(self, ticker):
        if ticker == "MISSING":
            return {"ticker": None}
        return {"ticker": ticker, "title": "Q?", "rules_primary": "R", "category": "Politics",
                "close_time": "2026-11-05T00:00:00Z"}


class FakePipeline:
    def __init__(self, *a, **k):
        pass

    def build(self, market, *, now, freshness=None):
        return BuildResult(status="frozen", brief_hash="a" * 64, word_count=2000, freeze_ts=now)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(brief_v2_routes, "KalshiClient", FakeKalshi)
    monkeypatch.setattr(brief_v2_routes, "BriefPipeline", FakePipeline)
    app = Flask(__name__)
    app.register_blueprint(brief_v2_bp, url_prefix="/api/brief/v2")
    return app.test_client()


def test_build_returns_frozen_result(client):
    resp = client.post("/api/brief/v2/build", json={"ticker": "KXPRES-24"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["status"] == "frozen"
    assert body["data"]["brief_hash"] == "a" * 64


def test_build_missing_ticker_400(client):
    resp = client.post("/api/brief/v2/build", json={})
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_build_market_not_found_404(client):
    resp = client.post("/api/brief/v2/build", json={"ticker": "MISSING"})
    assert resp.status_code == 404
    assert resp.get_json()["success"] is False
