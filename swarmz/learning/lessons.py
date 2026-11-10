"""Reflection lessons (Component 7).

After a train question resolves, each persona writes 0-3 one-sentence lessons
about forecasting method. Every lesson passes a contamination lint before it is
saved; rejects are kept with status 'rejected' and the reason.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from ..config import settings
from ..llm import LLM, LLMOutputError
from ..registry import asof
from ..registry.db import rows
from ..timeutil import now, utc

POSTMORTEM_SYSTEM = (
    "You are a forecaster reviewing one of your own past forecasts. Write general lessons about your "
    "forecasting METHOD: base rates, over- or under-confidence, how you weighed kinds of evidence in this "
    "category. Never mention specific people, organisations, places, dates, events, figures, prices, odds, "
    "markets or what actually happened. Each lesson is one sentence that would help on a different question."
)
MERGE_SYSTEM = (
    "You consolidate a forecaster's lessons into fewer, more general ones. Keep only method guidance. "
    "Never mention specific entities, dates, events, prices, odds or markets."
)

# --------------------------------------------------------------------- lint

_MARKET = re.compile(
    r"\b(odds|price[sd]?|pricing|bet|bets|betting|bettors?|bookmakers?|bookies?|wagers?|markets?|"
    r"polymarket|kalshi|predictit|metaculus|consensus|implied probabilit\w*|traders?|order ?book|"
    r"spread|favou?rite|underdog|crowd forecast\w*)\b", re.I)
_OUTCOME = re.compile(
    r"\b(happened|occurred|turned out|ended up|resolved (yes|no)|the (actual )?(outcome|result) was|"
    r"did (not |n't )?(happen|pass|win|occur)|was (approved|elected|passed|signed|announced|rejected|cancelled)|"
    r"(won|lost) the)\b", re.I)
_DATE = re.compile(
    r"\b(19|20)\d{2}\b|\b\d{1,2}/\d{1,2}(/\d{2,4})?\b|\b(january|february|march|april|may|june|july|august|"
    r"september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+\d{1,2}\b|"
    r"\b(q[1-4])\b", re.I)
_STOP = {
    "The", "Will", "Would", "Does", "Did", "Is", "Are", "Was", "Be", "By", "Before", "After", "On", "In", "At",
    "Of", "For", "To", "A", "An", "And", "Or", "If", "What", "When", "Which", "Who", "How", "Yes", "No", "This",
    "That", "Than", "Any", "More", "Less", "Least", "Most", "Above", "Below", "End", "Between", "From", "As", "It",
    "I", "My", "Me", "Be", "Not", "With", "Their", "There", "These", "Those", "Each", "Every", "Under", "Over",
}


def question_terms(question_text: str) -> set[str]:
    """Named-entity-ish tokens from the source question: capitalised words, acronyms, numbers."""
    terms = set()
    for tok in re.findall(r"\b[A-Z][A-Za-z0-9&.'-]*[A-Za-z0-9]\b|\b[A-Z]\b", question_text):
        if tok not in _STOP and len(tok) > 1:
            terms.add(tok)
    for num in re.findall(r"\b\d[\d,.]*\b", question_text):
        if len(num.replace(",", "").replace(".", "")) >= 2:
            terms.add(num)
    return terms


def lint(text: str, question_text: str) -> str | None:
    """Return a rejection reason, or None if the lesson is clean."""
    if m := _MARKET.search(text):
        return f"mentions markets/odds ({m.group(0)!r})"
    if m := _OUTCOME.search(text):
        return f"states an outcome ({m.group(0)!r})"
    if m := _DATE.search(text):
        return f"names a date ({m.group(0)!r})"
    for term in sorted(question_terms(question_text), key=len, reverse=True):
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text):
            return f"names an entity from the source question ({term!r})"
    return None


# ----------------------------------------------------------------- generate

class LessonList(BaseModel):
    lessons: list[str] = Field(default_factory=list, max_length=3)


def postmortem(llm: LLM, inp: dict) -> list[str]:
    user = (f"QUESTION: {inp['question_text']}\nCATEGORY: {inp['category']}\n"
            f"YOUR PROBABILITY: {inp['prob']:.2f}\nYOUR RATIONALE: {inp['rationale']}\n"
            f"OUTCOME: {'YES' if inp['outcome'] == 1 else 'NO'}\n"
            f"MEDIAN PROBABILITY OF YOUR GROUP: {inp['swarm_median']:.2f}\n\n"
            'Return JSON: {"lessons": ["...", "..."]} with 0 to 3 lessons.')
    try:
        raw = llm.json(POSTMORTEM_SYSTEM, user, model=settings().lesson_model, max_tokens=500)
        return [s.strip() for s in LessonList.model_validate(raw).lessons if s and s.strip()]
    except (ValidationError, LLMOutputError, TypeError):
        return []


def _lesson_id(persona_id: str, key: str) -> str:
    return "L" + hashlib.sha256(f"{persona_id}|{key}".encode()).hexdigest()[:12]


def write_lessons_for_question(con, question_id: str, arm: str, as_of: datetime, llm: LLM,
                               review_path: Path | None = None) -> dict:
    """Post-mortems for one resolved train question and one arm's population. Idempotent."""
    if rows(con, "SELECT 1 FROM learning_log WHERE question_id = ? AND population = ? AND step = 'lessons'",
            [question_id, arm]):
        return {"skipped": "already done"}
    inputs = asof.lesson_inputs(con, question_id, arm, as_of)
    saved = rejected = 0
    review: list[str] = []
    for inp in inputs:
        for i, text in enumerate(postmortem(llm, inp)[: settings().learning["lessons"]["max_per_question"]]):
            reason = lint(text, inp["question_text"])
            con.execute("INSERT OR REPLACE INTO lessons VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                        [_lesson_id(inp["persona_id"], f"{question_id}|{i}"), inp["persona_id"], question_id,
                         text, utc(inp["resolve_ts"]), "rejected" if reason else "active", reason, now()])
            saved += reason is None
            rejected += reason is not None
            review.append(f"| {inp['persona_id']} | {'REJECT: ' + reason if reason else 'ok'} | {text} |")
        merge_if_over_cap(con, inp["persona_id"], llm)
    con.execute("INSERT INTO learning_log VALUES (?, ?, 'lessons', ?)", [question_id, arm, now()])
    if review_path and review:
        _append_review(con, review_path, question_id, arm, review)
    return {"saved": saved, "rejected": rejected, "personas": len(inputs)}


