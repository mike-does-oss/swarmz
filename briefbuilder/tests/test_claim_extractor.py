from dataclasses import dataclass

import pytest

from app.services.brief_v2.claim_extractor import (
    Claim, ClaimExtractionError, MAX_CLAIMS, extract_claims, build_prompt, CONFIRMED, REPORTED_UNCONFIRMED,
    clean_reading, extract, reading_problem,
)


@dataclass
class Doc:
    url: str
    source_name: str
    source_tier: int
    publication_time: str
    event_time: object
    full_text: str


class FakeLLM:
    def __init__(self, payload):
        self._payload = payload
        self.messages = None

    def chat_json(self, messages, max_tokens=None):
        self.messages = messages
        self.calls = getattr(self, 'calls', 0) + 1
        if isinstance(self._payload, Exception):
            raise self._payload
        if isinstance(self._payload, list):          # one answer per call, in order
            answer = self._payload[min(self.calls, len(self._payload)) - 1]
            if isinstance(answer, Exception):
                raise answer
            return answer
        return self._payload


DOCS = [
    Doc("https://reuters.com/a", "Reuters", 2, "2026-07-01T00:00:00Z", None, "The vote passed 60-40."),
    Doc("https://apnews.com/b", "AP", 2, "2026-07-01T00:00:00Z", None, "Officials confirmed the vote passed."),
]


def test_build_prompt_includes_doc_fields_and_urls():
    msgs = build_prompt(DOCS)
    joined = " ".join(m["content"] for m in msgs)
    assert "https://reuters.com/a" in joined
    assert "Reuters" in joined
    assert "The vote passed 60-40." in joined


def test_extract_claims_parses_llm_output():
    payload = {"claims": [
        {"text": "The vote passed.", "source_urls": ["https://reuters.com/a", "https://apnews.com/b"],
         "status": "CONFIRMED", "event_time": "2026-06-30T00:00:00Z", "publication_time": "2026-07-01T00:00:00Z"},
    ]}
    claims = extract_claims(DOCS, FakeLLM(payload))
    assert len(claims) == 1
    assert claims[0].text == "The vote passed."
    assert claims[0].status == CONFIRMED
    assert claims[0].source_urls == ["https://reuters.com/a", "https://apnews.com/b"]


def test_extract_claims_drops_unknown_source_urls_and_normalizes_status():
    payload = {"claims": [
        {"text": "X.", "source_urls": ["https://reuters.com/a", "https://hallucinated.com/z"],
         "status": "maybe"},
    ]}
    claims = extract_claims(DOCS, FakeLLM(payload))
    assert claims[0].source_urls == ["https://reuters.com/a"]   # hallucinated url dropped
    assert claims[0].status == REPORTED_UNCONFIRMED             # unknown status normalized


def test_extract_claims_fails_loudly_on_an_unusable_answer():
    # An unusable answer used to come back as "no claims", and the brief was frozen empty.
    for bad in ({"nonsense": True}, "not a dict", ValueError("invalid JSON: cut off")):
        llm = FakeLLM(bad)
        with pytest.raises(ClaimExtractionError):
            extract_claims(DOCS, llm)
        assert llm.calls == 2                       # tried again before giving up


def test_extract_claims_retries_a_cut_off_answer_asking_for_fewer():
    good = {"claims": [{"text": "The vote passed.", "source_urls": ["https://reuters.com/a"], "status": "x"}]}
    llm = FakeLLM([ValueError("invalid JSON: cut off"), good])
    assert [c.text for c in extract_claims(DOCS, llm)] == ["The vote passed."]
    assert f"at most {MAX_CLAIMS // 2} claims" in llm.messages[1]["content"]


def test_prompt_names_the_question_and_bounds_the_answer():
    user = build_prompt(DOCS, "Will oil settle above 86.99?")[1]["content"]
    assert "Will oil settle above 86.99?" in user and "latest value" in user
    assert f"at most {MAX_CLAIMS} claims" in user


def test_extract_claims_drops_claim_with_no_valid_sources():
    payload = {"claims": [
        {"text": "Invented.", "source_urls": ["https://hallucinated.com/z"], "status": "CONFIRMED"},
        {"text": "Real.", "source_urls": ["https://reuters.com/a"], "status": "REPORTED-UNCONFIRMED"},
    ]}
    claims = extract_claims(DOCS, FakeLLM(payload))
    assert [c.text for c in claims] == ["Real."]  # sourceless claim dropped


# -- the latest reading of what the question resolves on ---------------------------------------

@dataclass
class LiveDoc(Doc):
    retrieved_at: str = "2026-11-10T00:00:00Z"
    date_basis: str = "published"
    latest_value: bool = False


QUOTE = LiveDoc("https://x.com/quote", "x", 4, "2026-11-10T00:00:00Z", None, "Nasdaq-100 27,907.63",
                date_basis="retrieved", latest_value=True)
READING = {"quantity": "Nasdaq-100 close", "value": "27,907.63", "as_of": "2026-11-09",
           "updates": "Daily", "source_url": "https://x.com/quote"}
