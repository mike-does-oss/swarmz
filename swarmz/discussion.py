"""The discussion a swarm run produced, as plain text for the final question.

OASIS keeps every post, reply and like in the simulation's own SQLite file. When an agent is
asked for its probability it reads that thread directly, with its own entries marked "(you)".

This replaces per-agent graph memory (2026-11-10). The graph only recorded an agent's own
actions, and a handful of extracted facts came back out, so what other agents said hardly
reached the final answer, while writing it took about 750 model calls and half of a run's cost.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

MAX_CHARS = 60_000  # about 15K tokens; a 20-agent, 10-round thread is about 33K characters


@dataclass
class Comment:
    comment_id: int
    agent_id: int
    text: str
    likes: int = 0
    at: str = ""       # when it was written, as OASIS recorded it; "" if the file has no times


@dataclass
class Post:
    post_id: int
    agent_id: int
    text: str
    likes: int = 0
    comments: list[Comment] = field(default_factory=list)
    at: str = ""


def load(db_path: str | Path) -> list[Post]:
    """Posts with their replies, oldest first. A missing file is an empty discussion."""
    path = Path(db_path)
    if not path.exists():
        return []
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        agent_of = dict(con.execute("SELECT user_id, agent_id FROM user").fetchall())
        post_at, comment_at = (_time_column(con, t) for t in ("post", "comment"))
        raw = con.execute(f"SELECT post_id, user_id, original_post_id, content, quote_content, num_likes, {post_at} "
                          "FROM post ORDER BY post_id").fetchall()
        content = {r[0]: _clean(r[3]) for r in raw}
        posts: dict[int, Post] = {}
        for pid, uid, original, text, quote, likes, at in raw:
            body = _clean(text)
            if original is not None:  # a repost or a quote of an earlier post
                body = " ".join(x for x in (_clean(quote), f"(sharing: {content.get(original) or body})") if x)
            if body:
                posts[pid] = Post(pid, agent_of.get(uid, uid), body, likes or 0, at=str(at or ""))
        for cid, pid, uid, text, likes, at in con.execute(
                f"SELECT comment_id, post_id, user_id, content, num_likes, {comment_at} FROM comment ORDER BY comment_id"):
            if pid in posts and _clean(text):
                posts[pid].comments.append(Comment(cid, agent_of.get(uid, uid), _clean(text), likes or 0, str(at or "")))
    finally:
        con.close()
    return list(posts.values())


def _time_column(con, table: str) -> str:
    cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    return "created_at" if "created_at" in cols else "''"


def render(posts: list[Post], agent_id: int | None, names: dict[int, str], max_chars: int = MAX_CHARS) -> str:
    """The thread as one agent reads it. Too long a thread loses other agents' least-liked,
    oldest replies first; posts and the agent's own replies are always kept."""
    dropped: set[int] = set()
    candidates = sorted((c for p in posts for c in p.comments if c.agent_id != agent_id),
                        key=lambda c: (c.likes, c.comment_id))
    text = _text(posts, agent_id, names, dropped)
    while len(text) > max_chars and candidates:
        over = len(text) - max_chars
        while candidates and over > 0:  # drop about as much as we are over, then measure again
            c = candidates.pop(0)
            dropped.add(c.comment_id)
            over -= len(c.text) + 30
        text = _text(posts, agent_id, names, dropped)
    return text[:max_chars]


def said(posts: list[Post], max_chars: int = 3000) -> dict[int, list[str]]:
    """What each agent wrote, posts and replies together, oldest first (without times: its posts,
    then its replies). An agent that wrote more than max_chars keeps its most recent entries,
    since its latest view is its stance."""
    entries = [(p.at, 0, p.post_id, p.agent_id, p.text) for p in posts]
    entries += [(c.at, 1, c.comment_id, c.agent_id, c.text) for p in posts for c in p.comments]
    out: dict[int, list[str]] = {}
    for _, _, _, aid, text in sorted(entries):
        out.setdefault(aid, []).append(text)
    for aid, texts in out.items():
        kept, size = [], 0
        for t in reversed(texts):
            if kept and size + len(t) > max_chars:
                break
            kept.append(t)
            size += len(t) + 3
        out[aid] = kept[::-1]
    return out


def _text(posts, agent_id, names, dropped: set[int]) -> str:
    lines = []
    for p in posts:
        lines.append(f"[Post {p.post_id}] {_who(p.agent_id, agent_id, names)}{_likes(p.likes)}: {p.text}")
        lines += [f"    reply from {_who(c.agent_id, agent_id, names)}{_likes(c.likes)}: {c.text}"
                  for c in p.comments if c.comment_id not in dropped]
        lines.append("")
    if dropped:
        lines.append(f"({len(dropped)} replies left out to keep this short)")
    return "\n".join(lines).rstrip()


def _who(aid: int, me: int | None, names: dict[int, str]) -> str:
    name = names.get(aid) or f"Agent {aid}"
    return f"{name} (you)" if aid == me else name


def _likes(n: int) -> str:
    return f", {n} like{'' if n == 1 else 's'}" if n else ""


def _clean(text) -> str:
    return " ".join(str(text or "").split())
