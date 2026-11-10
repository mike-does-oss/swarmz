from app.services.zep_entity_reader import EntityNode
from app.services.agent_planner import plan_agent_entities, synthetic_public_entity


def _entity(uid, edges=0):
    return EntityNode(
        uuid=uid, name=f"Entity {uid}", labels=["Person"], summary="s",
        attributes={}, related_edges=[{} for _ in range(edges)], related_nodes=[],
    )


def test_n_equals_e_returns_all():
    ents = [_entity("a"), _entity("b"), _entity("c")]
    out = plan_agent_entities(ents, 3)
    assert len(out) == 3
    assert {e.uuid for e in out} == {"a", "b", "c"}


def test_none_or_zero_defaults_to_e():
    ents = [_entity("a"), _entity("b")]
    assert len(plan_agent_entities(ents, None)) == 2
    assert len(plan_agent_entities(ents, 0)) == 2


def test_n_less_than_e_takes_most_central():
    ents = [_entity("low", edges=0), _entity("high", edges=5), _entity("mid", edges=2)]
    out = plan_agent_entities(ents, 2)
    assert [e.uuid for e in out] == ["high", "mid"]  # top-2 by centrality


def test_n_greater_than_e_keeps_all_and_expands():
    ents = [_entity("a", edges=3), _entity("b", edges=0)]
    out = plan_agent_entities(ents, 10, public_fraction=0.3)
    assert len(out) == 10
    # all originals present
    assert {"a", "b"}.issubset({e.uuid for e in out})
    # extra = 8; public = round(8*0.3)=2 public personas
    publics = [e for e in out if e.attributes.get("synthetic_public")]
    assert len(publics) == 2
    # duplication is weighted toward the more central entity 'a'
    dup_a = sum(1 for e in out if e.uuid == "a") - 1  # minus the one original
    dup_b = sum(1 for e in out if e.uuid == "b") - 1
    assert dup_a > dup_b


def test_is_deterministic():
    ents = [_entity("a", edges=3), _entity("b", edges=1), _entity("c", edges=0)]
    assert [e.uuid for e in plan_agent_entities(ents, 12)] == \
           [e.uuid for e in plan_agent_entities(ents, 12)]


def test_empty_graph_yields_public_personas():
    out = plan_agent_entities([], 4)
    assert len(out) == 4
    assert all(e.attributes.get("synthetic_public") for e in out)


def test_synthetic_public_entity_shape():
    e = synthetic_public_entity(7)
    assert e.uuid == "synthetic-public-7"
    assert e.labels == ["Person"]
    assert e.attributes.get("synthetic_public") is True


def test_n_equals_e_preserves_original_order():
    ents = [_entity("low", edges=0), _entity("high", edges=5), _entity("mid", edges=2)]
    out = plan_agent_entities(ents, 3)
    assert [e.uuid for e in out] == ["low", "high", "mid"]  # original order, not centrality-sorted


def test_none_default_preserves_original_order():
    ents = [_entity("low", edges=0), _entity("high", edges=5)]
    assert [e.uuid for e in plan_agent_entities(ents, None)] == ["low", "high"]
