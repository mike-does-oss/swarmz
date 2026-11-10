from app.storage.db import get_connection, insert_provenance, get_provenance
from app.storage.schemas import (
    BriefProvenance,
    SourceRecord,
    DiscardedDoc,
    ScrubberRemoval,
)


def _prov(brief_hash="b" * 64):
    return BriefProvenance(
        brief_hash=brief_hash,
        question_id="Q1",
        freeze_ts="2026-07-04T12:00:00Z",
        sources=[
            SourceRecord("https://ex.com/a", "Reuters", 2, "2026-07-01T00:00:00Z", "2026-07-01T01:00:00Z"),
            SourceRecord("https://ex.com/b", "AP", 2, None, "2026-07-02T00:00:00Z"),
        ],
        discarded=[DiscardedDoc("https://ex.com/late", "publication_time > freeze_ts")],
        scrubber_removals=[ScrubberRemoval("odds were 2/1", "odds string")],
        decomposer_output_json='{"KEY_ACTORS": ["x"]}',
        pipeline_versions={"decomposer": "d1", "assembler": "a1", "scrubber": "s1"},
    )


def test_insert_and_get_provenance_roundtrips(data_dir):
    conn = get_connection(str(data_dir / "shoals.db"))
    insert_provenance(conn, _prov())
    got = get_provenance(conn, "b" * 64)
    assert got == _prov()


def test_get_missing_provenance_returns_none(data_dir):
    conn = get_connection(str(data_dir / "shoals.db"))
    assert get_provenance(conn, "nope") is None
