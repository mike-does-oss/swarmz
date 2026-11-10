"""Brier and relative Brier (Component 5).

Brier = (p - o)^2.  RelBrier_i = Brier_i - median_j Brier_j over all agents in the
same run of the question. Negative = better than the run's typical agent.
Aggregates get rel Brier against the same run's agent median.
"""

from __future__ import annotations

from statistics import median

from ..registry.db import rows
from ..timeutil import now


def brier(p: float, o: int) -> float:
    return (p - o) ** 2


def score_question(con, question_id: str) -> int:
    """(Re)score every complete run of a resolved question. Returns rows written. Void/open -> 0."""
    q = rows(con, "SELECT * FROM questions WHERE question_id = ?", [question_id])
    if not q or q[0]["status"] != "resolved":
        return 0
    q = q[0]
    o = int(q["outcome"])
    # A run that started after the outcome was known is not a forecast: mark it late and never score it.
    # (Reversible, so a corrected resolve_ts puts runs back.)
    con.execute("""UPDATE runs SET status = CASE WHEN started_at >= ? THEN 'late' ELSE 'complete' END
                   WHERE question_id = ? AND status IN ('complete', 'late')""", [q["resolve_ts"], question_id])
    con.execute("DELETE FROM scores WHERE question_id = ?", [question_id])
    t = now()
    n = 0
    for run in rows(con, "SELECT run_id, arm FROM runs WHERE question_id = ? AND status = 'complete'", [question_id]):
        fc = rows(con, "SELECT persona_id, prob FROM forecasts WHERE run_id = ?", [run["run_id"]])
        if not fc:
            continue
        b = {f["persona_id"]: brier(f["prob"], o) for f in fc}
        med = median(b.values())
        base = [question_id, run["run_id"], run["arm"]]
        tail = [q["category"], q["split"], q["resolve_ts"], t]
        for f in fc:
            con.execute("INSERT INTO scores VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
                        base + [f["persona_id"], f["prob"], o, b[f["persona_id"]], b[f["persona_id"]] - med] + tail)
            n += 1
        for a in rows(con, "SELECT method, prob FROM aggregates WHERE run_id = ? AND prob IS NOT NULL", [run["run_id"]]):
            ab = brier(a["prob"], o)
            con.execute("INSERT INTO scores VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        base + [a["method"], a["prob"], o, ab, ab - med] + tail)
            n += 1
    return n
