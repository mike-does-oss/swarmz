import app.services.prediction.run_prediction as rp
from app.storage.schemas import PredictionRow
from app.services.simulation_runner import RunnerStatus


ROW = PredictionRow(
    question_id="Q1", question_text="Will X win?", resolution_criteria="Resolves YES if X wins.",
    category="politics", freeze_ts="2026-07-04T12:00:00Z", brief_hash="a" * 64,
    brief_word_count=2000, market_mid=0.58,
)


class FakeState:
    def __init__(self):
        self.simulation_id = "sim_1"
        self.graph_id = "g1"
        self.profiles_count = 50


class FakeManager:
    def create_simulation(self, project_id=None, graph_id=None, **k):
        return FakeState()

    def prepare_simulation(self, simulation_id, **k):
        self.prepared_num_agents = k.get("num_agents")
        return FakeState()


class FakeRunState:
    def __init__(self, status):
        self.runner_status = status


class FakeRunner:
    stopped = None

    @classmethod
    def start_simulation(cls, simulation_id, **k):
        return FakeRunState(RunnerStatus.RUNNING)

    @classmethod
    def get_run_state(cls, simulation_id):
        return FakeRunState(RunnerStatus.COMPLETED)

    @classmethod
    def stop_simulation(cls, simulation_id):
        cls.stopped = simulation_id


class FakeReport:
    def __init__(self, *a, **k):
        pass

    def generate_structured_report(self, agent_predictions, topic_tags=None):
        class R:
            def to_dict(self_inner):
                return {"scenarios": [
                    {"probability": 0.7, "implied_outcome": "yes"},
                    {"probability": 0.3, "implied_outcome": "no"},
                ]}
        return R()


class FakeBaseline:
    prob = 0.55


def _wire(monkeypatch, tmp_path, captured):
    monkeypatch.setattr(rp, "read_brief", lambda h, d: "## QUESTION\nWill X win?\n...brief body...")
    monkeypatch.setattr(rp, "build_graph_from_text", lambda text, req, name="brief-v2": "g1")
    monkeypatch.setattr(rp, "SimulationManager", lambda *a, **k: FakeManager())
    monkeypatch.setattr(rp, "SimulationRunner", FakeRunner)
    monkeypatch.setattr(rp, "gather_agents", lambda sid: [{"agent_id": 0, "persona": "e", "texts": ["yes"]}])
    monkeypatch.setattr(rp, "extract_agent_predictions",
                        lambda agents, question, llm: [{"agent_id": 0, "persona": "e",
                                                        "predicted_outcome": "yes", "confidence": 0.8, "prediction": "y"}])
    monkeypatch.setattr(rp, "ReportAgent", FakeReport)
    monkeypatch.setattr(rp, "baseline_forecast", lambda brief, q, llm: FakeBaseline())
    monkeypatch.setattr(rp, "LLMClient", lambda *a, **k: object())
    monkeypatch.setattr(rp, "_now", lambda: 100.0)  # deterministic clock

    # storage: a real sqlite with the frozen row inserted
    from app.storage.db import get_connection, insert_prediction
    conn = get_connection(str(tmp_path / "shoals.db"))
    insert_prediction(conn, ROW)
    conn.close()

    def _capture_update(conn, question_id, **kw):
        captured.update(kw)
        captured["question_id"] = question_id
    monkeypatch.setattr(rp, "update_prediction_run", _capture_update)


def test_run_prediction_writes_mirofish_and_baseline(monkeypatch, tmp_path):
    captured = {}
    _wire(monkeypatch, tmp_path, captured)
    result = rp.run_prediction("Q1", num_agents=50, poll_interval=0,
                               db_path=str(tmp_path / "shoals.db"), briefs_dir=str(tmp_path))
    assert result.status == "complete"
    assert result.mirofish_prob == 0.7      # yes/(yes+no)
    assert result.baseline_prob == 0.55
    assert result.agent_count == 50         # from profiles_count
    assert captured["mirofish_prob"] == 0.7
    assert captured["baseline_prob"] == 0.55
    assert captured["agent_count"] == 50
    assert captured["question_id"] == "Q1"


def test_run_prediction_missing_row_fails(monkeypatch, tmp_path):
    captured = {}
    _wire(monkeypatch, tmp_path, captured)
    result = rp.run_prediction("NOPE", poll_interval=0,
                               db_path=str(tmp_path / "shoals.db"), briefs_dir=str(tmp_path))
    assert result.status == "failed"
    assert "not found" in (result.reason or "")


def test_run_prediction_partial_when_swarm_fails_but_baseline_ok(monkeypatch, tmp_path):
    captured = {}
    _wire(monkeypatch, tmp_path, captured)

    class FailRunner(FakeRunner):
        stopped = None

        @classmethod
        def get_run_state(cls, simulation_id):
            return FakeRunState(RunnerStatus.FAILED)
    monkeypatch.setattr(rp, "SimulationRunner", FailRunner)

    result = rp.run_prediction("Q1", num_agents=50, poll_interval=0,
                               db_path=str(tmp_path / "shoals.db"), briefs_dir=str(tmp_path))
    assert result.status == "partial"
    assert result.mirofish_prob is None
    assert result.baseline_prob == 0.55      # baseline still written
    assert captured["baseline_prob"] == 0.55
    assert FailRunner.stopped == "sim_1"     # orphaned simulation was stopped on failure
