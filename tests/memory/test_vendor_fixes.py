"""Regression tests for fixes made to the vendored MiroFish code during the first live runs."""

from pathlib import Path

from swarmz.mirofish_bridge import ensure_vendor_path

ensure_vendor_path()

VENDOR = Path(__file__).resolve().parents[2] / "vendor" / "mirofish"


class _LLM:
    def __init__(self, response):
        self.response = response

    def chat_json(self, messages):
        return self.response


def test_extractor_matches_string_and_int_agent_ids():
    from app.services.prediction.agent_prediction_extractor import extract_agent_predictions

    agents = [{"agent_id": 3, "persona": "a", "texts": ["x"]}, {"agent_id": 7, "persona": "b", "texts": ["y"]}]
    llm = _LLM({"predictions": [
        {"agent_id": "3", "predicted_outcome": "NO", "confidence": 0.8},
        {"agent_id": 7, "predicted_outcome": "YES", "confidence": 0.6},
        {"agent_id": "99", "predicted_outcome": "YES", "confidence": 0.9},
    ]})
    assert len(extract_agent_predictions(agents, "q", _LLM({"predictions": [
        {"agent_id": "AGENT 3", "predicted_outcome": "NO", "confidence": 0.8},
        {"agent_id": "agent_7", "predicted_outcome": "YES", "confidence": 0.6}]}))) == 2
    out = extract_agent_predictions(agents, "q", llm)
    assert [(p["agent_id"], p["predicted_outcome"], p["persona"]) for p in out] == [(3, "no", "a"), (7, "yes", "b")]


def test_create_completion_retries_without_temperature():
    from app.utils.llm_client import create_completion

    calls = []

    class Completions:
        def create(self, **kw):
            calls.append(dict(kw))
            if "temperature" in kw:
                raise ValueError("Error code: 400 - `temperature` is deprecated for this model.")
            return "ok"

    class Client:
        class chat:
            completions = Completions()

    assert create_completion(Client(), model="m", messages=[], temperature=0.7) == "ok"
    assert len(calls) == 2 and "temperature" not in calls[1]


def test_single_platform_runs_use_parallel_script_with_flag():
    # The single-platform OASIS scripts never write <platform>/actions.jsonl, so the
    # runner could not see completion and timed out after an hour.
    src = (VENDOR / "app/services/simulation_runner.py").read_text()
    assert '"--reddit-only"' in src and '"--twitter-only"' in src
    assert 'script_name = "run_reddit_simulation.py"' not in src


def test_setup_calls_do_not_send_json_object_response_format():
    for f in ["app/services/oasis_profile_generator.py", "app/services/simulation_config_generator.py"]:
        assert 'response_format={"type": "json_object"}' not in (VENDOR / f).read_text()


def test_fulltext_query_drops_bare_punctuation_tokens():
    import swarmz.memory.graphiti_backend  # noqa: F401  (installs the patch)
    from graphiti_core.driver.falkordb.fulltext import build_falkor_fulltext_query

    q = build_falkor_fulltext_query("ESPN https://www.espn.com/nfl/team/depth/_/name/bal depth chart", ["shared"])
    words = q.split("(", 2)[-1].rstrip(")").split(" | ")
    assert "_" not in words and "depth" in words and "bal" in words
