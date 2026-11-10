"""
Decomposer (spec §4.2).

Turns a QuestionPackage into categorized search sub-queries (JSON only). The
odds/market-price ban is enforced in CODE — any generated query that targets
betting odds is dropped and recorded, not merely discouraged in the prompt.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any

from .odds_terms import is_odds_query
from ...utils.logger import get_logger

logger = get_logger('mirofish.services.decomposer')

CATEGORIES = [
    "KEY_ACTORS", "RECENT_EVENTS", "FUNDAMENTALS", "EXPERT_FORECASTS",
    "HISTORICAL_ANALOGUES", "STAKEHOLDER_POSITIONS", "RESOLUTION_EDGE_CASES",
]

_MAX_PER_CATEGORY = 6

_SYSTEM = (
    "You decompose a prediction question into concrete web-search queries for neutral research. "
    "Output JSON only. Use exactly these keys: " + ", ".join(CATEGORIES) + ". "
    "Each key maps to 3 to 6 concrete search strings. "
    "HARD RULE: never write a query designed to retrieve betting odds, bookmaker lines, "
    "sportsbook prices, or prediction-market prices (Polymarket, Kalshi, Betfair, moneylines, "
    "implied probabilities). Focus on facts, fundamentals, expert analysis, base rates, and "
    "stakeholder positions. Anchor to the resolution criteria, not the headline."
)


@dataclass
class DecomposerOutput:
    queries_by_category: Dict[str, List[str]] = field(default_factory=dict)
    dropped_odds_queries: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'queries_by_category': self.queries_by_category,
            'dropped_odds_queries': self.dropped_odds_queries,
        }


def build_prompt(qp) -> List[Dict[str, str]]:
    user = (
        f"QUESTION: {qp.question_text}\n"
        f"RESOLUTION CRITERIA: {qp.resolution_criteria}\n"
        f"CATEGORY: {getattr(qp, 'category', '')}\n\n"
        "Return JSON mapping each of the 7 category keys to a list of 3-6 search strings."
    )
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


def decompose(qp, llm) -> DecomposerOutput:
    try:
        response = llm.chat_json(build_prompt(qp))
    except Exception as e:
        logger.error(f"decomposer LLM call failed: {e}")
        response = {}
    if not isinstance(response, dict):
        response = {}

    by_category: Dict[str, List[str]] = {}
    dropped: List[str] = []
    for cat in CATEGORIES:
        raw = response.get(cat) or response.get(cat.lower()) or []
        clean: List[str] = []
        for q in raw if isinstance(raw, list) else []:
            if not isinstance(q, str) or not q.strip():
                continue
            q = q.strip()
            if is_odds_query(q):
                dropped.append(q)
                continue
            clean.append(q)
        by_category[cat] = clean[:_MAX_PER_CATEGORY]

    if dropped:
        logger.info(f"decomposer: dropped {len(dropped)} odds/market queries")
    return DecomposerOutput(queries_by_category=by_category, dropped_odds_queries=dropped)
