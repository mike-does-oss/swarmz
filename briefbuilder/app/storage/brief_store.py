"""
Immutable, content-addressed brief store.

Frozen briefs are written to `<briefs_dir>/<sha256>.md` and are write-once.
Integrity is verified on read by re-hashing the file contents. Byte-identical
delivery to both consumers (MiroFish seed and baseline LLM) is guaranteed by
addressing every brief by the hash of its exact bytes.
"""

import os
import hashlib

from ..utils.logger import get_logger

logger = get_logger('briefbuilder.storage.brief_store')


class BriefIntegrityError(Exception):
    """Raised when stored brief bytes do not match their hash name."""


def compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def brief_path(brief_hash: str, briefs_dir: str) -> str:
    return os.path.join(briefs_dir, f"{brief_hash}.md")


def write_brief(text: str, briefs_dir: str) -> str:
    os.makedirs(briefs_dir, exist_ok=True)
    brief_hash = compute_hash(text)
    path = brief_path(brief_hash, briefs_dir)
    try:
        with open(path, "x", encoding="utf-8", newline="") as f:
            f.write(text)
        logger.info(f"Froze brief {brief_hash}")
        return brief_hash
    except FileExistsError:
        # Already frozen — verify the existing bytes are identical.
        with open(path, "r", encoding="utf-8", newline="") as f:
            existing = f.read()
        if compute_hash(existing) != brief_hash:
            raise BriefIntegrityError(
                f"Existing file at {path} does not match its hash name"
            )
        return brief_hash


def read_brief(brief_hash: str, briefs_dir: str) -> str:
    path = brief_path(brief_hash, briefs_dir)
    if not os.path.exists(path):
        raise FileNotFoundError(f"No frozen brief at {path}")
    with open(path, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    actual = compute_hash(text)
    if actual != brief_hash:
        raise BriefIntegrityError(
            f"Brief integrity check failed: expected {brief_hash}, got {actual}"
        )
    return text
