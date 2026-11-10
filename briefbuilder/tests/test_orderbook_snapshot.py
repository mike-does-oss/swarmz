from app.services.orderbook_snapshot import OrderBookSnapshot, snapshot_order_book


class FakeClient:
    def __init__(self, orderbook):
        self._orderbook = orderbook
        self.calls = []

    def get_orderbook(self, ticker, depth=10):
        self.calls.append((ticker, depth))
        return self._orderbook


def test_snapshot_computes_bid_ask_mid_and_depth():
    # yes best bid 0.40 (40c), no best bid 0.55 (55c) -> yes_ask = 1 - 0.55 = 0.45
    ob = {"yes": [["0.40", 100], ["0.38", 200]], "no": [["0.55", 50]]}
    client = FakeClient(ob)
    snap = snapshot_order_book(client, "T1", "2026-07-04T12:00:00Z", depth=5)
    assert snap.ticker == "T1"
    assert snap.bid == 0.40
    assert round(snap.ask, 4) == 0.45
    assert round(snap.mid, 4) == round((0.40 + 0.45) / 2, 4)
    # depth = 0.40*100 + 0.38*200 + 0.55*50 = 40 + 76 + 27.5 = 143.5
    assert round(snap.depth_usd, 2) == 143.5
    assert snap.snapshot_ts == "2026-07-04T12:00:00Z"
    assert client.calls == [("T1", 5)]


def test_snapshot_missing_side_yields_none_mid_but_keeps_depth():
    ob = {"yes": [["0.40", 100]], "no": []}
    snap = snapshot_order_book(FakeClient(ob), "T2", "2026-07-04T12:00:00Z")
    assert snap.bid == 0.40
    assert snap.ask is None
    assert snap.mid is None
    assert round(snap.depth_usd, 2) == 40.0


def test_snapshot_empty_book():
    snap = snapshot_order_book(FakeClient({}), "T3", "2026-07-04T12:00:00Z")
    assert snap.bid is None and snap.ask is None and snap.mid is None
    assert snap.depth_usd == 0.0


def test_to_dict_roundtrip_fields():
    snap = snapshot_order_book(FakeClient({"yes": [["0.40", 10]], "no": [["0.50", 10]]}),
                               "T4", "2026-07-04T12:00:00Z")
    d = snap.to_dict()
    assert set(d.keys()) == {"ticker", "bid", "ask", "mid", "depth_usd", "snapshot_ts"}


def test_snapshot_skips_malformed_levels():
    ob = {"yes": [["0.40", 100], ["bad"], []], "no": [["0.55", 50]]}
    snap = snapshot_order_book(FakeClient(ob), "T", "2026-07-04T12:00:00Z")
    assert snap.bid == 0.40
    assert round(snap.depth_usd, 2) == round(0.40 * 100 + 0.55 * 50, 2)
