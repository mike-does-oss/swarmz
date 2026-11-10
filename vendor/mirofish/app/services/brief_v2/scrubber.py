"""
Scrubber (spec §4.6).

A separate LLM call (model #2) whose only job is contamination removal: delete
odds / prediction-market prices / bookmaker references tied to a number /
"implied probability" / market-derived forecasts; keep polls, non-market expert
forecasts, statistical model outputs, base rates, and fundamentals. Mixed
sentences are rewritten to preserve the keep-item and flagged [SCRUBBED-REWRITE].
Emits the cleaned brief plus a removal log.
"""

from dataclasses import dataclass, asdict
from typing import List, Dict, Any

from ...utils.logger import get_logger

logger = get_logger('mirofish.services.scrubber')

_SYSTEM = (
    "You are a contamination scrubber for a neutral research brief. Your ONLY job is to "
    "remove betting/market contamination. REMOVE (whole sentence) anything with betting odds, "
    "prediction-market prices, bookmaker or exchange references tied to a number, "
    "'implied probability/odds', or forecasts derived from betting markets. "
    "KEEP polls, expert/analyst forecasts NOT derived from markets, statistical model outputs "
    "(Elo, power ratings) unless stated to incorporate odds, base rates, and fundamentals. "
    "For a mixed sentence, rewrite it to preserve the keep-item and prefix it with "
    "[SCRUBBED-REWRITE]. Do not change anything else. Return JSON only."
)


@dataclass
class Removal:
    quoted_text: str
    rule: str
    action: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScrubResult:
    cleaned_brief: str
    removals: List[Removal]
    degraded: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {'cleaned_brief': self.cleaned_brief,
                'removals': [r.to_dict() for r in self.removals],
                'degraded': self.degraded}


def build_prompt(brief_text: str) -> List[Dict[str, str]]:
    user = (
        "BRIEF:\n\n" + brief_text + "\n\n"
        "Return JSON of the form:\n"
        '{"cleaned_brief": "<the brief with contamination removed/rewritten>", '
        '"removals": [{"quoted_text": "<removed or rewritten sentence>", '
        '"rule": "<which rule triggered>", "action": "delete|rewrite"}]}'
    )
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


def scrub(brief_text: str, llm) -> ScrubResult:
    try:
        response = llm.chat_json(build_prompt(brief_text))
    except Exception as e:
        logger.error(f"scrubber LLM call failed: {e}")
        return ScrubResult(cleaned_brief=brief_text, removals=[], degraded=True)
    if not isinstance(response, dict):
        logger.error("scrubber returned a malformed response; passing brief through unscrubbed")
        return ScrubResult(cleaned_brief=brief_text, removals=[], degraded=True)

    cleaned = response.get('cleaned_brief')
    if not isinstance(cleaned, str):
        cleaned = brief_text
        degraded = True
    else:
        degraded = False
    removals: List[Removal] = []
    for r in response.get('removals') or []:
        if not isinstance(r, dict):
            continue
        removals.append(Removal(
            quoted_text=r.get('quoted_text', ''),
            rule=r.get('rule', ''),
            action=r.get('action', 'delete'),
        ))
    logger.info(f"scrubber: {len(removals)} removal(s)")
    return ScrubResult(cleaned_brief=cleaned, removals=removals, degraded=degraded)
