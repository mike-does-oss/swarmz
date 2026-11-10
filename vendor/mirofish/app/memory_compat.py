"""Zep-compatible shim over a swarmz MemoryBackend (swarmz local change).

MiroFish was written against the Zep Cloud SDK (``zep_cloud``). This module
exposes only the subset of ``client.graph.*`` MiroFish uses, returning objects
with the attribute names MiroFish reads (``uuid_``, ``name``, ``labels``,
``summary``, ``attributes``, ``fact``, ``source_node_uuid`` ...). Every call is
routed to a MemoryBackend looked up by MiroFish's ``graph_id``:

    from app import memory_compat
    memory_compat.register_backend(graph_id, backend)   # swarmz bridge does this

If a graph_id has no registered backend, the factory set with
``set_backend_factory`` is used; the default factory builds
``swarmz.memory.make_backend(graph_id, freeze_ts=now)`` when swarmz is importable
(so the Flask app keeps working), otherwise it raises.

The backend is duck-typed (see swarmz/memory/backend.py): build_seed_graph,
add_episodes, search, list_entities, list_facts, get_entity, entity_facts,
delete_graph, and attributes graph_name / freeze_ts.

Writes and groups:
  * episodes with no group_id (Zep-style ``graph.add`` / ``add_batch``) are seed
    data -> ``backend.build_seed_graph`` into the ``shared`` group, using the
    ontology stored by ``graph.set_ontology``, at ``seed_reference_time``.
  * episodes with a group_id (the simulation memory updater sets one per agent)
    -> ``backend.add_episodes``.
Reference times never exceed the backend's freeze time (see
``reference_time_for_round``); the backend raises FreezeViolation if they do.
"""

from __future__ import annotations

import threading
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel

SHARED_GROUP = "shared"

# Simulated rounds are mapped to reference times just before the freeze time:
#   ref(round) = freeze_ts - (ROUND_HORIZON - round) seconds   (clamped to freeze_ts)
# so rounds are strictly ordered and never later than the freeze. The seed brief
# sits one second before round 0.
ROUND_HORIZON = 1000


def agent_group(agent_id) -> str:
    return f"agent_{agent_id}"


# ---------------------------------------------------------------------------
# Zep SDK stand-ins used by MiroFish code
# ---------------------------------------------------------------------------

class InternalServerError(Exception):
    """Stand-in for zep_cloud.InternalServerError (retried by zep_paging)."""


try:  # the backend's freeze-time guard error (never retried)
    from swarmz.memory.backend import FreezeViolation  # type: ignore
except Exception:  # swarmz not importable (e.g. MiroFish's own test venv)
    class FreezeViolation(ValueError):  # type: ignore[no-redef]
        """An episode's reference time is later than the question's freeze time."""

FREEZE_ERRORS = (FreezeViolation,)


class EntityModel(BaseModel):
    """Stand-in for zep_cloud.external_clients.ontology.EntityModel."""


class EdgeModel(BaseModel):
    """Stand-in for zep_cloud.external_clients.ontology.EdgeModel."""


EntityText = str


@dataclass
class EntityEdgeSourceTarget:
    source: str = "Entity"
    target: str = "Entity"


@dataclass
class EpisodeData:
    """Zep EpisodeData plus optional swarmz fields (group_id, reference_time, name)."""
    data: str
    type: str = "text"
    group_id: Optional[str] = None
    reference_time: Optional[datetime] = None
    name: Optional[str] = None
    source_description: Optional[str] = None


@dataclass
class _Episode:
    """Duck-typed swarmz.memory.Episode."""
    name: str
    body: str
    group_id: str
    reference_time: datetime
    source_description: str = ""


@dataclass
class ZepEpisode:
    uuid_: str
    data: str
    group_id: str
    processed: bool = True
    created_at: Optional[str] = None

    @property
    def uuid(self) -> str:
        return self.uuid_


@dataclass
class ZepNode:
    uuid_: str
    name: str
    labels: List[str] = field(default_factory=list)
    summary: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None
    group_id: str = SHARED_GROUP

    @property
    def uuid(self) -> str:
        return self.uuid_


@dataclass
class ZepEdge:
    uuid_: str
    name: str
    fact: str
    source_node_uuid: Optional[str]
    target_node_uuid: Optional[str]
    attributes: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None
    valid_at: Optional[str] = None
    invalid_at: Optional[str] = None
    expired_at: Optional[str] = None
    episodes: List[str] = field(default_factory=list)
    group_id: str = SHARED_GROUP

    @property
    def uuid(self) -> str:
        return self.uuid_

    @property
    def fact_type(self) -> str:
        return self.name


@dataclass
class GraphSearchResults:
    edges: List[ZepEdge] = field(default_factory=list)
    nodes: List[ZepNode] = field(default_factory=list)
    episodes: List[Any] = field(default_factory=list)


