"""
Swarm reducer — the fork's categorical scenario distribution -> binary P(YES).

The MiroFish fork's report_agent produces a track-record-weighted probability
distribution over categorical outcomes (report_agent.StructuredInvestmentReport).
Because agents are framed on the binary resolution upstream (Plan 9), mirofish_prob
is the YES share of the YES+NO mass; neutral/uncertain mass is excluded from the
binary.
"""

from typing import Optional, Dict, Any

from ...utils.logger import get_logger

logger = get_logger('mirofish.services.swarm_reducer')

_YES = {"yes", "bullish", "true", "pass", "win"}
_NO = {"no", "bearish", "false", "fail", "lose"}


def normalize_outcome(outcome: str) -> str:
    key = (outcome or "").strip().lower()
    if key in _YES:
        return "yes"
    if key in _NO:
        return "no"
    return "uncertain"


def reduce_to_yes_prob(structured_report: Dict[str, Any]) -> Optional[float]:
    scenarios = (structured_report or {}).get("scenarios") or []
    yes_mass = 0.0
    no_mass = 0.0
    for s in scenarios:
        prob = s.get("probability") or 0.0
        bucket = normalize_outcome(s.get("implied_outcome", ""))
        if bucket == "yes":
            yes_mass += prob
        elif bucket == "no":
            no_mass += prob
    denom = yes_mass + no_mass
    if denom <= 0:
        logger.info("swarm_reducer: no YES/NO mass in report; mirofish_prob=None")
        return None
    return yes_mass / denom
