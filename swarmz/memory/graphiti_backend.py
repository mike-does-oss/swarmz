"""GraphitiBackend: MemoryBackend on graphiti-core + FalkorDB.

Layout: one FalkorDB graph database per question run (``graph_name`` =
``q_<question_id>__<arm>__<run_id>``). Inside it, Graphiti ``group_id`` is a
plain property: ``shared`` for the seed graph and ``agent_<id>`` per agent.

Graphiti's FalkorDriver normally maps every group_id to its *own* database
(``driver.clone(database=group_id)``). That would put every run's ``agent_0``
into one global graph, so we pin the driver: ``clone`` returns the same driver
and group_id stays a filter inside the run's database.

Graphiti is async; this class is a sync facade. All coroutines run on the shared
memory loop (``swarmz.memory._loop``) so it is safe to call from MiroFish's
worker threads.
"""

from __future__ import annotations

import os

os.environ.setdefault("GRAPHITI_TELEMETRY_ENABLED", "false")
os.environ.setdefault("SEMAPHORE_LIMIT", "10")

import asyncio  # noqa: E402
import logging  # noqa: E402
from datetime import datetime  # noqa: E402
from typing import Any, Optional  # noqa: E402

from graphiti_core import Graphiti  # noqa: E402
from graphiti_core.driver.falkordb_driver import FalkorDriver  # noqa: E402
from graphiti_core.edges import EntityEdge  # noqa: E402
from graphiti_core.errors import GroupsEdgesNotFoundError, NodeNotFoundError  # noqa: E402
from graphiti_core.nodes import EntityNode, EpisodeType  # noqa: E402
from graphiti_core.search.search_config_recipes import (  # noqa: E402
    COMBINED_HYBRID_SEARCH_RRF,
    EDGE_HYBRID_SEARCH_RRF,
    NODE_HYBRID_SEARCH_RRF,
)
from graphiti_core.utils.bulk_utils import RawEpisode  # noqa: E402
from graphiti_core.driver.falkordb import fulltext as _falkor_fulltext  # noqa: E402

_orig_sanitize = _falkor_fulltext.sanitize_falkor_fulltext_query


def _sanitize_fulltext(query: str) -> str:
    """graphiti's sanitizer keeps '_' as a word, so a URL like espn.com/nfl/team/depth/_/name/bal
    yields a bare '_' token and RediSearch rejects the whole query (which killed an A2 run).
    Drop tokens with no letters or digits."""
    return " ".join(w for w in _orig_sanitize(query).split() if any(c.isalnum() for c in w))


if not getattr(_falkor_fulltext.sanitize_falkor_fulltext_query, "_swarmz_patched", False):
    _sanitize_fulltext._swarmz_patched = True  # type: ignore[attr-defined]
    _falkor_fulltext.sanitize_falkor_fulltext_query = _sanitize_fulltext
from pydantic import BaseModel, Field, create_model  # noqa: E402

from . import _loop  # noqa: E402
from .backend import SHARED_GROUP, Entity, Episode, Fact, MemoryBackend, SearchResult  # noqa: E402
from .falkor import FalkorConfig, config_from_env, delete_graph_async, get_async_client  # noqa: E402

logger = logging.getLogger("swarmz.memory.graphiti")

# Generous: one call may ingest a whole round of agent episodes (LLM-bound).
DEFAULT_TIMEOUT = float(os.environ.get("SWARMZ_MEMORY_TIMEOUT", "900"))

_ENTITY_RESERVED = set(EntityNode.model_fields.keys())
_EDGE_RESERVED = set(EntityEdge.model_fields.keys())


class _PinnedFalkorDriver(FalkorDriver):
    """FalkorDriver whose clone() keeps the same database (group_id = property filter)."""

    def clone(self, database: str):  # noqa: D401
        return self


def _safe_attr(name: str, reserved: set[str]) -> str:
    name = "".join(c if (c.isalnum() or c == "_") else "_" for c in name) or "attr"
    if name[0].isdigit():
        name = f"a_{name}"
    if name in reserved or name.lower() in reserved:
        return f"entity_{name}"
    return name


