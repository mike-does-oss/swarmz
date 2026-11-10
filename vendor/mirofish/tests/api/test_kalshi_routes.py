import pytest
import requests
from flask import Flask

import app.api.kalshi as kalshi_routes
from app.api import kalshi_bp


class FakeClient:
    last_limit = None

    def __init__(self, *args, **kwargs):
        pass

    def list_markets(self, status='open', limit=100, cursor=None, series_ticker=None):
        FakeClient.last_limit = limit
        return ([{"ticker": "T1", "title": "Q?", "yes_bid": 0.30}], "next123")

    def list_featured_markets(self, limit=60, per_series=6):
        FakeClient.last_limit = limit
        return [{"ticker": "T1", "title": "Q?", "yes_bid": 0.30}]

    def get_market(self, ticker):
        if ticker == "UPSTREAM404":
            resp = requests.Response()
            resp.status_code = 404
            raise requests.HTTPError(response=resp)
        if ticker == "MISSING":
            return {"ticker": None}
        return {"ticker": ticker, "title": "Q?", "yes_ask": 0.55}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(kalshi_routes, "KalshiClient", FakeClient)
    app = Flask(__name__)
    app.register_blueprint(kalshi_bp, url_prefix="/api/kalshi")
    return app.test_client()


def test_list_markets_route_featured_default(client):
    # No series_ticker/cursor -> curated featured markets (cursor is None).
    resp = client.get("/api/kalshi/markets?status=open&limit=5")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["markets"][0]["ticker"] == "T1"
    assert body["data"]["cursor"] is None


def test_list_markets_route_raw_feed_with_series(client):
    # An explicit series_ticker opts into the raw paged feed (cursor passthrough).
    resp = client.get("/api/kalshi/markets?series_ticker=KXPRES")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"]["markets"][0]["ticker"] == "T1"
    assert body["data"]["cursor"] == "next123"


def test_get_market_route(client):
    resp = client.get("/api/kalshi/market/KXPRES-24")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["ticker"] == "KXPRES-24"


def test_get_market_route_404_when_missing(client):
    resp = client.get("/api/kalshi/market/MISSING")
    assert resp.status_code == 404
    assert resp.get_json()["success"] is False


def test_get_market_route_maps_upstream_404(client):
    resp = client.get("/api/kalshi/market/UPSTREAM404")
    assert resp.status_code == 404
    assert resp.get_json()["success"] is False


def test_list_markets_limit_is_floored(client):
    client.get("/api/kalshi/markets?limit=-5")
    assert FakeClient.last_limit == 1
