"""
Agent-prediction extractor.

Turns each agent's simulation posts into a binary stance on the market question
({predicted_outcome: yes|no|uncertain, confidence}). This is the producer that
feeds report_agent.generate_structured_report, which the fork ships but never
wires to a producer.
"""

import re
from typing import List, Dict, Any

from .swarm_reducer import normalize_outcome
from ...utils.logger import get_logger

logger = get_logger('mirofish.services.agent_prediction_extractor')

_MAX_TEXT_CHARS = 3000

_SYSTEM = (
    "You classify each agent's stance on a binary prediction question based on their posts. "
    "For each agent, decide predicted_outcome = YES, NO, or UNCERTAIN (does the question resolve YES?), "
    "and a confidence in [0,1]. Base it only on the agent's own posts. Return JSON only."
)


def build_prompt(agents: List[Dict[str, Any]], question: str) -> List[Dict[str, str]]:
    blocks = []
    for a in agents:
        texts = " | ".join(a.get("texts") or [])[:_MAX_TEXT_CHARS]
        blocks.append(f"AGENT {a.get('agent_id')} ({a.get('persona', '')}): {texts}")
    user = (
        f"QUESTION: {question}\n\n"
        "AGENTS:\n" + "\n".join(blocks) + "\n\n"
        'Return JSON: {"predictions": [{"agent_id": "...", "predicted_outcome": "YES|NO|UNCERTAIN", '
        '"confidence": 0.0, "prediction": "one-line summary"}]}'
    )
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


def _clamp(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, v))


def extract_agent_predictions(agents: List[Dict[str, Any]], question: str, llm) -> List[Dict[str, Any]]:
    # swarmz: match ids as strings; the model returns "3" for agent 3
    by_id = {str(a.get("agent_id")): a for a in agents}
    try:
        response = llm.chat_json(build_prompt(agents, question))
    except Exception as e:
        logger.error(f"agent prediction extraction failed: {e}")
        return []
    if not isinstance(response, dict) or not isinstance(response.get("predictions"), list):
        logger.error("agent prediction extraction returned a malformed response")
        return []

    if response["predictions"] and not any(str(i.get("agent_id")) in by_id for i in response["predictions"] if isinstance(i, dict)):
        logger.warning(f"agent ids in reply did not match posting agents: "
                       f"{[i.get('agent_id') for i in response['predictions'] if isinstance(i, dict)][:10]}")
    out: List[Dict[str, Any]] = []
    for item in response["predictions"]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("agent_id")).strip()
        if key not in by_id:  # e.g. "AGENT 19" or "agent_19"
            m = re.search(r"\d+", key)
            key = m.group(0) if m else key
        if key not in by_id:
            continue
        agent_id = by_id[key].get("agent_id")
        out.append({
            "agent_id": agent_id,
            "persona": by_id[key].get("persona", ""),
            "predicted_outcome": normalize_outcome(item.get("predicted_outcome", "")),
            "confidence": _clamp(item.get("confidence")),
            "prediction": item.get("prediction", ""),
        })
    return out
