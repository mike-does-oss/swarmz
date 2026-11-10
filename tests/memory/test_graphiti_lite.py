"""GraphitiBackend on embedded FalkorDB (falkordblite).

Default test: real Graphiti pipeline with fake LLM/embedder/reranker (no network).
Integration test: real Anthropic LLM + OpenAI/Voyage embedder (needs keys).
"""

import os
from datetime import timedelta

import pytest

from swarmz.memory import SHARED_GROUP, Episode, agent_group
from swarmz.memory import falkor


@pytest.fixture
def fake_backend_lite(freeze_ts, lite_cfg, request):
    from swarmz.memory.graphiti_backend import GraphitiBackend
    from tests.memory.fakes import FakeEmbedder, FakeLLM, FakeReranker

    name = f"q_test_{request.node.name[:20]}__a1__r1"
    b = GraphitiBackend(name, freeze_ts, llm_client=FakeLLM(), embedder=FakeEmbedder(),
                        cross_encoder=FakeReranker(), falkor=lite_cfg)
    yield b
    b.delete_graph()


ONTOLOGY = {
    "entity_types": [{"name": "Person", "description": "A person",
                      "attributes": [{"name": "role", "description": "role"},
                                     {"name": "name", "description": "reserved name"}]}],
    "edge_types": [{"name": "WORKS_FOR", "description": "employment",
                    "source_targets": [{"source": "Person", "target": "Organization"}]}],
}


def test_seed_agent_groups_search_and_cleanup(fake_backend_lite, freeze_ts, lite_cfg):
    b = fake_backend_lite
    b.build_seed_graph(["Alice works for Acme. Bob founded Acme in Paris.",
                        "Carol met Alice in Berlin."], ONTOLOGY, freeze_ts - timedelta(hours=1))
    b.add_episodes([
        Episode(name="r1a0", body="Dave thinks Acme will win.", group_id=agent_group(0),
                reference_time=freeze_ts - timedelta(seconds=10)),
        Episode(name="r1a1", body="Eve doubts Acme and Bob.", group_id=agent_group(1),
                reference_time=freeze_ts - timedelta(seconds=10)),
    ])
    assert b.count_episodes() == 4
    assert b.count_episodes([agent_group(1)]) == 1

    res = b.search("Alice Acme", limit=10)
    assert any("Alice works for Acme" in f.fact for f in res.facts)

    own = b.search("Acme", group_ids=[agent_group(0)], limit=10, scope="both")
    assert [f.fact for f in own.facts] == ["Dave thinks Acme will win."]
    assert {e.name for e in own.entities} <= {"Dave", "Acme"}
    assert all(e.group_id == agent_group(0) for e in own.entities)

    shared = b.list_entities([SHARED_GROUP])
    assert {"Alice", "Acme", "Bob"} <= {e.name for e in shared}
    assert len(b.list_facts()) > len(b.list_facts([SHARED_GROUP]))

    alice = next(e for e in shared if e.name == "Alice")
    assert b.get_entity(alice.uuid).name == "Alice"
    assert b.entity_facts(alice.uuid)
    assert b.get_entity("does-not-exist") is None

    assert b.graph_name in falkor.list_graphs(cfg=lite_cfg)
    b.delete_graph()
    assert b.graph_name not in falkor.list_graphs(cfg=lite_cfg)


def test_graphs_are_isolated_per_run(freeze_ts, lite_cfg):
    from swarmz.memory.graphiti_backend import GraphitiBackend
    from tests.memory.fakes import FakeEmbedder, FakeLLM, FakeReranker

    kw = dict(llm_client=FakeLLM(), embedder=FakeEmbedder(), cross_encoder=FakeReranker(),
              falkor=lite_cfg)
    b1 = GraphitiBackend("q_iso__a1__r1", freeze_ts, **kw)
    b2 = GraphitiBackend("q_iso__a2__r1", freeze_ts, **kw)
    try:
        b1.add_episodes([Episode(name="x", body="Alice met Bob.", group_id=agent_group(0),
                                 reference_time=freeze_ts)])
        assert b1.count_episodes([agent_group(0)]) == 1
        assert b2.count_episodes([agent_group(0)]) == 0
        assert b2.search("Alice Bob").facts == []
    finally:
        b1.delete_graph()
        b2.delete_graph()


def test_ping_lite(lite_cfg):
    assert falkor.ping(lite_cfg)


def test_ontology_to_models_renames_reserved():
    from swarmz.memory.graphiti_backend import ontology_to_models

    ents, edges, emap = ontology_to_models(ONTOLOGY)
    assert "entity_name" in ents["Person"].model_fields
    assert "role" in ents["Person"].model_fields
    assert emap[("Person", "Organization")] == ["WORKS_FOR"]


def _have_real_keys() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and bool(
        os.environ.get("OPENAI_API_KEY") or os.environ.get("VOYAGE_API_KEY"))


@pytest.mark.integration
@pytest.mark.skipif(not _have_real_keys(), reason="needs ANTHROPIC_API_KEY and an embedding key")
def test_real_stack_smoke(freeze_ts, lite_cfg):
    from swarmz.memory import make_backend

    b = make_backend("q_integration__a1__r1", freeze_ts, falkor=lite_cfg)
    try:
        b.add_episodes([Episode(
            name="smoke", group_id=agent_group(0), reference_time=freeze_ts - timedelta(days=1),
            body="Maria Lopez, the mayor of Springfield, announced a new bridge project on Monday.",
            source_description="smoke test")])
        res = b.search("Who announced the bridge project?", group_ids=[agent_group(0)])
        assert res.facts, "expected at least one fact"
        assert any("bridge" in f.fact.lower() for f in res.facts)
    finally:
        b.delete_graph()


def test_mode_autodetect(monkeypatch):
    import socket

    from swarmz.memory.falkor import REPO_ROOT, config_from_env

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free_port = s.getsockname()[1]
    s.close()  # nothing listens here now
    monkeypatch.delenv("FALKORDB_MODE", raising=False)
    monkeypatch.setenv("FALKORDB_HOST", "127.0.0.1")
    monkeypatch.setenv("FALKORDB_PORT", str(free_port))
    monkeypatch.setenv("FALKORDB_LITE_PATH", "data/x")
    cfg = config_from_env()
    assert cfg.mode == "lite"
    assert cfg.lite_path == str(REPO_ROOT / "data" / "x")

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        monkeypatch.setenv("FALKORDB_PORT", str(listener.getsockname()[1]))
        assert config_from_env().mode == "server"
        monkeypatch.setenv("FALKORDB_MODE", "lite")
        assert config_from_env().mode == "lite"
    finally:
        listener.close()
