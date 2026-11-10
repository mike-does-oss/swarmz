"""`swarmz report`: leaderboard, arm comparison, calibration bins, learning curve.

Reports read scores directly; nothing here feeds learning.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from ..registry.db import rows

# latest complete run per (question, arm)
_LATEST = """
    latest AS (
        SELECT run_id FROM (
            SELECT run_id, row_number() OVER (PARTITION BY question_id, arm ORDER BY started_at DESC) rn
            FROM runs WHERE status = 'complete') WHERE rn = 1)
"""


def leaderboard(con, population: str | None = None, limit: int = 25) -> list[dict]:
    where = "AND s.arm = ?" if population else ""
    return rows(con, f"""
        SELECT s.arm AS population, s.persona_id, p.status, p.generation, count(*) AS n,
               avg(s.rel_brier) AS mean_rel_brier, avg(s.brier) AS mean_brier
        FROM scores s JOIN personas p USING (persona_id)
        WHERE s.persona_id IS NOT NULL AND s.split = 'train' {where}
        GROUP BY ALL ORDER BY s.arm, mean_rel_brier LIMIT {int(limit)}
    """, [population] if population else [])


def leaderboard_by_category(con, population: str | None = None) -> list[dict]:
    where = "AND s.arm = ?" if population else ""
    return rows(con, f"""
        SELECT s.arm AS population, s.category, s.persona_id, count(*) AS n, avg(s.rel_brier) AS mean_rel_brier
        FROM scores s JOIN personas p USING (persona_id)
        WHERE s.persona_id IS NOT NULL AND s.split = 'train' {where}
        GROUP BY ALL ORDER BY s.arm, s.category, mean_rel_brier
    """, [population] if population else [])


def arm_comparison(con) -> list[dict]:
    return rows(con, f"""
        WITH {_LATEST}
        SELECT s.arm, s.method, s.split, count(*) AS n, avg(s.brier) AS mean_brier
        FROM scores s JOIN latest USING (run_id)
        WHERE s.method IS NOT NULL
        GROUP BY ALL ORDER BY s.split, mean_brier
    """)


def against_baselines(con) -> list[dict]:
    """Does an arm earn its cost? Mean Brier of every arm and aggregate on resolved questions, and
    its gap to three things that cost almost nothing: always saying 50%, one plain model call
    (arm A0), and the market's price when the brief froze. Each gap is over the questions both
    sides forecast. Negative = the arm did better than that baseline."""
    return rows(con, f"""
        WITH {_LATEST},
        s AS (SELECT s.question_id, s.arm, s.method, s.brier FROM scores s JOIN latest USING (run_id)
              WHERE s.method IS NOT NULL AND s.split <> 'excluded'),
        one AS (SELECT question_id, brier FROM s WHERE arm = 'A0' AND method = 'equal'),
        mkt AS (SELECT question_id, power(market_prob - outcome, 2) AS brier FROM questions
                WHERE status = 'resolved' AND market_prob IS NOT NULL)
        SELECT s.arm, s.method, count(*) AS n, round(avg(s.brier), 4) AS mean_brier,
               round(avg(s.brier) - 0.25, 4) AS vs_always_50,
               round(avg(s.brier - one.brier), 4) AS vs_one_call,
               round(avg(s.brier - mkt.brier), 4) AS vs_market, count(mkt.brier) AS n_with_market
        FROM s LEFT JOIN one USING (question_id) LEFT JOIN mkt USING (question_id)
        GROUP BY ALL ORDER BY mean_brier""")


def spread_by_question(con, limit: int = 60) -> list[dict]:
    """How far apart each panel was on each question (latest run per arm), newest question first.
    sd_before is the same agents before they talked; moved is how far the average agent shifted.
    market is the price at freeze where it was recorded."""
    return rows(con, f"""
        WITH {_LATEST}
        SELECT r.question_id, r.arm, count(*) AS n, round(avg(f.prob), 3) AS mean,
               round(stddev_pop(f.prob), 3) AS sd, min(f.prob) AS low, max(f.prob) AS high,
               count(*) FILTER (WHERE f.prob > 0.5) AS yes_side,
               count(*) FILTER (WHERE f.prob < 0.5) AS no_side,
               round(avg(g.prior_prob), 3) AS mean_before, round(stddev_pop(g.prior_prob), 3) AS sd_before,
               round(avg(abs(f.prob - g.prior_prob)), 3) AS moved,
               round(avg(g.public_prob), 3) AS mean_public,
               q.market_prob AS market, q.outcome
        FROM forecasts f JOIN latest USING (run_id) JOIN runs r USING (run_id)
             JOIN questions q ON q.question_id = r.question_id
             LEFT JOIN swarm_stages g ON g.run_id = f.run_id AND g.persona_id = f.persona_id
        WHERE q.split <> 'excluded'
        GROUP BY r.question_id, r.arm, q.freeze_ts, q.market_prob, q.outcome
        HAVING count(*) > 1
        ORDER BY q.freeze_ts DESC, r.question_id, r.arm LIMIT {int(limit)}""")


EXTREMIZE_GRID = (1.0, 1.5, 2.0, 2.5, 3.0)


def extremize_grid(con, grid: tuple[float, ...] = EXTREMIZE_GRID) -> list[dict]:
    """What pushing each panel's answer away from 50% would have scored. For each arm: mean Brier
    of logistic(a * mean log-odds) over its resolved questions, for each a in the grid (a = 1 is
    what the arm reports today). Looks backwards at stored forecasts, so it costs nothing and
    changes nothing. With few questions the best a is noise: adopt one (weighting.extremize_a)
    only when n is large, and note that the calibrated aggregate fits the same multiplier by
    itself once calibration.min_fit_n questions have resolved."""
    from ..capture.aggregate import extremized
    runs = rows(con, f"""
        WITH {_LATEST}
        SELECT r.arm, q.outcome, list(f.prob) AS probs
        FROM forecasts f JOIN latest USING (run_id) JOIN runs r USING (run_id)
             JOIN questions q ON q.question_id = r.question_id
        WHERE q.status = 'resolved' AND q.split <> 'excluded'
        GROUP BY r.arm, r.run_id, q.outcome""")
    by_arm: dict[str, list] = {}
    for r in runs:
        by_arm.setdefault(r["arm"], []).append(r)
    out = []
    for arm in sorted(by_arm):
        briers = {a: sum((extremized(r["probs"], a) - r["outcome"]) ** 2 for r in by_arm[arm]) / len(by_arm[arm])
                  for a in grid}
        out.append({"arm": arm, "n": len(by_arm[arm]), **{f"a={a:g}": round(b, 4) for a, b in briers.items()},
                    "best_a": min(briers, key=briers.get)})
    return out


def calibration_bins(con, method: str = "calibrated", bins: int = 10) -> list[dict]:
    return rows(con, f"""
        WITH {_LATEST}
        SELECT s.arm, least(floor(s.prob * {bins}), {bins - 1})::INT AS bin, count(*) AS n,
               avg(s.prob) AS mean_pred, avg(s.outcome) AS observed
        FROM scores s JOIN latest USING (run_id)
        WHERE s.method = ? AND s.split <> 'excluded'
        GROUP BY ALL ORDER BY s.arm, bin
    """, [method])


def learning_curve(con, method: str = "calibrated") -> list[dict]:
    return rows(con, f"""
        WITH {_LATEST}
        SELECT s.arm, r.generation, count(*) AS n, avg(s.brier) AS holdout_brier
        FROM scores s JOIN latest USING (run_id) JOIN runs r ON r.run_id = s.run_id
        WHERE s.method = ? AND s.split = 'holdout'
        GROUP BY ALL ORDER BY s.arm, r.generation
    """, [method])


def _table(title: str, data: list[dict]) -> Table:
    t = Table(title=title, title_justify="left")
    if not data:
        t.add_column("(no data yet)")
        return t
    for c in data[0]:
        t.add_column(c, justify="right" if isinstance(data[0][c], (int, float)) else "left")
    for r in data:
        t.add_row(*[f"{v:.4f}" if isinstance(v, float) else "" if v is None else str(v) for v in r.values()])
    return t


def persona_by_tag(con, key: str = "category", population: str | None = None, prior: float = 10.0,
                   split: str | None = None) -> list[dict]:
    """Which personas do best on which tag values.

    shrunk_rel_brier pulls each mean toward 0 (the run median) by ``prior`` pseudo-questions,
    so a persona with 2 lucky questions doesn't top the table. Negative = better than the crowd.
    Reporting only: learning never reads tags.
    """
    where, params = ["s.persona_id IS NOT NULL", "t.key = ?"], [key]
    if population:
        where.append("s.arm = ?"); params.append(population)
    if split:
        where.append("s.split = ?"); params.append(split)
    else:
        where.append("s.split <> 'excluded'")
    return rows(con, f"""
        WITH {_LATEST}
        SELECT s.arm AS population, t.value AS tag_value, s.persona_id, count(*) AS n,
               round(avg(s.rel_brier), 4) AS mean_rel_brier,
               round(sum(s.rel_brier) / (count(*) + {float(prior)}), 4) AS shrunk_rel_brier,
               round(avg(s.brier), 4) AS mean_brier
        FROM scores s JOIN latest USING (run_id) JOIN question_tags t ON t.question_id = s.question_id
        WHERE {" AND ".join(where)}
        GROUP BY ALL ORDER BY population, tag_value, shrunk_rel_brier""", params)


def arm_by_tag(con, key: str = "category", split: str | None = None) -> list[dict]:
    """Mean Brier per arm and aggregate method for each tag value (latest run per question and arm)."""
    where, params = ["s.method IS NOT NULL", "t.key = ?"], [key]
    if split:
        where.append("s.split = ?"); params.append(split)
    else:
        where.append("s.split <> 'excluded'")
    return rows(con, f"""
        WITH {_LATEST}
        SELECT t.value AS tag_value, s.arm, s.method, count(*) AS n, round(avg(s.brier), 4) AS mean_brier
        FROM scores s JOIN latest USING (run_id) JOIN question_tags t ON t.question_id = s.question_id
        WHERE {" AND ".join(where)}
        GROUP BY ALL ORDER BY tag_value, mean_brier""", params)


def tag_counts(con) -> list[dict]:
    return rows(con, """
        SELECT t.key, t.value, count(*) AS questions,
               count(*) FILTER (WHERE q.status = 'resolved') AS resolved
        FROM question_tags t JOIN questions q USING (question_id)
        GROUP BY ALL ORDER BY t.key, questions DESC""")


def cost_by_arm(con) -> list[dict]:
    """Spend per arm: runs, total and mean cost, tokens. Includes failed runs (they cost money too)."""
    return rows(con, """
        WITH per_run AS (
            SELECT u.arm, u.run_id, sum(u.cost_usd) AS cost, sum(u.calls) AS calls,
                   sum(u.input_tokens + u.cache_read_tokens + u.cache_write_tokens) AS input_tokens,
                   sum(u.output_tokens) AS output_tokens, bool_or(u.cost_usd IS NULL) AS unpriced
            FROM usage u WHERE u.context = 'run' GROUP BY u.arm, u.run_id)
        SELECT p.arm, count(*) AS runs, count(*) FILTER (WHERE r.status = 'complete') AS complete,
               round(sum(p.cost), 4) AS total_usd, round(avg(p.cost), 4) AS usd_per_run,
               sum(p.calls) AS calls, sum(p.input_tokens) AS input_tokens, sum(p.output_tokens) AS output_tokens,
               bool_or(p.unpriced) AS has_unpriced
        FROM per_run p LEFT JOIN runs r USING (run_id)
        GROUP BY p.arm ORDER BY p.arm""")


def cost_by_stage(con, arm: str | None = None) -> list[dict]:
    """Where the money goes: per arm and stage (setup, simulation, memory, elicit, report, ...)."""
    where, params = "WHERE context = 'run'", []
    if arm:
        where += " AND arm = ?"; params.append(arm)
    return rows(con, f"""
        SELECT arm, stage, model, sum(calls) AS calls,
               sum(input_tokens + cache_read_tokens + cache_write_tokens) AS input_tokens,
               sum(output_tokens) AS output_tokens, round(sum(cost_usd), 4) AS total_usd,
               round(sum(cost_usd) / count(DISTINCT run_id), 4) AS usd_per_run
        FROM usage {where} GROUP BY arm, stage, model ORDER BY arm, total_usd DESC NULLS LAST""", params)


def cost_other(con) -> list[dict]:
    """Spend outside question runs (lessons, calibration, selection/mutation)."""
    return rows(con, """
        SELECT context, stage, model, sum(calls) AS calls, round(sum(cost_usd), 4) AS total_usd
        FROM usage WHERE context != 'run' GROUP BY context, stage, model ORDER BY total_usd DESC NULLS LAST""")


def _spread_compact(data: list[dict]) -> list[dict]:
    """The spread rows folded into fewer, narrower columns so the table fits a terminal."""
    f = lambda v: "" if v is None else f"{v:.2f}"
    return [{"question": r["question_id"], "arm": r["arm"], "n": r["n"], "mean": f(r["mean"]), "sd": f(r["sd"]),
             "range": f"{f(r['low'])}-{f(r['high'])}", "sides": f"{r['yes_side']}/{r['no_side']}",
             "before": f(r["mean_before"]), "sd before": f(r["sd_before"]),
             "moved": "" if r["moved"] is None else f"{r['moved']:.2f}", "posted": f(r["mean_public"]),
             "market": f(r["market"]), "outcome": "" if r["outcome"] is None else str(r["outcome"])} for r in data]


def print_report(con, sections=("leaderboard", "arms", "baselines", "spread", "calibration", "curve", "cost"), population: str | None = None,
                 console: Console | None = None, tag: str = "category") -> None:
    c = console or Console()
    if "leaderboard" in sections:
        c.print(_table("Persona leaderboard (train, mean relative Brier; negative = better)", leaderboard(con, population)))
        c.print(_table("Leaderboard by category", leaderboard_by_category(con, population)))
    if "arms" in sections:
        c.print(_table("Arm comparison (mean Brier, latest run per question)", arm_comparison(con)))
    if "baselines" in sections:
        c.print(_table("Against the cheap baselines (gap in mean Brier; negative = the arm did better)",
                       against_baselines(con)))
    if "spread" in sections:
        c.print(_table("Spread: how far apart each panel was (before = alone, before the discussion; "
                       "sides = agents above/below 50%)", _spread_compact(spread_by_question(con))))
    if "extremize" in sections:
        c.print(_table("Extremizing, looked at backwards (mean Brier if the panel's log-odds were multiplied by a)",
                       extremize_grid(con)))
    if "calibration" in sections:
        c.print(_table("Calibration bins (calibrated aggregate)", calibration_bins(con)))
    if "curve" in sections:
        c.print(_table("Learning curve (holdout Brier by generation)", learning_curve(con)))
    if "tags" in sections:
        c.print(_table("Question tags", tag_counts(con)))
        c.print(_table(f"Arms by {tag} (mean Brier, latest run per question)", arm_by_tag(con, tag)))
        c.print(_table(f"Personas by {tag} (shrunk mean relative Brier; negative = better)",
                       persona_by_tag(con, tag, population)))
    if "cost" in sections:
        c.print(_table("Cost by arm (USD; config/pricing.yaml)", cost_by_arm(con)))
        c.print(_table("Cost by stage", cost_by_stage(con)))
        c.print(_table("Cost outside runs", cost_other(con)))
