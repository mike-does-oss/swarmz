"""Swarm aggregation in log-odds space (Component 4 + 6)."""

from __future__ import annotations

import math
from statistics import median, pstdev

METHODS = ("equal", "weighted", "calibrated", "native")
# swarm arms only: "prior" is the panel before the discussion, "public" is what agents last posted
SWARM_METHODS = ("prior", "public")


def clip(p: float, lo: float = 0.01, hi: float = 0.99) -> float:
    return min(hi, max(lo, float(p)))


def logit(p: float) -> float:
    p = clip(p, 1e-6, 1 - 1e-6)
    return math.log(p / (1 - p))


def logistic(x: float) -> float:
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def equal(probs: list[float]) -> float:
    return logistic(sum(logit(p) for p in probs) / len(probs))


def weighted(probs: list[float], weights: list[float], a: float = 1.0) -> float:
    tot = sum(weights)
    if tot <= 0:
        return logistic(a * sum(logit(p) for p in probs) / len(probs))
    return logistic(a * sum(w * logit(p) for p, w in zip(probs, weights)) / tot)


def track_record_weights(persona_ids: list[str], records: dict, min_n: int, k: float) -> list[float]:
    """exp(-k * mean rel Brier) for personas with >= min_n scored forecasts; the rest get
    the median eligible weight (1.0 if nobody is eligible yet)."""
    raw = {}
    for pid in persona_ids:
        r = records.get(pid)
        if r is not None and r.n >= min_n:
            raw[pid] = math.exp(-k * r.mean_rel_brier)
    fill = median(raw.values()) if raw else 1.0
    return [raw.get(pid, fill) for pid in persona_ids]


def apply_platt(p: float, params: dict | None) -> float:
    if not params:
        return p
    return logistic(params["a"] * logit(p) + params["b"])


def spread(probs: list[float], priors: list[float] | None = None) -> dict:
    """How far apart a panel is. Agreement is not accuracy, but a panel that all says 0.51 is
    telling you something different from one split between 0.2 and 0.8 with the same mean.
    ``priors`` are the same agents before they talked: sd falling far below prior_sd means the
    discussion pulled them together."""
    out = {"n": len(probs), "sd": round(pstdev(probs), 4) if probs else None,
           "low": min(probs) if probs else None, "high": max(probs) if probs else None,
           "yes_side": sum(p > 0.5 for p in probs), "no_side": sum(p < 0.5 for p in probs)}
    if priors:
        out["prior_sd"] = round(pstdev(priors), 4)
    return out


def extremized(probs: list[float], a: float) -> float:
    """Mean log-odds multiplied by ``a``. a > 1 pushes the panel's answer away from 50%."""
    return logistic(a * sum(logit(p) for p in probs) / len(probs))
