"""Post-hoc Platt calibration of the swarm aggregate (Component 6).

Fit: outcome ~ logistic(a * logit(p) + b) on train questions resolved before
the current time, for one arm. Refit every ``refit_every`` newly resolved
questions; skip until ``min_fit_n`` exist.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

import numpy as np

from ..capture.aggregate import logit
from ..config import settings
from ..registry import asof
from ..timeutil import now, utc


def fit_platt(probs: list[float], outcomes: list[int], l2: float = 1e-3, iters: int = 100) -> dict:
    x = np.array([logit(p) for p in probs])
    y = np.array(outcomes, dtype=float)
    X = np.column_stack([x, np.ones_like(x)])
    w = np.array([1.0, 0.0])          # start at identity
    prior = np.array([1.0, 0.0])      # shrink toward identity, not toward zero
    for _ in range(iters):
        z = np.clip(X @ w, -30, 30)
        p = 1 / (1 + np.exp(-z))
        g = X.T @ (p - y) + l2 * (w - prior) * len(y)
        H = (X.T * (p * (1 - p))) @ X + l2 * len(y) * np.eye(2)
        step = np.linalg.solve(H, g)
        w -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return {"a": float(w[0]), "b": float(w[1])}


def maybe_refit(con, arm: str, as_of: datetime, force: bool = False) -> dict | None:
    cfg = settings().learning["calibration"]
    method = cfg["source_method"]
    data = asof.calibration_data(con, arm, method, as_of)
    n = len(data)
    if n < cfg["min_fit_n"]:
        return None
    last = asof.calibration_fit(con, arm, method, as_of)
    if last and not force and n - last["n_questions"] < cfg["refit_every"]:
        return None
    params = fit_platt([d["prob"] for d in data], [d["outcome"] for d in data])
    fit_until = max(utc(d["resolve_ts"]) for d in data)
    fit_id = "fit-" + hashlib.sha256(f"{arm}|{method}|{fit_until.isoformat()}|{n}".encode()).hexdigest()[:10]
    con.execute("INSERT OR REPLACE INTO calibration_fits VALUES (?, ?, ?, ?, ?, ?, ?)",
                [fit_id, arm, method, json.dumps({**params, "kind": "platt"}), fit_until, n, now()])
    return {"fit_id": fit_id, "params": params, "n_questions": n, "fit_until_ts": fit_until}