def merge_if_over_cap(con, persona_id: str, llm: LLM) -> bool:
    cap = settings().learning["lessons"]["active_cap"]
    active = rows(con, "SELECT lesson_id, text, valid_from FROM lessons WHERE persona_id = ? AND status = 'active' "
                       "ORDER BY valid_from, lesson_id", [persona_id])
    if len(active) <= cap:
        return False
    target = max(1, cap // 2)
    try:
        raw = llm.json(MERGE_SYSTEM, "LESSONS:\n" + "\n".join(f"- {l['text']}" for l in active) +
                       f'\n\nReturn JSON: {{"lessons": [...]}} with at most {target} lessons.',
                       model=settings().lesson_model, max_tokens=800)
        merged = [s.strip() for s in raw.get("lessons", []) if isinstance(s, str) and s.strip()][:target]
    except Exception:
        merged = []
    merged = [m for m in merged if lint(m, "") is None]
    valid_from = max(utc(l["valid_from"]) for l in active)
    if merged:
        new_ids = []
        for i, text in enumerate(merged):
            lid = _lesson_id(persona_id, f"merge|{valid_from.isoformat()}|{i}")
            con.execute("INSERT OR REPLACE INTO lessons VALUES (?, ?, NULL, ?, ?, 'active', NULL, NULL, ?)",
                        [lid, persona_id, text, valid_from, now()])
            new_ids.append(lid)
        for l in active:
            con.execute("UPDATE lessons SET status = 'merged', merged_into = ? WHERE lesson_id = ?",
                        [new_ids[0], l["lesson_id"]])
    else:  # merge failed: retire the oldest beyond the cap
        for l in active[: len(active) - cap]:
            con.execute("UPDATE lessons SET status = 'merged' WHERE lesson_id = ?", [l["lesson_id"]])
    return True


def _append_review(con, path: Path, question_id: str, arm: str, lines: list[str]) -> None:
    n_reviewed = con.execute("SELECT count(DISTINCT question_id) FROM learning_log WHERE step = 'lessons'").fetchone()[0]
    if n_reviewed > settings().learning["lessons"]["review_first_n"]:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a") as f:
        if new:
            f.write("# Lessons review\n\nAll lessons and lint results for the first "
                    f"{settings().learning['lessons']['review_first_n']} resolved questions. Review by hand.\n")
        f.write(f"\n## {question_id} ({arm})\n\n| persona | lint | lesson |\n|---|---|---|\n")
        f.write("\n".join(lines) + "\n")
