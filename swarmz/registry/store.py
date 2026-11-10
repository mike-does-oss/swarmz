"""Plain CRUD on the registry (no learning reads; those live in asof.py)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from ..timeutil import now, utc
from .db import rows

# ---------------------------------------------------------------- personas

# Ten ways of reaching a number. A persona is one of these and nothing else: no job title, no
# biography. A title ("a regulatory lawyer") gave an agent no knowledge the model did not already
# have, and twenty titled agents on one brief landed within a few points of each other. What makes
# panel members differ is the method here, the part of the research each one reads
# (swarmz.diversity) and the lessons each collects from its own scored forecasts.
METHODS = [
    "Start from the base rate: how often things like this have happened before. Move away from it "
    "only as far as the evidence in this case forces you.",
    "Break the question into the things that must all happen for YES. Put a chance on each one and "
    "multiply them.",
    "Assume nothing changes. Count only evidence that a change is already in motion, not talk that "
    "one might come.",
    "Read the resolution wording first: the exact source, threshold and deadline. Ask what would "
    "literally have to be recorded for YES.",
    "Find the strongest case against the obvious answer before you commit, and say what it would "
    "take for that case to win.",
    "Go by the most recent dated evidence and the direction it is moving. Older evidence counts for less.",
    "Compare how much still has to happen with how much time is left. Slow processes rarely finish early.",
    "Write the story that ends in YES and the story that ends in NO. Favour the one that needs fewer surprises.",
    "Measure the gap between where things stand now and where they must be for YES, against how far "
    "things like this usually move in the time left.",
    "Sort the evidence by how well it is confirmed. Trust what is recorded or official, and discount "
    "claims from a single source or from people with a stake.",
]
POPULATIONS = ("PANEL", "A3")
SYSTEM_PERSONAS = {
    "A0": "A0-baseline",
    "A4": "A4-baseline",
}
BASELINE_PROMPT = (
    "You are a calibrated forecaster. Reason only from the research brief provided, "
    "and give your honest probability."
)


def method_prompt(i: int) -> str:
    return ("You forecast binary questions and are scored on the accuracy of your probabilities. "
            f"Your method: {METHODS[i % len(METHODS)]}")


def persona_id(population: str, generation: int, key: str) -> str:
    h = hashlib.sha256(f"{population}|{generation}|{key}".encode()).hexdigest()[:8]
    return f"{population}-g{generation}-{h}"


def insert_persona(con, *, persona_id: str, population: str, base_prompt: str, model: str,
                   generation: int = 0, parent_id: str | None = None, status: str = "active",
                   created_at: datetime | None = None) -> None:
    from .names import pseudonym
    taken = {r[0] for r in con.execute("SELECT display_name FROM personas WHERE display_name IS NOT NULL").fetchall()}
    name, handle = pseudonym(persona_id, taken)
    con.execute("""INSERT INTO personas (persona_id, population, generation, parent_id, status, base_prompt, model,
                                         created_at, display_name, handle)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [persona_id, population, generation, parent_id, status, base_prompt, model,
                 utc(created_at) or now(), name, handle])


def seed_population(con, size: int, model: str, seed: int = 0,
                    populations=POPULATIONS, created_at: datetime | None = None) -> dict[str, list[str]]:
    """Create generation-0 personas, one forecasting method each (methods repeat past ten).
    Populations seeded together get identical prompts, so they start equal. Also ensures the
    single system personas for A0 and A4 exist. Nothing is written if any population is already seeded."""
    for pop in populations:
        if count_personas(con, pop, ("active", "elite")):
            raise ValueError(f"population {pop} already seeded")
    out: dict[str, list[str]] = {}
    prompts = [method_prompt(i) for i in range(size)]
    for pop in populations:
        ids = []
        for i, p in enumerate(prompts):
            pid = persona_id(pop, 0, f"seed{seed}-{i}")
            insert_persona(con, persona_id=pid, population=pop, base_prompt=p, model=model,
                           created_at=created_at)
            ids.append(pid)
        out[pop] = ids
    ensure_system_personas(con, model, created_at)
    return out


