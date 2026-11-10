"""MemoryBackend: the only memory surface MiroFish sees.

MiroFish was written against Zep Cloud. Everything it needs from a memory
store goes through this interface, so the store can be swapped (Graphiti on
FalkorDB today) without touching simulation logic.

Groups: one group per agent (``agent_<id>``) plus ``shared`` for the seed
graph built from the frozen brief. Each question run gets its own graph
database, so nothing carries between questions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SHARED_GROUP = "shared"


def agent_group(agent_id: int | str) -> str:
    return f"agent_{agent_id}"


class FreezeViolation(ValueError):
    """An episode's reference time is later than the question's freeze time."""


@dataclass
class Episode:
    name: str
    body: str
    group_id: str
    reference_time: datetime
    source_description: str = ""


@dataclass
class Fact:
    uuid: str
    fact: str
    source_uuid: str | None = None
    target_uuid: str | None = None
    name: str = ""
    valid_at: datetime | None = None
    invalid_at: datetime | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class Entity:
    uuid: str
    name: str
    labels: list[str] = field(default_factory=list)
    summary: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    group_id: str = SHARED_GROUP


@dataclass
class SearchResult:
    facts: list[Fact] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)


class MemoryBackend(ABC):
    """One instance is bound to one question run's graph and freeze time."""

    def __init__(self, graph_name: str, freeze_ts: datetime):
        self.graph_name = graph_name
        self.freeze_ts = _utc(freeze_ts)

    # -- guard ------------------------------------------------------------
    def check_reference_time(self, reference_time: datetime) -> datetime:
        ref = _utc(reference_time)
        if ref > self.freeze_ts:
            raise FreezeViolation(
                f"episode reference_time {ref.isoformat()} is after the question "
                f"freeze time {self.freeze_ts.isoformat()}"
            )
        return ref

    # -- writes -----------------------------------------------------------
    @abstractmethod
    def build_seed_graph(self, chunks: list[str], ontology: dict | None,
                         reference_time: datetime) -> None:
        """Ingest the frozen brief chunks into the shared group."""

    @abstractmethod
    def add_episodes(self, episodes: list[Episode]) -> None:
        """Ingest episodes (batch per agent per round). Must call check_reference_time."""

    # -- reads ------------------------------------------------------------
    @abstractmethod
    def search(self, query: str, group_ids: list[str] | None = None,
               limit: int = 10, scope: str = "edges") -> SearchResult:
        """scope: 'edges' (facts), 'nodes' (entities) or 'both'."""

    @abstractmethod
    def list_entities(self, group_ids: list[str] | None = None) -> list[Entity]: ...

    @abstractmethod
    def list_facts(self, group_ids: list[str] | None = None) -> list[Fact]: ...

    @abstractmethod
    def get_entity(self, uuid: str) -> Entity | None: ...

    @abstractmethod
    def entity_facts(self, uuid: str) -> list[Fact]: ...

    # -- lifecycle --------------------------------------------------------
    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def delete_graph(self) -> None: ...


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)
