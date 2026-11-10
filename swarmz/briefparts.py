"""Parts of a frozen brief: its sections, the digest a simulated agent carries, and the slices
handed out when agents are each given part of the research.

A brief has fixed headings (QUANTITATIVE ANCHORS, CONFIRMED FACTS, ...). Nothing here adds to
or rewrites a brief: every function returns text cut from it.
"""

from __future__ import annotations

import re

_HEADING = re.compile(r"^#+[ \t]*(.+?)[ \t]*$", re.M)

# What a simulated agent carries into the discussion, most useful first. A question about a price
# or a statistic has its answer-deciding facts under QUANTITATIVE ANCHORS. A question about an
# event or a decision (an election, a vote, a ruling) often has almost nothing there: its state
# of play is under CONFIRMED FACTS and RECENT TIMELINE. So the digest draws on all three.
DIGEST_ORDER = ("QUANTITATIVE ANCHORS", "CONFIRMED FACTS", "RECENT TIMELINE",
                "STAKEHOLDER POSITIONS & INCENTIVES", "REPORTED BUT UNCONFIRMED",
                "CONFLICTING REPORTS", "OPEN UNCERTAINTIES")
DIGEST_CHARS = 2400

# Every agent reads these whichever slice it holds.
COMMON = ("QUESTION & RESOLUTION CRITERIA", "AS-OF")
# The evidence, split four ways. An agent given a slice reads two of the four.
LENSES = {
    "numbers": ("QUANTITATIVE ANCHORS", "RECENT TIMELINE"),
    "facts": ("CONFIRMED FACTS", "ENTITIES & RELATIONSHIPS"),
    "people": ("STAKEHOLDER POSITIONS & INCENTIVES", "REPORTED BUT UNCONFIRMED"),
    "doubts": ("CONFLICTING REPORTS", "OPEN UNCERTAINTIES"),
}
LENS_PAIRS = (("numbers", "people"), ("facts", "doubts"), ("numbers", "facts"),
              ("people", "doubts"), ("numbers", "doubts"), ("facts", "people"))


def sections(brief_text: str) -> list[tuple[str, str]]:
    """(heading, body) in the brief's order. Text before the first heading has heading ""."""
    text = brief_text or ""
    heads = list(_HEADING.finditer(text))
    out = []
    if not heads or heads[0].start() > 0:
        lead = text[:heads[0].start()] if heads else text
        if lead.strip():
            out.append(("", lead.strip()))
    for m, nxt in zip(heads, heads[1:] + [None]):
        out.append((m.group(1).upper(), text[m.end():nxt.start() if nxt else len(text)].strip()))
    return out


def digest(brief_text: str, max_chars: int = DIGEST_CHARS, order: tuple[str, ...] = DIGEST_ORDER) -> str:
    """The brief's deciding facts in at most ``max_chars``: sections in ``order``, each under its
    heading and cut at a line end, no one section taking more than half the room. "" if the text
    has none of these sections."""
    by_title = dict(sections(brief_text))
    out, room = [], max_chars
    for title in order:
        body = by_title.get(title, "")
        if not body or room <= 0:
            continue
        kept = _cut(body, min(room, max_chars // 2) - len(title) - 1)
        if kept:
            out.append(f"{title}\n{kept}")
            room -= len(title) + 1 + len(kept) + 2
    return "\n\n".join(out)


def _cut(body: str, max_chars: int) -> str:
    kept = ""
    for line in body.splitlines():
        if len(kept) + len(line) + 1 > max_chars:
            break
        kept += line + "\n"
    return kept.strip()


def brief_slice(brief_text: str, lenses: tuple[str, ...]) -> str:
    """The brief with only the common sections and the given lenses' sections, in the brief's own
    order. A text without the fixed headings comes back whole: there is nothing to split."""
    parts = sections(brief_text)
    wanted = set(COMMON) | {t for lens in lenses for t in LENSES[lens]}
    known = {t for ts in LENSES.values() for t in ts}
    if not any(title in known for title, _ in parts):
        return brief_text
    return "\n\n".join(f"## {title}\n\n{body}" for title, body in parts if title in wanted)
