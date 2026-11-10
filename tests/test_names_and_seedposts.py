"""Persona pseudonyms (stable, unique, flow into the swarm) and seed-post re-voicing."""

import json

from swarmz.registry import store
from swarmz.registry.names import ensure_names, pseudonym
from swarmz.seedposts import rewrite_seed_posts


def test_pseudonym_is_deterministic_unique_and_ascii_handle():
    a, ha = pseudonym("A2-g0-14970c09")
    assert pseudonym("A2-g0-14970c09") == (a, ha)
    b, _ = pseudonym("A2-g0-14970c09", taken={a})
    assert b != a
    assert ha.isascii() and ha.islower() and " " not in ha and ha == a.lower().replace(" ", "-")
    taken = set()
    for i in range(30):  # a cast of 30 gets 30 different animals
        n, _ = pseudonym(f"p{i}", taken); taken.add(n)
    assert len({n.split()[-1] for n in taken}) == 30


def test_seeded_personas_get_names_and_backfill_fills_gaps(con):
    store.seed_population(con, 6, "claude-haiku-5-5")
    ps = store.active_personas(con, "PANEL")
    names = [p["display_name"] for p in ps if p["status"] != "system"]
    assert len(names) == 6 and len(set(names)) == 6 and all(n for n in names)
    assert all(p["handle"] for p in ps)
    con.execute("UPDATE personas SET display_name = NULL, handle = NULL WHERE population = 'A3'")
    assert ensure_names(con) == 6
    assert ensure_names(con) == 0
    all_names = [r[0] for r in con.execute("SELECT display_name FROM personas").fetchall()]
    assert len(all_names) == len(set(all_names))


def test_swarm_request_carries_names(con, llm, monkeypatch):
    from swarmz.arms.runner import run_arm
    from tests.conftest import T0, add_q, fake_swarm

    store.seed_population(con, 6, "claude-haiku-5-5")
    add_q(con, "Q1", T0)
    seen = {}

    def swarm(req):
        seen["personas"] = req.personas
        return fake_swarm(req)

    run_arm(con, "Q1", "PANEL", llm=llm, swarm=swarm)
    names = [p.name for p in seen["personas"]]
    assert len(names) == 6 and all(names) and len(set(names)) == 6
    assert all(p.handle for p in seen["personas"])


def test_rewrite_seed_posts_keeps_facts_and_marks_original(tmp_path, llm):
    cfg = {"event_config": {"initial_posts": [
        {"content": "Lamar Jackson needs thumb surgery.", "poster_type": "TeamAccount", "poster_agent_id": 0},
        {"content": "Jaguars won 42-7.", "poster_type": "SportsMedia", "poster_agent_id": 2}]}}
    (tmp_path / "simulation_config.json").write_text(json.dumps(cfg))
    n = rewrite_seed_posts(str(tmp_path), {0: ("Adaeze Okafor", "You are a statistician."),
                                           2: ("Jonas Varga", "You are a journalist.")},
                           llm, "claude-haiku-5-5", question="Ravens win?")
    assert n == 2
    posts = json.loads((tmp_path / "simulation_config.json").read_text())["event_config"]["initial_posts"]
    assert posts[0]["content"] == "[revoiced] Lamar Jackson needs thumb surgery."
    assert posts[0]["original_content"] == "Lamar Jackson needs thumb surgery."
    assert posts[1]["poster_type"] == "Forecaster" and posts[1]["poster_agent_id"] == 2


def test_rewrite_seed_posts_survives_llm_failure(tmp_path):
    cfg = {"event_config": {"initial_posts": [{"content": "x", "poster_agent_id": 0}]}}
    (tmp_path / "simulation_config.json").write_text(json.dumps(cfg))

    class Broken:
        def json(self, *a, **k):
            raise RuntimeError("boom")

    assert rewrite_seed_posts(str(tmp_path), {0: ("A", "p")}, Broken(), "m") == 0
    assert json.loads((tmp_path / "simulation_config.json").read_text())["event_config"]["initial_posts"][0]["content"] == "x"
