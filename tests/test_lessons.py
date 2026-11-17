"""Lessons after a question resolves: they are saved once, and a report file that cannot be written does not lose them."""
from datetime import timedelta

from swarmz.arms.runner import run_arm
from swarmz.learning import lessons
from swarmz.learning.hooks import run_learning
from swarmz.registry import store
from swarmz.registry.db import rows
from swarmz.scoring.brier import score_question
from tests.conftest import T0, add_q, fake_swarm


def _resolved_panel_question(con, llm):
    store.seed_population(con, 6, "m")
    add_q(con, "Q1", T0)
    assert run_arm(con, "Q1", "PANEL", llm=llm, swarm=fake_swarm, started_at=T0).status == "complete"
    store.set_outcome(con, "Q1", 1, T0 + timedelta(days=1))
    score_question(con, "Q1")
    return T0 + timedelta(days=2)


def test_the_panel_learns_from_a_resolved_question_once(con, llm):
    later = _resolved_panel_question(con, llm)
    run_learning(con, later, llm, evolve=False)
    n = len(rows(con, "SELECT 1 FROM lessons WHERE status = 'active'"))
    assert n == 6                                           # one lesson from each of the six members
    assert rows(con, "SELECT population, step FROM learning_log") == [{"population": "PANEL", "step": "lessons"}]
    before = len(llm.calls)
    run_learning(con, later, llm, evolve=False)             # a second pass has nothing left to do and calls no model
    assert len(llm.calls) == before and len(rows(con, "SELECT 1 FROM lessons WHERE status = 'active'")) == n


def test_lessons_are_kept_when_the_review_file_cannot_be_written(con, llm, monkeypatch):
    later = _resolved_panel_question(con, llm)

    def read_only(*a, **k):
        raise OSError(30, "Read-only file system")
    monkeypatch.setattr(lessons, "_append_review", read_only)
    run_learning(con, later, llm, evolve=False)
    assert len(rows(con, "SELECT 1 FROM lessons WHERE status = 'active'")) == 6
    assert len(rows(con, "SELECT 1 FROM learning_log")) == 1
