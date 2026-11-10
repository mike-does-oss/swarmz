"""
Retriever (spec §4.3).

Searches (recency-biased), fetches full article text, tiers the source, resolves
a publication time, and enforces the freeze at ingest: any doc that postdates
`freeze_ts` — or whose publication time cannot be established — is dropped and
logged. Collaborators (search client, fetch function) are injected so the unit
is deterministic and offline in tests.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple, Callable

from .source_tier import classify_tier, domain_of
from ...utils.logger import get_logger

logger = get_logger('mirofish.services.retriever')


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
) -> Tuple[List[RetrievedDoc], List[Dict[str, str]]]:
    freeze_dt = parse_iso(freeze_ts)
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
                candidates[url] = {'page_age': r.get('page_age'), 'title': r.get('title', '')}

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
        ))

    reason_counts = dict(Counter(d['reason'] for d in discarded))
    logger.info(f"retriever: {len(docs)} kept, {len(discarded)} discarded {reason_counts}")
    return docs, discarded
