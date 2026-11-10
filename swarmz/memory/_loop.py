"""One background asyncio loop shared by every sync memory facade in the process.

Graphiti and the async FalkorDB client are asyncio-native; MiroFish is sync and
threaded. Async clients are bound to the loop they were created on, so all
graph work runs on this single daemon loop and sync callers block on futures.
``run`` is thread-safe and may be called from any thread except the loop
thread itself.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None


def get_loop() -> asyncio.AbstractEventLoop:
    global _loop, _thread
    with _lock:
        if _loop is None or _loop.is_closed() or _thread is None or not _thread.is_alive():
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _runner() -> None:
                asyncio.set_event_loop(loop)
                loop.call_soon(ready.set)
                loop.run_forever()

            t = threading.Thread(target=_runner, name="swarmz-memory-loop", daemon=True)
            t.start()
            ready.wait()
            _loop, _thread = loop, t
        return _loop


def in_loop_thread() -> bool:
    return _thread is not None and threading.current_thread() is _thread


def run(coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
    """Run ``coro`` on the shared loop and block until it finishes."""
    if in_loop_thread():
        coro.close()
        raise RuntimeError("swarmz.memory sync facade called from inside the memory loop")
    fut = asyncio.run_coroutine_threadsafe(coro, get_loop())
    try:
        return fut.result(timeout=timeout)
    except TimeoutError:
        fut.cancel()
        raise
