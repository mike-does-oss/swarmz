"""
Kalshi public market-data client.

Wraps Kalshi's unauthenticated `trade-api/v2` GET endpoints and normalizes
prices to probabilities in [0, 1]. Kalshi is mid-migration between integer-cents
fields (e.g. `yes_bid`) and dollar-string / fixed-point variants (e.g.
`yes_bid_dollars`), so normalization tries the plain field then the `_dollars`
variant and interprets the value by type: int = cents, float/str = dollars.
"""

from typing import Optional, List, Dict, Any, Tuple

import requests

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('briefbuilder.services.kalshi_client')


def price_to_prob(value: Any, unit: Optional[str] = None) -> Optional[float]:
    """Normalize a Kalshi price to a probability-scaled float.

    unit='cents'   -> divide by 100
    unit='dollars' -> use as-is (already dollars)
    unit=None      -> infer by type: int = cents (/100), float/str = dollars

    Returns a float (typically in [0, 1]; out-of-range inputs pass through
    unclamped so callers can detect anomalies). bool/None/non-numeric -> None.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = float(value)
    elif isinstance(value, str):
        try:
            num = float(value)
        except ValueError:
            return None
    else:
        return None
    if unit == 'dollars':
        return num
    if unit == 'cents':
        return num / 100.0
    # infer by original type
    if isinstance(value, int):
        return num / 100.0
    return num


def _first_price(m: Dict[str, Any], *keys: str) -> Optional[float]:
    for k in keys:
        if k in m and m[k] is not None:
            unit = 'dollars' if k.endswith('_dollars') else None
            return price_to_prob(m[k], unit=unit)
    return None


def _as_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def normalize_market(m: Dict[str, Any]) -> Dict[str, Any]:
    ticker = m.get('ticker')
    return {
        'ticker': ticker,
        'event_ticker': m.get('event_ticker'),
        'series_ticker': m.get('series_ticker'),
        # `title` is the event question ("Will the Fed cut rates?"); the
        # per-market strike lives in yes_sub_title ("Fed maintains rate").
        'title': m.get('title') or m.get('subtitle') or ticker,
        'subtitle': m.get('subtitle'),
        'yes_sub_title': m.get('yes_sub_title'),
        'no_sub_title': m.get('no_sub_title'),
        'rules_primary': m.get('rules_primary', ''),
        'rules_secondary': m.get('rules_secondary', ''),
        'category': m.get('category'),
        'status': m.get('status'),
        'close_time': m.get('close_time'),
        'open_time': m.get('open_time'),
        'yes_bid': _first_price(m, 'yes_bid', 'yes_bid_dollars'),
        'yes_ask': _first_price(m, 'yes_ask', 'yes_ask_dollars'),
        'volume': _as_float(m.get('volume', m.get('volume_fp'))),
        'open_interest': m.get('open_interest', m.get('open_interest_fp')),
        'raw': m,
    }


# Curated liquid single-outcome series. Kalshi's raw `status=open` feed is
# dominated by illiquid KXMVE multi-leg parlay combos (no price, no volume,
# comma-joined titles) — useless for prediction. These series are real,
# tradeable binary questions with live prices. Unknown/expired tickers just
# return empty and are skipped, so the list degrades gracefully.
FEATURED_SERIES: List[str] = [
    'KXFEDDECISION',   # Fed rate decision
    'KXCPIYOY',        # CPI inflation (year over year)
    'KXRECSSNBER',     # US recession call
    'KXBTCD',          # Bitcoin daily price
    'KXETHD',          # Ethereum daily price
    'KXNBA',           # NBA champion
    'KXNFL',           # NFL champion
    'KXNHL',           # NHL champion
    'KXHIGHNY',        # NYC daily high temp
    'KXHIGHLAX',       # LA daily high temp
    'KXHIGHCHI',       # Chicago daily high temp
    'KXAAAGASM',       # US average gas price
]


def _is_mve(m: Dict[str, Any]) -> bool:
    """Multi-variable-event parlay combo — not a single tradeable question."""
    return (m.get('ticker') or '').startswith('KXMVE') or bool(m.get('raw', {}).get('mve_selected_legs'))


def _is_priced(m: Dict[str, Any]) -> bool:
    return (m.get('yes_bid') or 0) > 0 or (m.get('yes_ask') or 0) > 0


class KalshiClient:
    def __init__(self, base_url: Optional[str] = None, session=None, timeout: int = 30):
        self.base_url = (base_url or Config.KALSHI_API_BASE).rstrip('/')
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def list_markets(
        self,
        status: str = 'open',
        limit: int = 100,
        cursor: Optional[str] = None,
        series_ticker: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        params: Dict[str, Any] = {'status': status, 'limit': limit}
        if cursor:
            params['cursor'] = cursor
        if series_ticker:
            params['series_ticker'] = series_ticker
        data = self._get('/markets', params)
        markets = [normalize_market(m) for m in data.get('markets', [])]
        logger.info(f"Kalshi list_markets returned {len(markets)} markets (status={status})")
        return markets, data.get('cursor')

    def list_featured_markets(self, limit: int = 60, per_series: int = 6) -> List[Dict[str, Any]]:
        """Liquid, single-outcome binary markets across a curated set of series.

        Fans out one request per series, keeps only priced non-parlay markets,
        takes the most-traded few per series, then returns the global top by
        volume. Per-series failures are logged and skipped.
        """
        collected: List[Dict[str, Any]] = []
        for series in FEATURED_SERIES:
            try:
                markets, _ = self.list_markets(status='open', limit=20, series_ticker=series)
            except Exception as e:
                logger.warning(f"Featured series {series} failed: {e}")
                continue
            usable = [m for m in markets if _is_priced(m) and not _is_mve(m)]
            usable.sort(key=lambda m: m.get('volume') or 0, reverse=True)
            collected.extend(usable[:per_series])
        collected.sort(key=lambda m: m.get('volume') or 0, reverse=True)
        logger.info(f"Kalshi featured markets: {len(collected)} across {len(FEATURED_SERIES)} series")
        return collected[:limit]

    def get_market(self, ticker: str) -> Dict[str, Any]:
        data = self._get(f'/markets/{ticker}')
        return normalize_market(data.get('market', {}))

    def get_orderbook(self, ticker: str, depth: int = 10) -> Dict[str, Any]:
        data = self._get(f'/markets/{ticker}/orderbook', {'depth': depth})
        return data.get('orderbook', {})
