from app.services.prediction.agent_prediction_extractor import (
    build_prompt, extract_agent_predictions,
)


AGENTS = [
    {"agent_id": "a1", "persona": "economist", "texts": ["The data clearly points to passage."]},
    {"agent_id": "a2", "persona": "skeptic", "texts": ["I doubt it will happen."]},
]
QUESTION = "Will the bill pass by year end?"


class FakeLLM:
    def __init__(self, payload):
        self._payload = payload
        self.messages = None

    def chat_json(self, messages):
        self.messages = messages
        return self._payload


def test_build_prompt_includes_question_and_agent_texts():
    joined = " ".join(m["content"] for m in build_prompt(AGENTS, QUESTION))
    assert QUESTION in joined
    assert "The data clearly points to passage." in joined
    assert "a1" in joined and "a2" in joined


def test_extract_normalizes_outcome_and_clamps_confidence():
    payload = {"predictions": [
        {"agent_id": "a1", "predicted_outcome": "YES", "confidence": 1.4, "prediction": "will pass"},
        {"agent_id": "a2", "predicted_outcome": "bearish", "confidence": 0.8, "prediction": "won't"},
    ]}
    out = extract_agent_predictions(AGENTS, QUESTION, FakeLLM(payload))
    assert out[0]["agent_id"] == "a1" and out[0]["predicted_outcome"] == "yes"
    assert out[0]["confidence"] == 1.0  # clamped
    assert out[0]["persona"] == "economist"  # resolved from input
    assert out[1]["predicted_outcome"] == "no"


def test_extract_drops_unknown_agents_and_malformed_items():
    payload = {"predictions": [
        {"agent_id": "ghost", "predicted_outcome": "yes", "confidence": 0.9},  # unknown agent
        {"predicted_outcome": "yes"},                                          # no agent_id
        {"agent_id": "a1", "predicted_outcome": "yes", "confidence": 0.5, "prediction": "x"},
    ]}
    out = extract_agent_predictions(AGENTS, QUESTION, FakeLLM(payload))
    assert [p["agent_id"] for p in out] == ["a1"]


def test_extract_returns_empty_on_malformed_response():
    assert extract_agent_predictions(AGENTS, QUESTION, FakeLLM("nope")) == []
    assert extract_agent_predictions(AGENTS, QUESTION, FakeLLM({"x": 1})) == []
