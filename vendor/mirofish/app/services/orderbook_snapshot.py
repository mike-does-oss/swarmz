"""
Order-book snapshot for the freeze step.

Kalshi order books return bids only. For a binary market a YES ask equals
`1 - best NO bid`. `depth_usd` is the total resting notional across both sides
(sum of price * count). The snapshot timestamp is supplied by the caller so it
can be aligned with `freeze_ts` (acceptance criterion: within 60s of freeze).
"""

from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any

from .kalshi_client import price_to_prob


@dataclass
class OrderBookSnapshot:
    ticker: str
    bid: Optional[float]
    ask: Optional[float]
    mid: Optional[float]
    depth_usd: float
    snapshot_ts: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _best_bid(levels: List[Any]) -> Optional[float]:
    best: Optional[float] = None
    for level in levels or []:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        p = price_to_prob(level[0])
        if p is None:
            continue
        if best is None or p > best:
            best = p
    return best


def _side_depth(levels: List[Any]) -> float:
    total = 0.0
    for level in levels or []:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        p = price_to_prob(level[0])
        if p is None:
            continue
        count = level[1]
        try:
            count = float(count)
        except (TypeError, ValueError):
            continue
        total += p * count
    return total


def snapshot_order_book(client, ticker: str, snapshot_ts: str, depth: int = 10) -> OrderBookSnapshot:
    ob: Dict[str, Any] = client.get_orderbook(ticker, depth=depth)
    yes_levels = ob.get('yes') or []
    no_levels = ob.get('no') or []

    bid = _best_bid(yes_levels)
    best_no = _best_bid(no_levels)
    ask = None if best_no is None else round(1.0 - best_no, 6)
    mid = None if (bid is None or ask is None) else round((bid + ask) / 2.0, 6)
    depth_usd = _side_depth(yes_levels) + _side_depth(no_levels)

    return OrderBookSnapshot(
        ticker=ticker,
        bid=bid,
        ask=ask,
        mid=mid,
        depth_usd=depth_usd,
        snapshot_ts=snapshot_ts,
    )
