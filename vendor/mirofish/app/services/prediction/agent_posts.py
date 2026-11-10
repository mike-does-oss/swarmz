"""
Gather each agent's simulation posts + persona, ready for the stance extractor.

Groups CREATE_POST content by agent_id from the OASIS action log and maps each
agent to its persona via the prepared profiles (agent_id == profile user_id).
"""

from typing import List, Dict, Any

from ..simulation_runner import SimulationRunner
from ..simulation_manager import SimulationManager
from ...utils.logger import get_logger

logger = get_logger('mirofish.services.agent_posts')


def gather_agents(simulation_id: str, platform: str = "reddit") -> List[Dict[str, Any]]:
    actions = SimulationRunner.get_all_actions(simulation_id, platform=platform)
    texts_by_agent: Dict[int, List[str]] = {}
    for a in actions:
        if getattr(a, "action_type", None) != "CREATE_POST":
            continue
        content = (getattr(a, "action_args", None) or {}).get("content", "")
        if content:
            texts_by_agent.setdefault(a.agent_id, []).append(content)

    profiles = SimulationManager().get_profiles(simulation_id, platform=platform) or []
    persona_by_id = {p.get("user_id"): p.get("persona", "") for p in profiles}

    agents = [
        {"agent_id": agent_id, "persona": persona_by_id.get(agent_id, ""), "texts": texts}
        for agent_id, texts in texts_by_agent.items()
    ]
    logger.info(f"gather_agents: {len(agents)} agents posted in {simulation_id}")
    return agents
