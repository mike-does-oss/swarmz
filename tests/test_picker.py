"""Short-dated question picker: time window, one per event/occasion, category round-robin, past events."""

from datetime import timedelta

from swarmz import picker
from swarmz.timeutil import now


def _iso(hours: float) -> str:
    return (now() + timedelta(hours=hours)).isoformat() + "Z"


def _m(ticker, event, hours, mid=50, vol=5000, close_hours=None, **kw):
    return {"ticker": ticker, "event_ticker": event, "title": ticker, "market_type": "binary",
            "expected_expiration_time": _iso(hours), "close_time": _iso(close_hours or hours),
            "yes_bid": mid - 1, "yes_ask": mid + 1, "volume": vol, **kw}


class Events:
    def __init__(self, strikes):
        self.strikes = strikes

    def get(self, url, timeout=None, params=None):
        ev = url.rsplit("/", 1)[-1]
        body = {"event": {"strike_date": self.strikes.get(ev)}}

        class R:
            def json(self):
                return body
        return R()


def test_candidates_window_price_volume_and_one_per_event():
    ms = [
        _m("A-1", "KXA-E1", 10, mid=50), _m("A-2", "KXA-E1", 10, mid=80),     # same event: keep the 50c one
        _m("B-1", "KXB-E2", 2),                                                # resolves too soon
        _m("C-1", "KXC-E3", 10, close_hours=100),                              # closes late but resolves in window
        _m("D-1", "KXD-E4", 10, mid=97),                                       # already near-decided
        _m("F-1", "KXF-E5", 10, vol=10),                                       # illiquid
        _m("G-1", "KXG-E6", 50),                                               # too far out
    ]
    got = picker.candidates(ms, min_hours=4, max_hours=36, min_volume=1000)
    assert sorted(c.ticker for c in got) == ["A-1", "C-1"]


def test_pick_round_robin_occasion_dedupe_and_past_events():
    series = {"KXNFLGAME": ("Sports", "NFL Game"), "KXNFLTOTAL": ("Sports", "NFL Total"),
              "KXNBAGAME": ("Sports", "NBA Game"), "KXBTCD": ("Crypto", "Bitcoin"),
              "KXRAIN": ("Climate and Weather", "Rain")}
    ms = [
        _m("G1", "KXNFLGAME-26NOV08BALCIN", 20, vol=900_000),
        _m("T1", "KXNFLTOTAL-26NOV08BALCIN", 20, vol=800_000),   # same game as G1: skipped
        _m("N1", "KXNBAGAME-26NOV05LALPHX", 6, vol=700_000),
        _m("B1", "KXBTCD-26NOV0517", 20, vol=10_000),
        _m("R1", "KXRAIN-26NOV04-TPA", 20, vol=50_000),          # its day ends within min_hours: skipped
    ]
    cands = picker.candidates(ms, min_hours=4, max_hours=36, min_volume=1000)
    session = Events({"KXRAIN-26NOV04-TPA": _iso(2)})
    got = picker.pick(cands, n=4, max_per_category=2, session=session, series=series, min_hours=4)
    assert [c.ticker for c in got] == ["G1", "B1", "N1"]
    assert picker.occasion("KXNFLTOTAL-26NOV08BALCIN") == picker.occasion("KXNFLGAME-26NOV08BALCIN")


def test_mid_price_reads_cents_or_dollars():
    assert picker.mid_cents({"yes_bid": 40, "yes_ask": 44}) == 42
    assert picker.mid_cents({"yes_bid_dollars": "0.40", "yes_ask_dollars": "0.44"}) == 42
    assert picker.mid_cents({"last_price": 30}) == 30


def test_siblings_of_registered_questions_are_excluded():
    registered = {"KXCBDECISIONMEXICO-26NOV05-C25", "KXNFLGAME-26NOV05JACBAL-BAL"}
    ev = {picker.event_of(q) for q in registered} | {picker.occasion(picker.event_of(q)) for q in registered}
    ms = [_m("KXCBDECISIONMEXICO-26NOV05-HOLD", "KXCBDECISIONMEXICO-26NOV05", 20),   # same decision
          _m("KXNFLTOTAL-26NOV05JACBAL-45", "KXNFLTOTAL-26NOV05JACBAL", 20),         # same game
          _m("KXCPI-26OCT-T0.2", "KXCPI-26OCT", 20)]
    got = picker.candidates(ms, min_hours=4, max_hours=36, min_volume=1000, exclude=registered, exclude_events=ev)
    assert [c.ticker for c in got] == ["KXCPI-26OCT-T0.2"]
    assert picker.event_of("KXDJI-05NOV26-GE52800P00") == "KXDJI-05NOV26"
