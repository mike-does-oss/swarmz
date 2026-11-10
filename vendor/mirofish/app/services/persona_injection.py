"""Persona injection (swarmz local change, arm A2).

Hook point: after ``SimulationManager.prepare_simulation(num_agents=len(personas))``
has written the profile files and simulation_config.json, overwrite each
agent's persona text with the given persona and return agent_id -> persona_id.

Why here: prepare_simulation already sizes the swarm to exactly N agents
(agent_planner pads/trims entities to N), generates profile files OASIS reads
(reddit_profiles.json / twitter_profiles.csv) and an agent config keyed by the
same agent ids. Rewriting only the persona fields keeps every other part of the
simulation (activity config, initial posts, names) untouched. Callers should pass
``use_llm_for_profiles=False`` to prepare_simulation when injecting, since the
LLM-written personas would be discarded anyway.

Agent i (``user_id`` i, in file order) gets ``personas[i]``.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Dict, List, Sequence, Tuple

REDDIT_FILE = "reddit_profiles.json"
TWITTER_FILE = "twitter_profiles.csv"


def _bio(text: str, limit: int = 150) -> str:
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return first[:limit]


def _fit(items: list, n: int, make_extra) -> list:
    """Trim to n, or pad by cloning the last item via make_extra(template, new_index)."""
    if len(items) >= n:
        return items[:n]
    out = list(items)
    while len(out) < n:
        out.append(make_extra(out[-1] if out else None, len(out)))
    return out


def apply_personas(sim_dir: str, personas: Sequence[tuple]) -> Dict[int, str]:
    """Overwrite persona text (and, when given, the public name) in the prepared profile files.

    Args:
        sim_dir: the simulation directory (contains the profile files)
        personas: [(persona_id, persona_text[, name, handle]), ...]; agent i gets personas[i].
            With a name, the agent posts under it instead of the entity MiroFish derived
            from the brief (see swarmz.registry.names), in the profiles and in the
            agent_configs entity_name the action log and memory writer use.

    Returns:
        {agent_id: persona_id}
    """
    personas = [(p[0], p[1], p[2] if len(p) > 2 and p[2] else None, p[3] if len(p) > 3 and p[3] else None)
                for p in personas]
    names = {i: (nm, hd or f"agent_{i}") for i, (_, _, nm, hd) in enumerate(personas) if nm}
    n = len(personas)
    if n == 0:
        raise ValueError("apply_personas: empty persona list")
    mapping: Dict[int, str] = {}
    touched = False

    reddit_path = os.path.join(sim_dir, REDDIT_FILE)
    if os.path.exists(reddit_path):
        with open(reddit_path, "r", encoding="utf-8") as f:
            profiles: List[dict] = json.load(f)

        def _extra(tpl, idx):
            p = dict(tpl or {})
            p.update({"user_id": idx, "username": f"agent_{idx}", "name": f"Agent {idx}"})
            return p

        profiles = _fit(profiles, n, _extra)
        for i, (pid, text, _nm, _hd) in enumerate(personas):
            p = profiles[i]
            p["user_id"] = i
            p["persona"] = text
            p["bio"] = _bio(text) or p.get("bio", "")
            if i in names:
                p["name"], p["username"] = names[i]
            mapping[i] = pid
        with open(reddit_path, "w", encoding="utf-8") as f:
            json.dump(profiles, f, ensure_ascii=False, indent=2)
        touched = True

    twitter_path = os.path.join(sim_dir, TWITTER_FILE)
    if os.path.exists(twitter_path):
        with open(twitter_path, "r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        fieldnames = ["user_id", "name", "username", "user_char", "description"]

        def _extra_row(tpl, idx):
            r = dict(tpl or {k: "" for k in fieldnames})
            r.update({"user_id": idx, "username": f"agent_{idx}", "name": f"Agent {idx}"})
            return r

        rows = _fit(rows, n, _extra_row)
        for i, (pid, text, _nm, _hd) in enumerate(personas):
            r = rows[i]
            r["user_id"] = i
            r["user_char"] = (text or "").replace("\n", " ").replace("\r", " ")
            r["description"] = _bio(text) or r.get("description", "")
            if i in names:
                r["name"], r["username"] = names[i]
            mapping[i] = pid
        with open(twitter_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        touched = True

    if not touched:
        raise FileNotFoundError(f"no profile files in {sim_dir}; run prepare_simulation first")

    # Keep the activity config in step with the agent count (only ever changes when
    # profile generation produced a different count than requested).
    config_path = os.path.join(sim_dir, "simulation_config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        cfgs = [c for c in config.get("agent_configs") or [] if int(c.get("agent_id", 0)) < n]
        have = {int(c.get("agent_id", 0)) for c in cfgs}
        template = cfgs[-1] if cfgs else None
        if template is not None:
            for i in range(n):
                if i not in have:
                    c = dict(template)
                    c["agent_id"] = i
                    cfgs.append(c)
        for c in cfgs:  # the action log and memory writer name agents from here
            i = int(c.get("agent_id", 0))
            if i in names:
                c["entity_name"], c["entity_type"] = names[i][0], "Forecaster"
        if names or cfgs != config.get("agent_configs"):
            config["agent_configs"] = sorted(cfgs, key=lambda c: int(c.get("agent_id", 0)))
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
    return mapping
