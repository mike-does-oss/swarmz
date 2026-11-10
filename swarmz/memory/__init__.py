"""swarmz memory: the MemoryBackend contract plus the Graphiti/FalkorDB implementation.

``import swarmz.memory`` is cheap: graphiti-core is only imported when a backend
is built (``make_backend`` / ``GraphitiBackend``).
"""

from __future__ import annotations

import os

# Must be set before graphiti_core is imported anywhere.
os.environ.setdefault("GRAPHITI_TELEMETRY_ENABLED", "false")
os.environ.setdefault("SEMAPHORE_LIMIT", "10")

from datetime import datetime  # noqa: E402

from .backend import (  # noqa: E402
    SHARED_GROUP,
    Entity,
    Episode,
    Fact,
    FreezeViolation,
    MemoryBackend,
    SearchResult,
    agent_group,
)


def make_backend(graph_name: str, freeze_ts: datetime, **kwargs) -> MemoryBackend:
    """Build the default backend (Graphiti on FalkorDB, mode from FALKORDB_MODE).

    kwargs are passed to GraphitiBackend (llm_client, embedder, cross_encoder, falkor, ...).
    """
    from .graphiti_backend import GraphitiBackend

    return GraphitiBackend(graph_name, freeze_ts, **kwargs)


def __getattr__(name: str):
    if name == "GraphitiBackend":
        from .graphiti_backend import GraphitiBackend

        return GraphitiBackend
    raise AttributeError(name)


__all__ = [
    "SHARED_GROUP", "Entity", "Episode", "Fact", "FreezeViolation", "MemoryBackend",
    "SearchResult", "agent_group", "make_backend", "GraphitiBackend",
]
