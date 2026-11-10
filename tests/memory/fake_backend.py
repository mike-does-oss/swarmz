"""In-memory MemoryBackend for shim / bridge tests (no graph DB, no LLM)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from swarmz.memory.backend import SHARED_GROUP, Entity, Episode, Fact, MemoryBackend, SearchResult


def _words(s: str) -> set[str]:
    return set(re.findall(r"\w+", (s or "").lower()))


class FakeMemoryBackend(MemoryBackend):
    def __init__(self, graph_name: str, freeze_ts: datetime):
        super().__init__(graph_name, freeze_ts)
        self.episodes: list[Episode] = []
        self.add_calls: list[list[Episode]] = []
        self.seed_calls: list[tuple[list[str], dict | None, datetime]] = []
        self.entities: dict[str, Entity] = {}
        self.facts: list[Fact] = []
        self.ontology: dict | None = None
        self.deleted = False

    def set_ontology(self, ontology):
        self.ontology = ontology

    def _ingest(self, body: str, group_id: str):
        names = [w for w in re.findall(r"\b[A-Z][a-z]+\b", body)]
        ids = []
        for n in names:
            key = f"{group_id}:{n}"
            if key not in self.entities:
                self.entities[key] = Entity(uuid=uuid.uuid4().hex, name=n,
                                            labels=["Entity", "Person"], summary=f"{n} summary",
                                            group_id=group_id)
            ids.append(self.entities[key].uuid)
        for a, b in zip(ids, ids[1:]):
            f = Fact(uuid=uuid.uuid4().hex, fact=body.strip(), source_uuid=a, target_uuid=b,
                     name="RELATES_TO")
            f.group_id = group_id  # type: ignore[attr-defined]
            self.facts.append(f)

    def build_seed_graph(self, chunks, ontology, reference_time):
        ref = self.check_reference_time(reference_time)
        self.seed_calls.append((list(chunks), ontology, ref))
        self.add_episodes([Episode(name=f"seed{i}", body=c, group_id=SHARED_GROUP,
                                   reference_time=ref) for i, c in enumerate(chunks)])

    def add_episodes(self, episodes):
        for ep in episodes:
            self.check_reference_time(ep.reference_time)
        self.add_calls.append(list(episodes))
        for ep in episodes:
            self.episodes.append(ep)
            self._ingest(ep.body, ep.group_id)

    def _in(self, gid, group_ids):
        return group_ids is None or gid in group_ids

    def search(self, query, group_ids=None, limit=10, scope="edges"):
        q = _words(query)
        out = SearchResult()
        if scope in ("edges", "both"):
            facts = [f for f in self.facts if self._in(getattr(f, "group_id", SHARED_GROUP), group_ids)
                     and q & _words(f.fact)]
            out.facts = facts[:limit]
        if scope in ("nodes", "both"):
            ents = [e for e in self.entities.values() if self._in(e.group_id, group_ids)
                    and q & _words(e.name)]
            out.entities = ents[:limit]
        return out

    def list_entities(self, group_ids=None):
        return [e for e in self.entities.values() if self._in(e.group_id, group_ids)]

    def list_facts(self, group_ids=None):
        return [f for f in self.facts if self._in(getattr(f, "group_id", SHARED_GROUP), group_ids)]

    def get_entity(self, uuid):
        return next((e for e in self.entities.values() if e.uuid == uuid), None)

    def entity_facts(self, uuid):
        return [f for f in self.facts if uuid in (f.source_uuid, f.target_uuid)]

    def close(self):
        pass

    def delete_graph(self):
        self.deleted = True
