from datetime import datetime, timezone

from app.services.brief_v2.retriever import RetrievedDoc, is_live, retrieve, parse_iso


class FakeSearch:
    def __init__(self, results_by_query):
        self._by_query = results_by_query

    def search(self, query, count=5, freshness=None):
        return self._by_query.get(query, [])


def _fetcher(table):
    """table: url -> article dict (or None)."""
    def _fetch(url):
        return table.get(url)
    return _fetch


FREEZE = "2026-07-04T12:00:00Z"
NOW = "2026-07-04T12:00:00Z"


def test_parse_iso_variants():
    assert parse_iso("2026-07-04T12:00:00Z") is not None
    assert parse_iso("2026-07-04") is not None
    assert parse_iso(None) is None
    assert parse_iso("not a date") is None


def test_retrieve_keeps_prefreeze_docs_with_full_fields():
    search = FakeSearch({"q1": [{"url": "https://www.reuters.com/a", "title": "Reuters headline", "page_age": None}]})
    table = {"https://www.reuters.com/a": {
        "full_text": "Body.", "publication_time": "2026-07-01T00:00:00Z", "source_name": "Reuters"}}
    docs, discarded = retrieve(["q1"], FREEZE, NOW,
                               search_client=search, fetch_article=_fetcher(table))
    assert discarded == []
    assert len(docs) == 1
    d = docs[0]
    assert d.url == "https://www.reuters.com/a"
    assert d.title == "Reuters headline"
    assert d.source_tier == 2
    assert d.source_name == "Reuters"
    assert d.publication_time == "2026-07-01T00:00:00Z"
    assert d.full_text == "Body."
    assert d.retrieved_at == NOW
    assert d.event_time is None


def test_retrieve_rejects_postfreeze_doc():
    search = FakeSearch({"q1": [{"url": "https://x.com/late", "page_age": None}]})
    table = {"https://x.com/late": {
        "full_text": "Body.", "publication_time": "2026-07-05T00:00:00Z", "source_name": "x"}}
    docs, discarded = retrieve(["q1"], FREEZE, NOW,
                               search_client=search, fetch_article=_fetcher(table))
    assert docs == []
    assert discarded == [{"url": "https://x.com/late", "reason": "publication_time > freeze_ts"}]


def test_retrieve_rejects_doc_without_publication_time():
    search = FakeSearch({"q1": [{"url": "https://x.com/nodate", "page_age": None}]})
    table = {"https://x.com/nodate": {
        "full_text": "Body.", "publication_time": None, "source_name": "x"}}
    docs, discarded = retrieve(["q1"], FREEZE, NOW,
                               search_client=search, fetch_article=_fetcher(table))
    assert docs == []
    assert discarded == [{"url": "https://x.com/nodate", "reason": "no_publication_time"}]


def test_retrieve_uses_page_age_when_article_date_missing():
    search = FakeSearch({"q1": [{"url": "https://x.com/p", "page_age": "2026-07-01"}]})
    table = {"https://x.com/p": {
        "full_text": "Body.", "publication_time": None, "source_name": "x"}}
    docs, discarded = retrieve(["q1"], FREEZE, NOW,
                               search_client=search, fetch_article=_fetcher(table))
    assert len(docs) == 1
    assert docs[0].publication_time == "2026-07-01"


def test_retrieve_rejects_failed_fetch():
    search = FakeSearch({"q1": [{"url": "https://x.com/dead", "page_age": None}]})
    docs, discarded = retrieve(["q1"], FREEZE, NOW,
                               search_client=search, fetch_article=_fetcher({}))
    assert docs == []
    assert discarded == [{"url": "https://x.com/dead", "reason": "fetch_failed"}]


def test_retrieve_rejects_date_only_on_freeze_day():
    search = FakeSearch({"q1": [{"url": "https://x.com/today", "page_age": None}]})
    table = {"https://x.com/today": {
        "full_text": "Body.", "publication_time": "2026-07-04", "source_name": "x"}}
    docs, discarded = retrieve(["q1"], "2026-07-04T12:00:00Z", NOW,
                               search_client=search, fetch_article=_fetcher(table))
    assert docs == []
    assert discarded == [{"url": "https://x.com/today",
                          "reason": "date_only_unverifiable_on_freeze_day"}]


