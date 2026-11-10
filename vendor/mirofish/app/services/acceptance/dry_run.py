"""
Dry-run harness (spec §8): drive resolved HISTORICAL questions through the
pipeline before any live prediction. The caller injects a BriefPipeline
configured with archived-source retrieval and a past freeze_ts, plus the
run_prediction fn; this scores swarm vs baseline Brier against known outcomes.
It is the pre-registration gate — no live prediction is logged until the dry
run looks sane.

Callers MUST inject scratch-scoped collaborators (archived-source pipeline + a
non-production run_fn) — the harness has no store isolation of its own.
"""

from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any

from ...utils.logger import get_logger

logger = get_logger('mirofish.services.acceptance.dry_run')


@dataclass
class DryCase:
    question_id: str
    market: Dict[str, Any]
    freeze_ts: str
    expected_outcome: int  # 0 or 1


@dataclass
class DryResult:
    question_id: str
    status: str
    mirofish_prob: Optional[float]
    baseline_prob: Optional[float]
    expected_outcome: int
    mirofish_brier: Optional[float]
    baseline_brier: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _brier(prob: Optional[float], outcome: int) -> Optional[float]:
    if prob is None:
        return None
    return (prob - outcome) ** 2


def run_dry_case(case: DryCase, *, pipeline, run_fn) -> DryResult:
    build = pipeline.build(case.market, now=case.freeze_ts)
    if getattr(build, "status", None) != "frozen":
        return DryResult(case.question_id, "build_rejected", None, None,
                         case.expected_outcome, None, None)
    run = run_fn(case.question_id)
    mp, bp = getattr(run, "mirofish_prob", None), getattr(run, "baseline_prob", None)
    return DryResult(
        question_id=case.question_id, status=getattr(run, "status", "unknown"),
        mirofish_prob=mp, baseline_prob=bp, expected_outcome=case.expected_outcome,
        mirofish_brier=_brier(mp, case.expected_outcome),
        baseline_brier=_brier(bp, case.expected_outcome),
    )


def _mean(values: List[Optional[float]]) -> Optional[float]:
    got = [v for v in values if v is not None]
    return sum(got) / len(got) if got else None


def run_dry_suite(cases: List[DryCase], *, pipeline, run_fn) -> Dict[str, Any]:
    results = [run_dry_case(c, pipeline=pipeline, run_fn=run_fn) for c in cases]
    mirofish_scored = [r.mirofish_brier for r in results if r.mirofish_brier is not None]
    baseline_scored = [r.baseline_brier for r in results if r.baseline_brier is not None]
    return {
        "count": len(results),
        "results": [r.to_dict() for r in results],
        "mean_mirofish_brier": _mean([r.mirofish_brier for r in results]),
        "mean_baseline_brier": _mean([r.baseline_brier for r in results]),
        "mirofish_scored_count": len(mirofish_scored),
        "baseline_scored_count": len(baseline_scored),
        "abstained_count": sum(1 for r in results if r.mirofish_brier is None),
    }
