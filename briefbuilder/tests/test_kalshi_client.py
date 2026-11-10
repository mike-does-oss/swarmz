import os
import pytest

from app.services.kalshi_client import price_to_prob, normalize_market, KalshiClient


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    """Records GET calls and returns queued payloads."""
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return FakeResponse(self._payloads.pop(0))


def test_price_to_prob_int_is_cents():
    assert price_to_prob(42) == 0.42
    assert price_to_prob(1) == 0.01
    assert price_to_prob(100) == 1.0


def test_price_to_prob_float_and_str_are_dollars():
    assert price_to_prob(0.42) == 0.42
    assert price_to_prob("0.42") == 0.42


def test_price_to_prob_handles_none_and_garbage():
    assert price_to_prob(None) is None
    assert price_to_prob("n/a") is None
    assert price_to_prob(True) is None


def test_price_to_prob_unit_dollars_keeps_whole_numbers():
    assert price_to_prob(1, unit="dollars") == 1.0
    assert price_to_prob(0, unit="dollars") == 0.0
    assert price_to_prob("1", unit="dollars") == 1.0


def test_price_to_prob_unit_cents():
    assert price_to_prob(1, unit="cents") == 0.01


def test_normalize_market_dollars_field_whole_number_not_miscaled():
    m = normalize_market({"ticker": "T", "yes_bid_dollars": 1})
    assert m["yes_bid"] == 1.0  # $1.00, not 0.01


def test_normalize_market_prefers_plain_then_dollars_field():
    cents = normalize_market({"ticker": "T1", "yes_bid": 42, "yes_ask": 45})
    assert cents["yes_bid"] == 0.42 and cents["yes_ask"] == 0.45
    dollars = normalize_market({"ticker": "T2", "yes_bid_dollars": "0.42"})
    assert dollars["yes_bid"] == 0.42
    assert dollars["title"] == "T2"  # falls back to ticker when title absent
    assert dollars["raw"]["ticker"] == "T2"


def test_list_markets_normalizes_and_returns_cursor():
    session = FakeSession([
        {"markets": [{"ticker": "T1", "yes_bid": 30}], "cursor": "next123"}
    ])
    client = KalshiClient(base_url="https://x/v2", session=session)
    markets, cursor = client.list_markets(status="open", limit=5)
    assert markets[0]["ticker"] == "T1"
    assert markets[0]["yes_bid"] == 0.30
    assert cursor == "next123"
    url, params = session.calls[0]
    assert url == "https://x/v2/markets"
    assert params["status"] == "open" and params["limit"] == 5


def test_get_market_unwraps_market_key():
    session = FakeSession([{"market": {"ticker": "T9", "yes_ask": 55}}])
    client = KalshiClient(base_url="https://x/v2", session=session)
    m = client.get_market("T9")
    assert m["ticker"] == "T9" and m["yes_ask"] == 0.55
    assert session.calls[0][0] == "https://x/v2/markets/T9"


def test_get_orderbook_returns_orderbook_object():
    session = FakeSession([{"orderbook": {"yes": [["0.40", 100]], "no": [["0.55", 50]]}}])
    client = KalshiClient(base_url="https://x/v2", session=session)
    ob = client.get_orderbook("T9", depth=3)
    assert ob["yes"] == [["0.40", 100]]
    assert session.calls[0][0] == "https://x/v2/markets/T9/orderbook"
    assert session.calls[0][1]["depth"] == 3


@pytest.mark.skipif(os.environ.get("KALSHI_LIVE") != "1",
                    reason="set KALSHI_LIVE=1 to hit the real Kalshi API")
def test_live_smoke_list_markets():
    client = KalshiClient()
    markets, _ = client.list_markets(status="open", limit=1)
    assert isinstance(markets, list)
    if markets:
        assert "ticker" in markets[0]