def test_retrieve_keeps_date_only_before_freeze_day():
    search = FakeSearch({"q1": [{"url": "https://x.com/y", "page_age": None}]})
    table = {"https://x.com/y": {
        "full_text": "Body.", "publication_time": "2026-07-03", "source_name": "x"}}
    docs, discarded = retrieve(["q1"], "2026-07-04T12:00:00Z", NOW,
                               search_client=search, fetch_article=_fetcher(table))
    assert len(docs) == 1


def test_retrieve_dedupes_url_across_queries():
    hits = [{"url": "https://www.reuters.com/a", "page_age": None}]
    search = FakeSearch({"q1": hits, "q2": hits})
    calls = []

    def counting_fetch(url):
        calls.append(url)
        return {"full_text": "Body.", "publication_time": "2026-07-01", "source_name": "Reuters"}

    docs, discarded = retrieve(["q1", "q2"], FREEZE, NOW,
                               search_client=search, fetch_article=counting_fetch)
    assert len(docs) == 1
    assert calls == ["https://www.reuters.com/a"]  # fetched once


# -- live builds: the freeze is now, so a page just downloaded cannot be from after it ----------

def test_is_live_only_when_the_freeze_is_now():
    clock = lambda: datetime(2026, 7, 4, 12, 5, tzinfo=timezone.utc)
    assert is_live("2026-07-04T12:00:00Z", clock) is True
    assert is_live("2026-07-03T12:00:00Z", clock) is False      # a back-dated build
    assert is_live("not a date", clock) is False


def test_live_build_keeps_quote_pages_and_dates_them_by_retrieval():
    # 2026-11-10: quote pages carry no date, or the date the page was first made, and were all
    # dropped. The briefs then had no current price and every forecast was a coin flip.
    search = FakeSearch({"price now": [{"url": "https://x.com/quote", "page_age": None},
                                       {"url": "https://x.com/today", "page_age": None},
                                       {"url": "https://x.com/tomorrow", "page_age": None}],
                         "background": [{"url": "https://www.reuters.com/a", "page_age": None}]})
    table = {"https://x.com/quote": {"full_text": "74,970", "publication_time": None, "source_name": "x"},
             "https://x.com/today": {"full_text": "t", "publication_time": "2026-07-04", "source_name": "x"},
             "https://x.com/tomorrow": {"full_text": "t", "publication_time": "2026-07-05", "source_name": "x"},
             "https://www.reuters.com/a": {"full_text": "b", "publication_time": "2026-07-01T00:00:00Z",
                                           "source_name": "Reuters"}}
    docs, discarded = retrieve(["price now", "background"], FREEZE, NOW, search_client=search,
                               fetch_article=_fetcher(table), live=True, latest_value_queries=["price now"])
    assert discarded == []
    by_url = {d.url: d for d in docs}
    quote = by_url["https://x.com/quote"]
    assert (quote.publication_time, quote.date_basis, quote.latest_value) == (NOW, "retrieved", True)
    assert by_url["https://x.com/today"].date_basis == "published"        # dated today: kept as dated
    late = by_url["https://x.com/tomorrow"]                               # a wrong date, not a leak
    assert (late.publication_time, late.date_basis) == (NOW, "retrieved")
    reuters = by_url["https://www.reuters.com/a"]
    assert (reuters.date_basis, reuters.latest_value) == ("published", False)
    assert all(parse_iso(d.publication_time) <= parse_iso(FREEZE) for d in docs)


def test_back_dated_build_still_discards_what_it_cannot_date():
    search = FakeSearch({"price now": [{"url": "https://x.com/quote", "page_age": None}]})
    table = {"https://x.com/quote": {"full_text": "74,970", "publication_time": None, "source_name": "x"}}
    docs, discarded = retrieve(["price now"], FREEZE, NOW, search_client=search,
                               fetch_article=_fetcher(table), latest_value_queries=["price now"])
    assert docs == [] and discarded[0]["reason"] == "no_publication_time"