def _iso(ts) -> Optional[str]:
    if ts is None:
        return None
    return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)


def to_zep_node(ent) -> ZepNode:
    return ZepNode(uuid_=ent.uuid, name=ent.name or "", labels=list(ent.labels or []),
                   summary=ent.summary or "", attributes=dict(ent.attributes or {}),
                   created_at=_iso(getattr(ent, "created_at", None)),
                   group_id=getattr(ent, "group_id", SHARED_GROUP))


def to_zep_edge(f) -> ZepEdge:
    return ZepEdge(uuid_=f.uuid, name=f.name or "", fact=f.fact or "",
                   source_node_uuid=f.source_uuid, target_node_uuid=f.target_uuid,
                   attributes=dict(f.attributes or {}),
                   created_at=_iso(getattr(f, "created_at", None)),
                   valid_at=_iso(f.valid_at), invalid_at=_iso(f.invalid_at),
                   expired_at=_iso(getattr(f, "expired_at", None)),
                   episodes=list(getattr(f, "episodes", None) or []),
                   group_id=getattr(f, "group_id", SHARED_GROUP))


# ---------------------------------------------------------------------------
# Backend registry
# ---------------------------------------------------------------------------

_lock = threading.RLock()
_backends: Dict[str, Any] = {}
_ontologies: Dict[str, dict] = {}
_episodes: Dict[str, ZepEpisode] = {}
_factory: Optional[Callable[[str], Any]] = None


def _default_factory(graph_id: str):
    try:
        from swarmz.memory import make_backend  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            f"no memory backend registered for graph {graph_id!r} and swarmz.memory "
            "is not importable; call app.memory_compat.register_backend() first") from e
    return make_backend(graph_id, datetime.now(timezone.utc))


def set_backend_factory(factory: Optional[Callable[[str], Any]]) -> None:
    global _factory
    _factory = factory


def register_backend(graph_id: str, backend) -> None:
    with _lock:
        _backends[graph_id] = backend


def unregister_backend(graph_id: str) -> None:
    with _lock:
        _backends.pop(graph_id, None)
        _ontologies.pop(graph_id, None)


def get_backend(graph_id: str):
    with _lock:
        b = _backends.get(graph_id)
        if b is None:
            b = (_factory or _default_factory)(graph_id)
            _backends[graph_id] = b
        return b


def registered_graph_ids() -> List[str]:
    with _lock:
        return list(_backends)


def reset() -> None:
    """Forget all registrations (tests)."""
    with _lock:
        _backends.clear()
        _ontologies.clear()
        _episodes.clear()


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def reference_time_for_round(graph_id: str, round_num: int) -> datetime:
    """Simulated-time reference for an agent episode in ``round_num``; never after freeze."""
    freeze = _utc(get_backend(graph_id).freeze_ts)
    ref = freeze - timedelta(seconds=ROUND_HORIZON - int(round_num or 0))
    return min(ref, freeze)


def seed_reference_time(graph_id: str) -> datetime:
    freeze = _utc(get_backend(graph_id).freeze_ts)
    return freeze - timedelta(seconds=ROUND_HORIZON + 1)


def ontology_from_models(entities: Optional[dict], edges: Optional[dict]) -> dict:
    """Zep-style ontology classes (built by graph_builder.set_ontology) -> MiroFish dict."""
    def _attrs(model) -> list:
        out = []
        for name, f in getattr(model, "model_fields", {}).items():
            out.append({"name": name, "description": f.description or name})
        return out

    onto = {"entity_types": [], "edge_types": []}
    for name, model in (entities or {}).items():
        onto["entity_types"].append({"name": name, "description": (model.__doc__ or "").strip(),
                                     "attributes": _attrs(model)})
    for name, spec in (edges or {}).items():
        model, sts = spec if isinstance(spec, tuple) else (spec, [])
        onto["edge_types"].append({
            "name": name, "description": (model.__doc__ or "").strip(),
            "attributes": _attrs(model),
            "source_targets": [{"source": getattr(st, "source", "Entity"),
                                "target": getattr(st, "target", "Entity")} for st in sts],
        })
    return onto


# ---------------------------------------------------------------------------
# Zep-compatible client
# ---------------------------------------------------------------------------

def _paginate(items: list, limit: Optional[int], uuid_cursor: Optional[str]) -> list:
    items = sorted(items, key=lambda x: x.uuid_, reverse=True)
    if uuid_cursor:
        items = [x for x in items if x.uuid_ < uuid_cursor]
    return items[:limit] if limit else items


class _EpisodeAPI:
    def get(self, uuid_: str, **_):
        ep = _episodes.get(uuid_)
        if ep is None:
            raise KeyError(f"episode {uuid_} not found")
        return ep


