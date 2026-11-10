"""
Triage orchestration (spec §4.4).

Quarantine injection docs -> collapse wire copies -> extract atomic claims (LLM)
-> enforce the two-source rule in code. The code guardrail may only downgrade
the model's status, so CONFIRMED always means >=2 independent (non-wire-copy)
sources.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any

from .injection_filter import quarantine
from .dedup import group_near_duplicates, count_independent
from .claim_extractor import extract, Claim, CONFIRMED, REPORTED_UNCONFIRMED
from ...utils.logger import get_logger

logger = get_logger('briefbuilder.services.triage')


@dataclass
class TriageResult:
    claims: List[Claim] = field(default_factory=list)
    wire_groups: Dict[str, int] = field(default_factory=dict)
    quarantined: List[Dict[str, str]] = field(default_factory=list)
    sources: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # the latest value of what the question resolves on (see claim_extractor.Extraction)
    latest_reading: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'claims': [c.to_dict() for c in self.claims],
            'wire_groups': self.wire_groups,
            'quarantined': self.quarantined,
            'sources': self.sources,
            'latest_reading': self.latest_reading,
        }


def _source_map(docs: List[Any]) -> Dict[str, Dict[str, Any]]:
    """URL -> structured source metadata (NO full_text: preserves the no-raw-text boundary)."""
    return {
        d.url: {
            'source_name': getattr(d, 'source_name', None),
            'source_tier': getattr(d, 'source_tier', None),
            'publication_time': getattr(d, 'publication_time', None),
            'event_time': getattr(d, 'event_time', None),
        }
        for d in docs
    }


def _derive_timestamps(claims: List[Claim], sources: Dict[str, Dict[str, Any]]) -> List[Claim]:
    """Set each claim's timestamps from its cited sources (earliest), not from the LLM."""
    for claim in claims:
        pubs = [sources[u]['publication_time'] for u in claim.source_urls
                if u in sources and sources[u].get('publication_time')]
        events = [sources[u]['event_time'] for u in claim.source_urls
                  if u in sources and sources[u].get('event_time')]
        claim.publication_time = min(pubs) if pubs else None
        claim.event_time = min(events) if events else None
    return claims


def validate_two_source(claims: List[Claim], url_to_group: Dict[str, int]) -> List[Claim]:
    for claim in claims:
        if claim.status == CONFIRMED and count_independent(claim.source_urls, url_to_group) < 2:
            claim.status = REPORTED_UNCONFIRMED
    return claims


def triage(docs: List[Any], llm, *, dedup_threshold: float = 0.8, question: str = "") -> TriageResult:
    clean, quarantined = quarantine(docs)
    sources = _source_map(clean)
    if not clean:
        logger.info(f"triage: 0 clean docs, {len(quarantined)} quarantined")
        return TriageResult(quarantined=quarantined, sources=sources)

    wire_groups = group_near_duplicates(clean, threshold=dedup_threshold)
    extraction = extract(clean, llm, question)
    claims = validate_two_source(extraction.claims, wire_groups)
    claims = _derive_timestamps(claims, sources)
    logger.info(
        f"triage: {len(clean)} clean docs, {len(quarantined)} quarantined, "
        f"{len(set(wire_groups.values()))} source groups, {len(claims)} claims"
    )
    return TriageResult(claims=claims, wire_groups=wire_groups, quarantined=quarantined, sources=sources,
                        latest_reading=extraction.latest_reading)
