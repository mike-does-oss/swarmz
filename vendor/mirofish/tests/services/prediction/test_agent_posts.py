from dataclasses import dataclass

import app.services.prediction.agent_posts as ap


@dataclass
class FakeAction:
    agent_id: int
    action_type: str
    action_args: dict


class FakeRunner:
    actions = [
        FakeAction(0, "CREATE_POST", {"content": "I think yes."}),
        FakeAction(0, "LIKE_POST", {"post_id": 1}),
        FakeAction(1, "CREATE_POST", {"content": "No way."}),
        FakeAction(2, "LIKE_POST", {"post_id": 2}),  # agent 2 never posts
    ]
    last_platform = None

    @classmethod
    def get_all_actions(cls, simulation_id, platform=None, agent_id=None, round_num=None):
        cls.last_platform = platform
        return list(cls.actions)


class FakeManager:
    def get_profiles(self, simulation_id, platform="reddit"):
        return [
            {"user_id": 0, "persona": "economist"},
            {"user_id": 1, "persona": "skeptic"},
            {"user_id": 2, "persona": "lurker"},
        ]


def test_gather_agents_groups_posts_and_maps_persona(monkeypatch):
    monkeypatch.setattr(ap, "SimulationRunner", FakeRunner)
    monkeypatch.setattr(ap, "SimulationManager", lambda *a, **k: FakeManager())
    agents = gathered = ap.gather_agents("sim_x")
    by_id = {a["agent_id"]: a for a in agents}
    # only agents that posted are included (0 and 1, not 2)
    assert set(by_id) == {0, 1}
    assert by_id[0]["persona"] == "economist"
    assert by_id[0]["texts"] == ["I think yes."]
    assert by_id[1]["texts"] == ["No way."]


def test_gather_agents_missing_profile_yields_empty_persona(monkeypatch):
    monkeypatch.setattr(ap, "SimulationRunner", FakeRunner)

    class NoProfiles:
        def get_profiles(self, simulation_id, platform="reddit"):
            return []

    monkeypatch.setattr(ap, "SimulationManager", lambda *a, **k: NoProfiles())
    agents = ap.gather_agents("sim_x")
    assert all(a["persona"] == "" for a in agents)
    assert {a["agent_id"] for a in agents} == {0, 1}


def test_gather_agents_forwards_platform_to_actions(monkeypatch):
    monkeypatch.setattr(ap, "SimulationRunner", FakeRunner)
    monkeypatch.setattr(ap, "SimulationManager", lambda *a, **k: FakeManager())
    ap.gather_agents("sim_x", platform="reddit")
    assert FakeRunner.last_platform == "reddit"


def test_gather_agents_handles_missing_action_args(monkeypatch):
    class Runner2:
        @classmethod
        def get_all_actions(cls, simulation_id, platform=None, agent_id=None, round_num=None):
            @dataclass
            class A:
                agent_id: int
                action_type: str
                action_args: object
            return [A(0, "CREATE_POST", None), A(0, "CREATE_POST", {"content": "yes"})]
    monkeypatch.setattr(ap, "SimulationRunner", Runner2)
    monkeypatch.setattr(ap, "SimulationManager", lambda *a, **k: FakeManager())
    agents = ap.gather_agents("sim_x")
    # None action_args skipped (no content), the valid one kept
    assert agents == [{"agent_id": 0, "persona": "economist", "texts": ["yes"]}]
