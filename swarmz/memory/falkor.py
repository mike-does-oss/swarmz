"""FalkorDB connection factory: server mode (Docker Compose) or lite mode (embedded).

Environment:
  FALKORDB_MODE      'server' | 'lite'. If unset: 'server' when something accepts
                     TCP connections on FALKORDB_HOST:FALKORDB_PORT, else 'lite'.
  FALKORDB_HOST      server host (default localhost)
  FALKORDB_PORT      server port (default 6379)
  FALKORDB_USERNAME / FALKORDB_PASSWORD   optional server auth
  FALKORDB_LITE_PATH directory for the embedded database (default
                     <repo>/data/falkordb_lite; relative paths are relative to the
                     repo root). Lite mode runs an embedded
                     redis-server + FalkorDB module via falkordblite; it is only
                     reachable from this process.

One async client per configuration is created lazily on the shared memory loop
and reused by every backend (never closed per backend: in lite mode closing it
would stop the embedded server under other backends).

Lite mode, across processes: the first process to open a database starts the
embedded server; others find it through ``falkordb.rdb.settings`` and connect to
the same one. When a process exits and nothing else is connected, the server is
stopped and saves the database (see ``_stop_embedded_server``).
"""

from __future__ import annotations

import atexit
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import _loop

_REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = _REPO_ROOT
DEFAULT_LITE_PATH = _REPO_ROOT / "data" / "falkordb_lite"
LITE_DB_FILENAME = "falkordb.rdb"


@dataclass(frozen=True)
class FalkorConfig:
    mode: str                 # 'server' | 'lite'
    host: str = "localhost"
    port: int = 6379
    username: str | None = None
    password: str | None = None
    lite_path: str = str(DEFAULT_LITE_PATH)

    @property
    def key(self) -> tuple:
        if self.mode == "lite":
            return ("lite", os.path.abspath(self.lite_path))
        return ("server", self.host, self.port, self.username)

    def describe(self) -> str:
        if self.mode == "lite":
            return f"lite:{os.path.abspath(self.lite_path)}"
        return f"server:{self.host}:{self.port}"


def _port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def config_from_env() -> FalkorConfig:
    host = os.environ.get("FALKORDB_HOST", "localhost")
    port = int(os.environ.get("FALKORDB_PORT", "6379"))
    mode = (os.environ.get("FALKORDB_MODE") or "").strip().lower()
    if not mode:
        mode = "server" if _port_open(host, port) else "lite"
    if mode not in ("server", "lite"):
        raise ValueError(f"FALKORDB_MODE must be 'server' or 'lite', got {mode!r}")
    return FalkorConfig(
        mode=mode,
        host=host,
        port=port,
        username=os.environ.get("FALKORDB_USERNAME") or None,
        password=os.environ.get("FALKORDB_PASSWORD") or None,
        lite_path=_resolve_lite_path(os.environ.get("FALKORDB_LITE_PATH")),
    )


def _resolve_lite_path(raw: str | None) -> str:
    """Relative FALKORDB_LITE_PATH values are relative to the repo root, not the cwd."""
    if not raw:
        return str(DEFAULT_LITE_PATH)
    p = Path(os.path.expanduser(raw))
    return str(p if p.is_absolute() else _REPO_ROOT / p)


_clients: dict[tuple, Any] = {}


async def get_async_client(cfg: FalkorConfig):
    """Return the shared async FalkorDB client for ``cfg``. Must run on the memory loop."""
    client = _clients.get(cfg.key)
    if client is not None:
        return client
    if cfg.mode == "lite":
        from redislite import client as redislite_client
        from redislite.async_falkordb_client import AsyncFalkorDB

        # a cold start loads the whole database file (5 s at 50 MB); the default allows 10 s
        redislite_client.RedisMixin.start_timeout = max(redislite_client.RedisMixin.start_timeout, 120)
        path = Path(cfg.lite_path)
        path.mkdir(parents=True, exist_ok=True)
        client = AsyncFalkorDB(str(path / LITE_DB_FILENAME))
        # run our exit hook before redislite's own, which would otherwise disconnect first
        atexit.unregister(_shutdown)
        atexit.register(_shutdown)
    else:
        from falkordb.asyncio import FalkorDB

        client = FalkorDB(host=cfg.host, port=cfg.port, username=cfg.username,
                          password=cfg.password)
    _clients[cfg.key] = client
    return client


