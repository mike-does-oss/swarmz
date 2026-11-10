"""Question tags: Kalshi auto-tagging, manual tags, per-tag reports, and the no-leak guardrail."""

from pathlib import Path

import pytest

from swarmz import tags
from swarmz.registry import store
from swarmz.scoring.brier import score_question
from swarmz.scoring.report import arm_by_tag, persona_by_tag, tag_counts
from tests.conftest import FakeLLM, add_q

ROOT = Path(__file__).resolve().parents[1]


class FakeKalshi:
    def __init__(self):
        self.data = {
            "/markets/KXCPI-26OCT-T0.2": {"market": {"event_ticker": "KXCPI-26OCT", "strike_type": "greater",
                                                     "close_time": "2026-11-10T13:29:00Z"}},
            "/events/KXCPI-26OCT": {"event": {"series_ticker": "KXCPI", "category": "Economics"}},
            "/series/KXCPI": {"series": {"category": "Economics", "tags": ["Inflation"], "title": "CPI",
                                         "frequency": "monthly"}},
        }

    def get(self, url, timeout=None):
        path = url.split("/trade-api/v2", 1)[-1]
        body = self.data[path]

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return body
        return R()


def test_buckets():
    assert tags.horizon_bucket("2026-11-05T00:00:00Z", "2026-11-10T13:29:00Z") == "4-7d"
    assert tags.horizon_bucket("2026-11-05", "2027-03-01") == "60d+"
    assert tags.market_bucket(0.05) == "0-10c" and tags.market_bucket(55) == "30-70c" and tags.market_bucket(0.95) == "90-100c"
    assert tags.market_bucket(None) is None


def test_kalshi_auto_tag_sets_category_and_tags(con):
    add_q(con, "KXCPI-26OCT-T0.2", "2026-11-05T00:00:00", category="other", source="kalshi")
    got = tags.tag_from_kalshi(con, "KXCPI-26OCT-T0.2", session=FakeKalshi())
    assert got == {"category": ["Economics"], "frequency": ["monthly"], "horizon": ["4-7d"],
                   "series": ["CPI"], "strike_type": ["greater"], "topic": ["Inflation"]}
    assert store.get_question(con, "KXCPI-26OCT-T0.2")["category"] == "Economics"


def test_manual_tags_add_replace_remove(con):
    add_q(con, "q1", "2026-11-01")
    tags.set_tags(con, "q1", tags.parse_pairs(["topic=Fed", "topic=Rates", "difficulty=hard"]), "manual")
    assert tags.tags_for(con, "q1") == {"difficulty": ["hard"], "topic": ["Fed", "Rates"]}
    tags.remove_tag(con, "q1", "topic", "Fed")
    tags.set_tags(con, "q1", [("difficulty", "easy")], "manual", replace_keys=True)
    assert tags.tags_for(con, "q1") == {"difficulty": ["easy"], "topic": ["Rates"]}
    with pytest.raises(ValueError):
        tags.parse_pairs(["no-equals-sign"])


def test_reports_by_tag(con, cfg):
    from swarmz.arms.runner import run_arm

    store.seed_population(con, 6, "m", seed=1)
    for i, (topic, outcome) in enumerate([("Fed", 1), ("Fed", 0), ("AI", 1)]):
        qid = f"q{i}"
        add_q(con, qid, "2026-11-01", split="train")
        tags.set_tags(con, qid, [("topic", topic)], "manual")
        run_arm(con, qid, "A3", llm=FakeLLM())
        store.set_outcome(con, qid, outcome, "2026-12-01")
        score_question(con, qid)
    counts = {(r["key"], r["value"]): r["questions"] for r in tag_counts(con)}
    assert counts == {("topic", "Fed"): 2, ("topic", "AI"): 1}
    by_arm = {(r["tag_value"], r["method"]): r["n"] for r in arm_by_tag(con, "topic")}
    assert by_arm[("Fed", "equal")] == 2 and by_arm[("AI", "equal")] == 1
    personas = persona_by_tag(con, "topic", population="A3")
    assert {r["tag_value"] for r in personas} == {"Fed", "AI"}
    assert all(r["n"] in (1, 2) for r in personas)
    # shrinkage: |shrunk| <= |mean|
    assert all(abs(r["shrunk_rel_brier"]) <= abs(r["mean_rel_brier"]) + 1e-9 for r in personas)


def test_tags_never_reach_prompts_or_learning():
    """Tags (including the market-price bucket) are reporting metadata only."""
    for d in ["swarmz/capture", "swarmz/learning", "swarmz/arms"]:
        for f in (ROOT / d).rglob("*.py"):
            src = f.read_text()
            assert "question_tags" not in src and "swarmz.tags" not in src and "from .. import tags" not in src, f
    asof_src = (ROOT / "swarmz/registry/asof.py").read_text()
    assert "question_tags" not in asof_src and "market_bucket" not in asof_src
