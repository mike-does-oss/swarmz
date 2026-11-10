from app.services.brief_v2.pipeline import BriefPipeline, BuildResult
from app.storage.db import get_connection, get_prediction, get_provenance


MARKET = {
    "ticker": "KXPRES-24", "title": "Will X win?",
    "rules_primary": "Resolves YES if X wins per AP.", "category": "Politics",
    "close_time": "2026-11-05T00:00:00Z", "event_ticker": "KXPRES",
}
NOW = "2026-07-04T12:00:00Z"


class FakeSearch:
    def search(self, query, count=5, freshness=None):
        return [{"url": "https://reuters.com/a", "title": "T", "page_age": None}]


def _fetch(url):
    return {"full_text": "The ministry stated the vote passed.",
            "publication_time": "2026-07-01T00:00:00Z", "source_name": "Reuters"}


class FakeDecomposerLLM:
    def chat_json(self, messages):
        return {"KEY_ACTORS": ["x background"], "RECENT_EVENTS": ["x recent"]}


class FakeTriageLLM:
    def __init__(self, answer=None):
        self._answer = answer

    def chat_json(self, messages, max_tokens=None):
        if isinstance(self._answer, Exception):
            raise self._answer
        if self._answer is not None:
            return self._answer
        return {"claims": [{"text": "The vote passed.", "source_urls": ["https://reuters.com/a"],
                            "status": "REPORTED-UNCONFIRMED"}]}


class FakeAssemblerLLM:
    def chat(self, messages, temperature=0.7, max_tokens=4096):
        return "## QUESTION & RESOLUTION CRITERIA\nClean brief body about the vote."


class FakeScrubberLLM:
    def __init__(self, cleaned=None, degraded_payload=None):
        self._cleaned = cleaned
        self._degraded_payload = degraded_payload

    def chat_json(self, messages, max_tokens=None):
        if self._degraded_payload is not None:
            return self._degraded_payload
        return {"cleaned_brief": self._cleaned, "removals": []}


class FakeKalshi:
    def get_orderbook(self, ticker, depth=10):
        return {"yes": [["0.40", 100]], "no": [["0.55", 50]]}


def _pipeline(tmp_path, scrubber_llm, min_words=1, max_words=100000, triage_llm=None):
    return BriefPipeline(
        kalshi=FakeKalshi(), search_client=FakeSearch(), fetch_article=_fetch,
        decomposer_llm=FakeDecomposerLLM(), triage_llm=triage_llm or FakeTriageLLM(),
        assembler_llm=FakeAssemblerLLM(), scrubber_llm=scrubber_llm,
        briefs_dir=str(tmp_path / "briefs_v2"), db_path=str(tmp_path / "shoals.db"),
        min_words=min_words, max_words=max_words,
    )


def test_build_freezes_and_persists(tmp_path):
    clean = "The ministry stated the vote passed. Polls show 52 percent support in the region."
    p = _pipeline(tmp_path, FakeScrubberLLM(cleaned=clean))
    result = p.build(MARKET, now=NOW)
    assert result.status == "frozen"
    assert result.brief_hash and result.brief_text == clean
    assert result.word_count == len(clean.split())
    # persisted rows exist
    conn = get_connection(str(tmp_path / "shoals.db"))
    row = get_prediction(conn, "KXPRES-24")
    assert row is not None and row.brief_hash == result.brief_hash
    assert row.market_mid is not None and row.market_snapshot_ts == NOW
    assert get_provenance(conn, result.brief_hash) is not None


def test_build_rejects_when_scrubber_degraded(tmp_path):
    p = _pipeline(tmp_path, FakeScrubberLLM(degraded_payload="not a dict"))
    result = p.build(MARKET, now=NOW)
    assert result.status == "rejected" and result.reject_reason == "scrubber_degraded"


def test_a_line_that_still_mentions_odds_is_dropped_and_the_rest_is_kept(tmp_path):
    dirty = "The vote passed on Tuesday, Reuters reports.\nBetting odds put it at 2/1 currently across books.\nThe ministry confirmed the count."
    result = _pipeline(tmp_path, FakeScrubberLLM(cleaned=dirty)).build(dict(MARKET), now=NOW)
    assert result.status == "frozen"
    assert "odds" not in result.brief_text and "Reuters reports" in result.brief_text and "ministry confirmed" in result.brief_text


def test_a_brief_that_is_nothing_but_odds_is_still_refused(tmp_path):
    dirty = "The vote result. Betting odds put it at 2/1 currently across books."
    result = _pipeline(tmp_path, FakeScrubberLLM(cleaned=dirty)).build(dict(MARKET), now=NOW)
    assert result.status == "rejected" and result.reject_reason.startswith("word_count_out_of_range")


def test_build_rejects_on_word_count_out_of_range(tmp_path):
    clean = "Three word brief."
    p = _pipeline(tmp_path, FakeScrubberLLM(cleaned=clean), min_words=1500, max_words=3000)
    result = p.build(MARKET, now=NOW)
    assert result.status == "rejected"
    assert result.reject_reason.startswith("word_count_out_of_range")


def test_build_rejects_empty_query_set(tmp_path):
    class EmptyDecomposer:
        def chat_json(self, messages):
            return {}
    p = _pipeline(tmp_path, FakeScrubberLLM(cleaned="x"))
    p.decomposer_llm = EmptyDecomposer()
    result = p.build(MARKET, now=NOW)
    assert result.status == "rejected" and result.reject_reason == "empty_query_set"


