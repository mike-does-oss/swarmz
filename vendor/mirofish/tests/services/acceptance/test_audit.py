from app.services.acceptance.audit import (
    Finding, check_freeze_integrity, check_word_count, check_snapshot_timing,
    check_hash_reproducible, check_regex_odds,
)
from app.storage.schemas import BriefProvenance, SourceRecord, PredictionRow
from app.storage.brief_store import write_brief


FREEZE = "2026-07-04T12:00:00Z"


def _prov(pub_times):
    return BriefProvenance(
        brief_hash="a" * 64, question_id="Q1", freeze_ts=FREEZE,
        sources=[SourceRecord(f"https://s{i}", "S", 2, None, pt) for i, pt in enumerate(pub_times)],
    )


def _row(**kw):
    base = dict(question_id="Q1", question_text="Q?", resolution_criteria="R", category="politics",
                freeze_ts=FREEZE, brief_hash="a" * 64, brief_word_count=2000,
                market_snapshot_ts=FREEZE)
    base.update(kw)
    return PredictionRow(**base)


def test_freeze_integrity_ok_and_violation():
    assert check_freeze_integrity(_prov(["2026-07-01T00:00:00Z", "2026-07-04T00:00:00Z"])).ok
    bad = check_freeze_integrity(_prov(["2026-07-05T00:00:00Z"]))  # after freeze
    assert bad.ok is False and "https://s0" in bad.detail


def test_word_count():
    assert check_word_count(_row(brief_word_count=2000)).ok
    assert check_word_count(_row(brief_word_count=1200)).ok is False
    assert check_word_count(_row(brief_word_count=4000)).ok is False


def test_snapshot_timing():
    assert check_snapshot_timing(_row(market_snapshot_ts=FREEZE)).ok
    assert check_snapshot_timing(_row(market_snapshot_ts="2026-07-04T12:00:30Z")).ok       # 30s
    assert check_snapshot_timing(_row(market_snapshot_ts="2026-07-04T12:05:00Z")).ok is False  # 5m
    assert check_snapshot_timing(_row(market_snapshot_ts=None)).ok is False


def test_hash_reproducible(tmp_path):
    briefs = str(tmp_path / "briefs")
    h = write_brief("frozen body", briefs)
    assert check_hash_reproducible(h, briefs).ok
    assert check_hash_reproducible("0" * 64, briefs).ok is False  # missing file


def test_regex_odds():
    assert check_regex_odds("Reuters reported the vote passed. Polls show 52%.").ok
    assert check_regex_odds("The betting odds were 2/1.").ok is False
