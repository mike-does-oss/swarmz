"""DuckDB connection and numbered migrations.

Migration 1 is schema.sql; later migrations live in migrations/NNN_name.sql.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb

HERE = Path(__file__).parent


def _migrations() -> list[tuple[int, Path]]:
    items = [(1, HERE / "schema.sql")]
    for p in sorted((HERE / "migrations").glob("*.sql")):
        m = re.match(r"(\d+)_", p.name)
        if m and int(m.group(1)) > 1:
            items.append((int(m.group(1)), p))
    return sorted(items)


def migrate(con: duckdb.DuckDBPyConnection) -> int:
    con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMP DEFAULT current_timestamp)")
    done = {r[0] for r in con.execute("SELECT version FROM schema_version").fetchall()}
    applied = 0
    for version, path in _migrations():
        if version in done:
            continue
        con.execute("BEGIN")
        try:
            con.execute(path.read_text())
            con.execute("INSERT INTO schema_version (version) VALUES (?)", [version])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        applied += 1
    return applied


def connect(path: str | Path | None = None, wait_s: float = 120) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) and migrate. ':memory:' works for tests.

    DuckDB allows one writing process at a time; if another swarmz process (e.g. the resolve
    watcher) holds the file, retry for up to ``wait_s`` seconds instead of failing.
    """
    import time

    if path is None:
        from ..config import settings
        path = settings().db_path
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    deadline, delay = time.monotonic() + wait_s, 0.5
    while True:
        try:
            con = duckdb.connect(str(path))
            break
        except duckdb.IOException as e:
            if "lock" not in str(e).lower() or time.monotonic() > deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 10)
    migrate(con)
    return con


def rows(con, sql: str, params: list | None = None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
