"""
Decomposer (spec §4.2).

Turns a QuestionPackage into categorized search sub-queries (JSON only). The
odds/market-price ban is enforced in CODE — any generated query that targets
betting odds is dropped and recorded, not merely discouraged in the prompt.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Any, Optional

from .odds_terms import is_odds_query
from ...utils.logger import get_logger

logger = get_logger('briefbuilder.services.decomposer')

# Looks up the number (or status) the question resolves on, as it stands today. Without it a
# brief for "will the index close above 27,900" had every fact but the index level.
LATEST_VALUE = "LATEST_VALUE"

# Looks up what is happening in the world that moves the outcome. Without it a brief for a
# presidential approval question was all poll numbers and nothing about the presidency
# (found 2026-11-10): every other heading, anchored to the resolution criteria, asked about the number.
DRIVERS = "DRIVERS"

CATEGORIES = [
    LATEST_VALUE, DRIVERS, "KEY_ACTORS", "RECENT_EVENTS", "FUNDAMENTALS", "EXPERT_FORECASTS",
    "HISTORICAL_ANALOGUES", "STAKEHOLDER_POSITIONS", "RESOLUTION_EDGE_CASES",
]

_MAX_PER_CATEGORY = 6

_SYSTEM = (
    "You decompose a prediction question into concrete web-search queries for neutral research. "
    "Output JSON only. Use exactly these keys: " + ", ".join(CATEGORIES) + ". "
    "Each key maps to 3 to 6 concrete search strings. "
    "LATEST_VALUE holds 2 or 3 searches for the most recent published value of the quantity (or the "
    "current status of the matter) the question resolves on, phrased the way a person would look it "
    "up today, for example 'Nasdaq-100 index today', 'bitcoin price now', 'WTI crude oil price today', "
    "'San Francisco airport forecast high tomorrow', 'latest CPI release month over month'. These "
    "look up the number itself, not commentary about it. Looking up the price or level of the "
    "underlying asset, index or statistic is required; only betting and prediction-market prices are banned. "
    "DRIVERS holds searches for what is actually happening in the world that moves the outcome: the "
    "events, decisions, conditions and performance behind the number, not reports or commentary about "
    "the number itself. For a presidential approval question that is the economy and prices, the "
    "president's major decisions, and crises or scandals in the news; for a stock index, earnings, "
    "interest-rate news and the day's market-moving events; for a game, injuries and form. Fit them to "
    "the time left: with a day or two to go, ask what could still move the outcome before it resolves; "
    "with weeks or months, cover the underlying forces. "
    "HARD RULE: never write a query designed to retrieve betting odds, bookmaker lines, "
    "sportsbook prices, or prediction-market prices (Polymarket, Kalshi, Betfair, moneylines, "
    "implied probabilities). Focus on facts, fundamentals, expert analysis, base rates, and "
    "stakeholder positions. Anchor to the resolution criteria, not the headline."
)


@dataclass
class DecomposerOutput:
    queries_by_category: Dict[str, List[str]] = field(default_factory=dict)
    dropped_odds_queries: List[str] = field(default_factory=list)
    # days from the build to resolution, when known, and how many DRIVERS searches each pass
    # over the headings takes because of it (see drivers_weight)
    horizon_days: Optional[int] = None
    drivers_weight: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            'queries_by_category': self.queries_by_category,
            'dropped_odds_queries': self.dropped_odds_queries,
            'horizon_days': self.horizon_days,
            'drivers_weight': self.drivers_weight,
        }


def horizon_days(qp, today: str) -> Optional[int]:
    """Whole days from today to when the question is expected to resolve, or None if either date is unknown."""
    raw = getattr(qp, 'raw_market', None) or {}
    resolves = raw.get('expected_expiration_time') or getattr(qp, 'close_ts', '') or ''
    try:
        return max(0, (date.fromisoformat(str(resolves)[:10]) - date.fromisoformat(today[:10])).days)
    except ValueError:
        return None


def drivers_weight(days: Optional[int]) -> int:
    """How many DRIVERS searches to take for each one from the other headings. Over a day or two the
    latest reading decides most questions; the further off resolution is, the more the forces behind it do."""
    if days is None or days <= 2:
        return 1
    return 2 if days <= 30 else 3


def build_prompt(qp, today: str = "") -> List[Dict[str, str]]:
    days = horizon_days(qp, today) if today else None
    user = (
        (f"TODAY: {today}\n" if today else "") +
        (f"RESOLVES IN: {days} day{'' if days == 1 else 's'}\n" if days is not None else "") +
        f"QUESTION: {qp.question_text}\n"
        f"RESOLUTION CRITERIA: {qp.resolution_criteria}\n"
        f"CATEGORY: {getattr(qp, 'category', '')}\n\n"
        f"Return JSON mapping each of the {len(CATEGORIES)} category keys to a list of search strings. "
        f"Write {2 * drivers_weight(days)} DRIVERS searches."
    )
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


def decompose(qp, llm, today: str = "") -> DecomposerOutput:
    """today: the date of the build, so searches for the latest value name the right day."""
    try:
        response = llm.chat_json(build_prompt(qp, today))
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
    days = horizon_days(qp, today) if today else None
    return DecomposerOutput(queries_by_category=by_category, dropped_odds_queries=dropped,
                            horizon_days=days, drivers_weight=drivers_weight(days))