def ensure_system_personas(con, model: str, created_at: datetime | None = None) -> None:
    for pop, pid in SYSTEM_PERSONAS.items():
        if not rows(con, "SELECT 1 FROM personas WHERE persona_id = ?", [pid]):
            insert_persona(con, persona_id=pid, population=pop, base_prompt=BASELINE_PROMPT,
                           model=model, status="system", created_at=created_at)


def count_personas(con, population: str, statuses=("active", "elite")) -> int:
    q = f"SELECT count(*) FROM personas WHERE population = ? AND status IN ({','.join('?' * len(statuses))})"
    return con.execute(q, [population, *statuses]).fetchone()[0]


def active_personas(con, population: str, elite_only: bool = False) -> list[dict]:
    statuses = ("elite",) if elite_only else ("active", "elite", "system")
    q = (f"SELECT * FROM personas WHERE population = ? AND status IN ({','.join('?' * len(statuses))}) "
         "ORDER BY persona_id")
    return rows(con, q, [population, *statuses])


def current_generation(con, population: str) -> int:
    r = con.execute("SELECT coalesce(max(generation), 0) FROM personas WHERE population = ?", [population]).fetchone()
    return int(r[0])


# ---------------------------------------------------------------- questions

def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def default_split(question_id: str, holdout_pct: float = 0.2) -> str:
    h = int(hashlib.sha256(question_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "holdout" if h < holdout_pct else "train"


def add_question(con, *, question_id: str, question_text: str, freeze_ts, brief_path: str | Path,
                 category: str = "other", resolution_criteria: str = "", split: str | None = None,
                 source: str = "manual", resolve_ts=None) -> dict:
    brief_path = str(Path(brief_path).resolve())
    sha = sha256_file(brief_path)
    split = split or default_split(question_id)
    if split not in ("train", "holdout"):
        raise ValueError("split must be train or holdout")
    con.execute("""
        INSERT INTO questions (question_id, question_text, resolution_criteria, category, freeze_ts,
                               resolve_ts, brief_sha256, brief_path, split, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [question_id, question_text, resolution_criteria or question_text, category, utc(freeze_ts),
          utc(resolve_ts), sha, brief_path, split, source])
    return get_question(con, question_id)


def get_question(con, question_id: str) -> dict | None:
    r = rows(con, "SELECT * FROM questions WHERE question_id = ?", [question_id])
    return r[0] if r else None


def read_brief(q: dict) -> bytes:
    data = Path(q["brief_path"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != q["brief_sha256"]:
        raise ValueError(f"brief for {q['question_id']} does not match its recorded sha256")
    return data


def set_outcome(con, question_id: str, outcome: int | None, resolve_ts, void: bool = False) -> None:
    if void:
        con.execute("UPDATE questions SET status = 'void', outcome = NULL, resolve_ts = ? WHERE question_id = ?",
                    [utc(resolve_ts), question_id])
        return
    if outcome not in (0, 1):
        raise ValueError("outcome must be 0 or 1")
    con.execute("UPDATE questions SET status = 'resolved', outcome = ?, resolve_ts = ? WHERE question_id = ?",
                [outcome, utc(resolve_ts), question_id])


def exclude_question(con, question_id: str, reason: str) -> dict:
    """Set a question aside: its runs, forecasts and scores stay on record, but nothing learns from
    it and it counts in neither the train nor the holdout results.

    Its lessons are retired (with any merged lesson that absorbed one), calibration fits that could
    have used it are dropped so the next learning pass refits without it, and its learning log
    entries go. Persona selection that already happened on its scores is not undone.
    """
    q = get_question(con, question_id)
    if q is None:
        raise KeyError(f"unknown question {question_id}")
    con.execute("UPDATE questions SET split = 'excluded' WHERE question_id = ?", [question_id])
    con.execute("UPDATE scores SET split = 'excluded' WHERE question_id = ?", [question_id])
    ids = [r["lesson_id"] for r in rows(con, "SELECT lesson_id FROM lessons WHERE source_question_id = ?",
                                        [question_id])]
    retired, seen = 0, set()
    while ids:   # follow merges: a merged lesson built from an excluded one is tainted too
        seen |= set(ids)
        marks = ", ".join("?" for _ in ids)
        retired += con.execute(f"SELECT count(*) FROM lessons WHERE lesson_id IN ({marks}) AND status = 'active'",
                               ids).fetchone()[0]
        con.execute(f"UPDATE lessons SET status = 'retired', lint_reason = ? "
                    f"WHERE lesson_id IN ({marks}) AND status = 'active'", [f"excluded: {reason}"] + ids)
        ids = [r["merged_into"] for r in rows(
            con, f"SELECT DISTINCT merged_into FROM lessons WHERE lesson_id IN ({marks}) AND merged_into IS NOT NULL",
            ids) if r["merged_into"] not in seen]
    fits = 0
    if q["resolve_ts"] is not None:
        fits = con.execute("SELECT count(*) FROM calibration_fits WHERE fit_until_ts >= ?", [q["resolve_ts"]]).fetchone()[0]
        con.execute("DELETE FROM calibration_fits WHERE fit_until_ts >= ?", [q["resolve_ts"]])
    con.execute("DELETE FROM learning_log WHERE question_id = ?", [question_id])
    return {"question_id": question_id, "was": q["split"], "lessons_retired": retired, "fits_dropped": fits}


# ---------------------------------------------------------------- runs

def new_run_id(question_id: str, arm: str, seed: int, started_at: datetime) -> str:
    h = hashlib.sha256(f"{question_id}|{arm}|{seed}|{started_at.isoformat()}".encode()).hexdigest()[:10]
    return f"r{h}"


def start_run(con, *, run_id: str, question_id: str, arm: str, generation: int, seed: int,
              config: dict, model_versions: dict, brief_sha256: str, started_at: datetime) -> None:
    con.execute("""INSERT INTO runs (run_id, question_id, arm, generation, started_at, config_json, seed,
                                    model_versions, brief_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [run_id, question_id, arm, generation, utc(started_at), json.dumps(config, sort_keys=True, default=str),
                 seed, json.dumps(model_versions, sort_keys=True), brief_sha256])


def finish_run(con, run_id: str, status: str, error: str | None = None) -> None:
    con.execute("UPDATE runs SET status = ?, error = ?, finished_at = ? WHERE run_id = ?",
                [status, error, now(), run_id])


def write_forecast(con, *, run_id: str, question_id: str, persona_id: str, prob: float,
                   rationale: str, elicited_at: datetime) -> None:
    con.execute("INSERT INTO forecasts VALUES (?, ?, ?, ?, ?, ?)",
                [run_id, question_id, persona_id, prob, rationale, utc(elicited_at)])


def write_aggregate(con, *, run_id: str, question_id: str, method: str, prob: float | None,
                    computed_at: datetime) -> None:
    con.execute("INSERT INTO aggregates VALUES (?, ?, ?, ?, ?)",
                [run_id, question_id, method, prob, utc(computed_at)])


def write_stage(con, *, run_id: str, question_id: str, persona_id: str, prior_prob: float | None,
                prior_rationale: str, public_prob: float | None, at: datetime) -> None:
    """A swarm agent before the discussion (prior) and in it (the last number it posted)."""
    con.execute("INSERT INTO swarm_stages VALUES (?, ?, ?, ?, ?, ?, ?)",
                [run_id, question_id, persona_id, prior_prob, prior_rationale, public_prob, at])


def set_market_prob(con, question_id: str, prob: float | None, at: datetime | None) -> None:
    """The market's price for YES when the brief froze. Scoring baseline only: no agent sees it."""
    con.execute("UPDATE questions SET market_prob = ?, market_ts = ? WHERE question_id = ?",
                [prob, at, question_id])