class _NodeAPI:
    def get_by_graph_id(self, graph_id: str, limit: Optional[int] = None,
                        uuid_cursor: Optional[str] = None, **_) -> List[ZepNode]:
        nodes = [to_zep_node(e) for e in get_backend(graph_id).list_entities()]
        return _paginate(nodes, limit, uuid_cursor)

    def get(self, uuid_: str, **_) -> Optional[ZepNode]:
        for gid in registered_graph_ids():
            ent = _backends[gid].get_entity(uuid_)
            if ent is not None:
                return to_zep_node(ent)
        return None

    def get_entity_edges(self, node_uuid: str, **_) -> List[ZepEdge]:
        for gid in registered_graph_ids():
            b = _backends[gid]
            if b.get_entity(node_uuid) is not None:
                return [to_zep_edge(f) for f in b.entity_facts(node_uuid)]
        return []


class _EdgeAPI:
    def get_by_graph_id(self, graph_id: str, limit: Optional[int] = None,
                        uuid_cursor: Optional[str] = None, **_) -> List[ZepEdge]:
        edges = [to_zep_edge(f) for f in get_backend(graph_id).list_facts()]
        return _paginate(edges, limit, uuid_cursor)


class _GraphAPI:
    def __init__(self):
        self.node = _NodeAPI()
        self.edge = _EdgeAPI()
        self.episode = _EpisodeAPI()

    def create(self, graph_id: str, name: Optional[str] = None,
               description: Optional[str] = None, **_):
        get_backend(graph_id)
        return {"graph_id": graph_id, "name": name, "description": description}

    def set_ontology(self, graph_ids: List[str], entities: Optional[dict] = None,
                     edges: Optional[dict] = None, ontology: Optional[dict] = None, **_):
        onto = ontology if ontology is not None else ontology_from_models(entities, edges)
        with _lock:
            for gid in graph_ids:
                _ontologies[gid] = onto
                b = get_backend(gid)
                if hasattr(b, "set_ontology"):
                    b.set_ontology(onto)

    def get_ontology(self, graph_id: str) -> Optional[dict]:
        return _ontologies.get(graph_id)

    def add(self, graph_id: str, data: str, type: str = "text", group_id: Optional[str] = None,
            reference_time: Optional[datetime] = None, name: Optional[str] = None,
            source_description: Optional[str] = None, **_) -> ZepEpisode:
        return self.add_batch(graph_id, [EpisodeData(
            data=data, type=type, group_id=group_id, reference_time=reference_time, name=name,
            source_description=source_description)])[0]

    def add_batch(self, graph_id: str, episodes: List[Any], **_) -> List[ZepEpisode]:
        backend = get_backend(graph_id)
        seed_chunks: List[str] = []
        seed_ref: Optional[datetime] = None
        agent_eps: List[_Episode] = []
        out: List[ZepEpisode] = []
        for i, ep in enumerate(episodes):
            data = getattr(ep, "data", None) or ""
            gid = getattr(ep, "group_id", None)
            ref = getattr(ep, "reference_time", None)
            if gid is None or gid == SHARED_GROUP:
                seed_chunks.append(data)
                if ref is not None:
                    seed_ref = ref if seed_ref is None else max(seed_ref, ref)
                gid = SHARED_GROUP
            else:
                agent_eps.append(_Episode(
                    name=getattr(ep, "name", None) or f"{gid}_{_uuid.uuid4().hex[:8]}",
                    body=data, group_id=gid,
                    reference_time=ref if ref is not None else _utc(backend.freeze_ts),
                    source_description=getattr(ep, "source_description", None) or "simulation"))
            out.append(ZepEpisode(uuid_=_uuid.uuid4().hex, data=data, group_id=gid,
                                  created_at=datetime.now(timezone.utc).isoformat()))
        if seed_chunks:
            backend.build_seed_graph(seed_chunks, _ontologies.get(graph_id),
                                     seed_ref if seed_ref is not None else seed_reference_time(graph_id))
        if agent_eps:
            backend.add_episodes(agent_eps)
        with _lock:
            for zep_ep in out:
                _episodes[zep_ep.uuid_] = zep_ep
        return out

    def search(self, graph_id: str, query: str, limit: int = 10, scope: str = "edges",
               reranker: Optional[str] = None, group_ids: Optional[List[str]] = None,
               **_) -> GraphSearchResults:
        res = get_backend(graph_id).search(query, group_ids=group_ids, limit=limit, scope=scope)
        return GraphSearchResults(edges=[to_zep_edge(f) for f in res.facts],
                                  nodes=[to_zep_node(e) for e in res.entities])

    def delete(self, graph_id: str, **_) -> None:
        b = get_backend(graph_id)
        b.delete_graph()
        unregister_backend(graph_id)


class Zep:
    """Drop-in for ``zep_cloud.client.Zep``: ``Zep(api_key=...)`` (the key is ignored)."""

    def __init__(self, api_key: Optional[str] = None, **_):
        self.graph = _GraphAPI()


MemoryClient = Zep
