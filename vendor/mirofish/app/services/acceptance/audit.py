"""
Deterministic acceptance checks (spec §6) over stored artifacts.

Each check is a pure function returning a Finding; the runner (run_audit.py)
loads the provenance/row/brief and applies them. No pipeline is re-run.
"""

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

from ..brief_v2.odds_terms import find_odds_contamination
from ..brief_v2.retriever import parse_iso
from ...storage.brief_store import read_brief, BriefIntegrityError
from ...utils.logger import get_logger

logger = get_logger('mirofish.services.acceptance.audit')


@dataclass
class Finding:
    check: str
    ok: bool
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def check_freeze_integrity(provenance) -> Finding:
    freeze = parse_iso(provenance.freeze_ts)
    violations = []
    for s in provenance.sources:
        pub = parse_iso(s.publication_time)
        if freeze is not None and pub is not None and pub > freeze:
            violations.append(s.url)
    ok = not violations
    detail = "no source postdates freeze" if ok else f"postdate freeze: {', '.join(violations)}"
    return Finding("freeze_integrity", ok, detail)


def check_word_count(row, lo: int = 1500, hi: int = 3000) -> Finding:
    wc = row.brief_word_count
    ok = wc is not None and lo <= wc <= hi
    return Finding("word_count", ok, f"{wc} words (want {lo}-{hi})")


def check_snapshot_timing(row, max_seconds: int = 60) -> Finding:
    freeze = parse_iso(row.freeze_ts)
    snap = parse_iso(row.market_snapshot_ts)
    if freeze is None or snap is None:
        return Finding("snapshot_timing", False, "missing snapshot or freeze timestamp")
    delta = abs((snap - freeze).total_seconds())
    ok = delta <= max_seconds
    return Finding("snapshot_timing", ok, f"snapshot {delta:.0f}s from freeze (max {max_seconds}s)")


def check_hash_reproducible(brief_hash: str, briefs_dir: str) -> Finding:
    try:
        read_brief(brief_hash, briefs_dir)
        return Finding("hash_reproducible", True, "brief bytes re-hash to stored hash")
    except (BriefIntegrityError, FileNotFoundError) as e:
        return Finding("hash_reproducible", False, str(e))


def check_regex_odds(brief_text: str) -> Finding:
    hits = find_odds_contamination(brief_text or "")
    ok = not hits
    return Finding("regex_odds", ok, "no odds strings" if ok else f"odds strings: {hits[:5]}")
