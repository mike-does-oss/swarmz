"""LLM / embedder / reranker clients for Graphiti ingestion.

Environment:
  GRAPHITI_LLM_MODEL        default 'claude-haiku-5-5'
  GRAPHITI_LLM_SMALL_MODEL  default = GRAPHITI_LLM_MODEL
  ANTHROPIC_API_KEY         required for the real LLM
  OPENAI_API_KEY            embedder (text-embedding-3-small), unless VOYAGE_API_KEY is set
  VOYAGE_API_KEY            Voyage embedder (needs the `voyageai` package installed)
  GRAPHITI_EMBEDDING_MODEL  override the embedding model name

Why Haiku 5.5 by default: graphiti's AnthropicClient gets structured output by
*forcing* a tool call (``tool_choice={'type':'tool'}``). Claude Opus 5.5 /
Sonnet 5.5 / Fable 5.1 reject forced tool_choice with a 400; Haiku 5.5 accepts
it, and it is by far the cheapest current model for high-volume extraction.
Also, anthropic SDK 2.x sends API version 2026-10-21, which rejects any
``temperature`` parameter, and graphiti always sends one — so we subclass the
client to drop it, and to fall back to ``tool_choice=auto`` + an instruction for
models that reject forced tool use (so GRAPHITI_LLM_MODEL=claude-sonnet-5-5 works).
"""

from __future__ import annotations

import os
import typing

from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.llm_client.anthropic_client import AnthropicClient
from graphiti_core.llm_client.config import LLMConfig, ModelSize

DEFAULT_LLM_MODEL = "claude-haiku-5-5"

# Models that 400 on tool_choice {'type': 'tool'|'any'}.
_NO_FORCED_TOOL_PREFIXES = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1",
                            "claude-mythos-5-1")


class SwarmzAnthropicClient(AnthropicClient):
    """graphiti AnthropicClient adapted to the current Messages API."""

    async def _generate_response(self, messages, response_model=None, max_tokens=None,
                                 model_size: ModelSize = ModelSize.medium, *, model=None):
        import json

        import anthropic
        from graphiti_core.llm_client.errors import RateLimitError, RefusalError

        system_message = messages[0]
        user_messages = [{"role": m.role, "content": m.content} for m in messages[1:]]
        effective_model: str = model or self.model
        if model_size == ModelSize.small and self.small_model:
            effective_model = model or self.small_model
        max_creation_tokens = self._resolve_max_tokens(max_tokens, effective_model)
        tools, tool_choice = self._create_tool(response_model)
        system = system_message.content
        if effective_model.startswith(_NO_FORCED_TOOL_PREFIXES):
            tool_choice = {"type": "auto"}
            system += (f"\n\nAlways respond by calling the `{tools[0]['name']}` tool "
                       "exactly once with your full answer.")
        try:
            result = await self.client.messages.create(
                system=system,
                max_tokens=max_creation_tokens,
                messages=typing.cast(typing.Any, user_messages),
                model=effective_model,
                tools=typing.cast(typing.Any, tools),
                tool_choice=typing.cast(typing.Any, tool_choice),
            )
        except anthropic.RateLimitError as e:
            raise RateLimitError(f"Rate limit exceeded: {e}") from e
        except anthropic.APIError as e:
            if "refused to respond" in str(e).lower():
                raise RefusalError(str(e)) from e
            raise

        if getattr(result, "stop_reason", None) == "refusal":
            raise RefusalError(f"model refused: {getattr(result, 'stop_details', None)}")
        usage = getattr(result, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        for item in result.content:
            if item.type == "tool_use":
                args = item.input if isinstance(item.input, dict) else json.loads(str(item.input))
                return args, in_tok, out_tok
        for item in result.content:
            if item.type == "text":
                return self._extract_json_from_text(item.text), in_tok, out_tok
        raise ValueError(f"Could not extract structured data from model response: {result.content}")


def make_llm_client() -> SwarmzAnthropicClient:
    model = os.environ.get("GRAPHITI_LLM_MODEL") or DEFAULT_LLM_MODEL
    small = os.environ.get("GRAPHITI_LLM_SMALL_MODEL") or model
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set (needed for Graphiti ingestion)")
    cfg = LLMConfig(api_key=api_key, model=model, small_model=small, max_tokens=8192)
    return SwarmzAnthropicClient(config=cfg)


def make_embedder() -> EmbedderClient:
    voyage_key = os.environ.get("VOYAGE_API_KEY")
    if voyage_key:
        try:
            from graphiti_core.embedder.voyage import VoyageAIEmbedder, VoyageAIEmbedderConfig
        except ImportError as e:  # voyageai not installed
            raise RuntimeError("VOYAGE_API_KEY is set but the `voyageai` package is not "
                               "installed (uv add voyageai)") from e
        model = os.environ.get("GRAPHITI_EMBEDDING_MODEL") or "voyage-3"
        return VoyageAIEmbedder(VoyageAIEmbedderConfig(api_key=voyage_key, embedding_model=model,
                                                       embedding_dim=1024))
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig

        model = os.environ.get("GRAPHITI_EMBEDDING_MODEL") or "text-embedding-3-small"
        return OpenAIEmbedder(OpenAIEmbedderConfig(api_key=openai_key, embedding_model=model,
                                                   embedding_dim=1536))
    raise RuntimeError("No embedding key: set OPENAI_API_KEY (text-embedding-3-small) "
                       "or VOYAGE_API_KEY")


class RankOrderReranker(CrossEncoderClient):
    """No-network reranker. Our search recipes use RRF, so this is only a placeholder
    that keeps graphiti from constructing its default OpenAI reranker."""

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        n = len(passages)
        return [(p, float(n - i)) for i, p in enumerate(passages)]
