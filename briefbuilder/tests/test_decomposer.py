from dataclasses import dataclass

from app.services.brief_v2.decomposer import (
    CATEGORIES, DecomposerOutput, build_prompt, decompose,
)


@dataclass
class QP:
    question_text: str
    resolution_criteria: str
    category: str


class FakeLLM:
    def __init__(self, payload):
        self._payload = payload
        self.messages = None

    def chat_json(self, messages):
        self.messages = messages
        return self._payload


QP1 = QP("Will X win?", "Resolves YES if X wins per AP.", "politics")


def test_categories_are_the_latest_value_then_the_seven_spec_categories():
    # LATEST_VALUE is first so its searches survive the round-robin cap on queries.
    assert CATEGORIES == [
        "LATEST_VALUE", "DRIVERS", "KEY_ACTORS", "RECENT_EVENTS", "FUNDAMENTALS", "EXPERT_FORECASTS",
        "HISTORICAL_ANALOGUES", "STAKEHOLDER_POSITIONS", "RESOLUTION_EDGE_CASES",
    ]


def test_build_prompt_includes_question_and_resolution():
    joined = " ".join(m["content"] for m in build_prompt(QP1))
    assert "Will X win?" in joined
    assert "Resolves YES if X wins per AP." in joined


def test_decompose_keeps_known_categories_and_caps_at_six():
    payload = {
        "KEY_ACTORS": ["a1", "a2"],
        "RECENT_EVENTS": ["e1", "e2", "e3", "e4", "e5", "e6", "e7"],  # 7 -> cap 6
        "UNKNOWN_CAT": ["ignored"],
    }
    out = decompose(QP1, FakeLLM(payload))
    assert isinstance(out, DecomposerOutput)
    assert out.queries_by_category["KEY_ACTORS"] == ["a1", "a2"]
    assert len(out.queries_by_category["RECENT_EVENTS"]) == 6
    assert "UNKNOWN_CAT" not in out.queries_by_category
    # every category is present (possibly empty)
    assert set(out.queries_by_category.keys()) == set(CATEGORIES)


def test_decompose_drops_and_records_odds_queries():
    payload = {"EXPERT_FORECASTS": ["expert model forecast", "betting odds for the race"]}
    out = decompose(QP1, FakeLLM(payload))
    assert out.queries_by_category["EXPERT_FORECASTS"] == ["expert model forecast"]
    assert out.dropped_odds_queries == ["betting odds for the race"]


def test_decompose_drops_blank_and_nonstring_queries():
    payload = {"KEY_ACTORS": ["ok", "", 123, None, "  "]}
    out = decompose(QP1, FakeLLM(payload))
    assert out.queries_by_category["KEY_ACTORS"] == ["ok"]


def test_prompt_asks_for_the_latest_value_and_names_today():
    # 2026-11-10: briefs on a Bitcoin and a Nasdaq question had no current price in them,
    # because no search ever looked the price up.
    joined = " ".join(m["content"] for m in build_prompt(QP1, today="2026-11-10"))
    assert "LATEST_VALUE" in joined and "TODAY: 2026-11-10" in joined
    out = decompose(QP1, FakeLLM({"LATEST_VALUE": ["bitcoin price today"]}), today="2026-11-10")
    assert out.queries_by_category["LATEST_VALUE"] == ["bitcoin price today"]   # a price is not betting odds
    assert out.dropped_odds_queries == []


def test_drivers_searches_grow_with_the_time_left():
    from types import SimpleNamespace
    from app.services.brief_v2.decomposer import drivers_weight, horizon_days
    qp = lambda close, raw=None: SimpleNamespace(question_text="Q", resolution_criteria="R", category="politics",
                                                 close_ts=close, raw_market=raw or {})
    assert horizon_days(qp("2026-11-11T18:00:00Z"), "2026-11-10") == 1
    # the expected resolution wins over the close: markets often stay open after the event
    assert horizon_days(qp("2027-01-31T00:00:00Z", {"expected_expiration_time": "2026-12-31T15:00:00Z"}), "2026-11-10") == 51
    assert horizon_days(qp(""), "2026-11-10") is None and horizon_days(qp("2026-11-01T00:00:00Z"), "2026-11-10") == 0
    assert [drivers_weight(d) for d in (None, 0, 2, 3, 30, 31, 400)] == [1, 1, 1, 2, 2, 3, 3]

    near, far = qp("2026-11-11T18:00:00Z"), qp("2026-12-31T00:00:00Z")
    assert "RESOLVES IN: 1 day\n" in build_prompt(near, today="2026-11-10")[1]["content"]
    assert "Write 2 DRIVERS searches" in build_prompt(near, today="2026-11-10")[1]["content"]
    assert "RESOLVES IN: 51 days" in build_prompt(far, today="2026-11-10")[1]["content"]
    assert "Write 6 DRIVERS searches" in build_prompt(far, today="2026-11-10")[1]["content"]
    out = decompose(far, FakeLLM({"DRIVERS": ["us inflation latest", "government shutdown news"]}), today="2026-11-10")
    assert (out.horizon_days, out.drivers_weight) == (51, 3)
    assert out.to_dict()["drivers_weight"] == 3
