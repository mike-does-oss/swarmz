"""The arithmetic for questions that resolve on a number clearing a line.

"Will the index be above 7,435 at the close?" has a rough answer before any judgement: how far
the latest reading is from the line, measured in the moves this quantity usually makes in the
time left. Panel members did that sum in their heads and got it wrong in both directions, so it
can be done once here and handed to every member as a note with its working shown.

It is off unless SWARMZ_THRESHOLD_NOTE=1. A replay on 29 stored briefs (2026-11-16) did not show it helps:
on the 7 resolved questions that got a note the panel scored 0.321 with it and 0.306 without, inside
run-to-run noise, and it halved the spread between members. Where the brief's latest reading was fresh
(an index the day before its close) it moved the panel the right way; where the reading was stale or the
market knew something newer, it anchored everyone on the wrong number. Turn it on to test, not by default.

One model call reads the brief and reports the numbers (latest reading, the line, the typical
move). It is not asked for a probability. The division and the conversion to "about N times in
100" are done in code. The note is a guide the members adjust, not a forecast.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ValidationError

from .llm import LLM, LLMOutputError

SYSTEM = (
    "You prepare the numbers for one arithmetic note for a forecasting panel. You are given a yes/no question "
    "and a research brief. Decide whether the question resolves on a measured number being above, below or "
    "between fixed lines at one fixed time: a price, an index level, a temperature, a rate, a count, a published "
    "statistic, a polling average. It does not apply to who wins a game or by how much, to whether an event "
    "happens, to a number that people decide (a central bank's rate, a vote count, a ruling, a price a company "
    "sets), to whether something is zero or not (did it rain at all), to a number that only has to touch a line "
    "at any time before the deadline, when the brief gives no latest reading or forecast of that number, or when "
    "its latest reading is stale: older than a few times the time left to the deadline. If in doubt, it does not "
    "apply. If it applies, report the numbers asked for. Take the latest reading "
    "and the lines only from the brief and the question. For the typical move, use recent readings in the brief "
    "where there are enough of them, otherwise what is generally known about how far this kind of quantity "
    "moves over that length of time. Do not estimate a probability."
)
ASK = (
    'Return JSON. If it does not apply: {"applies": false}. Otherwise: {"applies": true, '
    '"quantity": "<what is measured>", "unit": "<its unit, or \'\'>", '
    '"latest": <the latest reading as a number; if the number is not out yet and the brief has a forecast or '
    'consensus for the final value, that forecast>, "latest_is": "observed" or "forecast", '
    '"latest_as_of": "<when>", "yes_when": "above" or "below" or "between", '
    '"line": <the line as a number; the lower line for between>, "line_high": <the upper line for between, else null>, '
    '"time_left": "<time from the as-of date to the deciding moment, in the units this quantity moves in, '
    'e.g. 1 trading day>", "typical_move": <one standard deviation, in the same unit as latest, of the gap between '
    '"latest" and the value that will decide the question: for an observed reading, how far the quantity '
    'typically moves over the time left; for a forecast, the typical forecast error>, '
    '"typical_move_basis": "<one sentence: where that figure comes from>"}'
)


class Numbers(BaseModel):
    quantity: str
    unit: str = ""
    latest: float
    latest_is: str = "observed"
    latest_as_of: str = ""
    yes_when: str
    line: float
    line_high: float | None = None
    time_left: str = ""
    typical_move: float
    typical_move_basis: str = ""


def extract(llm: LLM, *, model: str, question: str, brief: str) -> Numbers | None:
    """The numbers for the note, or None when the question is not of this kind or they cannot be trusted."""
    try:
        raw = llm.json(SYSTEM, f"QUESTION:\n{question}\n\nRESEARCH BRIEF:\n{brief}\n\n{ASK}", model=model, max_tokens=500)
        if not isinstance(raw, dict) or not raw.get("applies"):
            return None
        n = Numbers.model_validate(raw)
    except (ValidationError, LLMOutputError, TypeError, ValueError):
        return None
    ok = (all(math.isfinite(x) for x in (n.latest, n.line, n.typical_move)) and n.typical_move > 0
          and n.yes_when in ("above", "below", "between")
          and (n.yes_when != "between" or (n.line_high is not None and math.isfinite(n.line_high) and n.line_high > n.line)))
    return n if ok else None


def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def chance(n: Numbers) -> float:
    """The share of the time a quantity at ``latest`` with spread ``typical_move`` ends on the YES side,
    if it moved at random with no drift."""
    below = lambda line: _phi((line - n.latest) / n.typical_move)
    if n.yes_when == "above":
        return 1 - below(n.line)
    if n.yes_when == "below":
        return below(n.line)
    return below(n.line_high) - below(n.line)


def _num(x: float) -> str:
    """A number to about four significant figures, with thousands separators and no trailing zeros."""
    places = 2 if abs(x) >= 100 else 3 if abs(x) >= 1 else 4
    text = f"{x:,.{places}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def note(n: Numbers) -> str:
    """The note every member reads, with its working shown."""
    u = f" {n.unit}" if n.unit and len(n.unit) > 1 else (n.unit or "")
    kind = "Latest reading" if n.latest_is != "forecast" else "Latest forecast of the final value"
    lines = [f"- {kind}: {n.quantity}: {_num(n.latest)}{u}" + (f" (as of {n.latest_as_of})." if n.latest_as_of else ".")]
    if n.yes_when == "between":
        inside = n.line <= n.latest <= n.line_high
        lines.append(f"- YES needs it between {_num(n.line)} and {_num(n.line_high)}{u}. It is now "
                     f"{'inside that band' if inside else 'outside that band'}: {_num(abs(n.latest - n.line))} from the lower "
                     f"line and {_num(abs(n.line_high - n.latest))} from the upper.")
    else:
        gap = n.latest - n.line
        side = "YES" if (gap > 0) == (n.yes_when == "above") else "NO"
        pct = f" ({abs(gap) / abs(n.line) * 100:.1f}%)" if abs(n.line) >= 100 else ""   # a share of a temperature means nothing
        lines.append(f"- YES needs it {n.yes_when} {_num(n.line)}{u}. It is now {_num(abs(gap))}{pct} "
                     f"{'above' if gap > 0 else 'below'} that line, which is the {side} side.")
        ratio = abs(gap) / n.typical_move
    what = "A typical miss for a forecast like this" if n.latest_is == "forecast" else "A typical move over that time"
    move = f"- Time left: {n.time_left or 'not stated'}. {what} is about {_num(n.typical_move)}{u}"
    lines.append(move + (f" ({n.typical_move_basis.rstrip('.')})." if n.typical_move_basis else "."))
    p = min(98, max(2, round(chance(n) * 100)))
    if n.yes_when != "between":
        lines.append(f"- So the line is {ratio:.1f} times that distance away. If the quantity moved at random with no drift, "
                     f"it would end on the YES side about {p} times in 100.")
    else:
        lines.append(f"- If the quantity moved at random with no drift, it would end inside the band about {p} times in 100.")
    lines.append("- This is a starting point. It leaves out anything in your reading that points one way: a scheduled "
                 "event, a trend, a newer forecast. Adjust for those, but do not ignore the distance.")
    return "ARITHMETIC NOTE (worked out from the numbers in the research; a rough guide, not a forecast)\n" + "\n".join(lines)
