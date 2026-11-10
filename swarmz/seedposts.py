"""Re-voice MiroFish's seed posts for the learning swarm (A2).

MiroFish drafts the opening posts in the voice of the entities it found in the brief
(a team account, a sports outlet, the exchange). A2 agents are forecaster personas
with their own names, so those posts are rewritten as the assigned persona sharing
the same information. Facts are kept; the voice changes. One LLM call per run,
tagged as the setup stage. Any failure keeps the original posts: a run never
fails because of this step.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Mapping

logger = logging.getLogger(__name__)

SYSTEM = (
    "You rewrite the opening posts of a forecasting discussion board. Each post was drafted in the "
    "voice of an organisation or person in the news; it will now be posted by a forecaster with the "
    "given name and persona. Rewrite each post in that forecaster's own voice, as someone sharing or "
    "reacting to this information. Keep every fact, name, number and date exactly. Never speak as the "
    "organisation or person the post is about, and never claim inside knowledge. English only, at most "
    "70 words per post. Return JSON: {\"posts\": [{\"i\": <index>, \"content\": \"...\"}, ...]} with one entry "
    "per input post, same indices."
)


def rewrite_seed_posts(sim_dir: str, personas: Mapping[int, tuple[str, str]], llm, model: str,
                       question: str = "") -> int:
    """personas: agent_id -> (name, persona text). Returns how many posts were rewritten."""
    path = os.path.join(sim_dir, "simulation_config.json")
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    posts = (config.get("event_config") or {}).get("initial_posts") or []
    if not posts:
        return 0
    lines = []
    for i, p in enumerate(posts):
        name, persona = personas.get(int(p.get("poster_agent_id", 0)), ("A forecaster", ""))
        lines.append(f"[{i}] posted by {name}. Persona: {persona.strip()[:400]}\nOriginal: {p.get('content', '')}")
    user = (f"QUESTION UNDER DISCUSSION: {question}\n\n" if question else "") + "POSTS:\n\n" + "\n\n".join(lines)
    try:
        out = llm.json(SYSTEM, user, model=model, max_tokens=400 + 120 * len(posts))
        got = {int(x["i"]): str(x["content"]).strip() for x in out.get("posts", []) if x.get("content")}
    except Exception as e:  # keep MiroFish's posts rather than fail the run
        logger.warning(f"seed post rewrite skipped: {e}")
        return 0
    n = 0
    for i, p in enumerate(posts):
        if got.get(i):
            p["original_content"], p["content"] = p.get("content", ""), got[i]
            p["poster_type"] = "Forecaster"
            n += 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    return n
