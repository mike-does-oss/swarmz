"""Selection and mutation of personas (`swarmz evolve`, Component 6).

Seeded and deterministic given the same registry state: who is culled, promoted
and which parent each slot copies are decided by a seeded RNG over a sorted
leaderboard. Population size stays constant. Every change goes to
selection_events. Retired personas are kept, never deleted.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel

from ..config import settings
from ..llm import LLM
from ..registry import asof, store
from ..registry.db import rows
from ..timeutil import now, utc

VARIATIONS = [
    "more sceptical of dramatic change",
    "more anchored on historical base rates",
    "more attentive to deadlines, schedules and exact resolution wording",
    "more willing to move far from 50% when evidence is strong",
    "more cautious about extreme probabilities",
    "more focused on the incentives of the key decision makers",
    "more alert to evidence that is stale or second-hand",
]
MUTATE_SYSTEM = (
    "You edit forecaster persona prompts. Rewrite the persona with one controlled variation, "
    "keeping its method and voice. Give it no job title or biography. Do not mention any specific event, entity, date or market."
)


class Mutated(BaseModel):
    base_prompt: str


@dataclass
class Plan:
    population: str
    generation: int          # generation the new personas belong to
    events: list[dict] = field(default_factory=list)
    new_personas: list[dict] = field(default_factory=list)
    skipped: str | None = None


def due(con, population: str, as_of: datetime) -> bool:
    every = settings().learning["selection"]["select_every"]
    last = con.execute("SELECT max(as_of) FROM selection_events WHERE population = ?", [population]).fetchone()[0]
    resolved = asof.resolved_train_questions(con, as_of)
    new = [r for r in resolved if last is None or utc(r["resolve_ts"]) >= utc(last)]
    return len(new) >= every


def plan(con, population: str, as_of: datetime, seed: int) -> Plan:
    cfg = settings().learning["selection"]
    gen = store.current_generation(con, population)
    out = Plan(population, gen + 1)
    personas = [p for p in store.active_personas(con, population) if p["status"] != "system"]
    records = asof.track_records(con, population, as_of)
    eligible = sorted((p for p in personas if (r := records.get(p["persona_id"])) and r.n >= cfg["min_n"]),
                      key=lambda p: (records[p["persona_id"]].mean_rel_brier, p["persona_id"]))
    if len(eligible) < 5:
        out.skipped = f"only {len(eligible)} personas have >= {cfg['min_n']} scored forecasts"
        return out
    rng = random.Random(f"{seed}|{population}|{gen + 1}")
    n_cull = int(math.floor(cfg["cull_pct"] * len(eligible)))
    n_elite = max(1, int(math.ceil(cfg["elite_quantile"] * len(eligible))))
    elite = eligible[:n_elite]
    culled = eligible[len(eligible) - n_cull:] if n_cull else []
    elite_ids = {p["persona_id"] for p in elite}
    culled_ids = {p["persona_id"] for p in culled}

    def ev(pid, action, reason):
        eid = hashlib.sha256(f"{population}|{gen + 1}|{pid}|{action}|{seed}".encode()).hexdigest()[:12]
        out.events.append({"event_id": eid, "population": population, "generation": gen + 1,
                           "persona_id": pid, "action": action, "reason": reason})

    for p in eligible:
        rel = records[p["persona_id"]].mean_rel_brier
        if p["persona_id"] in culled_ids:
            ev(p["persona_id"], "cull", f"bottom {cfg['cull_pct']:.0%}, mean rel Brier {rel:+.4f}")
        elif p["persona_id"] in elite_ids:
            ev(p["persona_id"], "promote", f"top quintile, mean rel Brier {rel:+.4f}")
        else:
            ev(p["persona_id"], "keep", f"mean rel Brier {rel:+.4f}")

    n_explore = min(n_cull, int(round(cfg["explore_pct"] * len(personas))))
    for slot in range(n_cull):
        pid = store.persona_id(population, gen + 1, f"{seed}-{slot}")
        if slot < n_cull - n_explore:
            parent = rng.choice(elite)
            variation = rng.choice(VARIATIONS)
            out.new_personas.append({"persona_id": pid, "parent_id": parent["persona_id"], "kind": "mutate",
                                     "parent_prompt": parent["base_prompt"], "variation": variation,
                                     "model": parent["model"]})
            ev(pid, "mutate", f"child of {parent['persona_id']}: {variation}")
        else:
            prompt = store.method_prompt(rng.randrange(len(store.METHODS)))
            out.new_personas.append({"persona_id": pid, "parent_id": None, "kind": "spawn",
                                     "base_prompt": prompt, "model": settings().agent_model})
            ev(pid, "spawn", "fresh persona (explore)")
    return out


def mutate_prompt(llm: LLM, parent_prompt: str, variation: str) -> str:
    raw = llm.json(MUTATE_SYSTEM,
                   f"PARENT PERSONA:\n{parent_prompt}\n\nVARIATION: make this forecaster {variation}.\n\n"
                   'Return JSON: {"base_prompt": "<the rewritten persona, 2-5 sentences>"}',
                   model=settings().mutate_model, max_tokens=600)
    return Mutated.model_validate(raw).base_prompt.strip()


def evolve(con, population: str, as_of: datetime, seed: int, llm: LLM, dry_run: bool = False) -> Plan:
    p = plan(con, population, as_of, seed)
    if dry_run or p.skipped:
        return p
    t = utc(as_of)
    for np_ in p.new_personas:
        if np_["kind"] == "mutate":
            np_["base_prompt"] = mutate_prompt(llm, np_["parent_prompt"], np_["variation"])
    con.execute("BEGIN")
    try:
        for e in p.events:
            if e["action"] == "cull":
                con.execute("UPDATE personas SET status = 'retired' WHERE persona_id = ?", [e["persona_id"]])
            elif e["action"] == "promote":
                con.execute("UPDATE personas SET status = 'elite' WHERE persona_id = ?", [e["persona_id"]])
            elif e["action"] == "keep":
                con.execute("UPDATE personas SET status = 'active' WHERE persona_id = ?", [e["persona_id"]])
        for np_ in p.new_personas:
            store.insert_persona(con, persona_id=np_["persona_id"], population=population,
                                 base_prompt=np_["base_prompt"], model=np_["model"], generation=p.generation,
                                 parent_id=np_["parent_id"], created_at=t)
        for e in p.events:
            con.execute("INSERT INTO selection_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [e["event_id"], e["population"], e["generation"], e["persona_id"], e["action"],
                         e["reason"], t, now()])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return p


def population_events(con, population: str) -> list[dict]:
    return rows(con, "SELECT generation, persona_id, action, reason FROM selection_events "
                     "WHERE population = ? ORDER BY generation, action, persona_id", [population])
