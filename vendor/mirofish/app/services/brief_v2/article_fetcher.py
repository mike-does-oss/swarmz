"""
Full-text article fetcher (spec §4.3: fetch full article text, not snippets).

Uses trafilatura to download and extract the main body text plus publication-date
metadata. The download and extraction steps are injectable so tests run offline.
"""

from typing import Optional, Callable, Dict, Any
from urllib.parse import urlparse

from ...utils.logger import get_logger

logger = get_logger('mirofish.services.article_fetcher')


def _default_fetch(url: str) -> Optional[str]:
    import trafilatura
    from trafilatura.settings import use_config
    # Bound each download so a slow/hanging server can't stall the whole
    # synchronous build (default trafilatura timeout is 30s per URL).
    cfg = use_config()
    cfg.set("DEFAULT", "DOWNLOAD_TIMEOUT", "8")
    try:
        return trafilatura.fetch_url(url, config=cfg)
    except Exception as e:
        logger.info(f"fetch_article: download error for {url}: {e}")
        return None


def _default_extract(html: str) -> Optional[Dict[str, Any]]:
    import trafilatura
    data = trafilatura.bare_extraction(html, with_metadata=True)
    if not data:
        return None
    # trafilatura may return a dict or a Document-like object depending on version.
    get = data.get if isinstance(data, dict) else (lambda k: getattr(data, k, None))
    return {'text': get('text'), 'date': get('date'), 'sitename': get('sitename')}


def _domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith('www.') else netloc


def fetch_article(
    url: str,
    fetch_fn: Optional[Callable[[str], Optional[str]]] = None,
    extract_fn: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
) -> Optional[Dict[str, Any]]:
    fetch_fn = fetch_fn or _default_fetch
    extract_fn = extract_fn or _default_extract

    html = fetch_fn(url)
    if not html:
        logger.info(f"fetch_article: download failed for {url}")
        return None
    data = extract_fn(html)
    if not data or not data.get('text'):
        logger.info(f"fetch_article: no extractable text for {url}")
        return None
    return {
        'full_text': data.get('text'),
        'publication_time': data.get('date'),
        'source_name': data.get('sitename') or _domain(url),
    }
