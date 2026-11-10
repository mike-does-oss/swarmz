"""Every time-filtered learning read goes through this module.

Rules enforced here, so callers can't get them wrong:
  * temporal hygiene: only rows whose source question resolved strictly before
    ``as_of`` (scores, calibration data), or whose ``valid_from`` / ``fit_until_ts``
    is strictly before ``as_of`` (lessons, calibration fits);
  * holdout: only questions with split = 'train' and status = 'resolved'.

No other module queries ``scores``, ``lessons`` or ``calibration_fits`` for
learning. (Reports read them directly; reports don't feed learning.)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from ..timeutil import utc
from .db import rows

_TRAIN_RESOLVED = "q.split = 'train' AND q.status = 'resolved' AND q.resolve_ts < ?"


@dataclass
class TrackRecord:
    persona_id: str
    n: int
    mean_rel_brier: float
    mean_brier: float


def track_records(con, arm: str, as_of: datetime, category: str | None = None) -> dict[str, TrackRecord]:
    """Per-persona scored history for one arm's population, known before as_of."""
    as_of = utc(as_of)
    sql = f"""
        SELECT s.persona_id, count(*) AS n, avg(s.rel_brier) AS rel, avg(s.brier) AS brier
        FROM scores s JOIN questions q USING (question_id)
        WHERE s.persona_id IS NOT NULL AND s.arm = ? AND {_TRAIN_RESOLVED}
    """
    params: list = [arm, as_of]
    if category:
        sql += " AND q.category = ?"
        params.append(category)
    sql += " GROUP BY s.persona_id"
    return {r["persona_id"]: TrackRecord(r["persona_id"], r["n"], r["rel"], r["brier"]) for r in rows(con, sql, params)}


def lessons_for(con, persona_id: str, as_of: datetime) -> list[dict]:
    """Active lessons usable at as_of (valid_from before it, from train questions only)."""
    return rows(con, """
        SELECT l.lesson_id, l.text, l.valid_from
        FROM lessons l LEFT JOIN questions q ON q.question_id = l.source_question_id
        WHERE l.persona_id = ? AND l.status = 'active' AND l.valid_from < ?
          AND (l.source_question_id IS NULL OR q.split = 'train')
        ORDER BY l.valid_from, l.lesson_id
    """, [persona_id, utc(as_of)])


def calibration_fit(con, arm: str, method: str, as_of: datetime) -> dict | None:
    """Latest fit whose whole data window closed before as_of."""
    r = rows(con, """
        SELECT fit_id, params_json, fit_until_ts, n_questions FROM calibration_fits
        WHERE arm = ? AND method = ? AND fit_until_ts < ?
        ORDER BY fit_until_ts DESC, n_questions DESC, created_at DESC LIMIT 1
    """, [arm, method, utc(as_of)])
    if not r:
        return None
    out = r[0]
    out["params"] = json.loads(out.pop("params_json"))
    return out


def calibration_data(con, arm: str, method: str, as_of: datetime) -> list[dict]:
    """(prob, outcome, resolve_ts) of one aggregate method on train questions resolved before as_of.

    One row per question: the latest run of that arm on the question.
    """
    return rows(con, f"""
        SELECT question_id, prob, outcome, resolve_ts FROM (
            SELECT s.question_id, s.prob, s.outcome, q.resolve_ts,
                   row_number() OVER (PARTITION BY s.question_id ORDER BY r.started_at DESC) AS rn
            FROM scores s JOIN questions q USING (question_id) JOIN runs r ON r.run_id = s.run_id
            WHERE s.method = ? AND s.arm = ? AND {_TRAIN_RESOLVED}
        ) WHERE rn = 1 ORDER BY resolve_ts, question_id
    """, [method, arm, utc(as_of)])


def resolved_train_questions(con, as_of: datetime) -> list[dict]:
    """Train questions resolved before as_of, in resolution order."""
    return rows(con, f"""
        SELECT q.question_id, q.category, q.resolve_ts, q.outcome FROM questions q
        WHERE {_TRAIN_RESOLVED} ORDER BY q.resolve_ts, q.question_id
    """, [utc(as_of)])


def lesson_inputs(con, question_id: str, arm: str, as_of: datetime) -> list[dict]:
    """Per-persona post-mortem inputs for a resolved train question (latest run of the arm)."""
    return rows(con, f"""
        WITH r AS (
            SELECT run_id FROM runs WHERE question_id = ? AND arm = ? AND status = 'complete'
            ORDER BY started_at DESC LIMIT 1
        )
        SELECT f.persona_id, f.prob, f.rationale, q.question_text, q.category, q.outcome, q.resolve_ts,
               (SELECT median(f2.prob) FROM forecasts f2 WHERE f2.run_id = f.run_id) AS swarm_median
        FROM forecasts f JOIN r USING (run_id) JOIN questions q ON q.question_id = f.question_id
        JOIN personas p ON p.persona_id = f.persona_id
        WHERE {_TRAIN_RESOLVED} AND p.status IN ('active', 'elite', 'system')
        ORDER BY f.persona_id
    """, [question_id, arm, utc(as_of)])
