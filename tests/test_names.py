"""Persona pseudonyms: stable, unique, and carried into the panel."""

from swarmz.registry import store
from swarmz.registry.names import ensure_names, pseudonym


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

