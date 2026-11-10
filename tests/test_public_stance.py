"""The digest of a brief's deciding facts, and how a panel member's number is written in a post."""

from swarmz.briefparts import digest
from swarmz.panel import stated

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


def test_a_probability_is_posted_with_its_side():
    assert stated(0.3) == "NO, 30%" and stated(0.655) == "YES, 66%"
