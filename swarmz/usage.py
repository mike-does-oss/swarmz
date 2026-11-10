"""Per-run token usage and cost.

    with usage.track() as u:
        ... any Anthropic / OpenAI SDK calls, in any thread ...
    usage.save(con, u, context="run", run_id=..., question_id=..., arm=...)

The brief builder runs in a separate Python (swarmz.picker); ``subprocess_env`` puts a
``sitecustomize`` hook on its PYTHONPATH that appends calls to a JSONL file,
and ``merge_file`` adds that file to the active tracker afterwards.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from .timeutil import now
from .usage_hook import _usage_core

HOOK_DIR = Path(__file__).resolve().parent / "usage_hook"
PRICING_PATH = Path(__file__).resolve().parent.parent / "config" / "pricing.yaml"
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
KEY_FIELDS = ("stage", "provider", "host", "model")


@dataclass
class Tracker:
    totals: dict = field(default_factory=dict)   # (stage, provider, host, model) -> counts
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, rec: dict) -> None:
        key = tuple(str(rec.get(k) or "") for k in KEY_FIELDS)
        with self.lock:
            t = self.totals.setdefault(key, {"calls": 0, **{f: 0 for f in TOKEN_FIELDS}})
            t["calls"] += int(rec.get("calls", 1))
            for f in TOKEN_FIELDS:
                t[f] += int(rec.get(f, 0) or 0)

    def rows(self) -> list[dict]:
        with self.lock:
            out = [dict(zip(KEY_FIELDS, k), **v) for k, v in sorted(self.totals.items())]
        for r in out:
            r["cost_usd"] = cost(r["model"], r)
        return out

    @property
    def cost_usd(self) -> float:
        return sum(r["cost_usd"] or 0.0 for r in self.rows())

    @property
    def unpriced(self) -> list[str]:
        return sorted({r["model"] for r in self.rows() if r["cost_usd"] is None})


_stack: list[Tracker] = []
_stack_lock = threading.Lock()


def _sink(rec: dict) -> None:
    with _stack_lock:
        top = _stack[-1] if _stack else None
    if top is not None:
        top.add(rec)


def install() -> None:
    _usage_core.install(_sink)


@contextmanager
def track():
    """Collect usage from every SDK call made until the block exits (all threads)."""
    install()
    t = Tracker()
    with _stack_lock:
        _stack.append(t)
    try:
        yield t
    finally:
        with _stack_lock:
            _stack.remove(t)


# ------------------------------------------------------------------ pricing

@lru_cache
def prices() -> dict:
    if not PRICING_PATH.exists():
        return {}
    return (yaml.safe_load(PRICING_PATH.read_text()) or {}).get("models") or {}


def _price_for(model: str) -> dict | None:
    table = prices()
    if model in table:
        return table[model]
    # dated or prefixed ids, e.g. "anthropic/claude-haiku-5-5" or "claude-haiku-5-5-2026..."
    for name in sorted(table, key=len, reverse=True):
        if name in model:
            return table[name]
    return None


def cost(model: str, counts: dict) -> float | None:
    p = _price_for(model)
    if p is None:
        return None
    return sum(counts.get(f, 0) * float(p.get(f.replace("_tokens", ""), 0.0)) for f in TOKEN_FIELDS) / 1e6


# --------------------------------------------------------------- subprocess

def subprocess_env(usage_file: Path, stage: str = "brief") -> dict:
    """Env vars that make a child Python log its SDK calls to ``usage_file`` under ``stage``."""
    pp = os.environ.get("PYTHONPATH", "")
    return {"SWARMZ_USAGE_FILE": str(usage_file), "SWARMZ_USAGE_STAGE": stage,
            "PYTHONPATH": str(HOOK_DIR) + (os.pathsep + pp if pp else "")}


def merge_file(path: Path) -> int:
    """Add a subprocess JSONL usage file to the active tracker. Returns calls added."""
    path = Path(path)
    if not path.exists():
        return 0
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        _sink(rec)
        n += int(rec.get("calls", 1))
    return n


# ------------------------------------------------------------------ storage

def save(con, tracker: Tracker, *, context: str, run_id: str | None = None,
         question_id: str | None = None, arm: str | None = None) -> float:
    """Write the tracker's totals to the ``usage`` table. Returns total cost (priced models only)."""
    at = now()
    total = 0.0
    for r in tracker.rows():
        key = "|".join([context, run_id or "", question_id or "", arm or "", at.isoformat(),
                        *(r[k] for k in KEY_FIELDS)])
        con.execute("INSERT INTO usage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ["u-" + hashlib.sha256(key.encode()).hexdigest()[:16], context, run_id, question_id, arm,
                     r["stage"], r["provider"], r["host"], r["model"], r["calls"], r["input_tokens"],
                     r["output_tokens"], r["cache_read_tokens"], r["cache_write_tokens"], r["cost_usd"], at])
        total += r["cost_usd"] or 0.0
    return total


def reprice(con, only_missing: bool = True) -> int:
    """Recompute cost_usd from stored tokens (e.g. after adding a model to config/pricing.yaml)."""
    prices.cache_clear()
    where = "WHERE cost_usd IS NULL" if only_missing else ""
    n = 0
    for r in con.execute(f"SELECT usage_id, model, input_tokens, output_tokens, cache_read_tokens, "
                         f"cache_write_tokens FROM usage {where}").fetchall():
        c = cost(r[1], dict(zip(TOKEN_FIELDS, r[2:])))
        if c is not None:
            con.execute("UPDATE usage SET cost_usd = ? WHERE usage_id = ?", [c, r[0]])
            n += 1
    return n

