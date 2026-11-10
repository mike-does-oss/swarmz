"""
Baseline forecaster (spec §7 second baseline).

A single-LLM forecast of P(YES) from the same frozen brief the swarm receives.
It isolates whether the swarm adds anything over the brief itself. Separate from
the builder — the builder never grades its own homework.
"""

from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any

from ...utils.logger import get_logger

logger = get_logger('mirofish.services.baseline')

_SYSTEM = (
    "You are a calibrated forecaster. Given a neutral research brief and a binary question, "
    "output your probability that the question resolves YES, as a number in [0,1], plus a short "
    "rationale grounded only in the brief. Return JSON only."
)


@dataclass
class BaselineForecast:
    prob: Optional[float]
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_prompt(brief_text: str, question: str) -> List[Dict[str, str]]:
    user = (
        f"QUESTION: {question}\n\n"
        f"BRIEF:\n{brief_text}\n\n"
        'Return JSON: {"probability": 0.0, "rationale": "..."}'
    )
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


def baseline_forecast(brief_text: str, question: str, llm) -> BaselineForecast:
    try:
        response = llm.chat_json(build_prompt(brief_text, question))
    except Exception as e:
        logger.error(f"baseline forecast LLM call failed: {e}")
        return BaselineForecast(prob=None, rationale="")
    if not isinstance(response, dict):
        return BaselineForecast(prob=None, rationale="")

    raw = response.get("probability")
    prob: Optional[float]
    try:
        prob = max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        prob = None
    return BaselineForecast(prob=prob, rationale=response.get("rationale", "") or "")
