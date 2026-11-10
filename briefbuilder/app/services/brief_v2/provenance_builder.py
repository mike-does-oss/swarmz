"""
Provenance & prediction-row builders (spec §5.1, §5.2).

Maps the pipeline's stage outputs onto the Plan-1 storage dataclasses. Wire
copies etc. are already collapsed upstream; here we simply flatten sources,
merge the retriever + triage discard lists, reduce scrubber removals to the
persisted {quoted_text, rule} shape, and stamp the three stage prompt hashes.
"""

import json
import hashlib
from typing import Dict, List, Any, Optional

from .decomposer import _SYSTEM as _DECOMPOSER_SYSTEM
from .assembler import _SYSTEM as _ASSEMBLER_SYSTEM
from .scrubber import _SYSTEM as _SCRUBBER_SYSTEM
from ...storage.schemas import (
    BriefProvenance, PredictionRow, SourceRecord, DiscardedDoc, ScrubberRemoval,
)


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_provenance(
    brief_hash: str,
    qp,
    freeze_ts: str,
    sources_map: Dict[str, Dict[str, Any]],
    retriever_discarded: List[Dict[str, str]],
    triage_quarantined: List[Dict[str, str]],
    scrub_removals: List[Any],
    decomposer_output,
) -> BriefProvenance:
    sources = [
        SourceRecord(
            url=url,
            source_name=meta.get('source_name') or '',
            tier=meta.get('source_tier') if meta.get('source_tier') is not None else 4,
            event_time=meta.get('event_time'),
            publication_time=meta.get('publication_time'),
        )
        for url, meta in sources_map.items()
    ]
    discarded = [
        DiscardedDoc(url=d.get('url', ''), reason=d.get('reason', ''))
        for d in (list(retriever_discarded) + list(triage_quarantined))
    ]
    removals = [ScrubberRemoval(quoted_text=r.quoted_text, rule=r.rule) for r in scrub_removals]
    return BriefProvenance(
        brief_hash=brief_hash,
        question_id=qp.question_id,
        freeze_ts=freeze_ts,
        sources=sources,
        discarded=discarded,
        scrubber_removals=removals,
        decomposer_output_json=json.dumps(decomposer_output.to_dict()),
        pipeline_versions={
            'decomposer': prompt_hash(_DECOMPOSER_SYSTEM),
            'assembler': prompt_hash(_ASSEMBLER_SYSTEM),
            'scrubber': prompt_hash(_SCRUBBER_SYSTEM),
        },
    )


def build_prediction_row(
    qp, freeze_ts: str, brief_hash: str, word_count: int, snapshot, agent_count: Optional[int] = None,
) -> PredictionRow:
    return PredictionRow(
        question_id=qp.question_id,
        question_text=qp.question_text,
        resolution_criteria=qp.resolution_criteria,
        category=qp.category,
        freeze_ts=freeze_ts,
        brief_hash=brief_hash,
        brief_word_count=word_count,
        market_bid=snapshot.bid,
        market_ask=snapshot.ask,
        market_mid=snapshot.mid,
        market_depth_usd=snapshot.depth_usd,
        market_snapshot_ts=snapshot.snapshot_ts,
        agent_count=agent_count,
    )
