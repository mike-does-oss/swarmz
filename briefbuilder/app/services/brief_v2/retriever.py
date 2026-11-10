"""
Retriever (spec §4.3).

Searches (recency-biased), fetches full article text, tiers the source, resolves
a publication time, and enforces the freeze at ingest: any doc that postdates
`freeze_ts` — or whose publication time cannot be established — is dropped and
logged. Collaborators (search client, fetch function) are injected so the unit
is deterministic and offline in tests.

A live build is the exception. When the freeze is the moment of retrieval, a page
that was just downloaded cannot hold anything from after the freeze, whatever date
it shows or lacks. The date checks exist for back-dated builds; in a live build
they threw away the pages that matter most for a question that resolves within
days: quote pages, which carry no date, and everything published on the day
(found 2026-11-10: briefs for a Bitcoin and a Nasdaq-100 question had no price
newer than two months). Such pages are kept and marked as dated by retrieval.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple, Callable, Sequence

from .source_tier import classify_tier, domain_of
from ...utils.logger import get_logger

logger = get_logger('briefbuilder.services.retriever')


@dataclass
class RetrievedDoc:
    url: str
    title: str
    source_name: str
    source_tier: int
    event_time: Optional[str]
    publication_time: Optional[str]
    full_text: str
    retrieved_at: str
    # "published": publication_time is the page's own date. "retrieved": the page's date was
    # missing or unusable in a live build, and publication_time is the time of retrieval.
    date_basis: str = "published"
    # found by a search for the latest value of what the question resolves on
    latest_value: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts or not isinstance(ts, str):
        return None
    s = ts.strip().replace('Z', '+00:00')
    dt: Optional[datetime] = None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.fromisoformat(s[:10])  # date-only fallback
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


LIVE_WINDOW = timedelta(minutes=15)


def is_live(freeze_ts: str, clock: Optional[Callable[[], datetime]] = None) -> bool:
    """True when the freeze is now: the build is retrieving at the moment it freezes."""
    freeze_dt = parse_iso(freeze_ts)
    if freeze_dt is None:
        return False
    now = clock() if clock else datetime.now(timezone.utc)
    return abs(now - freeze_dt) <= LIVE_WINDOW


def _is_date_only(ts: str) -> bool:
    s = (ts or "").strip()
    return "T" not in s.upper() and ":" not in s


def retrieve(
    queries: List[str],
    freeze_ts: str,
    retrieved_at: str,
    *,
    search_client,
    fetch_article: Callable[[str], Optional[Dict[str, Any]]],
    count_per_query: int = 5,
    freshness: Optional[str] = None,
    max_workers: int = 8,
    live: bool = False,
    latest_value_queries: Sequence[str] = (),
) -> Tuple[List[RetrievedDoc], List[Dict[str, str]]]:
    """live: the freeze is the moment of retrieval (see the module docstring).
    latest_value_queries: the queries that look up the latest value of what the question
    resolves on; the docs they find are marked so later stages keep them."""
    freeze_dt = parse_iso(freeze_ts)
    latest = set(latest_value_queries)
    docs: List[RetrievedDoc] = []
    discarded: List[Dict[str, str]] = []

    # 1) Collect unique candidate URLs across all queries (first title/page_age wins).
    candidates: Dict[str, Dict[str, Any]] = {}
    for query in queries:
        try:
            results = search_client.search(query, count=count_per_query, freshness=freshness)
        except Exception as e:  # a failed search should not abort the whole run
            logger.error(f"search failed for {query!r}: {e}")
            continue
        for r in results:
            url = r.get('url')
            if url and url not in candidates:
                candidates[url] = {'page_age': r.get('page_age'), 'title': r.get('title', ''),
                                   'latest_value': False}
            if url and query in latest:
                candidates[url]['latest_value'] = True

    # 2) Fetch every unique URL concurrently (full-text fetch is the bottleneck;
    #    each URL is fetched exactly once).
    def _fetch_one(url):
        try:
            return url, fetch_article(url), None
        except Exception as e:
            return url, None, e

    if candidates:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            fetched = list(ex.map(_fetch_one, list(candidates.keys())))
    else:
        fetched = []

    # 3) Apply freeze/tier checks deterministically (in candidate order).
    for url, art, err in fetched:
        meta = candidates[url]
        if err is not None:
            logger.warning(f"fetch_error for {url}: {err}")
            discarded.append({'url': url, 'reason': 'fetch_error'})
            continue
        if not art or not art.get('full_text'):
            discarded.append({'url': url, 'reason': 'fetch_failed'})
            continue

        publication_time = art.get('publication_time') or meta.get('page_age')
        pub_dt = parse_iso(publication_time)
        date_basis = 'published'
        if live:
            # Downloaded at the freeze, so nothing in it is from after the freeze. A missing
            # date, or one past the freeze (a site in a timezone ahead), is replaced by the
            # retrieval time; a date on the freeze day is kept as it is.
            if pub_dt is None or (freeze_dt is not None and pub_dt > freeze_dt):
                publication_time, date_basis = retrieved_at, 'retrieved'
        else:
            if pub_dt is None:
                discarded.append({'url': url, 'reason': 'no_publication_time'})
                continue
            if freeze_dt is not None and pub_dt > freeze_dt:
                discarded.append({'url': url, 'reason': 'publication_time > freeze_ts'})
                continue
            if (freeze_dt is not None
                    and _is_date_only(publication_time)
                    and pub_dt.date() == freeze_dt.date()
                    and (freeze_dt.hour or freeze_dt.minute or freeze_dt.second)):
                discarded.append({'url': url, 'reason': 'date_only_unverifiable_on_freeze_day'})
                continue

        docs.append(RetrievedDoc(
            url=url,
            title=meta.get('title', ''),
            source_name=art.get('source_name') or domain_of(url),
            source_tier=classify_tier(url),
            event_time=None,
            publication_time=publication_time,
            full_text=art['full_text'],
            retrieved_at=retrieved_at,
            date_basis=date_basis,
            latest_value=bool(meta.get('latest_value')),
        ))

    reason_counts = dict(Counter(d['reason'] for d in discarded))
    logger.info(f"retriever: {len(docs)} kept, {len(discarded)} discarded {reason_counts}")
    return docs, discarded
