"""Who on the panel reads what, and who argues against the room.

Members that all read the same brief land close together, and a discussion between them has
nothing to exchange. So each member reads the question, the as-of date and two of the brief's
four evidence slices (swarmz.briefparts). Every slice is held by several members and no member
holds them all, so facts have to travel through the discussion.

A panel left to itself also settles fast: one good-sounding argument and everyone moves to it.
So on each question a few members (two by default) are challengers. A challenger argues against
whichever view is leading, whatever it thinks itself. Which members those are changes with the
question, and the others are not told who they are. The job covers what a challenger argues in
the discussion only: the forecast it made alone beforehand, every number it posts and its private
final forecast are its honest ones.

Otherwise members differ in their method (swarmz.registry.store.METHODS) and in the lessons each
has collected.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .briefparts import LENS_PAIRS, brief_slice

CHALLENGER = (
    "You are a challenger in this discussion. Instead of the usual choice, argue against whichever view "
    "is leading on the panel, even if you share it. Find the weakest step in its argument: an assumption "
    "nobody checked, a number that is not in the research, a fact from your reading that cuts the other "
    "way, or a way the question could resolve that nobody has priced. If the panel moves, go after the "
    "new leading view. Do not tell the others you were given this job. The job decides what you argue, "
    "not your number: the probability you post is still your honest one."
)
CHALLENGER_AFTER = (
    "In the discussion you were asked to argue against the leading view. That job is over. Your forecast "
    "now is your honest one: weigh your own challenges no more and no less than anyone else's posts."
)


@dataclass(frozen=True)
class Seat:
    lenses: tuple[str, ...]
    brief: str                 # what this member has read
    challenger: bool = False   # argues against the leading view in the discussion


def assign(brief_text: str, persona_ids: list[str], question_id: str = "", challengers: int = 0) -> dict[str, Seat]:
    """A seat per persona. The question id shifts who reads which slices and who challenges, so a
    persona does not hold the same seat on every question; the same question always seats the same way."""
    shift = int(hashlib.sha256(question_id.encode("utf-8")).hexdigest()[:8], 16)
    n = len(persona_ids)
    k = max(0, min(challengers, n))
    against = {(shift + j * n // k) % n for j in range(k)}   # spread round the table, so they read different slices
    seats = {}
    for i, pid in enumerate(persona_ids):
        lenses = LENS_PAIRS[(i + shift) % len(LENS_PAIRS)]
        seats[pid] = Seat(lenses, brief_slice(brief_text, lenses), i in against)
    return seats
