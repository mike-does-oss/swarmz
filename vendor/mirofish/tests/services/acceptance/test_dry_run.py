from app.services.acceptance.dry_run import DryCase, run_dry_case, run_dry_suite


MARKET = {"ticker": "KX-HIST-1", "title": "Did X happen?", "rules_primary": "R", "category": "Politics"}


class FakeBuildResult:
    def __init__(self, status="frozen"):
        self.status = status


class FakePipeline:
    def __init__(self, status="frozen"):
        self._status = status
        self.freeze_ts = None

    def build(self, market, *, now, freshness=None):
        self.freeze_ts = now
        return FakeBuildResult(self._status)


class FakeRunResult:
    def __init__(self, mirofish, baseline):
        self.status = "complete"
        self.mirofish_prob = mirofish
        self.baseline_prob = baseline


def _run_fn(mirofish, baseline):
    def _fn(question_id, **kw):
        return FakeRunResult(mirofish, baseline)
    return _fn


def test_run_dry_case_scores_brier():
    case = DryCase("KX-HIST-1", MARKET, "2026-01-01T00:00:00Z", expected_outcome=1)
    pipeline = FakePipeline()
    result = run_dry_case(case, pipeline=pipeline, run_fn=_run_fn(0.8, 0.6))
    assert pipeline.freeze_ts == "2026-01-01T00:00:00Z"   # built at the historical freeze
    assert result.expected_outcome == 1
    # Brier = (prob - outcome)^2
    assert round(result.mirofish_brier, 4) == round((0.8 - 1) ** 2, 4)
    assert round(result.baseline_brier, 4) == round((0.6 - 1) ** 2, 4)


def test_run_dry_case_build_rejected():
    case = DryCase("KX-HIST-1", MARKET, "2026-01-01T00:00:00Z", expected_outcome=0)
    result = run_dry_case(case, pipeline=FakePipeline(status="rejected"), run_fn=_run_fn(0.5, 0.5))
    assert result.status == "build_rejected"
    assert result.mirofish_brier is None


def test_run_dry_suite_aggregates_mean_brier():
    cases = [
        DryCase("A", MARKET, "2026-01-01T00:00:00Z", 1),
        DryCase("B", MARKET, "2026-01-01T00:00:00Z", 0),
    ]
    report = run_dry_suite(cases, pipeline=FakePipeline(), run_fn=_run_fn(0.9, 0.5))
    assert report["count"] == 2
    # mirofish brier: (0.9-1)^2=0.01 and (0.9-0)^2=0.81 -> mean 0.41
    assert round(report["mean_mirofish_brier"], 4) == 0.41
    assert round(report["mean_baseline_brier"], 4) == 0.25   # (0.5-1)^2=.25, (0.5-0)^2=.25
    assert report["mirofish_scored_count"] == 2
    assert report["abstained_count"] == 0
