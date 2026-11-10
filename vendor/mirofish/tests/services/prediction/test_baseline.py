from app.services.prediction.baseline import BaselineForecast, build_prompt, baseline_forecast


BRIEF = "## QUESTION\nWill X win?\n## CONFIRMED FACTS\n- X leads polls by 5 points."
QUESTION = "Will X win?"


class FakeLLM:
    def __init__(self, payload):
        self._payload = payload
        self.messages = None

    def chat_json(self, messages):
        self.messages = messages
        return self._payload


def test_build_prompt_includes_brief_and_question():
    joined = " ".join(m["content"] for m in build_prompt(BRIEF, QUESTION))
    assert "X leads polls by 5 points." in joined
    assert QUESTION in joined


def test_baseline_forecast_parses_and_clamps():
    out = baseline_forecast(BRIEF, QUESTION, FakeLLM({"probability": 1.3, "rationale": "poll lead"}))
    assert isinstance(out, BaselineForecast)
    assert out.prob == 1.0  # clamped
    assert out.rationale == "poll lead"


def test_baseline_forecast_none_on_malformed():
    assert baseline_forecast(BRIEF, QUESTION, FakeLLM("nope")).prob is None
    assert baseline_forecast(BRIEF, QUESTION, FakeLLM({"rationale": "no prob"})).prob is None
    assert baseline_forecast(BRIEF, QUESTION, FakeLLM({"probability": "abc"})).prob is None
