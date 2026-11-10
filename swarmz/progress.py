"""A way to watch a run while it happens.

The runner and the panel call emit() at each milestone. Nothing listens unless someone set a sink
(the cloud runner does, to show the run on the site). A sink that fails is ignored: watching a run
must never be able to break it.
"""

from __future__ import annotations

from typing import Callable

_sink: Callable[[str, dict], None] | None = None


def set_sink(fn: Callable[[str, dict], None] | None) -> None:
    global _sink
    _sink = fn


def emit(kind: str, **data) -> None:
    if _sink is None:
        return
    try:
        _sink(kind, data)
    except Exception:
        pass
