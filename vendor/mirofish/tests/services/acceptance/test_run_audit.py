from app.services.acceptance.run_audit import audit_all
from app.storage.db import get_connection, insert_prediction, insert_provenance
from app.storage.schemas import PredictionRow, BriefProvenance, SourceRecord, ScrubberRemoval
from app.storage.brief_store import write_brief


FREEZE = "2026-07-04T12:00:00Z"


def _seed(tmp_path, *, word_count, brief_body, pub):
    briefs = str(tmp_path / "briefs")
    h = write_brief(brief_body, briefs)
    conn = get_connection(str(tmp_path / "shoals.db"))
    insert_prediction(conn, PredictionRow(
        question_id="Q1", question_text="Q?", resolution_criteria="R", category="politics",
        freeze_ts=FREEZE, brief_hash=h, brief_word_count=word_count, market_snapshot_ts=FREEZE))
    insert_provenance(conn, BriefProvenance(
        brief_hash=h, question_id="Q1", freeze_ts=FREEZE,
        sources=[SourceRecord("https://s0", "Reuters", 2, None, pub)],
        scrubber_removals=[ScrubberRemoval("odds were 2/1", "odds string")]))
    conn.close()
    return briefs, str(tmp_path / "shoals.db"), h


def test_audit_all_passes_clean_brief(tmp_path):
    briefs, db, h = _seed(tmp_path, word_count=2000, brief_body="Reuters reports the vote passed.", pub="2026-07-01T00:00:00Z")
    conn = get_connection(db)
    report = audit_all(conn, briefs)
    conn.close()
    assert report["summary"]["total"] == 1
    assert report["summary"]["passed"] == 1
    b = report["briefs"][0]
    assert b["passed"] is True and b["brief_hash"] == h
    assert b["removals"][0]["quoted_text"] == "odds were 2/1"   # surfaced for human review
    assert b["llm_audits_run"] is False
    assert report["summary"]["llm_audits_run"] is False


def test_audit_all_flags_bad_brief(tmp_path):
    # too short + a post-freeze source + odds text -> multiple failures
    briefs, db, h = _seed(tmp_path, word_count=100, brief_body="The betting odds were 2/1.", pub="2026-07-09T00:00:00Z")
    conn = get_connection(db)
    report = audit_all(conn, briefs)
    conn.close()
    assert report["summary"]["passed"] == 0
    failed = {c["check"] for c in report["briefs"][0]["checks"] if not c["ok"]}
    assert {"word_count", "freeze_integrity", "regex_odds"}.issubset(failed)


def test_audit_all_fails_missing_provenance(tmp_path):
    from app.storage.brief_store import write_brief
    from app.storage.db import get_connection, insert_prediction
    from app.storage.schemas import PredictionRow
    briefs = str(tmp_path / "briefs")
    h = write_brief("Reuters reports the vote passed.", briefs)
    conn = get_connection(str(tmp_path / "shoals.db"))
    insert_prediction(conn, PredictionRow(
        question_id="Qnp", question_text="Q?", resolution_criteria="R", category="politics",
        freeze_ts="2026-07-04T12:00:00Z", brief_hash=h, brief_word_count=2000,
        market_snapshot_ts="2026-07-04T12:00:00Z"))
    # NOTE: no insert_provenance
    report = audit_all(conn, briefs)
    conn.close()
    assert report["summary"]["passed"] == 0
    prov_check = [c for c in report["briefs"][0]["checks"] if c["check"] == "provenance_present"][0]
    assert prov_check["ok"] is False


def test_audit_all_runs_llm_audits_when_llm_given(tmp_path):
    from app.storage.brief_store import write_brief
    from app.storage.db import get_connection, insert_prediction, insert_provenance
    from app.storage.schemas import PredictionRow, BriefProvenance, SourceRecord
    briefs = str(tmp_path / "briefs")
    h = write_brief("Reuters reports the vote passed.", briefs)
    conn = get_connection(str(tmp_path / "shoals.db"))
    insert_prediction(conn, PredictionRow(
        question_id="Ql", question_text="Q?", resolution_criteria="Will X win?", category="politics",
        freeze_ts="2026-07-04T12:00:00Z", brief_hash=h, brief_word_count=2000,
        market_snapshot_ts="2026-07-04T12:00:00Z"))
    insert_provenance(conn, BriefProvenance(brief_hash=h, question_id="Ql", freeze_ts="2026-07-04T12:00:00Z",
        sources=[SourceRecord("https://s0", "Reuters", 2, None, "2026-07-01T00:00:00Z")]))

    class FakeLLM:
        def chat_json(self, messages):
            # neutrality: no leak; odds: clean
            return {"implies_outcome": False, "note": "", "found": []}
    report = audit_all(conn, briefs, llm=FakeLLM())
    conn.close()
    checks = {c["check"] for c in report["briefs"][0]["checks"]}
    assert "neutrality_llm" in checks and "odds_llm" in checks
    assert report["briefs"][0]["llm_audits_run"] is True