def ontology_to_models(ontology: dict | None) -> tuple[dict, dict, dict]:
    """MiroFish ontology dict -> (entity_types, edge_types, edge_type_map) for Graphiti.

    Ontology format (MiroFish OntologyGenerator):
      {"entity_types": [{"name", "description", "attributes": [{"name","description"}]}],
       "edge_types":   [{"name", "description", "attributes": [...],
                         "source_targets": [{"source","target"}]}]}
    """
    if not ontology:
        return {}, {}, {}
    entity_types: dict[str, type[BaseModel]] = {}
    for ent in ontology.get("entity_types") or []:
        if isinstance(ent, str):
            ent = {"name": ent}
        name = ent.get("name")
        if not name:
            continue
        fields: dict[str, Any] = {}
        for attr in ent.get("attributes") or []:
            an = _safe_attr(attr.get("name", ""), _ENTITY_RESERVED)
            fields[an] = (Optional[str], Field(default=None,
                                               description=attr.get("description") or an))
        model = create_model(name, __base__=BaseModel, **fields)
        model.__doc__ = ent.get("description") or f"A {name} entity."
        entity_types[name] = model

    edge_types: dict[str, type[BaseModel]] = {}
    edge_type_map: dict[tuple[str, str], list[str]] = {}
    for edge in ontology.get("edge_types") or []:
        if isinstance(edge, str):
            edge = {"name": edge}
        name = edge.get("name")
        if not name:
            continue
        fields = {}
        for attr in edge.get("attributes") or []:
            an = _safe_attr(attr.get("name", ""), _EDGE_RESERVED)
            fields[an] = (Optional[str], Field(default=None,
                                               description=attr.get("description") or an))
        model = create_model(name, __base__=BaseModel, **fields)
        model.__doc__ = edge.get("description") or f"A {name} relationship."
        edge_types[name] = model
        for st in edge.get("source_targets") or [{"source": "Entity", "target": "Entity"}]:
            key = (st.get("source") or "Entity", st.get("target") or "Entity")
            edge_type_map.setdefault(key, [])
            if name not in edge_type_map[key]:
                edge_type_map[key].append(name)
    return entity_types, edge_types, edge_type_map


def _entity(node: EntityNode) -> Entity:
    return Entity(uuid=node.uuid, name=node.name, labels=list(node.labels or []),
                  summary=node.summary or "", attributes=dict(node.attributes or {}),
                  group_id=node.group_id)


def _fact(edge: EntityEdge) -> Fact:
    f = Fact(uuid=edge.uuid, fact=edge.fact, source_uuid=edge.source_node_uuid,
             target_uuid=edge.target_node_uuid, name=edge.name, valid_at=edge.valid_at,
             invalid_at=edge.invalid_at, attributes=dict(edge.attributes or {}))
    # Extra Zep-era fields the shim can surface (not part of the Fact contract).
    f.created_at = edge.created_at  # type: ignore[attr-defined]
    f.expired_at = edge.expired_at  # type: ignore[attr-defined]
    f.episodes = list(edge.episodes or [])  # type: ignore[attr-defined]
    f.group_id = edge.group_id  # type: ignore[attr-defined]
    return f


