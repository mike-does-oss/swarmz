from app.storage.schemas import (
    SourceRecord,
    DiscardedDoc,
    ScrubberRemoval,
    BriefProvenance,
    PredictionRow,
)


def test_prediction_row_roundtrips_through_dict():
    row = PredictionRow(
        question_id="KXPRES-24-DJT",
        question_text="Will X happen?",
        resolution_criteria="Resolves YES if ...",
        category="politics",
        freeze_ts="2026-07-04T12:00:00Z",
        brief_hash="a" * 64,
        brief_word_count=2000,
        mirofish_prob=0.61,
        baseline_prob=0.55,
        market_mid=0.58,
        agent_count=50,
    )
    restored = PredictionRow.from_dict(row.to_dict())
    assert restored == row
    assert restored.outcome is None
    assert restored.notes == ""


def test_brief_provenance_roundtrips_with_nested_records():
    prov = BriefProvenance(
        brief_hash="b" * 64,
        question_id="KXPRES-24-DJT",
        freeze_ts="2026-07-04T12:00:00Z",
        sources=[
            SourceRecord("https://ex.com/a", "Reuters", 2, "2026-07-01T00:00:00Z", "2026-07-01T01:00:00Z"),
        ],
        discarded=[DiscardedDoc("https://ex.com/late", "publication_time > freeze_ts")],
        scrubber_removals=[ScrubberRemoval("odds were 2/1", "odds string")],
        decomposer_output_json='{"KEY_ACTORS": ["x"]}',
        pipeline_versions={"decomposer": "d1", "assembler": "a1", "scrubber": "s1"},
    )
    restored = BriefProvenance.from_dict(prov.to_dict())
    assert restored == prov
    assert restored.sources[0].source_name == "Reuters"
    assert restored.discarded[0].reason == "publication_time > freeze_ts"
