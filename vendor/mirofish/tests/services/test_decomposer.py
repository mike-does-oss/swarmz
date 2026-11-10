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


def test_categories_are_the_seven_spec_categories():
    assert CATEGORIES == [
        "KEY_ACTORS", "RECENT_EVENTS", "FUNDAMENTALS", "EXPERT_FORECASTS",
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
    # every one of the 7 categories is present (possibly empty)
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
