"""Zep-compatible shim (vendor/mirofish/app/memory_compat.py) over a fake MemoryBackend."""

import json
import time
from datetime import timedelta

import pytest

from swarmz.memory import SHARED_GROUP, FreezeViolation, agent_group
from tests.memory.fake_backend import FakeMemoryBackend

GID = "q_shim__a1__r1"


@pytest.fixture
def shim(vendor_app, freeze_ts):
    backend = FakeMemoryBackend(GID, freeze_ts)
    vendor_app.register_backend(GID, backend)
    return vendor_app, backend


def test_unregistered_graph_uses_factory(vendor_app, freeze_ts):
    made = []
    vendor_app.set_backend_factory(lambda gid: made.append(gid) or FakeMemoryBackend(gid, freeze_ts))
    client = vendor_app.Zep(api_key="ignored")
    client.graph.create(graph_id="q_new__a1__r1", name="n")
    assert made == ["q_new__a1__r1"]


def test_seed_add_batch_goes_to_shared_with_ontology(shim, freeze_ts):
    compat, backend = shim
    client = compat.Zep()
    onto = {"entity_types": [{"name": "Person", "description": "p", "attributes": []}],
            "edge_types": []}
    client.graph.set_ontology(graph_ids=[GID], ontology=onto)
    eps = client.graph.add_batch(graph_id=GID, episodes=[
        compat.EpisodeData(data="Alice met Bob.", type="text"),
        compat.EpisodeData(data="Bob met Carol.", type="text")])
    assert len(eps) == 2 and all(e.uuid_ for e in eps)
    assert client.graph.episode.get(uuid_=eps[0].uuid_).processed is True
    chunks, ontology, ref = backend.seed_calls[0]
    assert chunks == ["Alice met Bob.", "Bob met Carol."]
    assert ontology == onto and backend.ontology == onto
    assert ref < freeze_ts
    assert all(ep.group_id == SHARED_GROUP for ep in backend.episodes)


def test_agent_episodes_keep_group_and_time(shim, freeze_ts):
    compat, backend = shim
    client = compat.Zep()
    ref = compat.reference_time_for_round(GID, 3)
    client.graph.add_batch(graph_id=GID, episodes=[
        compat.EpisodeData(data="Alice: posted", group_id=agent_group(0), reference_time=ref),
        compat.EpisodeData(data="Bob: posted", group_id=agent_group(1), reference_time=ref)])
    assert backend.seed_calls == []
    assert [len(c) for c in backend.add_calls] == [2]
    assert {ep.group_id for ep in backend.episodes} == {agent_group(0), agent_group(1)}
    late = compat.EpisodeData(data="Late: x", group_id=agent_group(0),
                              reference_time=freeze_ts + timedelta(seconds=1))
    with pytest.raises(FreezeViolation):
        client.graph.add_batch(graph_id=GID, episodes=[late])


def test_reference_time_policy(shim, freeze_ts):
    compat, _ = shim
    r0 = compat.reference_time_for_round(GID, 0)
    r5 = compat.reference_time_for_round(GID, 5)
    assert compat.seed_reference_time(GID) < r0 < r5 < freeze_ts
    assert compat.reference_time_for_round(GID, 10_000) == freeze_ts


def test_reads_have_zep_attribute_names(shim, freeze_ts):
    compat, backend = shim
    client = compat.Zep()
    client.graph.add(graph_id=GID, type="text", data="Alice met Bob in Paris.")
    res = client.graph.search(graph_id=GID, query="Alice", limit=5, scope="edges",
                              reranker="cross_encoder")
    assert res.edges and res.edges[0].fact == "Alice met Bob in Paris."
    e = res.edges[0]
    for attr in ("uuid_", "name", "fact", "source_node_uuid", "target_node_uuid", "attributes",
                 "created_at", "valid_at", "invalid_at", "expired_at", "episodes"):
        assert hasattr(e, attr)
    nodes = client.graph.search(graph_id=GID, query="Alice", limit=5, scope="nodes").nodes
    assert nodes and nodes[0].name == "Alice" and nodes[0].labels == ["Entity", "Person"]
    node = client.graph.node.get(uuid_=nodes[0].uuid_)
    assert node.name == "Alice" and node.summary
    assert client.graph.node.get(uuid_="nope") is None
    edges = client.graph.node.get_entity_edges(node_uuid=nodes[0].uuid_)
    assert edges and edges[0].fact.startswith("Alice")


