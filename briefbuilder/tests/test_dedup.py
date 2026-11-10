from dataclasses import dataclass

from app.services.brief_v2.dedup import (
    normalize_text, shingles, jaccard, group_near_duplicates, count_independent,
)


@dataclass
class Doc:
    url: str
    full_text: str


def test_normalize_and_shingles():
    assert normalize_text("Hello, WORLD!!  Foo.") == "hello world foo"
    sh = shingles("a b c d e f", k=3)
    assert "a b c" in sh and "d e f" in sh


def test_jaccard_bounds():
    assert jaccard(set(), set()) == 0.0
    assert jaccard({"x"}, {"x"}) == 1.0
    assert jaccard({"x", "y"}, {"y", "z"}) == 1 / 3


def test_group_near_duplicates_collapses_wire_copies():
    body = "The central bank raised rates by fifty basis points citing persistent inflation pressure across the economy today."
    docs = [
        Doc("https://reuters.com/a", body),
        Doc("https://yahoo.com/reuters-reprint", body),          # verbatim wire reprint
        Doc("https://otheroutlet.com/c", "A completely different unrelated story about sports scores and playoff brackets."),
    ]
    groups = group_near_duplicates(docs, threshold=0.8)
    assert groups["https://reuters.com/a"] == groups["https://yahoo.com/reuters-reprint"]
    assert groups["https://otheroutlet.com/c"] != groups["https://reuters.com/a"]


def test_count_independent_counts_distinct_groups():
    url_to_group = {"a": 0, "b": 0, "c": 1}
    assert count_independent(["a", "b"], url_to_group) == 1   # same wire group
    assert count_independent(["a", "c"], url_to_group) == 2   # independent
    assert count_independent(["a", "unknown"], url_to_group) == 1  # unknown ignored
