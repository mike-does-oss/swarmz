"""Persona injection hook (A2) and bridge helpers, no simulation run."""

import csv
import json
import sys

import pytest

from swarmz.memory import Episode, agent_group
from tests.memory.fake_backend import FakeMemoryBackend
from tests.test_discussion import make_db


def _write_profiles(sim_dir, n):
    profiles = [{"user_id": i, "username": f"u{i}", "name": f"N{i}", "bio": "b",
                 "persona": f"generated {i}", "karma": 1000} for i in range(n)]
    (sim_dir / "reddit_profiles.json").write_text(json.dumps(profiles))
    with open(sim_dir / "twitter_profiles.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "name", "username", "user_char", "description"])
        for i in range(n):
            w.writerow([i, f"N{i}", f"u{i}", f"generated {i}", "b"])
    cfg = {"agent_configs": [{"agent_id": i, "activity_level": 0.5} for i in range(n)]}
    (sim_dir / "simulation_config.json").write_text(json.dumps(cfg))


@pytest.mark.parametrize("generated", [3, 2, 5])
def test_apply_personas_exact_count(vendor_app, tmp_path, generated):
    from app.services.persona_injection import apply_personas

    _write_profiles(tmp_path, generated)
    personas = [("p_a", "Contrarian macro trader.\nLikes base rates."),
                ("p_b", "Cautious superforecaster."), ("p_c", "Local journalist.")]
    mapping = apply_personas(str(tmp_path), personas)
    assert mapping == {0: "p_a", 1: "p_b", 2: "p_c"}
    profiles = json.loads((tmp_path / "reddit_profiles.json").read_text())
    assert [p["persona"] for p in profiles] == [t for _, t in personas]
    assert [p["user_id"] for p in profiles] == [0, 1, 2]
    assert profiles[0]["bio"] == "Contrarian macro trader."
    rows = list(csv.DictReader(open(tmp_path / "twitter_profiles.csv")))
    assert [r["user_char"] for r in rows][0] == "Contrarian macro trader. Likes base rates."
    assert len(rows) == 3
    cfg = json.loads((tmp_path / "simulation_config.json").read_text())
    assert [c["agent_id"] for c in cfg["agent_configs"]] == [0, 1, 2]


def test_apply_personas_sets_pseudonyms_everywhere(vendor_app, tmp_path):
    from app.services.persona_injection import apply_personas

    _write_profiles(tmp_path, 2)
    cfg = json.loads((tmp_path / "simulation_config.json").read_text())
    for c in cfg["agent_configs"]:
        c["entity_name"], c["entity_type"] = f"Entity {c['agent_id']}", "TeamAccount"
    (tmp_path / "simulation_config.json").write_text(json.dumps(cfg))
    apply_personas(str(tmp_path), [("p_a", "Trader.", "Adaeze Okafor", "aokafor"), ("p_b", "Judge.", "Jonas Varga", "jvarga")])
    profiles = json.loads((tmp_path / "reddit_profiles.json").read_text())
    assert [(p["name"], p["username"]) for p in profiles] == [("Adaeze Okafor", "aokafor"), ("Jonas Varga", "jvarga")]
    rows = list(csv.DictReader(open(tmp_path / "twitter_profiles.csv")))
    assert [(r["name"], r["username"]) for r in rows] == [("Adaeze Okafor", "aokafor"), ("Jonas Varga", "jvarga")]
    cfg = json.loads((tmp_path / "simulation_config.json").read_text())
    assert [(c["entity_name"], c["entity_type"]) for c in cfg["agent_configs"]] == \
        [("Adaeze Okafor", "Forecaster"), ("Jonas Varga", "Forecaster")]
    # without names the entity names stay (A1 path, and old callers)
    _write_profiles(tmp_path, 2)
    apply_personas(str(tmp_path), [("p_a", "Trader."), ("p_b", "Judge.")])
    assert [p["name"] for p in json.loads((tmp_path / "reddit_profiles.json").read_text())] == ["N0", "N1"]


def test_apply_personas_needs_profiles(vendor_app, tmp_path):
    from app.services.persona_injection import apply_personas

    with pytest.raises(FileNotFoundError):
        apply_personas(str(tmp_path), [("p", "x")])


def test_memory_context_own_group_then_shared(freeze_ts):
    from swarmz.mirofish_bridge import memory_context

    b = FakeMemoryBackend("q_ctx__a1__r1", freeze_ts)
    b.build_seed_graph(["Alice leads the Election polls."], None, freeze_ts)
    b.add_episodes([Episode(name="e", body="Bob doubts the Election result.",
                            group_id=agent_group(3), reference_time=freeze_ts),
                    Episode(name="e2", body="Carol loves the Election.",
                            group_id=agent_group(4), reference_time=freeze_ts)])
    ctx = memory_context(b, 3, "Who wins the election?")
    lines = ctx.splitlines()
    assert lines[0].startswith("- [own] Bob doubts")
    assert any(ln.startswith("- [brief] Alice") for ln in lines)
    assert "Carol" not in ctx
    assert len(memory_context(b, 3, "election", max_chars=40)) <= 40


def test_import_swarmz_does_not_import_mirofish():
    import subprocess

    code = ("import sys, swarmz, swarmz.memory, swarmz.mirofish_bridge; "
            "assert 'app' not in sys.modules, 'vendor app imported'; "
            "assert 'graphiti_core' not in sys.modules, 'graphiti imported eagerly'")
    subprocess.run([sys.executable, "-c", code], check=True)


class _State:
    def __init__(self, sid):
        self.simulation_id = sid
        self.status = "ready"
        self.profiles_count = 0


def _fake_mirofish(monkeypatch, tmp_path, n_generated):
    """Stub every heavy MiroFish collaborator of run_swarm (no LLM, no OASIS)."""
    import app.services.prediction.agent_posts as ap
    import app.services.prediction.graph_from_text as gft
    import app.services.simulation_manager as sm
    import app.services.simulation_runner as sr
    import swarmz.mirofish_bridge as br
    from app import memory_compat

    calls = {}

    def fake_build(brief, question, name="x", graph_id=None):
        calls["graph_id"] = graph_id
        memory_compat.Zep().graph.add(graph_id=graph_id, data="Alice leads the Election polls.")
        return graph_id

    class FakeManager:
        def create_simulation(self, project_id, graph_id, enable_twitter, enable_reddit):
            return _State("sim_1")

        def prepare_simulation(self, sid, simulation_requirement, document_text, num_agents,
                               use_llm_for_profiles=True):
            calls["prepare"] = (num_agents, use_llm_for_profiles)
            _write_profiles(tmp_path, n_generated)
            return _State(sid)

        def _get_simulation_dir(self, sid):
            return str(tmp_path)

        def get_profiles(self, sid, platform="reddit"):
            return json.loads((tmp_path / "reddit_profiles.json").read_text())

    class FakeRunner:
        _processes = {}

        @classmethod
        def start_simulation(cls, sid, platform, max_rounds, enable_graph_memory_update, graph_id):
            calls["start"] = (enable_graph_memory_update, graph_id, max_rounds)
            ref = memory_compat.reference_time_for_round(graph_id, 1)
            memory_compat.Zep().graph.add_batch(graph_id=graph_id, episodes=[
                memory_compat.EpisodeData(data="Dana posted Election doubts.",
                                          group_id=agent_group(0), reference_time=ref)])

        @classmethod
        def get_run_state(cls, sid):
            return type("S", (), {"runner_status": "completed"})()

        @classmethod
        def close_simulation_env(cls, sid):
            calls["closed"] = True

        @classmethod
        def stop_simulation(cls, sid):
            pass

    monkeypatch.setattr(gft, "build_graph_from_text", fake_build)
    monkeypatch.setattr(sm, "SimulationManager", FakeManager)
    monkeypatch.setattr(sr, "SimulationRunner", FakeRunner)
    monkeypatch.setattr(ap, "gather_agents", lambda sid, platform="reddit": [
        {"agent_id": 0, "persona": "", "texts": ["I doubt it."]}])

    def boom(*a, **k):
        raise RuntimeError("report agent down")
    monkeypatch.setattr(br, "_native_prob", boom)
    return calls


def test_run_swarm_a2_wiring(vendor_app, tmp_path, monkeypatch, freeze_ts):
    from swarmz.bridge import PersonaSpec, SwarmRequest
    from swarmz.mirofish_bridge import run_swarm

    calls = _fake_mirofish(monkeypatch, tmp_path, n_generated=3)
    backend = FakeMemoryBackend("q_br__a2__r1", freeze_ts)
    req = SwarmRequest(question_id="q", question="Will the Election go to Alice?",
                       brief_text="brief", freeze_ts=freeze_ts, graph_name="q_br__a2__r1",
                       personas=[PersonaSpec("p1", "Persona one", prior_prob=0.35, prior_rationale="Alice trails.",
                                             role="Attack the leading view."),
                                 PersonaSpec("p2", "Persona two")],
                       max_rounds=3)
    make_db(tmp_path / "reddit_simulation.db")   # the thread OASIS would have left behind
    res = run_swarm(req, backend=backend, poll_interval=0)
    assert calls["graph_id"] == "q_br__a2__r1"
    assert calls["prepare"] == (2, False)
    assert calls["start"] == (False, "q_br__a2__r1", 3)   # agent actions are not written to the graph
    assert [(a.agent_id, a.persona_id, a.persona_text) for a in res.agents] == [
        (0, "p1", "Persona one"), (1, "p2", "Persona two")]
    assert res.agents[0].posts == ["I doubt it."] and res.agents[1].posts == []
    # both agents read the whole thread, each with its own entries marked
    assert all("Inflation is at 3.45%" in a.discussion and "The board cut in March." in a.discussion for a in res.agents)
    assert "(you): The board cut in March." in res.agents[1].discussion
    assert "(you): The board cut in March." not in res.agents[0].discussion
    assert res.meta["discussion"] == {"posts": 2, "replies": 2} and res.meta["agent_graph_memory"] is False
    assert [a.memory_context for a in res.agents] == ["", ""]
    assert res.native_prob is None and "report agent down" in res.meta["native_error"]
    assert res.meta["persona_map"] == {"0": "p1", "1": "p2"}
    # in the simulation each agent's persona also carries the question and the order to take a side;
    # the run reports the persona without it (asserted above)
    assert res.meta["agents_told_to_take_a_side"] == 2
    told = json.loads((tmp_path / "reddit_profiles.json").read_text())[0]["persona"]
    assert told.startswith("Persona one") and "Will the Election go to Alice?" in told and "YES or NO" in told
    # an agent that forecast alone opens with that number, and one with a job is told it
    assert "committed to this: NO, 35%. Your reason: Alice trails." in told and "Attack the leading view." in told
    other = json.loads((tmp_path / "reddit_profiles.json").read_text())[1]["persona"]
    assert "YOUR FORECAST BEFORE" not in other and "YOUR JOB" not in other
    assert res.meta["agents_opening_with_their_own_forecast"] == 1 and res.meta["agents_with_a_job"] == 1
    assert res.agents[0].public_prob is None                  # "I doubt it." names no number
    assert vendor_app.registered_graph_ids() == []


def test_run_swarm_graph_memory_can_be_switched_back_on(vendor_app, tmp_path, monkeypatch, freeze_ts):
    from swarmz.bridge import PersonaSpec, SwarmRequest
    from swarmz.config import settings
    from swarmz.mirofish_bridge import run_swarm

    monkeypatch.setattr(settings(), "agent_graph_memory", True)
    calls = _fake_mirofish(monkeypatch, tmp_path, n_generated=3)
    req = SwarmRequest(question_id="q", question="Will the Election go to Alice?",
                       brief_text="brief", freeze_ts=freeze_ts, graph_name="q_br__a2__r2",
                       personas=[PersonaSpec("p1", "Persona one"), PersonaSpec("p2", "Persona two")],
                       max_rounds=3)
    res = run_swarm(req, backend=FakeMemoryBackend("q_br__a2__r2", freeze_ts), poll_interval=0)
    assert calls["start"] == (True, "q_br__a2__r2", 3)
    assert "[own] Dana posted Election doubts." in res.agents[0].memory_context
    assert "[brief] Alice leads" in res.agents[1].memory_context
    assert res.agents[0].discussion == ""   # no simulation file in this stub


def test_run_swarm_a1_uses_num_agents(vendor_app, tmp_path, monkeypatch, freeze_ts):
    from swarmz.bridge import SwarmRequest
    from swarmz.mirofish_bridge import run_swarm

    calls = _fake_mirofish(monkeypatch, tmp_path, n_generated=4)
    req = SwarmRequest(question_id="q", question="Election?", brief_text="brief",
                       freeze_ts=freeze_ts, graph_name="q_br__a1__r1", personas=None,
                       num_agents=4)
    res = run_swarm(req, backend=FakeMemoryBackend("q_br__a1__r1", freeze_ts), poll_interval=0)
    assert calls["prepare"] == (4, True)
    assert len(res.agents) == 4 and all(a.persona_id is None for a in res.agents)
    assert res.agents[2].persona_text == "generated 2"


def _stances(*sides):
    return [{"agent_id": i, "predicted_outcome": s, "confidence": 0.7, "prediction": ""} for i, s in enumerate(sides)]


def test_native_number_needs_half_the_speakers_to_take_a_side():
    from swarmz.mirofish_bridge import enough_stances, stance_counts

    one_no = stance_counts(_stances("no", *["uncertain"] * 7))
    assert one_no == {"yes": 0, "no": 1, "uncertain": 7}
    assert not enough_stances(one_no, 8)                      # the run that scored 0.000 on one NO
    assert enough_stances(stance_counts(_stances("yes", "no", "no", "no", *["uncertain"] * 4)), 8)
    assert not enough_stances(stance_counts(_stances("yes")), 1)   # a lone voice is not a swarm


def test_native_prob_is_empty_without_a_report_when_few_take_a_side(vendor_app, monkeypatch):
    import app.services.prediction.agent_prediction_extractor as ape
    import app.services.report_agent as ra
    import app.utils.llm_client as lc
    import swarmz.mirofish_bridge as br

    class Report:
        made = 0

        def __init__(self, graph_id, sid, question):
            Report.made += 1

        def generate_structured_report(self, predictions, topic_tags=None):
            return type("R", (), {"to_dict": lambda self: {"scenarios": [
                {"probability": 0.75, "implied_outcome": "no"}, {"probability": 0.25, "implied_outcome": "yes"}]}})()

    monkeypatch.setattr(lc, "LLMClient", lambda: None)
    monkeypatch.setattr(ra, "ReportAgent", Report)
    payload = [{"agent_id": i, "persona": "", "texts": ["x"]} for i in range(8)]

    monkeypatch.setattr(ape, "extract_agent_predictions", lambda a, q, llm: _stances("no", *["uncertain"] * 7))
    assert br._native_prob(payload, "Q?", "g", "sim")[0] is None and Report.made == 0
    monkeypatch.setattr(ape, "extract_agent_predictions",
                        lambda a, q, llm: _stances("yes", "no", "no", "no", *["uncertain"] * 4))
    assert br._native_prob(payload, "Q?", "g", "sim")[0] == 0.25 and Report.made == 1


def test_native_stances_are_read_from_replies_too(vendor_app, tmp_path, monkeypatch, freeze_ts):
    import swarmz.mirofish_bridge as br
    from swarmz.bridge import PersonaSpec, SwarmRequest

    _fake_mirofish(monkeypatch, tmp_path, n_generated=3)
    seen = {}

    def fake_native(payload, question, graph_id, sid):
        seen["texts"] = {a["agent_id"]: a["texts"] for a in payload}
        return None, _stances("no", "uncertain")
    monkeypatch.setattr(br, "_native_prob", fake_native)
    make_db(tmp_path / "reddit_simulation.db")
    req = SwarmRequest(question_id="q", question="Q?", brief_text="brief", freeze_ts=freeze_ts,
                       graph_name="q_br__a2__r3", personas=[PersonaSpec("p1", "one"), PersonaSpec("p2", "two")])
    res = br.run_swarm(req, backend=FakeMemoryBackend("q_br__a2__r3", freeze_ts), poll_interval=0)
    assert seen["texts"][0] == ["Inflation is at 3.45%. Hold looks likely."]
    assert seen["texts"][1][0] == "The board cut in March." and seen["texts"][1][1].startswith("reply number 2")
    assert res.native_prob is None and res.meta["native_stances"] == {"yes": 0, "no": 1, "uncertain": 1}
    assert res.meta["native_error"] == "only 1 of 2 agents who spoke took a YES or NO side"


def test_brief_graph_is_built_once_per_question_and_copied_after(vendor_app, tmp_path, monkeypatch, freeze_ts):
    import swarmz.memory as mem
    import swarmz.mirofish_bridge as br
    from swarmz.bridge import SwarmRequest

    graphs: set[str] = set()   # stands in for the graph database

    def fake_copy(src, dest):
        if src not in graphs or dest in graphs:
            return False
        graphs.add(dest)
        return True

    def fake_backend(name, freeze):
        graphs.add(name)
        return FakeMemoryBackend(name, freeze)
    monkeypatch.setattr(br, "_copy_graph", fake_copy)
    monkeypatch.setattr(mem, "make_backend", fake_backend)

    def run(arm_graph, brief="brief"):
        calls = _fake_mirofish(monkeypatch, tmp_path, n_generated=2)
        req = SwarmRequest(question_id="KX-Q.1", question="Q?", brief_text=brief, freeze_ts=freeze_ts,
                           graph_name=arm_graph, personas=None, num_agents=2)
        return calls, br.run_swarm(req, poll_interval=0)

    kept = br.brief_graph_name("KX-Q.1", "Q?", "brief")
    assert kept.startswith("q_KX_Q_1__brief__")
    calls, first = run("q_KX_Q_1__A1__r1")
    assert calls["graph_id"] == "q_KX_Q_1__A1__r1" and first.meta["brief_graph"] == f"built, kept as {kept}"
    calls, second = run("q_KX_Q_1__A2__r2")
    assert "graph_id" not in calls and second.meta["brief_graph"] == f"copied from {kept}"   # no second build
    calls, other = run("q_KX_Q_1__A2__r3", brief="a different brief")
    assert "graph_id" in calls and other.meta["brief_graph"].startswith("built, kept as")     # new brief, new graph


def test_brief_graph_is_not_shared_when_agents_write_into_it(vendor_app, tmp_path, monkeypatch, freeze_ts):
    import swarmz.memory as mem
    import swarmz.mirofish_bridge as br
    from swarmz.bridge import SwarmRequest
    from swarmz.config import settings

    monkeypatch.setattr(settings(), "agent_graph_memory", True)
    monkeypatch.setattr(br, "_copy_graph", lambda src, dest: pytest.fail("the brief graph must not be shared"))
    monkeypatch.setattr(mem, "make_backend", FakeMemoryBackend)
    calls = _fake_mirofish(monkeypatch, tmp_path, n_generated=2)
    req = SwarmRequest(question_id="q", question="Q?", brief_text="brief", freeze_ts=freeze_ts,
                       graph_name="q_q__A1__r1", personas=None, num_agents=2)
    assert br.run_swarm(req, poll_interval=0).meta["brief_graph"] == "built" and calls["graph_id"] == "q_q__A1__r1"
