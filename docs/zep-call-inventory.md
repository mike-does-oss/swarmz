# Zep call inventory (vendored MiroFish)

MiroFish (fork, `vendor/mirofish/`, upstream `a1fb546`) was written against
the Zep Cloud SDK (`zep_cloud==3.13.0`). Every Zep usage is listed below with how
it is handled now. Summary:

- **No `zep_cloud` import remains** under `vendor/mirofish/app` or `scripts`
  (`tests/memory/test_no_zep.py` greps for it). `zep-cloud` is removed from the
  vendor `pyproject.toml`, `requirements.txt` and `uv.lock`.
- The `zep_cloud.client.Zep` client is replaced by a **Zep-compatible shim**,
  `vendor/mirofish/app/memory_compat.py`, which implements only the subset of
  `client.graph.*` MiroFish calls and returns objects with the attribute names
  MiroFish reads (`uuid_`, `name`, `labels`, `summary`, `attributes`, `fact`,
  `source_node_uuid`, `target_node_uuid`, `created_at`, `valid_at`, `invalid_at`,
  `expired_at`, `episodes`). Each call is routed to a `swarmz.memory.MemoryBackend`
  looked up by MiroFish's `graph_id` (`memory_compat.register_backend`; unknown ids
  go to a factory that builds `swarmz.memory.make_backend(graph_id, now)`).
- The backend is `swarmz.memory.GraphitiBackend` (graphiti-core 0.31.2 on FalkorDB):
  one FalkorDB graph per run (`q_<question_id>__<arm>__<run_id>`), groups
  `shared` (seed brief) and `agent_<id>` (each agent's own simulation memory).
- MiroFish service code changed only at the import line in most files. Real logic
  changes: the memory updater (per-agent, per-round batches with reference times),
  `GraphBuilderService.create_graph` / `build_graph_from_text` (optional caller-chosen
  `graph_id`), `Config` (no ZEP_API_KEY requirement), the simulation runner
  (`MIROFISH_SIM_PYTHON`, round-end flush), plus a new `services/persona_injection.py`.

Legend for **Handling**: *replaced* = now served by the shim/backend;
*unchanged via shim* = MiroFish code untouched, works through the shim;
*removed* = the Zep call no longer exists.

## Client construction

| File | Function | Zep call | Purpose | Handling |
|---|---|---|---|---|
| `app/utils/zep_paging.py` | module | `from zep_cloud import InternalServerError`; `from zep_cloud.client import Zep` (type hint) | retry on Zep 5xx | replaced: `from ..memory_compat import InternalServerError, Zep` (stand-in exception; the shim never raises it, but `ConnectionError/OSError` retries still apply) |
| `app/services/graph_builder.py` | module, `GraphBuilderService.__init__` | `from zep_cloud.client import Zep`; `from zep_cloud import EpisodeData, EntityEdgeSourceTarget`; `Zep(api_key=...)` | graph build client | replaced: same names imported from `memory_compat`; `Zep(api_key)` ignores the key |
| `app/services/zep_tools.py` | `ZepToolsService.__init__` (l.429) | `Zep(api_key=...)` | report-agent retrieval client | replaced (import line only) |
| `app/services/zep_entity_reader.py` | `ZepEntityReader.__init__` (l.86) | `Zep(api_key=...)` | entity reads for profile generation | replaced (import line only) |
| `app/services/oasis_profile_generator.py` | `OasisProfileGenerator.__init__` (l.207) | `Zep(api_key=...)` | per-entity context search | replaced (import line only) |
| `app/services/zep_graph_memory_updater.py` | `ZepGraphMemoryUpdater.__init__` (l.245) | `Zep(api_key=...)` | push agent actions into the graph | replaced; class rewritten (see writes) |
| `app/config.py` | `Config.ZEP_API_KEY`, `Config.validate()` | `ZEP_API_KEY` env, required by `validate()` | Zep auth | `validate()` no longer requires it; `ZEP_API_KEY` defaults to the placeholder `'local-memory'` so legacy `if not ZEP_API_KEY` guards in services and Flask routes pass. The value is never sent anywhere. |

## Writes

| File | Function | Zep call | Purpose | Handling |
|---|---|---|---|---|
| `graph_builder.py` | `create_graph(name)` | `client.graph.create(graph_id, name, description)` | create a standalone graph `mirofish_<hex>` | replaced: shim resolves/creates the backend for the id. New optional `graph_id=` param so swarmz can use `q_<qid>__<arm>__<run>` (upstream id scheme kept when omitted). |
| `graph_builder.py` | `set_ontology` | `from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel` (dynamic pydantic classes) + `client.graph.set_ontology(graph_ids, entities, edges)` | custom entity/edge types | replaced: stand-in `EntityModel/EdgeModel` (pydantic BaseModel), `EntityText = str`; the shim converts the classes back to MiroFish's ontology dict and the backend turns that into Graphiti `entity_types` / `edge_types` / `edge_type_map` (attribute names that clash with Graphiti's node/edge fields get an `entity_` prefix). |
| `graph_builder.py` | `add_text_batches` | `client.graph.add_batch(graph_id, episodes=[EpisodeData(data, type="text")])` | ingest brief chunks (3 per call) | replaced: episodes without a group are seed data → `backend.build_seed_graph(chunks, ontology, ref)` into group `shared` (Graphiti `add_episode_bulk`, falls back to sequential `add_episode`). Returns episode objects with `uuid_`. |
| `graph_builder.py` | `_wait_for_episodes` | `client.graph.episode.get(uuid_)` → `.processed` | poll Zep's async ingestion | replaced: Graphiti ingestion is synchronous, so the shim's episodes are `processed=True` immediately; the loop exits on the first pass. |
| `graph_builder.py` | `delete_graph` | `client.graph.delete(graph_id)` | drop a graph (Flask route) | replaced: `backend.delete_graph()` (drops the FalkorDB graph) + unregister. |
| `zep_graph_memory_updater.py` | `_send_batch_activities` | `client.graph.add(graph_id, type="text", data=<5 mixed actions>)` | every 5 actions per platform, one mixed episode, no reference time | **removed**, replaced by `_send_round`: activities are buffered per (platform, round, agent); a round is flushed when a later round's action arrives, on `round_end` (`flush_round`, async marker so the monitor thread never blocks) or on stop. One `client.graph.add_batch` per round with **one episode per agent** in group `agent_<id>`; `reference_time = memory_compat.reference_time_for_round(...)` (never after freeze); `FreezeViolation` is never retried. |
| `app/services/ontology_generator.py` | `generate_python_code` (l.364) | emits the text `from zep_cloud.external_clients.ontology import ...` | downloadable ontology .py | neutralised: emits `from app.memory_compat import EntityModel, EntityText, EdgeModel`. |