CLAIM = {"text": "The Nasdaq-100 closed at 27,907.63 on November 9, 2026.",
         "source_urls": ["https://x.com/quote"], "status": "REPORTED-UNCONFIRMED"}


def test_prompt_dates_a_quote_page_by_retrieval_and_asks_for_the_reading():
    user = build_prompt([QUOTE, LiveDoc(*DOCS[0].__dict__.values())], question="Will the Nasdaq-100 close above 27,900?")[1]["content"]
    assert "PAGE HAS NO DATE OF ITS OWN. RETRIEVED: 2026-11-10T00:00:00Z" in user
    assert "LOOKED UP FOR: the latest value" in user
    assert "PUBLICATION_TIME: 2026-07-01T00:00:00Z\nRETRIEVED: 2026-11-10T00:00:00Z" in user
    assert '"latest_reading"' in user
    assert '"latest_reading"' not in build_prompt(DOCS)[1]["content"]    # no question, no reading asked for


def test_extract_returns_the_reading_with_the_claims():
    out = extract([QUOTE], FakeLLM({"latest_reading": READING, "claims": [CLAIM]}), "Will it close above 27,900?")
    assert [c.text for c in out.claims] == [CLAIM["text"]]
    assert out.latest_reading == {**READING, "updates": "daily", "kind": "value"}
    assert extract_claims([QUOTE], FakeLLM({"claims": [CLAIM]}), "Q?") == out.claims   # old entry point still works


def test_a_reading_is_only_as_good_as_its_source():
    known = {"https://x.com/quote"}
    assert clean_reading(None, known) is None and clean_reading({"value": "1"}, known) is None
    made_up = clean_reading({**READING, "source_url": "https://elsewhere.com"}, known)
    assert (made_up["value"], made_up["as_of"], made_up["source_url"]) == (None, None, None)
    assert clean_reading({**READING, "updates": "whenever"}, known)["updates"] == "irregular"


def test_pages_looked_up_for_the_latest_value_survive_the_cap_on_documents():
    wires = [LiveDoc(f"https://reuters.com/{i}", "Reuters", 2, "2026-11-01T00:00:00Z", None, "t") for i in range(25)]
    llm = FakeLLM({"claims": []})
    extract(wires + [QUOTE], llm, "Q?")              # the quote page is tier 4 and last in line
    assert "https://x.com/quote" in llm.messages[1]["content"]


def test_reading_problem_judges_only_fast_moving_quantities():
    freeze = "2026-11-10T00:04:00Z"
    assert reading_problem(None, freeze) is None                                   # not a measured quantity
    assert reading_problem({**READING, "updates": "daily"}, freeze) is None        # yesterday's close
    assert reading_problem({**READING, "updates": "daily", "as_of": "2026-11-06"}, freeze) is None   # over a weekend
    assert reading_problem({**READING, "updates": "continuous", "as_of": "2026-09-03"}, freeze) == "stale_reading:2026-09-03"
    assert reading_problem({**READING, "updates": "daily", "value": None}, freeze) == "no_current_reading"
    assert reading_problem({**READING, "updates": "daily", "as_of": None}, freeze) == "reading_has_no_date"
    assert reading_problem({**READING, "updates": "monthly", "as_of": "2026-09-30"}, freeze) is None  # CPI: latest release


# -- questions that resolve on an event or a decision, not a number ----------------------------

STATE = {"kind": "state", "quantity": "Senate confirmation of the nominee",
         "value": "The committee approved the nomination 12 to 11 on November 6; no floor vote has been "
                  "held; a cloture vote is set for November 12.",
         "as_of": "2026-11-09", "updates": "daily", "source_url": "https://x.com/quote"}


def test_prompt_asks_for_the_state_of_play_of_an_event_question():
    user = build_prompt([QUOTE], question="Will the Senate confirm the nominee before Nov 14?")[1]["content"]
    assert '"kind": "value|state"' in user and "the state of play" in user
    assert "the next scheduled step with its date" in user
    # 2026-11-10: the state of an NBA game came back as "favored by 10.5 points", a bookmaker's line
    assert "Keep betting lines, point spreads, odds and prediction-market prices out of it" in user


def test_the_state_of_an_event_is_kept_and_never_judged_for_staleness():
    known = {"https://x.com/quote"}
    state = clean_reading(STATE, known)
    assert state["kind"] == "state" and state["value"].startswith("The committee approved")
    assert state["updates"] == "irregular"                       # whatever rhythm the model claimed
    freeze = "2026-11-10T00:15:00Z"
    assert reading_problem(state, freeze) is None
    assert reading_problem({**state, "as_of": "2026-08-01"}, freeze) is None      # an old ruling still stands
    assert reading_problem({**state, "value": None, "as_of": None}, freeze) is None
    assert reading_problem({**state, "updates": "daily", "value": None}, freeze) is None
    assert clean_reading({**READING, "kind": "nonsense"}, known)["kind"] == "value"