class GraphitiBackend(MemoryBackend):
    def __init__(self, graph_name: str, freeze_ts: datetime, *, llm_client=None, embedder=None,
                 cross_encoder=None, falkor: FalkorConfig | None = None,
                 max_coroutines: int | None = None, use_bulk: bool | None = None,
                 timeout: float = DEFAULT_TIMEOUT):
        super().__init__(graph_name, freeze_ts)
        from .clients import RankOrderReranker, make_embedder, make_llm_client

        self.falkor = falkor or config_from_env()
        self.timeout = timeout
        if use_bulk is None:
            use_bulk = os.environ.get("GRAPHITI_USE_BULK", "1") != "0"
        self.use_bulk = use_bulk
        self._llm = llm_client or make_llm_client()
        self._embedder = embedder or make_embedder()
        self._cross = cross_encoder or RankOrderReranker()
        self._max_coroutines = max_coroutines
        self._entity_types: dict = {}
        self._edge_types: dict = {}
        self._edge_type_map: dict = {}
        self._closed = False
        self.graphiti: Graphiti = self._run(self._init())

    # -- plumbing ------------------------------------------------------------
    def _run(self, coro, timeout: float | None = None):
        return _loop.run(coro, timeout=timeout or self.timeout)

    async def _init(self) -> Graphiti:
        client = await get_async_client(self.falkor)
        driver = _PinnedFalkorDriver(falkor_db=client, database=self.graph_name)
        g = Graphiti(graph_driver=driver, llm_client=self._llm, embedder=self._embedder,
                     cross_encoder=self._cross, max_coroutines=self._max_coroutines)
        # The driver schedules index creation on construction when a loop is running.
        if driver._init_task is not None:
            await driver._init_task
        else:
            await driver.build_indices_and_constraints()
        return g

    @property
    def driver(self) -> FalkorDriver:
        return self.graphiti.driver

    def set_ontology(self, ontology: dict | None) -> None:
        self._entity_types, self._edge_types, self._edge_type_map = ontology_to_models(ontology)

    def _type_kwargs(self) -> dict:
        kw: dict[str, Any] = {}
        if self._entity_types:
            kw["entity_types"] = self._entity_types
        if self._edge_types:
            kw["edge_types"] = self._edge_types
            kw["edge_type_map"] = self._edge_type_map
        return kw

    # -- writes --------------------------------------------------------------
    def build_seed_graph(self, chunks: list[str], ontology: dict | None,
                         reference_time: datetime) -> None:
        ref = self.check_reference_time(reference_time)
        if ontology is not None:
            self.set_ontology(ontology)
        episodes = [Episode(name=f"seed_{i}", body=c, group_id=SHARED_GROUP, reference_time=ref,
                            source_description="frozen brief")
                    for i, c in enumerate(chunks) if c and c.strip()]
        self.add_episodes(episodes)

    def add_episodes(self, episodes: list[Episode]) -> None:
        if not episodes:
            return
        # Validate everything before writing anything.
        refs = [self.check_reference_time(ep.reference_time) for ep in episodes]
        by_group: dict[str, list[tuple[Episode, datetime]]] = {}
        for ep, ref in zip(episodes, refs):
            by_group.setdefault(ep.group_id or SHARED_GROUP, []).append((ep, ref))
        self._run(self._add_grouped(by_group))

    async def _add_grouped(self, by_group: dict[str, list[tuple[Episode, datetime]]]) -> None:
        results = await asyncio.gather(
            *[self._add_group(g, items) for g, items in by_group.items()],
            return_exceptions=True,
        )
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors:
            raise errors[0]

    async def _add_group(self, group_id: str, items: list[tuple[Episode, datetime]]) -> None:
        kw = self._type_kwargs()
        items = sorted(items, key=lambda it: it[1])
        if self.use_bulk and len(items) > 1:
            raw = [RawEpisode(name=ep.name, content=ep.body,
                              source_description=ep.source_description or "",
                              source=EpisodeType.text, reference_time=ref)
                   for ep, ref in items]
            try:
                await self.graphiti.add_episode_bulk(raw, group_id=group_id, **kw)
                return
            except Exception as e:  # pragma: no cover - depends on graphiti/falkor version
                logger.warning(f"add_episode_bulk failed for {group_id} ({e!r}); "
                               "falling back to sequential add_episode")
                self.use_bulk = False
        for ep, ref in items:
            await self.graphiti.add_episode(
                name=ep.name, episode_body=ep.body,
                source_description=ep.source_description or "",
                reference_time=ref, source=EpisodeType.text, group_id=group_id, **kw)

    # -- reads ---------------------------------------------------------------
    def search(self, query: str, group_ids: list[str] | None = None, limit: int = 10,
               scope: str = "edges") -> SearchResult:
        recipe = {"edges": EDGE_HYBRID_SEARCH_RRF, "nodes": NODE_HYBRID_SEARCH_RRF}.get(
            scope, COMBINED_HYBRID_SEARCH_RRF)
        config = recipe.model_copy(deep=True)
        config.limit = limit
        res = self._run(self.graphiti.search_(query, config=config, group_ids=group_ids or None))
        out = SearchResult()
        if scope in ("edges", "both"):
            out.facts = [_fact(e) for e in res.edges[:limit]]
        if scope in ("nodes", "both"):
            out.entities = [_entity(n) for n in res.nodes[:limit]]
        return out

    async def _group_ids(self, label: str) -> list[str]:
        if label == "Entity":
            q = "MATCH (n:Entity) RETURN DISTINCT n.group_id AS g"
        else:
            q = "MATCH (:Entity)-[e:RELATES_TO]->(:Entity) RETURN DISTINCT e.group_id AS g"
        res = await self.driver.execute_query(q)
        if not res:
            return []
        records, _, _ = res
        return [r["g"] for r in records if r.get("g")]

    def list_entities(self, group_ids: list[str] | None = None) -> list[Entity]:
        async def _list():
            gids = group_ids or await self._group_ids("Entity")
            if not gids:
                return []
            return await EntityNode.get_by_group_ids(self.driver, gids)
        return [_entity(n) for n in self._run(_list())]

    def list_facts(self, group_ids: list[str] | None = None) -> list[Fact]:
        async def _list():
            gids = group_ids or await self._group_ids("Edge")
            if not gids:
                return []
            try:
                return await EntityEdge.get_by_group_ids(self.driver, gids)
            except GroupsEdgesNotFoundError:
                return []
        return [_fact(e) for e in self._run(_list())]

    def get_entity(self, uuid: str) -> Entity | None:
        async def _get():
            try:
                return await EntityNode.get_by_uuid(self.driver, uuid)
            except NodeNotFoundError:
                return None
        node = self._run(_get())
        return _entity(node) if node is not None else None

    def entity_facts(self, uuid: str) -> list[Fact]:
        return [_fact(e) for e in self._run(EntityEdge.get_by_node_uuid(self.driver, uuid))]

    def count_episodes(self, group_ids: list[str] | None = None) -> int:
        q = "MATCH (e:Episodic) "
        if group_ids:
            q += "WHERE e.group_id IN $gids "
        q += "RETURN count(e) AS c"

        async def _count():
            res = await self.driver.execute_query(q, gids=group_ids or [])
            return res[0][0]["c"] if res and res[0] else 0
        return int(self._run(_count()))

    # -- lifecycle -------------------------------------------------------------
    def close(self) -> None:
        # The FalkorDB client is shared process-wide (see falkor.py); nothing to close here.
        self._closed = True

    def delete_graph(self) -> None:
        self._run(delete_graph_async(self.graph_name, self.falkor))
