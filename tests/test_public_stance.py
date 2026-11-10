"""Agents in the simulation are told the question, the brief's deciding facts and to take a side
in every post; registry agents also open with the forecast they made alone."""

import json

from swarmz.bridge import PersonaSpec
from swarmz.briefparts import digest
from swarmz.mirofish_bridge import commit_in_public, public_prob, stated

BRIEF = """## QUESTION & RESOLUTION CRITERIA
Will the Nasdaq-100 close above 27,899.99 on Nov 10, 2026?

## CONFIRMED FACTS
- The index is published by Nasdaq after the 4pm close.

## QUANTITATIVE ANCHORS
- Latest reading: the Nasdaq-100 closed at 27,907.63 on November 9, 2026.
- Resolution threshold: 27,899.99.

## RECENT TIMELINE
- November 5: the index fell 6.36%.
"""

# A question that resolves on a decision, not a number: almost nothing under QUANTITATIVE ANCHORS.
VOTE = """## QUESTION & RESOLUTION CRITERIA
Will the Senate confirm Dana Reyes as Secretary of Labor before Nov 14, 2026?

## CONFIRMED FACTS
- Current status: the committee approved the nomination 12 to 11 on November 6; no floor vote is scheduled.
- Two senators of the majority have said they will vote no.

## QUANTITATIVE ANCHORS
- No figures decide this question.

## RECENT TIMELINE
- November 9: the majority leader filed cloture, setting up a vote on November 12.

## OPEN UNCERTAINTIES
- Whether the two holdouts can be won over.
"""


def test_digest_leads_with_the_numbers_then_facts_and_timeline():
    d = digest(BRIEF)
    assert d.startswith("QUANTITATIVE ANCHORS\n- Latest reading: the Nasdaq-100 closed at 27,907.63")
    assert d.index("QUANTITATIVE ANCHORS") < d.index("CONFIRMED FACTS") < d.index("RECENT TIMELINE")
    assert "Will the Nasdaq-100 close" not in d            # the question is told separately
    short = digest(BRIEF, max_chars=200)                   # no section over half the room; cut at a line end
    assert "November 9, 2026.\n\nCONFIRMED FACTS" in short and "Resolution threshold" not in short
    assert digest("a brief with no such section") == ""


def test_digest_of_a_question_without_numbers_carries_the_state_of_play():
    # not every question is a price: a vote, a ruling or an election has its deciding facts elsewhere
    d = digest(VOTE)
    for said in ("approved the nomination 12 to 11", "Two senators", "filed cloture", "two holdouts"):
        assert said in d


def test_every_agent_is_told_the_question_the_facts_and_to_take_a_side(tmp_path):
    # 2026-11-10: agents saw only their persona and the feed, never the question. At most 2 of 20
    # said YES or NO in public and the vote share was empty on all six questions.
    path = tmp_path / "reddit_profiles.json"
    path.write_text(json.dumps([{"user_id": 0, "persona": "You are a statistician."},
                                {"user_id": 1, "persona": "You are a trader."}]))
    assert commit_in_public(str(tmp_path), "Will the Nasdaq-100 close above 27,899.99?", BRIEF) == 2
    first, second = json.loads(path.read_text())
    assert first["persona_plain"] == "You are a statistician."        # kept for the private forecast
    assert first["persona"].startswith("You are a statistician.\n\nTHE QUESTION THIS PANEL IS FORECASTING")
    for said in ("Will the Nasdaq-100 close above 27,899.99?", "closed at 27,907.63", "Begin it with YES or NO"):
        assert said in first["persona"] and said in second["persona"]
    assert "YOUR FORECAST BEFORE" not in first["persona"] and "YOUR JOB" not in first["persona"]

    commit_in_public(str(tmp_path), "Will the Nasdaq-100 close above 27,899.99?", BRIEF)   # told twice: said once
    again = json.loads(path.read_text())[0]
    assert again["persona"] == first["persona"] and again["persona_plain"] == "You are a statistician."


def test_an_agent_opens_with_its_own_forecast_and_may_hold_its_own_reading_and_a_job(tmp_path):
    path = tmp_path / "reddit_profiles.json"
    path.write_text(json.dumps([{"user_id": 0, "persona": "A."}, {"user_id": 1, "persona": "B."}]))
    seats = {0: PersonaSpec("p0", "A.", prior_prob=0.3, prior_rationale="Two  holdouts\nremain."),
             1: PersonaSpec("p1", "B.", prior_prob=0.72, prior_rationale="",
                            reading="## RECENT TIMELINE\n- November 9: cloture filed.", role="Attack the leading view.")}
    commit_in_public(str(tmp_path), "Will the Senate confirm?", VOTE, seats=seats)
    a, b = (p["persona"] for p in json.loads(path.read_text()))
    assert "committed to this: NO, 30%. Your reason: Two holdouts remain." in a
    assert "Your first post states this number." in a
    assert "KEY FACTS FROM THE PANEL'S RESEARCH BRIEF" in a and "Two senators" in a and "YOUR JOB" not in a
    assert "committed to this: YES, 72%." in b
    assert "WHAT YOU HAVE READ" in b and "cloture filed" in b and "Two senators" not in b   # only its own part
    assert "YOUR JOB ON THIS PANEL\nAttack the leading view." in b and "not your number" in b
    assert a.index("YOUR FORECAST BEFORE") < a.index("HOW YOU POST")


def test_the_number_an_agent_last_posted_is_read_back():
    assert stated(0.3) == "NO, 30%" and stated(0.655) == "YES, 66%"
    assert public_prob(["YES, 52%: the close was above the strike.", "NO, 45%: futures fell overnight."]) == 0.45
    assert public_prob(["I agree with Lynx.", "yes 61.5% now"]) == 0.615
    assert public_prob(["Rates are at 3.5% and nobody said yes or no."]) is None
    assert public_prob([]) is None


def test_a_brief_without_sections_still_gets_the_question_and_a_missing_file_is_not_fatal(tmp_path):
    assert commit_in_public(str(tmp_path), "Q?", "no sections") == 0
    (tmp_path / "reddit_profiles.json").write_text(json.dumps([{"user_id": 0, "persona": "P."}]))
    assert commit_in_public(str(tmp_path), "Will it rain?", "no sections") == 1
    persona = json.loads((tmp_path / "reddit_profiles.json").read_text())[0]["persona"]
    assert "Will it rain?" in persona and "KEY FACTS" not in persona