def test_paging_helpers_through_shim(shim):
    compat, backend = shim
    from app.utils.zep_paging import fetch_all_edges, fetch_all_nodes

    client = compat.Zep()
    names = ["Ann", "Ben", "Cat", "Dov", "Eli", "Fay", "Gus", "Hal", "Ivy", "Jon", "Kim", "Lou",
             "Mia", "Ned"]
    for i in range(7):
        client.graph.add(graph_id=GID, data=f"{names[2 * i]} met {names[2 * i + 1]} today.")
    nodes = fetch_all_nodes(client, GID, page_size=3)
    assert len(nodes) == len(backend.entities) == 14
    assert len({n.uuid_ for n in nodes}) == 14
    assert len(fetch_all_edges(client, GID, page_size=2)) == len(backend.facts) == 7


def test_graph_builder_service_flow(shim, monkeypatch):
    """GraphBuilderService (MiroFish) end to end against the shim."""
    compat, backend = shim
    from app.services import graph_builder as gb

    monkeypatch.setattr(gb.time, "sleep", lambda s: None)
    svc = gb.GraphBuilderService()
    assert svc.create_graph("brief", graph_id=GID) == GID
    ontology = {
        "entity_types": [{"name": "Person", "description": "A person.",
                          "attributes": [{"name": "name", "description": "full name"},
                                         {"name": "role", "description": "role"}]}],
        "edge_types": [{"name": "KNOWS", "description": "knows",
                        "attributes": [], "source_targets": [{"source": "Person",
                                                              "target": "Person"}]}],
    }
    svc.set_ontology(GID, ontology)
    names = {e["name"] for e in backend.ontology["entity_types"]}
    assert names == {"Person"}
    attrs = {a["name"] for a in backend.ontology["entity_types"][0]["attributes"]}
    assert attrs == {"entity_name", "role"}
    assert backend.ontology["edge_types"][0]["source_targets"] == [
        {"source": "Person", "target": "Person"}]
    uuids = svc.add_text_batches(GID, ["Alice met Bob.", "Bob met Carol.", "Carol met Dan.",
                                       "Dan met Eve."], batch_size=3)
    assert len(uuids) == 4
    svc._wait_for_episodes(uuids, timeout=5)
    info = svc._get_graph_info(GID)
    assert info.node_count == len(backend.entities)
    data = svc.get_graph_data(GID)
    assert data["edge_count"] == len(backend.facts)
    svc.delete_graph(GID)
    assert backend.deleted and GID not in compat.registered_graph_ids()


def test_entity_reader_and_tools(shim):
    compat, backend = shim
    from app.services.zep_entity_reader import ZepEntityReader
    from app.services.zep_tools import ZepToolsService

    compat.Zep().graph.add(graph_id=GID, data="Alice met Bob in Paris.")
    filtered = ZepEntityReader().filter_defined_entities(GID, enrich_with_edges=True)
    assert {e.name for e in filtered.entities} == {"Alice", "Bob", "Paris"}
    tools = ZepToolsService(llm_client=object())
    res = tools.search_graph(GID, "Alice Paris", limit=5)
    assert set(res.facts) == {"Alice met Bob in Paris."}
    assert len(tools.get_all_edges(GID)) == 2


def test_config_does_not_require_zep_key(vendor_app, monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, "LLM_API_KEY", "x")
    assert Config.validate() == []


def test_ontology_code_text_has_no_zep(vendor_app):
    from app.services.ontology_generator import OntologyGenerator

    gen = OntologyGenerator.__new__(OntologyGenerator)
    code = gen.generate_python_code({"entity_types": [{"name": "Person", "description": "p",
                                                       "attributes": []}],
                                     "edge_types": []})
    assert "zep_cloud" not in code and "memory_compat" in code


# --- memory updater: per agent per round ----------------------------------------

def _action(agent_id, rnd, content):
    return {"agent_id": agent_id, "agent_name": f"Agent{agent_id}", "action_type": "CREATE_POST",
            "action_args": {"content": content}, "round": rnd}


