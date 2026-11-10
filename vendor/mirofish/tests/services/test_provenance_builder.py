from dataclasses import dataclass

from app.services.brief_v2.provenance_builder import (
    prompt_hash, build_provenance, build_prediction_row,
)
from app.services.brief_v2.decomposer import DecomposerOutput
from app.services.brief_v2.scrubber import Removal
from app.services.orderbook_snapshot import OrderBookSnapshot
from app.storage.schemas import BriefProvenance, PredictionRow


@dataclass
class QP:
    question_id: str
    question_text: str
    resolution_criteria: str
    category: str


QP1 = QP("KXPRES-24", "Will X win?", "Resolves YES if X wins.", "politics")
SOURCES = {
    "https://reuters.com/a": {"source_name": "Reuters", "source_tier": 2,
                              "publication_time": "2026-07-01T00:00:00Z", "event_time": None},
}


def test_prompt_hash_is_stable_sha256():
    h = prompt_hash("hello")
    assert h == prompt_hash("hello") and len(h) == 64


def test_build_provenance_maps_all_fields():
    prov = build_provenance(
        brief_hash="a" * 64, qp=QP1, freeze_ts="2026-07-04T12:00:00Z",
        sources_map=SOURCES,
        retriever_discarded=[{"url": "https://late.com/z", "reason": "publication_time > freeze_ts"}],
        triage_quarantined=[{"url": "https://bad.com/x", "reason": "prompt_injection", "match": "..."}],
        scrub_removals=[Removal("odds were 2/1", "odds string", "delete")],
        decomposer_output=DecomposerOutput(queries_by_category={"KEY_ACTORS": ["x"]}, dropped_odds_queries=["betting odds"]),
    )
    assert isinstance(prov, BriefProvenance)
    assert prov.brief_hash == "a" * 64 and prov.question_id == "KXPRES-24"
    assert prov.sources[0].url == "https://reuters.com/a"
    assert prov.sources[0].tier == 2 and prov.sources[0].source_name == "Reuters"
    # discarded merges retriever + triage
    reasons = {d.reason for d in prov.discarded}
    assert "publication_time > freeze_ts" in reasons and "prompt_injection" in reasons
    # scrubber removal maps to {quoted_text, rule} only (no action)
    assert prov.scrubber_removals[0].quoted_text == "odds were 2/1"
    assert prov.scrubber_removals[0].rule == "odds string"
    # pipeline versions are the three stage prompt hashes
    assert set(prov.pipeline_versions.keys()) == {"decomposer", "assembler", "scrubber"}
    assert all(len(v) == 64 for v in prov.pipeline_versions.values())
    # decomposer json captured
    assert "KEY_ACTORS" in prov.decomposer_output_json


def test_build_prediction_row_fills_market_snapshot():
    snap = OrderBookSnapshot("KXPRES-24", 0.40, 0.45, 0.425, 143.5, "2026-07-04T12:00:00Z")
    row = build_prediction_row(QP1, "2026-07-04T12:00:00Z", "a" * 64, 2000, snap, agent_count=50)
    assert isinstance(row, PredictionRow)
    assert row.question_id == "KXPRES-24" and row.category == "politics"
    assert row.brief_hash == "a" * 64 and row.brief_word_count == 2000
    assert row.market_bid == 0.40 and row.market_ask == 0.45 and row.market_mid == 0.425
    assert row.market_depth_usd == 143.5 and row.market_snapshot_ts == "2026-07-04T12:00:00Z"
    assert row.agent_count == 50
    assert row.mirofish_prob is None and row.baseline_prob is None  # filled in Plan 7
