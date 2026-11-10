"""
Brief pipeline orchestrator (spec §3 pipeline, §4.7 freeze & log).

Runs intake -> decompose -> retrieve -> triage -> assemble -> scrub, applies the
freeze gates, and on success freezes the brief: SHA-256 file + order-book
snapshot + provenance + prediction row, written atomically. No simulation code
is touched here (MiroFish/baseline wiring is Plan 7).
"""

import sqlite3
from dataclasses import dataclass
from typing import Optional, Dict, Any

from . import intake
from .decomposer import decompose
from .retriever import retrieve
from .triage import triage
from .assembler import assemble
from .scrubber import scrub
from ..orderbook_snapshot import snapshot_order_book
from .stage_llm import assembler_llm, scrubber_llm, warn_if_same_model
from .brave_client import BraveSearchClient
from .article_fetcher import fetch_article as default_fetch_article
from .odds_terms import find_odds_contamination
from .provenance_builder import build_provenance, build_prediction_row
from ..kalshi_client import KalshiClient
from ...config import Config
from ...utils.llm_client import LLMClient
from ...utils.logger import get_logger
from ...storage.brief_store import write_brief
from ...storage.db import get_connection, insert_provenance, insert_prediction

logger = get_logger('mirofish.services.pipeline')


@dataclass
class BuildResult:
    status: str  # 'frozen' | 'rejected'
    reject_reason: Optional[str] = None
    brief_hash: Optional[str] = None
    brief_text: Optional[str] = None
    word_count: Optional[int] = None
    freeze_ts: Optional[str] = None
    prediction_row: Any = None
    provenance: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'status': self.status,
            'reject_reason': self.reject_reason,
            'brief_hash': self.brief_hash,
            'word_count': self.word_count,
            'freeze_ts': self.freeze_ts,
            'prediction_row': self.prediction_row.to_dict() if self.prediction_row else None,
        }


class BriefPipeline:
    def __init__(
        self, *, kalshi=None, search_client=None, fetch_article=None,
        decomposer_llm=None, triage_llm=None, assembler_llm=None, scrubber_llm=None,
        briefs_dir=None, db_path=None, count_per_query=5, dedup_threshold=0.8,
        min_words=1500, max_words=3000, max_queries=18,
    ):
        self.kalshi = kalshi or KalshiClient()
        self.search_client = search_client or BraveSearchClient()
        self.fetch_article = fetch_article or default_fetch_article
        self.decomposer_llm = decomposer_llm or LLMClient()
        self.triage_llm = triage_llm or LLMClient()
        self.assembler_llm = assembler_llm or _default_assembler_llm()
        self.scrubber_llm = scrubber_llm or _default_scrubber_llm()
        self.briefs_dir = briefs_dir or Config.BRIEFS_V2_DIR
        self.db_path = db_path or Config.SHOALS_DB_PATH
        self.count_per_query = count_per_query
        self.dedup_threshold = dedup_threshold
        self.min_words = min_words
        self.max_words = max_words
        self.max_queries = max_queries

    def _reject(self, reason: str, freeze_ts: str) -> BuildResult:
        logger.warning(f"brief build rejected: {reason}")
        return BuildResult(status='rejected', reject_reason=reason, freeze_ts=freeze_ts)

    def build(self, market: Dict[str, Any], *, now: str, freshness: Optional[str] = None) -> BuildResult:
        warn_if_same_model()
        qp = intake.from_kalshi_market(market)

        decomposition = decompose(qp, self.decomposer_llm)
        # Round-robin across categories, then cap total queries — keeps breadth
        # (each category represented) while bounding retrieval work/latency.
        cats = list(decomposition.queries_by_category.values())
        queries = []
        i = 0
        while any(i < len(c) for c in cats):
            for c in cats:
                if i < len(c):
                    queries.append(c[i])
            i += 1
        queries = queries[:self.max_queries]
        if not queries:
            return self._reject('empty_query_set', now)

        docs, discarded = retrieve(
            queries, now, now, search_client=self.search_client,
            fetch_article=self.fetch_article, count_per_query=self.count_per_query,
            freshness=freshness,
        )
        triage_result = triage(docs, self.triage_llm, dedup_threshold=self.dedup_threshold)
        raw_brief = assemble(qp, triage_result, now, self.assembler_llm, discarded=discarded)
        scrub_result = scrub(raw_brief, self.scrubber_llm)

        if scrub_result.degraded:
            return self._reject('scrubber_degraded', now)
        cleaned = scrub_result.cleaned_brief
        contamination = find_odds_contamination(cleaned)
        if contamination:
            logger.error(f"residual odds contamination, refusing freeze: {contamination[:3]}")
            return self._reject('residual_odds_contamination', now)
        word_count = len(cleaned.split())
        if not (self.min_words <= word_count <= self.max_words):
            return self._reject(f'word_count_out_of_range:{word_count}', now)

        # FREEZE
        brief_hash = write_brief(cleaned, self.briefs_dir)
        snapshot = snapshot_order_book(self.kalshi, qp.question_id, snapshot_ts=now)
        provenance = build_provenance(
            brief_hash, qp, now, triage_result.sources, discarded,
            triage_result.quarantined, scrub_result.removals, decomposition,
        )
        row = build_prediction_row(qp, now, brief_hash, word_count, snapshot)

        conn = get_connection(self.db_path)
        try:
            insert_provenance(conn, provenance, commit=False)
            insert_prediction(conn, row, commit=False)
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            return self._reject('question_already_built', now)
        finally:
            conn.close()

        logger.info(f"froze brief {brief_hash} for {qp.question_id} ({word_count} words)")
        return BuildResult(
            status='frozen', brief_hash=brief_hash, brief_text=cleaned,
            word_count=word_count, freeze_ts=now, prediction_row=row, provenance=provenance,
        )


def _default_assembler_llm() -> LLMClient:
    return assembler_llm()


def _default_scrubber_llm() -> LLMClient:
    return scrubber_llm()
