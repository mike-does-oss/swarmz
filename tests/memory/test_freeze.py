"""Freeze-time guard: no write may carry a reference time after the question's freeze."""

from datetime import timedelta, timezone

import pytest

from swarmz.memory import Episode, FreezeViolation, agent_group
from tests.memory.fake_backend import FakeMemoryBackend


def test_check_reference_time(freeze_ts):
    b = FakeMemoryBackend("q_x__a1__r1", freeze_ts)
    assert b.check_reference_time(freeze_ts) == freeze_ts
    assert b.check_reference_time(freeze_ts - timedelta(days=1)) < freeze_ts
    with pytest.raises(FreezeViolation):
        b.check_reference_time(freeze_ts + timedelta(seconds=1))


def test_naive_times_are_utc(freeze_ts):
    b = FakeMemoryBackend("q_x__a1__r1", freeze_ts.replace(tzinfo=None))
    assert b.freeze_ts.tzinfo == timezone.utc
    with pytest.raises(FreezeViolation):
        b.check_reference_time((freeze_ts + timedelta(minutes=1)).replace(tzinfo=None))


def test_add_episodes_rejects_late_episode(freeze_ts):
    b = FakeMemoryBackend("q_x__a1__r1", freeze_ts)
    late = Episode(name="e", body="Alice x", group_id=agent_group(0),
                   reference_time=freeze_ts + timedelta(hours=1))
    with pytest.raises(FreezeViolation):
        b.add_episodes([late])
    assert b.episodes == []


def test_graphiti_backend_rejects_before_writing(freeze_ts, lite_cfg):
    """GraphitiBackend validates every episode before any graph write (no LLM needed)."""
    from swarmz.memory.graphiti_backend import GraphitiBackend
    from tests.memory.fakes import FakeEmbedder, FakeLLM, FakeReranker

    llm = FakeLLM()
    b = GraphitiBackend("q_freeze__a1__r1", freeze_ts, llm_client=llm, embedder=FakeEmbedder(),
                        cross_encoder=FakeReranker(), falkor=lite_cfg)
    try:
        ok = Episode(name="ok", body="Alice met Bob.", group_id=agent_group(0),
                     reference_time=freeze_ts)
        late = Episode(name="late", body="Carol met Dan.", group_id=agent_group(1),
                       reference_time=freeze_ts + timedelta(seconds=1))
        with pytest.raises(FreezeViolation):
            b.add_episodes([ok, late])
        with pytest.raises(FreezeViolation):
            b.build_seed_graph(["Alice met Bob."], None, freeze_ts + timedelta(days=1))
        assert llm.calls == []
        assert b.count_episodes() == 0
    finally:
        b.delete_graph()
