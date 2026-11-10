"""
Web search through Claude Code, for running without a Brave key.

Asks Claude Code, with only its web search tool switched on, to run one search and hand back the
results as JSON in the shape BraveSearchClient returns. The pages themselves are still downloaded
and dated by the retriever, so nothing the model writes here reaches the brief except the links.
"""

import json
import re
from typing import Optional, List, Dict, Any

from ...config import Config
from ...utils.logger import get_logger

logger = get_logger('briefbuilder.services.claude_search')

_SYSTEM = (
    "You are a search relay. Run exactly one web search for the query you are given, then reply with "
    "one JSON array and nothing else. Each element is an object with the keys url, title, description "
    "and page_age (the page's publication date as YYYY-MM-DD if the result shows one, else null). "
    "List the results in the order the search returned them. Do not add, drop, rank or comment on them, "
    "and do not answer the query yourself."
)


class ClaudeSearchClient:
    def __init__(self, model: Optional[str] = None, ask=None):
        self.model = model or Config.LLM_MODEL_NAME
        self._ask = ask

    def search(self, query: str, count: int = 10, freshness: Optional[str] = None) -> List[Dict[str, Any]]:
        ask = self._ask
        if ask is None:
            from swarmz import claude_code
            ask = claude_code.ask
        recent = {'pd': ' Prefer results from the past day.', 'pw': ' Prefer results from the past week.',
                  'pm': ' Prefer results from the past month.'}.get(freshness or '', '')
        text = ask(_SYSTEM, f"Query: {query}\nReturn up to {count} results.{recent}", model=self.model, tools=('WebSearch',))
        m = re.search(r'\[.*\]', text, re.S)
        try:
            rows = json.loads(m.group(0)) if m else []
        except json.JSONDecodeError:
            rows = []
        results: List[Dict[str, Any]] = []
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict) and isinstance(r.get('url'), str) and r['url'].startswith('http'):
                results.append({'url': r['url'], 'title': str(r.get('title') or ''),
                                'description': str(r.get('description') or ''), 'page_age': r.get('page_age') or None})
        logger.info(f"Claude Code search '{query[:40]}' -> {len(results)} results")
        return results[:count]
