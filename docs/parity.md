# Zep → Graphiti parity check

**Status: skipped (2026-11-02).**

## Why

A meaningful parity check means ingesting the same frozen brief into Zep Cloud
and into Graphiti, then comparing what MiroFish actually consumes: the typed
entities `ZepEntityReader.filter_defined_entities` returns (which become agents),
and `ZepToolsService.search_graph` results for a few questions.

- **Graphiti side needs an embedder.** Graphiti's hybrid search needs real
  embeddings (OpenAI `text-embedding-3-small` or Voyage). This machine has
  `ANTHROPIC_API_KEY` but no `OPENAI_API_KEY` / `VOYAGE_API_KEY` (both empty in
  `.env`), and Anthropic has no embedding model. With the test's hashed
  bag-of-words embedder, a search comparison would measure the fake, not Graphiti.
- **Zep side is not free.** `~/Desktop/projects/shoals-new/.env` holds what looks
  like a real Zep Cloud project key (not a placeholder). Ingestion uses Zep
  credits and writes a graph to that project. The instructions were to do parity
  only if it's cheap, so no Zep calls were made.

## What was verified instead

- **Extraction with the real LLM** (one cheap run, about 27k input / 1.3k output
  tokens on `claude-haiku-5-5`, real Graphiti pipeline on falkordblite, fake
  embedder): a 2-chunk brief with a Person/Organization/WORKS_FOR ontology, plus
  one agent episode. The output had typed labels (`['Entity','Person']`,
  `['Entity','Organization']`), the custom `WORKS_FOR` edge, free-form edges
  (`IS_MAYOR_OF`, `APPROVED`), and the agent's episode kept in group `agent_0`.
  Typed labels are what MiroFish's entity filter depends on, so profile generation
  gets entities the same way it did with Zep.
- **Shape parity**: `tests/memory/test_shim.py` runs MiroFish's own
  `GraphBuilderService`, `ZepEntityReader`, `ZepToolsService`, `zep_paging` and the
  memory updater against the shim and checks every attribute name they read.

## How to run it later (once an embedding key exists)

1. Put `OPENAI_API_KEY` (or `VOYAGE_API_KEY` plus `uv add voyageai`) in `.env`.
2. `uv run pytest -m integration tests/memory` runs the real-stack smoke test.
3. For Zep parity, use the 3.11 venv, which still has `zep_cloud`
   (`~/Desktop/projects/shoals-new/backend/.venv`). Build a Zep graph from one frozen
   brief with the original pre-swarmz code in `shoals-new` (its `backend/` is untouched by this work). Build the same brief with
   `GraphitiBackend`. Compare (a) the set of typed entity names and their labels
   and (b) the top-10 facts for 3–5 questions, by Jaccard overlap and by manual
   review. Expect differences in wording: Graphiti and Zep use different
   extraction prompts and models.
