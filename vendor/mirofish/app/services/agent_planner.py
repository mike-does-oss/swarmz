"""
Agent planner — decouple swarm size N from graph entity count E.

The simulation generators emit exactly one agent/profile per element of the
entity list they receive (indexed 0..len-1, with agent_id and user_id aligned).
So swarm size is controlled purely by planning an N-length entity list BEFORE
the generators run — the generators themselves are unchanged. Deterministic so
runs are reproducible (no RNG).

  N == E : the E entities as-is.
  N <  E : the N most central entities (centrality = len(related_edges)).
  N >  E : all E + (N-E) extra slots, split by public_fraction into synthesized
           "general public" personas and weighted duplication of central entities.
"""

from typing import List, Optional

from .zep_entity_reader import EntityNode
from ..utils.logger import get_logger

logger = get_logger('mirofish.services.agent_planner')

DEFAULT_PUBLIC_FRACTION = 0.3


def _centrality(entity) -> int:
    return len(getattr(entity, 'related_edges', None) or [])


def _rank_by_centrality(entities: List) -> List:
    # Stable sort by centrality desc; ties keep original order (Python sort is stable).
    return sorted(entities, key=_centrality, reverse=True)


def synthetic_public_entity(i: int) -> EntityNode:
    return EntityNode(
        uuid=f"synthetic-public-{i}",
        name=f"Public Persona {i}",
        labels=["Person"],
        summary=(
            "A member of the general public with no special affiliation, reacting to "
            "the situation based on everyday concerns and common knowledge."
        ),
        attributes={"synthetic_public": True},
        related_edges=[],
        related_nodes=[],
    )


def _allocate_by_weight(entities: List, n: int) -> List:
    """Deterministic largest-remainder allocation of n duplicate slots across
    entities weighted by (1 + centrality). Returns a flat list of length n."""
    if n <= 0 or not entities:
        return []
    weights = [1 + _centrality(e) for e in entities]
    total = sum(weights)
    ideal = [w / total * n for w in weights]
    floors = [int(x) for x in ideal]
    remainder = n - sum(floors)
    # distribute the remaining slots to the largest fractional parts (stable by index)
    order = sorted(range(len(entities)), key=lambda i: (ideal[i] - floors[i], -i), reverse=True)
    counts = list(floors)
    for k in range(remainder):
        counts[order[k]] += 1
    out: List = []
    for entity, count in zip(entities, counts):
        out.extend([entity] * count)
    return out


def plan_agent_entities(entities: List, num_agents: Optional[int],
                        public_fraction: float = DEFAULT_PUBLIC_FRACTION) -> List:
    e_count = len(entities)
    if num_agents is None or num_agents <= 0:
        num_agents = e_count

    if e_count == 0:
        return [synthetic_public_entity(i) for i in range(num_agents)]

    if num_agents == e_count:
        return list(entities)  # unchanged original order — backward-compatible default
    if num_agents < e_count:
        return _rank_by_centrality(entities)[:num_agents]

    # num_agents > e_count
    result = list(entities)  # keep all E in their original order
    extra = num_agents - e_count
    num_public = round(extra * public_fraction)
    num_dup = extra - num_public
    ranked = _rank_by_centrality(entities)
    result.extend(_allocate_by_weight(ranked, num_dup))
    result.extend(synthetic_public_entity(i) for i in range(num_public))
    logger.info(
        f"agent planner: N={num_agents} from E={e_count} "
        f"(+{num_dup} weighted dups, +{num_public} public personas)"
    )
    return result
