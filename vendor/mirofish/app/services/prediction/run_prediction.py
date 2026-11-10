"""
Prediction-run orchestrator (spec §1 pipeline consumers).

Given a frozen brief (question_id -> brief_hash), drives:
  read brief -> graph -> create/prepare(N)/start/poll -> gather posts ->
  extract stances -> structured report -> mirofish_prob (reduce), plus a
  single-LLM baseline_prob, then writes both onto the frozen PredictionRow.

All heavy collaborators are referenced at module scope so tests monkeypatch them.
"""

import time
from dataclasses import dataclass, asdict
from typing import Optional, Any, Dict

from .graph_from_text import build_graph_from_text
from .agent_posts import gather_agents
from .agent_prediction_extractor import extract_agent_predictions
from .swarm_reducer import reduce_to_yes_prob
from .baseline import baseline_forecast
from ..simulation_manager import SimulationManager
from ..simulation_runner import SimulationRunner
from ..report_agent import ReportAgent
from ...config import Config
from ...utils.llm_client import LLMClient
from ...utils.logger import get_logger
from ...storage.brief_store import read_brief
from ...storage.db import get_connection, get_prediction, update_prediction_run

logger = get_logger('mirofish.services.run_prediction')

_TERMINAL_OK = {"completed"}
_TERMINAL_BAD = {"failed", "stopped"}


def _now() -> float:
    return time.monotonic()


def _status_str(run_state) -> str:
    raw = getattr(run_state, "runner_status", None)
    value = getattr(raw, "value", raw)  # enum -> its str value; plain str -> itself
    return str(value or "").lower()


@dataclass
class RunResult:
    status: str  # 'complete' | 'partial' | 'failed'
    question_id: str
    mirofish_prob: Optional[float] = None
    baseline_prob: Optional[float] = None
    agent_count: Optional[int] = None
    wall_clock_s: Optional[int] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _run_swarm(brief_text, question, num_agents, max_rounds, poll_interval, poll_timeout):
    """Returns (mirofish_prob, agent_count) or raises on a swarm-stage failure."""
    graph_id = build_graph_from_text(brief_text, question, name="brief-v2")
    manager = SimulationManager()
    state = manager.create_simulation(project_id=None, graph_id=graph_id,
                                      enable_twitter=False, enable_reddit=True)
    prepared = manager.prepare_simulation(
        state.simulation_id, simulation_requirement=question,
        document_text=brief_text, num_agents=num_agents,
    )
    agent_count = prepared.profiles_count
    sid = prepared.simulation_id
    SimulationRunner.start_simulation(sid, platform="reddit", max_rounds=max_rounds, graph_id=graph_id)
    try:
        deadline = _now() + poll_timeout
        while True:
            status = _status_str(SimulationRunner.get_run_state(sid))
            if status in _TERMINAL_OK:
                break
            if status in _TERMINAL_BAD:
                raise RuntimeError(f"simulation ended {status}")
            if _now() > deadline:
                raise TimeoutError("simulation poll timeout")
            time.sleep(poll_interval)

        agents = gather_agents(sid)
        if not agents:
            raise RuntimeError("no agent posts to classify")
        predictions = extract_agent_predictions(agents, question, LLMClient())
        report = ReportAgent(graph_id, sid, question)
        structured = report.generate_structured_report(predictions, topic_tags=None)
        mirofish_prob = reduce_to_yes_prob(structured.to_dict())
        return mirofish_prob, agent_count
    except Exception:
        try:
            SimulationRunner.stop_simulation(sid)
        except Exception as stop_err:
            logger.error(f"failed to stop simulation {sid}: {stop_err}")
        raise


def run_prediction(question_id, *, num_agents=None, max_rounds=None, poll_interval=10,
                   poll_timeout=1800, db_path=None, briefs_dir=None) -> RunResult:
    db_path = db_path or Config.SHOALS_DB_PATH
    briefs_dir = briefs_dir or Config.BRIEFS_V2_DIR

    conn = get_connection(db_path)
    try:
        row = get_prediction(conn, question_id)
    finally:
        conn.close()
    if row is None:
        return RunResult(status="failed", question_id=question_id, reason=f"prediction row not found: {question_id}")

    try:
        brief_text = read_brief(row.brief_hash, briefs_dir)
    except Exception as e:
        logger.error(f"brief unreadable for {question_id}: {e}")
        return RunResult(status="failed", question_id=question_id, reason=f"brief unreadable: {e}")
    question = row.resolution_criteria

    if max_rounds is None:
        max_rounds = Config.OASIS_DEFAULT_MAX_ROUNDS

    start = _now()
    mirofish_prob = None
    agent_count = None
    swarm_reason = None
    try:
        mirofish_prob, agent_count = _run_swarm(
            brief_text, question, num_agents, max_rounds, poll_interval, poll_timeout,
        )
    except Exception as e:
        swarm_reason = str(e)
        logger.error(f"swarm stage failed for {question_id}: {e}")

    swarm_succeeded = swarm_reason is None

    baseline = baseline_forecast(brief_text, question, LLMClient())
    wall_clock_s = int(_now() - start)

    conn = get_connection(db_path)
    try:
        update_prediction_run(
            conn, question_id,
            mirofish_prob=mirofish_prob, baseline_prob=baseline.prob,
            mirofish_wall_clock_s=wall_clock_s, agent_count=agent_count,
        )
    finally:
        conn.close()

    if swarm_succeeded and mirofish_prob is None:
        swarm_reason = "swarm produced no directional signal (all uncertain)"

    if swarm_succeeded:
        status = "complete"
    elif baseline.prob is not None:
        status = "partial"
    else:
        status = "failed"
    return RunResult(
        status=status, question_id=question_id, mirofish_prob=mirofish_prob,
        baseline_prob=baseline.prob, agent_count=agent_count,
        wall_clock_s=wall_clock_s, reason=swarm_reason,
    )
