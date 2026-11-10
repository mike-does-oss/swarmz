"""The discussion thread a swarm agent reads when asked for its probability."""

import sqlite3

from swarmz import discussion
from swarmz.arms.runner import _swarm_context
from swarmz.bridge import SwarmAgent

NAMES = {0: "Calibrated Osprey", 1: "Wary Lynx", 2: "Bold Heron"}


def make_db(path, n_replies=2):
    """A small OASIS-shaped simulation file. user_id is offset from agent_id on purpose."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE user (user_id INTEGER, agent_id INTEGER, user_name TEXT, name TEXT);
        CREATE TABLE post (post_id INTEGER, user_id INTEGER, original_post_id INTEGER, content TEXT,
                           quote_content TEXT, num_likes INTEGER);
        CREATE TABLE comment (comment_id INTEGER, post_id INTEGER, user_id INTEGER, content TEXT, num_likes INTEGER);
    """)
    con.executemany("INSERT INTO user VALUES (?, ?, NULL, ?)", [(10, 0, "a"), (11, 1, "b"), (12, 2, "c")])
    con.executemany("INSERT INTO post VALUES (?, ?, NULL, ?, NULL, ?)",
                    [(1, 10, "Inflation is at 3.45%.\nHold looks likely.", 2), (2, 11, "The board cut in March.", 0)])
    con.executemany("INSERT INTO comment VALUES (?, ?, ?, ?, ?)",
                    [(i, 1, 11 + i % 2, f"reply number {i} " + "x" * 200, 1 if i == 1 else 0)
                     for i in range(1, n_replies + 1)])
    con.commit()
    con.close()
    return path


def test_thread_shows_who_said_what_and_marks_the_reader(tmp_path):
    thread = discussion.load(make_db(tmp_path / "reddit_simulation.db"))
    assert [(p.post_id, p.agent_id, len(p.comments)) for p in thread] == [(1, 0, 2), (2, 1, 0)]
    text = discussion.render(thread, 1, NAMES)
    assert "[Post 1] Calibrated Osprey, 2 likes: Inflation is at 3.45%. Hold looks likely." in text
    assert "[Post 2] Wary Lynx (you): The board cut in March." in text
    assert "reply from Bold Heron, 1 like: reply number 1" in text and "reply from Wary Lynx (you): reply number 2" in text
    assert "(you)" not in discussion.render(thread, None, NAMES)   # an agent that never joined still reads it all


def test_long_thread_keeps_posts_own_replies_and_liked_replies(tmp_path):
    thread = discussion.load(make_db(tmp_path / "reddit_simulation.db", n_replies=40))
    text = discussion.render(thread, 1, NAMES, max_chars=6000)
    assert len(text) <= 6000 and "replies left out" in text
    assert "[Post 1]" in text and "[Post 2]" in text
    assert text.count("Wary Lynx (you): reply number") == 20   # every reply the reader wrote (even ids)
    assert "reply number 1 " in text                            # the liked reply outlives unliked ones


def test_missing_simulation_file_is_an_empty_discussion(tmp_path):
    assert discussion.load(tmp_path / "nope.db") == []
    assert discussion.render([], 0, NAMES) == ""


def test_final_question_context_is_brief_then_discussion():
    a = SwarmAgent(0, "p", "persona", posts=["mine"], discussion="[Post 1] Wary Lynx: hello")
    ctx = _swarm_context("THE BRIEF", a)
    assert ctx.startswith("THE BRIEF\n\nTHE PANEL'S DISCUSSION") and ctx.endswith("[Post 1] Wary Lynx: hello")
    assert "What you remember" not in ctx and "What you posted" not in ctx
    assert "(there was no discussion)" in _swarm_context("THE BRIEF", None)
    old = SwarmAgent(0, "p", "persona", posts=["mine"], memory_context="- [own] a fact")   # graph memory switched on
    assert "What you posted:\n- mine" in _swarm_context("B", old) and "What you remember:\n- [own] a fact" in _swarm_context("B", old)


def test_what_each_agent_wrote_covers_replies_and_keeps_the_latest(tmp_path):
    thread = discussion.load(make_db(tmp_path / "reddit_simulation.db", n_replies=6))
    said = discussion.said(thread)
    assert said[0] == ["Inflation is at 3.45%. Hold looks likely."]            # agent 0 only posted
    assert said[1][0] == "The board cut in March." and len(said[1]) == 4       # its post, then its three replies
    assert [t[:14] for t in said[2]] == ["reply number 1", "reply number 3", "reply number 5"]   # agent 2 only replied
    short = discussion.said(thread, max_chars=450)
    assert [t[:14] for t in short[1]] == ["reply number 4", "reply number 6"]  # too long: the oldest go first


def test_what_an_agent_last_said_is_read_from_replies_too_in_the_order_written(tmp_path):
    # On the first forecast-first run the posted number was read from posts only: 12 of 20 agents
    # had none, although every one of them had replied "YES, NN%".
    from swarmz.mirofish_bridge import public_prob

    path = tmp_path / "reddit_simulation.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE user (user_id INTEGER, agent_id INTEGER, user_name TEXT, name TEXT);
        CREATE TABLE post (post_id INTEGER, user_id INTEGER, original_post_id INTEGER, content TEXT,
                           quote_content TEXT, num_likes INTEGER, created_at TEXT);
        CREATE TABLE comment (comment_id INTEGER, post_id INTEGER, user_id INTEGER, content TEXT, num_likes INTEGER,
                              created_at TEXT);
    """)
    con.executemany("INSERT INTO user VALUES (?, ?, NULL, ?)", [(10, 0, "a"), (11, 1, "b")])
    con.executemany("INSERT INTO post VALUES (?, ?, NULL, ?, NULL, 0, ?)",
                    [(1, 10, "Miami are 4-6 and Utah 1-9. Waiting for the injury report.", "2026-11-10 12:01:00"),
                     (2, 10, "YES, 64%: George is out, but so is Adebayo.", "2026-11-10 12:40:00")])
    con.executemany("INSERT INTO comment VALUES (?, 1, ?, ?, 0, ?)",
                    [(1, 11, "YES, 70%: Utah's start decides it.", "2026-11-10 12:05:00"),
                     (2, 10, "YES, 72%: agreed for now.", "2026-11-10 12:10:00"),
                     (3, 11, "YES, 66%: Adebayo out changes it.", "2026-11-10 12:45:00")])
    con.commit()
    con.close()
    said = discussion.said(discussion.load(path))
    assert [t[:8] for t in said[0]] == ["Miami ar", "YES, 72%", "YES, 64%"]   # the later post comes after the reply
    assert public_prob(said[0]) == 0.64 and public_prob(said[1]) == 0.66
