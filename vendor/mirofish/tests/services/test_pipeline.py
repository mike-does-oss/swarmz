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
    def chat_json(self, messages):
        return {"claims": [{"text": "The vote passed.", "source_urls": ["https://reuters.com/a"],
                            "status": "REPORTED-UNCONFIRMED"}]}


class FakeAssemblerLLM:
    def chat(self, messages, temperature=0.7, max_tokens=4096):
        return "## QUESTION & RESOLUTION CRITERIA\nClean brief body about the vote."


class FakeScrubberLLM:
    def __init__(self, cleaned=None, degraded_payload=None):
        self._cleaned = cleaned
        self._degraded_payload = degraded_payload

    def chat_json(self, messages):
        if self._degraded_payload is not None:
            return self._degraded_payload
        return {"cleaned_brief": self._cleaned, "removals": []}


class FakeKalshi:
    def get_orderbook(self, ticker, depth=10):
        return {"yes": [["0.40", 100]], "no": [["0.55", 50]]}


def _pipeline(tmp_path, scrubber_llm, min_words=1, max_words=100000):
    return BriefPipeline(
        kalshi=FakeKalshi(), search_client=FakeSearch(), fetch_article=_fetch,
        decomposer_llm=FakeDecomposerLLM(), triage_llm=FakeTriageLLM(),
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


def test_build_rejects_on_residual_odds(tmp_path):
    dirty = "The vote result. Betting odds put it at 2/1 currently across books."
    p = _pipeline(tmp_path, FakeScrubberLLM(cleaned=dirty))
    result = p.build(MARKET, now=NOW)
    assert result.status == "rejected" and result.reject_reason == "residual_odds_contamination"


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
