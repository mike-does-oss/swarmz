import os
import pytest

from app.services.brief_v2.brave_client import BraveSearchClient


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "params": params})
        return FakeResponse(self._payload)


def test_search_maps_results_and_page_age():
    payload = {"web": {"results": [
        {"url": "https://a.com/1", "title": "T1", "description": "d1", "page_age": "2026-07-01T00:00:00Z"},
        {"url": "https://b.com/2", "title": "T2", "description": "d2", "age": "2026-06-30"},
    ]}}
    session = FakeSession(payload)
    client = BraveSearchClient(api_key="k", session=session)
    results = client.search("who is X", count=2, freshness="pm")
    assert results[0]["url"] == "https://a.com/1"
    assert results[0]["page_age"] == "2026-07-01T00:00:00Z"
    assert results[1]["page_age"] == "2026-06-30"  # falls back to `age`
    call = session.calls[0]
    assert call["headers"]["X-Subscription-Token"] == "k"
    assert call["params"]["q"] == "who is X"
    assert call["params"]["count"] == 2
    assert call["params"]["freshness"] == "pm"


def test_search_omits_freshness_when_none():
    session = FakeSession({"web": {"results": []}})
    client = BraveSearchClient(api_key="k", session=session)
    client.search("q")
    assert "freshness" not in session.calls[0]["params"]


def test_search_handles_missing_web_key():
    session = FakeSession({"web": None})
    client = BraveSearchClient(api_key="k", session=session)
    results = client.search("q")
    assert results == []


@pytest.mark.skipif(os.environ.get("BRAVE_LIVE") != "1",
                    reason="set BRAVE_LIVE=1 to hit the real Brave API")
def test_live_smoke_search():
    client = BraveSearchClient()
    results = client.search("reuters", count=1)
    assert isinstance(results, list)