## Reads

| File | Function | Zep call | Purpose | Handling |
|---|---|---|---|---|
| `utils/zep_paging.py` | `fetch_all_nodes` | `client.graph.node.get_by_graph_id(graph_id, limit, uuid_cursor)` | paginated node list (cap 2000) | replaced: `backend.list_entities()` (all groups), paginated by uuid descending in the shim. |
| `utils/zep_paging.py` | `fetch_all_edges` | `client.graph.edge.get_by_graph_id(graph_id, limit, uuid_cursor)` | paginated edge list | replaced: `backend.list_facts()`. |
| `graph_builder.py` | `_get_graph_info`, `get_graph_data` | `fetch_all_nodes/edges` | stats / graph JSON for the UI | unchanged via shim. `fact_type` = edge name. |
| `zep_entity_reader.py` | `get_all_nodes`, `get_all_edges`, `filter_defined_entities`, `get_entities_by_type` | `fetch_all_nodes/edges` | entities with custom labels → agents | unchanged via shim. Graphiti sets labels `['Entity', <Type>]` for typed entities, which is what the filter keeps. |
| `zep_entity_reader.py` | `get_node_edges` | `client.graph.node.get_entity_edges(node_uuid)` | an entity's facts | replaced: `backend.entity_facts(uuid)` (the shim finds the backend holding the node). |
| `zep_entity_reader.py` | `get_entity_with_context` | `client.graph.node.get(uuid_)` | single entity | replaced: `backend.get_entity(uuid)` across registered backends (Zep uuids were global; the call has no graph id). |
| `zep_tools.py` | `search_graph` | `client.graph.search(graph_id, query, limit, scope, reranker="cross_encoder")` | report-agent semantic search | replaced: `backend.search(query, limit, scope)` = Graphiti hybrid (BM25 + cosine) with **RRF** reranking; the `reranker` argument is ignored (no cross-encoder; avoids a second paid model). The keyword `_local_search` fallback is unchanged. |
| `zep_tools.py` | `get_all_nodes`, `get_all_edges`, `get_node_edges`, `get_entities_by_type`, `get_entity_summary`, `get_graph_statistics`, `get_simulation_context`, `insight_forge`, `panorama_search`, `quick_search` | `fetch_all_*`, `graph.search` | report-agent tools | unchanged via shim. `expired_at` is surfaced from Graphiti edges, so panorama's active/expired split still works. |
| `zep_tools.py` | `get_node_detail` | `client.graph.node.get(uuid_)` | node detail | replaced (as above). |
| `oasis_profile_generator.py` | `_search_zep_for_entity` | `client.graph.search(..., scope="edges", limit=30, reranker="rrf")` and `scope="nodes", limit=20` | enrich a profile prompt | replaced: `backend.search` (all groups — at profile time only the seed exists). |

