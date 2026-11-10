"""Backtest: replay historical resolved questions in time order, as if live.

Input is a JSONL file, one question per line:
  {"question_id", "question_text", "category", "freeze_ts", "resolve_ts",
   "outcome": 0 | 1 | null (void), "brief_path", "split"?: "train" | "holdout"}

Events are processed in timestamp order: at each freeze_ts the arms run; at
each resolve_ts the outcome is revealed, scored, and learning runs. Outcomes
are never written to the registry before their resolve_ts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .arms.runner import run_arm
from .config import settings
from .learning.hooks import run_learning
from .llm import LLM
from .registry import store
from .scoring.resolve import Resolution, apply
from .timeutil import utc

EPS = timedelta(microseconds=1)


class TrainingCutoffError(ValueError):
    pass


def models_in_run(extra: list[str] | None = None) -> list[str]:
    s = settings()
    return sorted({s.agent_model, s.elicit_model, s.mutate_model, s.lesson_model, *(extra or [])})


def latest_cutoff(models: list[str]) -> datetime:
    cutoffs = settings().training_cutoffs
    missing = [m for m in models if m not in cutoffs]
    if missing:
        raise TrainingCutoffError(f"no training cutoff recorded in config/models.yaml for: {', '.join(missing)}")
    return max(utc(cutoffs[m]) for m in models)


def check_cutoff(q: dict, cutoff: datetime) -> str | None:
    rt = utc(q["resolve_ts"])
    if rt is None or rt <= cutoff:
        return f"resolved {rt} on or before the latest model training cutoff {cutoff.date()}"
    return None


@dataclass
class BacktestReport:
    refused: dict[str, str] = field(default_factory=dict)
    runs: list = field(default_factory=list)
    learning: list = field(default_factory=list)


def load(path: str | Path) -> list[dict]:
    base = Path(path).parent
    out = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            q = json.loads(line)
            bp = Path(q["brief_path"])
            q["brief_path"] = str(bp if bp.is_absolute() else base / bp)
            out.append(q)
    return out


def run_backtest(con, questions: list[dict], *, arms: list[str], llm: LLM, swarm=None, seed: int = 0,
                 extra_models: list[str] | None = None, on_event=None) -> BacktestReport:
    rep = BacktestReport()
    cutoff = latest_cutoff(models_in_run(extra_models))
    accepted = []
    for q in questions:
        reason = check_cutoff(q, cutoff)
        if reason:
            rep.refused[q["question_id"]] = reason
            continue
        if store.get_question(con, q["question_id"]) is None:
            store.add_question(con, question_id=q["question_id"], question_text=q["question_text"],
                               category=q.get("category", "other"), freeze_ts=q["freeze_ts"],
                               brief_path=q["brief_path"], split=q.get("split"), source="backtest",
                               resolution_criteria=q.get("resolution_criteria", ""))
        accepted.append(q)

    events = []
    for q in accepted:
        events.append((utc(q["freeze_ts"]), 1, "freeze", q))
        events.append((utc(q["resolve_ts"]), 0, "resolve", q))
    events.sort(key=lambda e: (e[0], e[1], e[3]["question_id"]))

    for ts, _, kind, q in events:
        if kind == "freeze":
            for arm in arms:
                out = run_arm(con, q["question_id"], arm, llm=llm, swarm=swarm, seed=seed, started_at=ts)
                rep.runs.append(out)
                if on_event:
                    on_event(f"{ts:%Y-%m-%d} freeze  {q['question_id']} {arm}: {out.status}"
                             + (f" ({out.error})" if out.error else ""))
        else:
            void = q.get("outcome") is None
            apply(con, Resolution(q["question_id"], None if void else int(q["outcome"]), ts, void=void))
            learned = run_learning(con, ts + EPS, llm, seed=seed)
            rep.learning.append((q["question_id"], learned))
            if on_event:
                on_event(f"{ts:%Y-%m-%d} resolve {q['question_id']} -> {'void' if void else q['outcome']}; "
                         f"fits={list(learned['fits'])} evolve={learned['evolve']}")
    return rep
