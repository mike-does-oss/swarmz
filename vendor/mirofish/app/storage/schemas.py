"""
Storage schemas for BriefBuilder v2.

Dataclasses mirroring the prediction-log row (spec §5.1, plus `agent_count`)
and the per-brief provenance record (spec §5.2).
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any


@dataclass
class SourceRecord:
    url: str
    source_name: str
    tier: int
    event_time: Optional[str] = None
    publication_time: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DiscardedDoc:
    url: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScrubberRemoval:
    quoted_text: str
    rule: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BriefProvenance:
    brief_hash: str
    question_id: str
    freeze_ts: str
    sources: List[SourceRecord] = field(default_factory=list)
    discarded: List[DiscardedDoc] = field(default_factory=list)
    scrubber_removals: List[ScrubberRemoval] = field(default_factory=list)
    decomposer_output_json: str = ""
    pipeline_versions: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "brief_hash": self.brief_hash,
            "question_id": self.question_id,
            "freeze_ts": self.freeze_ts,
            "sources": [s.to_dict() for s in self.sources],
            "discarded": [d.to_dict() for d in self.discarded],
            "scrubber_removals": [r.to_dict() for r in self.scrubber_removals],
            "decomposer_output_json": self.decomposer_output_json,
            "pipeline_versions": self.pipeline_versions,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BriefProvenance":
        return cls(
            brief_hash=data["brief_hash"],
            question_id=data["question_id"],
            freeze_ts=data["freeze_ts"],
            sources=[SourceRecord(**s) for s in data.get("sources", [])],
            discarded=[DiscardedDoc(**d) for d in data.get("discarded", [])],
            scrubber_removals=[ScrubberRemoval(**r) for r in data.get("scrubber_removals", [])],
            decomposer_output_json=data.get("decomposer_output_json", ""),
            pipeline_versions=data.get("pipeline_versions", {}),
        )


@dataclass
class PredictionRow:
    question_id: str
    question_text: str
    resolution_criteria: str
    category: str
    freeze_ts: str
    brief_hash: str
    brief_word_count: int
    mirofish_prob: Optional[float] = None
    baseline_prob: Optional[float] = None
    market_bid: Optional[float] = None
    market_ask: Optional[float] = None
    market_mid: Optional[float] = None
    market_depth_usd: Optional[float] = None
    market_snapshot_ts: Optional[str] = None
    mirofish_cost_usd: Optional[float] = None
    mirofish_wall_clock_s: Optional[int] = None
    agent_count: Optional[int] = None
    outcome: Optional[int] = None
    resolved_at: Optional[str] = None
    notes: str = ""

    # Column order used by the predictions table (Task 3).
    COLUMNS = (
        "question_id", "question_text", "resolution_criteria", "category",
        "freeze_ts", "brief_hash", "brief_word_count", "mirofish_prob",
        "baseline_prob", "market_bid", "market_ask", "market_mid",
        "market_depth_usd", "market_snapshot_ts", "mirofish_cost_usd",
        "mirofish_wall_clock_s", "agent_count", "outcome", "resolved_at", "notes",
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PredictionRow":
        return cls(**{k: data[k] for k in cls.COLUMNS if k in data})