async def _close_all() -> None:
    for client in list(_clients.values()):
        embedded = getattr(getattr(client, "client", None), "_sync_client", None)
        try:
            if hasattr(client, "aclose"):
                await client.aclose()
            elif hasattr(client, "close"):
                await client.close()
        except Exception:
            pass
        if embedded is not None:
            _stop_embedded_server(embedded)
    _clients.clear()


def _stop_embedded_server(embedded) -> None:
    """Stop the embedded server if this process was its last client; it saves on the way out.

    redislite's async client never does this: it marks its server handle "async managed" and
    then skips the shutdown, so every process and every test session left a redis-server
    running (27 of them by 2026-11-10). Worse, once macOS clears the old server's temp files a
    new process starts a second server on the same database file, and whichever saves last
    silently drops the other's graphs.
    """
    try:
        embedded._async_managed = False
        embedded._cleanup()   # shuts down only when no other client is connected
    except Exception:
        pass


def _shutdown() -> None:
    if not _clients:
        return
    try:
        _loop.run(_close_all(), timeout=60)
    except Exception:
        pass


atexit.register(_shutdown)


# -- sync helpers (health check + cleanup command) ---------------------------

def ping(cfg: FalkorConfig | None = None, timeout: float = 30) -> bool:
    cfg = cfg or config_from_env()

    async def _ping() -> bool:
        client = await get_async_client(cfg)
        conn = getattr(client, "connection", None) or getattr(client, "client", None)
        res = await conn.ping()
        return bool(res)

    try:
        return _loop.run(_ping(), timeout=timeout)
    except Exception:
        return False


def list_graphs(prefix: str | None = None, cfg: FalkorConfig | None = None) -> list[str]:
    cfg = cfg or config_from_env()

    async def _list() -> list[str]:
        client = await get_async_client(cfg)
        names = await client.list_graphs()
        return sorted(n.decode() if isinstance(n, bytes) else n for n in (names or []))

    names = _loop.run(_list(), timeout=60)
    if prefix:
        names = [n for n in names if n.startswith(prefix)]
    return names


async def delete_graph_async(name: str, cfg: FalkorConfig) -> bool:
    client = await get_async_client(cfg)
    names = await client.list_graphs()
    names = [n.decode() if isinstance(n, bytes) else n for n in (names or [])]
    if name not in names:
        return False
    await client.select_graph(name).delete()
    return True


def delete_graph(name: str, cfg: FalkorConfig | None = None) -> bool:
    """Delete one graph database. Returns False if it did not exist."""
    cfg = cfg or config_from_env()
    return _loop.run(delete_graph_async(name, cfg), timeout=60)


def copy_graph(src: str, dest: str, cfg: FalkorConfig | None = None) -> bool:
    """Copy one graph database, indices included, to a new name (GRAPH.COPY, well under a second).
    Returns False, and copies nothing, if ``src`` is missing or ``dest`` already exists."""
    cfg = cfg or config_from_env()

    async def _copy() -> bool:
        client = await get_async_client(cfg)
        names = [n.decode() if isinstance(n, bytes) else n for n in (await client.list_graphs() or [])]
        if src not in names or dest in names:
            return False
        await client.select_graph(src).copy(dest)
        return True

    return _loop.run(_copy(), timeout=120)


def delete_graphs(prefix: str, cfg: FalkorConfig | None = None) -> list[str]:
    """Delete every graph whose name starts with ``prefix`` (e.g. 'q_'). Returns deleted names."""
    if not prefix:
        raise ValueError("refusing to delete graphs without a prefix")
    cfg = cfg or config_from_env()
    deleted = []
    for name in list_graphs(prefix=prefix, cfg=cfg):
        if delete_graph(name, cfg=cfg):
            deleted.append(name)
    return deleted