def test_memory_updater_batches_per_agent_per_round(shim, monkeypatch, freeze_ts):
    compat, backend = shim
    from app.services.zep_graph_memory_updater import ZepGraphMemoryUpdater

    up = ZepGraphMemoryUpdater(GID)
    up.start()
    for a in (_action(0, 1, "Alpha one"), _action(0, 1, "Alpha two"), _action(1, 1, "Beta one"),
              {"agent_id": 1, "agent_name": "x", "action_type": "DO_NOTHING", "round": 1}):
        up.add_activity_from_dict(a, "reddit")
    up.add_activity_from_dict(_action(1, 2, "Beta two"), "reddit")  # round 2 -> flush round 1
    deadline = time.time() + 5
    while not backend.add_calls and time.time() < deadline:
        time.sleep(0.05)
    assert len(backend.add_calls) == 1
    r1 = backend.add_calls[0]
    assert sorted(ep.group_id for ep in r1) == [agent_group(0), agent_group(1)]
    a0 = next(ep for ep in r1 if ep.group_id == agent_group(0))
    assert "Alpha one" in a0.body and "Alpha two" in a0.body
    assert a0.reference_time == compat.reference_time_for_round(GID, 1)

    up.stop()  # flushes round 2
    assert len(backend.add_calls) == 2
    (r2,) = backend.add_calls[1]
    assert r2.group_id == agent_group(1) and "Beta two" in r2.body
    assert r2.reference_time > a0.reference_time and r2.reference_time <= freeze_ts
    stats = up.get_stats()
    assert stats["episodes_sent"] == 3 and stats["items_sent"] == 4 and stats["skipped_count"] == 1


def test_memory_updater_flush_round(shim):
    compat, backend = shim
    from app.services.zep_graph_memory_updater import ZepGraphMemoryUpdater

    up = ZepGraphMemoryUpdater(GID)
    up._buffer(compat_activity(0, 4))
    up.flush_round(4, "reddit")
    assert len(backend.add_calls) == 1


def compat_activity(agent_id, rnd):
    from app.services.zep_graph_memory_updater import AgentActivity

    return AgentActivity(platform="reddit", agent_id=agent_id, agent_name="A",
                         action_type="CREATE_POST", action_args={"content": "hi"},
                         round_num=rnd, timestamp="t")


def test_sim_python_env(vendor_app, monkeypatch, tmp_path):
    import sys

    from app.services.simulation_runner import sim_python_executable

    monkeypatch.delenv("MIROFISH_SIM_PYTHON", raising=False)
    assert sim_python_executable() == sys.executable
    fake = tmp_path / "python3.11"
    fake.write_text("")
    monkeypatch.setenv("MIROFISH_SIM_PYTHON", str(fake))
    assert sim_python_executable() == str(fake)
    monkeypatch.setenv("MIROFISH_SIM_PYTHON", str(tmp_path / "missing"))
    with pytest.raises(ValueError):
        sim_python_executable()


def test_build_graph_from_text_accepts_graph_id(shim, monkeypatch):
    compat, backend = shim
    import app.services.prediction.graph_from_text as gft

    class FakeOnto:
        def generate(self, texts, simulation_requirement, additional_context=None):
            return {"entity_types": [{"name": "Person", "description": "p", "attributes": []}],
                    "edge_types": []}

    monkeypatch.setattr(gft, "OntologyGenerator", FakeOnto)
    monkeypatch.setattr("app.services.graph_builder.time.sleep", lambda s: None)
    gid = gft.build_graph_from_text("Alice met Bob. Bob met Carol.", "Will Alice win?",
                                    name="t", graph_id=GID)
    assert gid == GID
    assert backend.seed_calls and backend.ontology["entity_types"][0]["name"] == "Person"
    assert json.dumps(backend.ontology)  # plain-JSON ontology


def test_memory_updater_round_end_marker_is_async(shim):
    compat, backend = shim
    from app.services.zep_graph_memory_updater import ZepGraphMemoryUpdater

    up = ZepGraphMemoryUpdater(GID)
    up.start()
    try:
        up.add_activity_from_dict(_action(2, 7, "Gamma"), "reddit")
        up.flush_round(7, "reddit")  # returns immediately; worker writes
        deadline = time.time() + 5
        while not backend.add_calls and time.time() < deadline:
            time.sleep(0.05)
        assert [ep.group_id for ep in backend.add_calls[0]] == [agent_group(2)]
    finally:
        up.stop()
    assert len(backend.add_calls) == 1
