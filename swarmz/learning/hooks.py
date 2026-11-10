"""Post-resolution learning: lessons, calibration refits, selection.

Runs for every learning arm. ``as_of`` is the moment the learning happens
(now, or the simulated time in a backtest). Holdout questions never reach any
of this: every read goes through asof.py, which filters to split = 'train'.
"""

from __future__ import annotations

from datetime import datetime

from ..arms.runner import ARMS, LEARNING_ARMS
from ..config import settings
from ..llm import LLM
from ..registry import asof
from ..registry.db import rows
from . import calibration, lessons, selection
from .. import usage


def run_learning(con, as_of: datetime, llm: LLM, seed: int = 0, evolve: bool = True) -> dict:
    with usage.track() as u:
        report = _run_learning(con, as_of, llm, seed, evolve)
    report["cost_usd"] = usage.save(con, u, context="learn")
    return report


def _run_learning(con, as_of: datetime, llm: LLM, seed: int, evolve: bool) -> dict:
    report: dict = {"lessons": {}, "fits": {}, "evolve": {}}
    review = settings().reports_dir / "lessons_review.md"
    resolved = {r["question_id"] for r in asof.resolved_train_questions(con, as_of)}
    for arm in LEARNING_ARMS:
        spec = ARMS[arm]
        if spec.lessons:
            done = {r["question_id"] for r in rows(
                con, "SELECT question_id FROM learning_log WHERE population = ? AND step = 'lessons'", [arm])}
            for qid in sorted(resolved - done):
                has_run = rows(con, "SELECT 1 FROM runs WHERE question_id = ? AND arm = ? AND status = 'complete'",
                               [qid, arm])
                if has_run:
                    report["lessons"][f"{arm}:{qid}"] = lessons.write_lessons_for_question(
                        con, qid, arm, as_of, llm, review)
        if spec.calibration:
            fit = calibration.maybe_refit(con, arm, as_of)
            if fit:
                report["fits"][arm] = fit
        if evolve and spec.kind != "single" and selection.due(con, arm, as_of):
            p = selection.evolve(con, arm, as_of, seed, llm)
            report["evolve"][arm] = p.skipped or f"generation {p.generation}: {len(p.new_personas)} replaced"
    return report
