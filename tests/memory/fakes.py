"""No-network stand-ins for Graphiti's LLM, embedder and reranker.

FakeLLM does a crude but deterministic extraction (capitalised words become
entities, consecutive entities in a sentence get a fact edge with that
sentence as the fact) so a real Graphiti pipeline on falkordblite produces a
searchable graph without any API calls.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.embedder.client import EMBEDDING_DIM, EmbedderClient
from graphiti_core.llm_client.client import LLMClient
from graphiti_core.llm_client.config import LLMConfig, ModelSize

_STOP = {"The", "A", "An", "In", "On", "At", "He", "She", "It", "They", "This", "That", "We",
         "I", "And", "But", "Or", "If", "Of", "To", "For", "By", "As", "Is", "Was", "Be"}


def _block(text: str, tag: str) -> str | None:
    found = re.findall(rf"<{tag}>\n(.*?)\n</{tag}>", text, re.S)
    return found[-1] if found else None


def _current_text(text: str) -> str:
    for tag in ("CURRENT MESSAGES", "CURRENT MESSAGE", "CURRENT_MESSAGE", "TEXT", "JSON"):
        b = _block(text, tag)
        if b is not None:
            return b
    return text


def _entities(text: str) -> list[str]:
    seen: list[str] = []
    for w in re.findall(r"\b[A-Z][a-zA-Z]+\b", text):
        if w not in _STOP and w not in seen:
            seen.append(w)
    return seen


def _edges(text: str) -> list[dict[str, Any]]:
    out = []
    for sent in re.split(r"(?<=[.!?])\s+|\n", text):
        names = _entities(sent)
        for a, b in zip(names, names[1:]):
            out.append({"source_entity_name": a, "target_entity_name": b,
                        "relation_type": "RELATES_TO", "fact": sent.strip(),
                        "episode_indices": [0], "valid_at": None, "invalid_at": None})
    return out


class FakeLLM(LLMClient):
    def __init__(self):
        super().__init__(LLMConfig(api_key="fake", model="fake"), cache=False)
        self.calls: list[str] = []

    async def _generate_response(self, messages, response_model=None, max_tokens=0,
                                 model_size=ModelSize.medium):  # pragma: no cover - unused
        raise NotImplementedError

    async def generate_response(self, messages, response_model=None, max_tokens=None,
                                model_size=ModelSize.medium, group_id=None, prompt_name=None,
                                *, attribute_extraction=False, model=None) -> dict[str, Any]:
        name = response_model.__name__ if response_model is not None else "None"
        self.calls.append(name)
        user = messages[-1].content if messages else ""
        cur = _current_text(user)
        if name == "CombinedExtraction":
            return {"extracted_entities": [{"name": n, "entity_type_id": 0}
                                           for n in _entities(cur)],
                    "edges": [{k: v for k, v in e.items() if k not in ("valid_at", "invalid_at")}
                              for e in _edges(cur)]}
        if name == "ExtractedEntities":
            return {"extracted_entities": [{"name": n, "entity_type_id": 0, "episode_indices": [0]}
                                           for n in _entities(cur)]}
        if name == "ExtractedEdges":
            return {"edges": _edges(cur)}
        if name == "NodeResolutions":
            try:
                ents = json.loads(_block(user, "ENTITIES") or "[]")
            except json.JSONDecodeError:
                ents = []
            return {"entity_resolutions": [
                {"id": e.get("id", i), "name": e.get("name", ""), "duplicate_candidate_id": -1}
                for i, e in enumerate(ents)]}
        if name == "NodeDuplicate":
            return {"id": 0, "name": "", "duplicate_candidate_id": -1}
        if name == "EdgeDuplicate":
            return {"duplicate_facts": [], "contradicted_facts": []}
        if name == "SummarizedEntities":
            return {"summaries": []}
        if name in ("Summary", "EntitySummary"):
            return {"summary": cur[:200]}
        if name == "SummaryDescription":
            return {"description": cur[:100]}
        if name == "EdgeTimestamps":
            return {"valid_at": None, "invalid_at": None}
        if name == "BatchEdgeTimestamps":
            return {"timestamps": []}
        return {}


class FakeEmbedder(EmbedderClient):
    """Hashed bag-of-words embedding (cosine similarity ~ word overlap)."""

    def __init__(self, dim: int = EMBEDDING_DIM):
        self.dim = dim

    def _embed(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for w in re.findall(r"\w+", (text or "").lower()):
            h = int(hashlib.md5(w.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    async def create(self, input_data) -> list[float]:
        if isinstance(input_data, list):
            input_data = " ".join(str(x) for x in input_data)
        return self._embed(str(input_data))

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in input_data_list]


class FakeReranker(CrossEncoderClient):
    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        q = set(re.findall(r"\w+", query.lower()))
        scored = [(p, float(len(q & set(re.findall(r"\w+", p.lower()))))) for p in passages]
        return sorted(scored, key=lambda x: x[1], reverse=True)
