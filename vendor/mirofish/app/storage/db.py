"""
SQLite persistence for BriefBuilder v2: the prediction log (spec §5.1, plus
`agent_count`) and the per-brief provenance record (spec §5.2, stored with
JSON columns for its nested lists).
"""

import os
import json
import sqlite3
from typing import Optional, List

from ..utils.logger import get_logger
from .schemas import (
    PredictionRow,
    BriefProvenance,
    SourceRecord,
    DiscardedDoc,
    ScrubberRemoval,
)

logger = get_logger('mirofish.storage.db')

_CREATE_PREDICTIONS = """
CREATE TABLE IF NOT EXISTS predictions (
    question_id           TEXT PRIMARY KEY,
    question_text         TEXT NOT NULL,
    resolution_criteria   TEXT NOT NULL,
    category              TEXT NOT NULL,
    freeze_ts             TEXT NOT NULL,
    brief_hash            TEXT NOT NULL,
    brief_word_count      INTEGER NOT NULL,
    mirofish_prob         REAL,
    baseline_prob         REAL,
    market_bid            REAL,
    market_ask            REAL,
    market_mid            REAL,
    market_depth_usd      REAL,
    market_snapshot_ts    TEXT,
    mirofish_cost_usd     REAL,
    mirofish_wall_clock_s INTEGER,
    agent_count           INTEGER,
    outcome               INTEGER,
    resolved_at           TEXT,
    notes                 TEXT DEFAULT ''
)
"""

_CREATE_PROVENANCE = """
CREATE TABLE IF NOT EXISTS brief_provenance (
    brief_hash             TEXT PRIMARY KEY,
    question_id            TEXT NOT NULL,
    freeze_ts              TEXT NOT NULL,
    sources_json           TEXT NOT NULL,
    discarded_json         TEXT NOT NULL,
    scrubber_removals_json TEXT NOT NULL,
    decomposer_output_json TEXT NOT NULL,
    pipeline_versions_json TEXT NOT NULL
)
"""

# (table, column, column_declaration) — additive migrations applied idempotently.
_COLUMN_MIGRATIONS = [
    ("predictions", "market_snapshot_ts", "TEXT"),
]


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, column, decl in _COLUMN_MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.commit()


def get_connection(db_path: str) -> sqlite3.Connection:
    """
    Open (and initialize) a sqlite3 connection to `db_path`.

    Callers own the connection lifecycle. For the Flask predictions API
    (Plan 6), open one connection per request via flask.g and close it in
    teardown; do not share a single connection across request threads
    (sqlite3 default check_same_thread=True).
    """
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_PREDICTIONS)
    conn.execute(_CREATE_PROVENANCE)
    conn.commit()
    _ensure_columns(conn)


def insert_prediction(conn: sqlite3.Connection, row: PredictionRow, commit: bool = True) -> None:
    cols = PredictionRow.COLUMNS
    placeholders = ", ".join("?" for _ in cols)
    values = [getattr(row, c) for c in cols]
    conn.execute(
        f"INSERT INTO predictions ({', '.join(cols)}) VALUES ({placeholders})",
        values,
    )
    if commit:
        conn.commit()


def get_prediction(conn: sqlite3.Connection, question_id: str) -> Optional[PredictionRow]:
    cur = conn.execute(
        "SELECT * FROM predictions WHERE question_id = ?", (question_id,)
    )
    r = cur.fetchone()
    if r is None:
        return None
    return PredictionRow.from_dict(dict(r))


def list_predictions(
    conn: sqlite3.Connection, category: Optional[str] = None
) -> List[PredictionRow]:
    if category is None:
        cur = conn.execute("SELECT * FROM predictions ORDER BY freeze_ts DESC")
    else:
        cur = conn.execute(
            "SELECT * FROM predictions WHERE category = ? ORDER BY freeze_ts DESC",
            (category,),
        )
    return [PredictionRow.from_dict(dict(r)) for r in cur.fetchall()]


def update_outcome(
    conn: sqlite3.Connection, question_id: str, outcome: int, resolved_at: str, commit: bool = True
) -> None:
    conn.execute(
        "UPDATE predictions SET outcome = ?, resolved_at = ? WHERE question_id = ?",
        (outcome, resolved_at, question_id),
    )
    if commit:
        conn.commit()


def update_prediction_run(
    conn: sqlite3.Connection,
    question_id: str,
    *,
    mirofish_prob=None,
    baseline_prob=None,
    mirofish_cost_usd=None,
    mirofish_wall_clock_s=None,
    agent_count=None,
    commit: bool = True,
) -> None:
    cursor = conn.execute(
        "UPDATE predictions SET "
        "mirofish_prob = COALESCE(?, mirofish_prob), "
        "baseline_prob = COALESCE(?, baseline_prob), "
        "mirofish_cost_usd = COALESCE(?, mirofish_cost_usd), "
        "mirofish_wall_clock_s = COALESCE(?, mirofish_wall_clock_s), "
        "agent_count = COALESCE(?, agent_count) "
        "WHERE question_id = ?",
        (mirofish_prob, baseline_prob, mirofish_cost_usd,
         mirofish_wall_clock_s, agent_count, question_id),
    )
    if cursor.rowcount != 1:
        logger.warning(
            f"update_prediction_run matched {cursor.rowcount} rows for question_id={question_id!r}"
        )
    if commit:
        conn.commit()


def insert_provenance(conn: sqlite3.Connection, prov: BriefProvenance, commit: bool = True) -> None:
    conn.execute(
        """
        INSERT INTO brief_provenance (
            brief_hash, question_id, freeze_ts, sources_json, discarded_json,
            scrubber_removals_json, decomposer_output_json, pipeline_versions_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            prov.brief_hash,
            prov.question_id,
            prov.freeze_ts,
            json.dumps([s.to_dict() for s in prov.sources]),
            json.dumps([d.to_dict() for d in prov.discarded]),
            json.dumps([r.to_dict() for r in prov.scrubber_removals]),
            prov.decomposer_output_json,
            json.dumps(prov.pipeline_versions),
        ),
    )
    if commit:
        conn.commit()


def get_provenance(
    conn: sqlite3.Connection, brief_hash: str
) -> Optional[BriefProvenance]:
    cur = conn.execute(
        "SELECT * FROM brief_provenance WHERE brief_hash = ?", (brief_hash,)
    )
    r = cur.fetchone()
    if r is None:
        return None
    return BriefProvenance(
        brief_hash=r["brief_hash"],
        question_id=r["question_id"],
        freeze_ts=r["freeze_ts"],
        sources=[SourceRecord(**s) for s in json.loads(r["sources_json"])],
        discarded=[DiscardedDoc(**d) for d in json.loads(r["discarded_json"])],
        scrubber_removals=[
            ScrubberRemoval(**x) for x in json.loads(r["scrubber_removals_json"])
        ],
        decomposer_output_json=r["decomposer_output_json"],
        pipeline_versions=json.loads(r["pipeline_versions_json"]),
    )
