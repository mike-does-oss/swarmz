"""
Question intake (spec §4.1).

Turns a normalized Kalshi market into a QuestionPackage anchored to the exact
resolution criteria. All downstream pipeline stages anchor to
`resolution_criteria`, not the headline `question_text`.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any


# Kalshi category (lowercased) -> canonical prediction-log category.
_CATEGORY_MAP = {
    'politics': 'politics',
    'elections': 'politics',
    'economics': 'econ',
    'economy': 'econ',
    'financials': 'econ',
    'financial': 'econ',
    'sports': 'sports',
    'world': 'geopolitics',
    'geopolitics': 'geopolitics',
}


def map_category(kalshi_category: Optional[str]) -> str:
    if not kalshi_category:
        return 'other'
    return _CATEGORY_MAP.get(kalshi_category.strip().lower(), 'other')


@dataclass
class QuestionPackage:
    question_id: str
    question_text: str
    resolution_criteria: str
    category: str
    close_ts: str
    edge_cases: str = ''
    event_ticker: Optional[str] = None
    raw_market: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def from_kalshi_market(market: Dict[str, Any]) -> QuestionPackage:
    primary = (market.get('rules_primary') or '').strip()
    secondary = (market.get('rules_secondary') or '').strip()
    resolution = primary if not secondary else f"{primary}\n\n{secondary}"
    return QuestionPackage(
        question_id=market.get('ticker', ''),
        question_text=market.get('title') or market.get('ticker', ''),
        resolution_criteria=resolution,
        category=map_category(market.get('category')),
        close_ts=market.get('close_time') or '',
        event_ticker=market.get('event_ticker'),
        raw_market=dict(market),
    )
