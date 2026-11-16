from swarmz import threshold
from swarmz.briefparts import brief_slice, standing
from swarmz.threshold import Numbers


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply

    def json(self, system, user, *, model, max_tokens=1024, temperature=None):
        return self.reply


INDEX = {"applies": True, "quantity": "S&P 500 index level", "unit": "points", "latest": 7561.67,
         "latest_is": "observed", "latest_as_of": "13 Nov close", "yes_when": "above", "line": 7434.9999,
         "line_high": None, "time_left": "1 trading day", "typical_move": 72, "typical_move_basis": "about 0.95% a day"}


def test_the_sum_is_done_in_code_and_shown():
    n = threshold.extract(FakeLLM(INDEX), model="m", question="q", brief="b")
    assert round(threshold.chance(n), 2) == 0.96
    text = threshold.note(n)
    assert "126.67 (1.7%) above that line, which is the YES side" in text
    assert "1.8 times that distance away" in text and "about 96 times in 100" in text


def test_below_and_between_lines():
    below = Numbers(quantity="approval", latest=38.9, yes_when="below", line=38.8, typical_move=0.3)
    assert round(threshold.chance(below), 2) == 0.37 and "which is the NO side" in threshold.note(below)
    band = Numbers(quantity="high", unit="°F", latest=67, latest_is="forecast", yes_when="between", line=68, line_high=69.99, typical_move=2.5)
    assert round(threshold.chance(band), 2) == 0.23
    assert "outside that band" in threshold.note(band) and "typical miss for a forecast" in threshold.note(band)


def test_the_stated_chance_never_reaches_certainty():
    far = Numbers(quantity="price", latest=2040, yes_when="above", line=2100, typical_move=5)
    assert "about 2 times in 100" in threshold.note(far)


def test_no_note_when_it_does_not_apply_or_the_numbers_cannot_be_used():
    assert threshold.extract(FakeLLM({"applies": False}), model="m", question="q", brief="b") is None
    assert threshold.extract(FakeLLM({**INDEX, "typical_move": 0}), model="m", question="q", brief="b") is None
    assert threshold.extract(FakeLLM({**INDEX, "yes_when": "between"}), model="m", question="q", brief="b") is None
    assert threshold.extract(FakeLLM({**INDEX, "latest": "unknown"}), model="m", question="q", brief="b") is None
    assert threshold.extract(FakeLLM(["not", "an", "object"]), model="m", question="q", brief="b") is None


BRIEF = """## QUESTION & RESOLUTION CRITERIA

Will the index close above 7,435?

## AS-OF

2026-11-16

## CONFIRMED FACTS

- The exchange is open on Monday.

## QUANTITATIVE ANCHORS

- Latest reading: S&P 500 index level: 7561.67, as of 2026-11-13.
- The index closed at 7,592.40 on November 12.

## STAKEHOLDER POSITIONS & INCENTIVES

- Nobody has a stake.

## OPEN UNCERTAINTIES

- None noted.
"""


def test_every_member_reads_where_things_stand_whichever_slice_it_holds():
    assert standing(BRIEF) == "- Latest reading: S&P 500 index level: 7561.67, as of 2026-11-13."
    got = brief_slice(BRIEF, ("people", "doubts"))      # a slice without the numbers section
    assert "## WHERE THINGS STAND\n\n- Latest reading: S&P 500 index level: 7561.67" in got
    assert "7,592.40" not in got                         # the rest of the numbers still travel by discussion
    assert got.index("## AS-OF") < got.index("## WHERE THINGS STAND") < got.index("## STAKEHOLDER")


def test_a_brief_with_no_latest_reading_adds_nothing():
    plain = BRIEF.replace("- Latest reading: S&P 500 index level: 7561.67, as of 2026-11-13.\n", "")
    assert standing(plain) == "" and "WHERE THINGS STAND" not in brief_slice(plain, ("people", "doubts"))


def test_switched_on_the_note_reaches_every_member_and_is_kept_with_the_run(con, monkeypatch):
    import json
    from datetime import datetime

    from swarmz.arms.runner import run_arm
    from swarmz.config import settings
    from swarmz.registry import store
    from swarmz.registry.db import rows
    from tests.conftest import FakeLLM as PanelLLM, add_q

    class WithNumbers(PanelLLM):
        def __init__(self):
            super().__init__()
            self.read = []

        def json(self, system, user, *, model, max_tokens=1024, temperature=None):
            if system.startswith("You prepare the numbers"):
                return INDEX
            self.read.append(user)
            return super().json(system, user, model=model, max_tokens=max_tokens, temperature=temperature)

    t0 = datetime(2026, 11, 16)
    store.seed_population(con, 6, "m")
    add_q(con, "Q1", t0)
    llm = WithNumbers()
    assert run_arm(con, "Q1", "PANEL", llm=llm, started_at=t0).status == "complete"
    assert not any("ARITHMETIC NOTE" in u for u in llm.read)                 # off unless asked for

    add_q(con, "Q2", t0)
    monkeypatch.setattr(settings(), "threshold_note", True)
    llm = WithNumbers()
    out = run_arm(con, "Q2", "PANEL", llm=llm, started_at=t0)
    assert out.status == "complete", out.error
    assert llm.read and all("about 96 times in 100" in u for u in llm.read)  # alone, in every round, and at the end
    kept = json.loads(rows(con, "SELECT config_json FROM runs WHERE run_id = ?", [out.run_id])[0]["config_json"])
    assert kept["threshold_note"].startswith("ARITHMETIC NOTE")
