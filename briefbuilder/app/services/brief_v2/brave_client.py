"""
Brave Search client (retrieval, spec §4.3).

Returns candidate results (url + publication age + title) for the retriever to
fetch in full. Recency is controlled by the `freshness` param, passed through
from the caller.
"""

from typing import Optional, List, Dict, Any

import requests

from ...config import Config
from ...utils.logger import get_logger

logger = get_logger('briefbuilder.services.brave_client')


class BraveSearchClient:
    def __init__(self, api_key: Optional[str] = None, session=None, timeout: int = 30):
        self.api_key = api_key if api_key is not None else Config.BRAVE_API_KEY
        self.session = session or requests.Session()
        self.timeout = timeout
        self.base_url = 'https://api.search.brave.com/res/v1/web/search'

    def search(self, query: str, count: int = 10, freshness: Optional[str] = None) -> List[Dict[str, Any]]:
        headers = {'Accept': 'application/json', 'X-Subscription-Token': self.api_key}
        params: Dict[str, Any] = {'q': query, 'count': count}
        if freshness:
            params['freshness'] = freshness
        resp = self.session.get(self.base_url, headers=headers, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        results: List[Dict[str, Any]] = []
        for r in (data.get('web') or {}).get('results', [])[:count]:
            results.append({
                'url': r.get('url', ''),
                'title': r.get('title', ''),
                'description': r.get('description', ''),
                'page_age': r.get('page_age') or r.get('age'),
            })
        logger.info(f"Brave search '{query[:40]}' -> {len(results)} results")
        return results