## Indirect users (no direct Zep call)

| File | Use | Handling |
|---|---|---|
| `app/services/simulation_manager.py` (`prepare_simulation`) | `ZepEntityReader().filter_defined_entities` | unchanged via shim |
| `app/services/simulation_config_generator.py`, `agent_planner.py` | import `EntityNode` / `ZepEntityReader` | unchanged |
| `app/services/report_agent.py` | `ZepToolsService` tools listed above | unchanged via shim |
| `app/services/simulation_runner.py` | `ZepGraphMemoryManager.create_updater/get_updater/stop_updater/stop_all` | unchanged API; added `flush_round` call on `round_end`; OASIS subprocess uses `MIROFISH_SIM_PYTHON` |
| `app/services/prediction/graph_from_text.py` | `GraphBuilderService` sequence | unchanged sequence; optional `graph_id=` passthrough |
| `app/services/__init__.py` | re-exports Zep* services | unchanged |
| `app/api/graph.py` (l.287, 385, 570, 598) | `ZEP_API_KEY` guards + `GraphBuilderService` (build, get_graph_data, delete) | not on the prediction path; work through the shim (placeholder key passes the guards; an unregistered graph id gets a `make_backend(graph_id, now)` backend) |
| `app/api/simulation.py` (l.59–137, 484, 1410) | `ZEP_API_KEY` guards + `ZepEntityReader` | not on the prediction path; work through the shim |
| `app/api/report.py` (l.955, 1001) | `ZepToolsService` search / statistics | not on the prediction path; work through the shim |
| `scripts/*` (OASIS subprocess) | none — the subprocess never touched Zep; the parent process pushes actions | unchanged |

## Reference times

Zep episodes had no reference time. Now:

- seed brief chunks: `freeze_ts - (ROUND_HORIZON + 1)` s (`memory_compat.seed_reference_time`)
- agent episodes for round *r*: `min(freeze_ts, freeze_ts - (ROUND_HORIZON - r) s)`
  with `ROUND_HORIZON = 1000` (`memory_compat.reference_time_for_round`)

The simulation is a forecast made *at* the freeze time, so all simulated time is
pinned just before it; rounds stay strictly ordered (Graphiti uses reference times
to order episodes and to date facts) and nothing is ever after the freeze.
`MemoryBackend.check_reference_time` raises `FreezeViolation` for any later time;
`GraphitiBackend.add_episodes` validates the whole batch before writing anything.

## Other local changes in the vendor tree

- `app/config.py`: `.env` loading no longer overrides the process environment
  (upstream used `override=True`; set `MIROFISH_DOTENV_OVERRIDE=1` for the old
  behaviour). Under swarmz the repo-root `.env` is found by python-dotenv's search
  and would otherwise clobber variables swarmz sets programmatically.
- `app/services/persona_injection.py` (new): A2 persona hook, see
  `swarmz/mirofish_bridge.py`.