def test_duplicate_build_rejects(tmp_path):
    clean = "The ministry stated the vote passed. Polls show 52 percent support in the region."
    p = _pipeline(tmp_path, FakeScrubberLLM(cleaned=clean))
    assert p.build(MARKET, now=NOW).status == "frozen"
    second = p.build(MARKET, now=NOW)
    assert second.status == "rejected" and second.reject_reason == "question_already_built"


def test_build_rejects_a_brief_with_no_claims(tmp_path):
    # 2026-11-10: four briefs were frozen with no claims in them and every forecast came back near 50%.
    class NoAssembly:
        def chat(self, *a, **k):
            raise AssertionError("the brief must not be written when there are no claims")

    for triage_llm, reason in ((FakeTriageLLM({"claims": []}), "no_claims:1_docs"),
                               (FakeTriageLLM(ValueError("invalid JSON: cut off")), "claim_extraction_failed")):
        p = _pipeline(tmp_path, FakeScrubberLLM(cleaned="x"), triage_llm=triage_llm)
        p.assembler_llm = NoAssembly()
        result = p.build(MARKET, now=NOW)
        assert (result.status, result.reject_reason) == ("rejected", reason)


def test_build_rejects_a_price_question_without_a_current_price(tmp_path):
    # 2026-11-10: a Bitcoin brief's latest price was two months old; all five arms answered about 50%.
    claims = [{"text": "The vote passed.", "source_urls": ["https://reuters.com/a"], "status": "REPORTED-UNCONFIRMED"}]
    reading = {"quantity": "Bitcoin price", "value": "$78,000", "as_of": "2026-05-01",
               "updates": "continuous", "source_url": "https://reuters.com/a"}

    class NoAssembly:
        def chat(self, *a, **k):
            raise AssertionError("the brief must not be written without a current reading")

    for answer, reason in (({"latest_reading": reading, "claims": claims}, "stale_reading:2026-05-01"),
                           ({"latest_reading": {**reading, "value": None}, "claims": claims}, "no_current_reading")):
        p = _pipeline(tmp_path, FakeScrubberLLM(cleaned="x"), triage_llm=FakeTriageLLM(answer))
        p.assembler_llm = NoAssembly()
        result = p.build(MARKET, now=NOW)
        assert (result.status, result.reject_reason) == ("rejected", reason)
        assert result.latest_reading["quantity"] == "Bitcoin price"


def test_build_hands_the_current_reading_to_the_writer_and_the_result(tmp_path):
    claims = [{"text": "Bitcoin stood at $74,970.", "source_urls": ["https://reuters.com/a"], "status": "REPORTED-UNCONFIRMED"}]
    reading = {"quantity": "Bitcoin price", "value": "$74,970", "as_of": "2026-07-04T11:00:00Z",
               "updates": "continuous", "source_url": "https://reuters.com/a"}

    class Writer(FakeAssemblerLLM):
        def chat(self, messages, **k):
            self.user = messages[-1]["content"]
            return super().chat(messages)

    clean = "The ministry stated the vote passed. Polls show 52 percent support in the region."
    p = _pipeline(tmp_path, FakeScrubberLLM(cleaned=clean),
                  triage_llm=FakeTriageLLM({"latest_reading": reading, "claims": claims}))
    p.assembler_llm = Writer()
    result = p.build(MARKET, now=NOW)
    assert result.status == "frozen" and result.latest_reading == {**reading, "kind": "value"}
    assert "LATEST READING" in p.assembler_llm.user and "$74,970, as of 2026-07-04T11:00:00Z" in p.assembler_llm.user


def test_build_freezes_an_event_question_and_tells_the_writer_where_it_stands(tmp_path):
    # Not every question is about a price: a vote or a ruling has a state of play, never a stale quote.
    claims = [{"text": "The committee approved the nomination.", "source_urls": ["https://reuters.com/a"],
               "status": "CONFIRMED"}]
    state = {"kind": "state", "quantity": "Senate confirmation",
             "value": "Committee approved 12 to 11; no floor vote yet; cloture vote set for November 12.",
             "as_of": "2026-05-01", "updates": "daily", "source_url": "https://reuters.com/a"}

    class Writer(FakeAssemblerLLM):
        def chat(self, messages, **k):
            self.user = messages[-1]["content"]
            return super().chat(messages)

    clean = "The ministry stated the vote passed. Polls show 52 percent support in the region."
    for i, answer in enumerate(({"latest_reading": state, "claims": claims},
                                {"latest_reading": {**state, "value": None}, "claims": claims},   # nothing found: still built
                                {"latest_reading": None, "claims": claims})):
        home = tmp_path / str(i)
        home.mkdir()
        p = _pipeline(home, FakeScrubberLLM(cleaned=clean), triage_llm=FakeTriageLLM(answer))
        p.assembler_llm = Writer()
        result = p.build(MARKET, now=NOW)
        assert result.status == "frozen", result.reject_reason
        if answer["latest_reading"] and answer["latest_reading"]["value"]:
            assert result.latest_reading["kind"] == "state" and result.latest_reading["updates"] == "irregular"
            assert "CURRENT STATE (state it first under CONFIRMED FACTS" in p.assembler_llm.user
            assert "no floor vote yet; cloture vote set for November 12., as of 2026-05-01" in p.assembler_llm.user
            assert "LATEST READING" not in p.assembler_llm.user
        else:
            assert "CURRENT STATE" not in p.assembler_llm.user and "LATEST READING" not in p.assembler_llm.user
