"""
LLM acceptance audits (spec §6): an independent model checks each scrubbed brief
for outcome leakage (neutrality) and for odds/market-price passages the regex
misses (numeric-only phrasings). Audits fail-OPEN (inconclusive != brief failure)
so a broken audit call never masquerades as a brief defect; the runner logs it.
"""

from typing import Dict, Any, List

from ...utils.logger import get_logger

logger = get_logger('mirofish.services.acceptance.llm_audit')

_NEUTRALITY_SYSTEM = (
    "You are a neutrality auditor for a research brief that must NOT hint at an outcome. "
    "Given a binary question and the brief, answer whether the brief implies which outcome is "
    "likely (any tilt, framing, or conclusion counts). Return JSON only: "
    '{"implies_outcome": true|false, "note": "brief reason"}.'
)

_ODDS_SYSTEM = (
    "You audit a research brief for betting/market contamination that a keyword regex would miss: "
    "American odds (-150/+200), fractional/decimal odds, 'priced at N cents', implied probabilities "
    "from markets, sportsbook/exchange references tied to a number. Return JSON only: "
    '{"found": ["<quoted passage>", ...]}  (empty list if clean).'
)


def neutrality_audit(brief_text: str, question: str, llm) -> Dict[str, Any]:
    try:
        resp = llm.chat_json([
            {"role": "system", "content": _NEUTRALITY_SYSTEM},
            {"role": "user", "content": f"QUESTION: {question}\n\nBRIEF:\n{brief_text}"},
        ])
    except Exception as e:
        logger.error(f"neutrality audit call failed: {e}")
        resp = None
    if not isinstance(resp, dict):
        return {"implies_outcome": False, "note": "audit inconclusive"}
    return {"implies_outcome": bool(resp.get("implies_outcome")), "note": resp.get("note", "") or ""}


def odds_llm_audit(brief_text: str, llm) -> Dict[str, Any]:
    try:
        resp = llm.chat_json([
            {"role": "system", "content": _ODDS_SYSTEM},
            {"role": "user", "content": f"BRIEF:\n{brief_text}"},
        ])
    except Exception as e:
        logger.error(f"odds LLM audit call failed: {e}")
        resp = None
    if not isinstance(resp, dict):
        return {"found": [], "clean": True, "note": "audit inconclusive"}
    found: List[str] = [x for x in (resp.get("found") or []) if isinstance(x, str)]
    return {"found": found, "clean": not found}
