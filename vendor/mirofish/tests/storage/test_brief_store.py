import hashlib

import pytest

from app.storage.brief_store import (
    compute_hash,
    write_brief,
    read_brief,
    brief_path,
    BriefIntegrityError,
)


def test_compute_hash_is_stable_and_hex():
    h = compute_hash("hello world")
    assert h == compute_hash("hello world")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_write_then_read_roundtrips(data_dir):
    briefs = str(data_dir / "briefs_v2")
    text = "# Brief\n\nSome frozen content."
    h = write_brief(text, briefs)
    assert brief_path(h, briefs).endswith(f"{h}.md")
    assert read_brief(h, briefs) == text


def test_write_is_idempotent_for_identical_bytes(data_dir):
    briefs = str(data_dir / "briefs_v2")
    text = "same bytes"
    h1 = write_brief(text, briefs)
    h2 = write_brief(text, briefs)
    assert h1 == h2


def test_read_detects_tampering(data_dir):
    briefs = str(data_dir / "briefs_v2")
    h = write_brief("original", briefs)
    with open(brief_path(h, briefs), "w", encoding="utf-8") as f:
        f.write("tampered")
    with pytest.raises(BriefIntegrityError):
        read_brief(h, briefs)


def test_read_missing_raises_filenotfound(data_dir):
    briefs = str(data_dir / "briefs_v2")
    with pytest.raises(FileNotFoundError):
        read_brief("0" * 64, briefs)


def test_stored_bytes_match_hash_preimage(data_dir):
    briefs = str(data_dir / "briefs_v2")
    text = "line one\nline two\nline three\n"
    h = write_brief(text, briefs)
    with open(brief_path(h, briefs), "rb") as f:
        raw = f.read()
    # On-disk bytes must equal the exact preimage the hash was computed over.
    assert hashlib.sha256(raw).hexdigest() == h
    assert b"\r\n" not in raw
