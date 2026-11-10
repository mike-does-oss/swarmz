"""The panel's discussion: ten forecasters, a few rounds, plain model calls.

Each member has already read its own part of the research and committed to a forecast alone
(swarmz.arms.runner). That forecast is its opening post, so nobody starts on the fence and
opening costs no model call. Then the panel talks for ``req.max_rounds`` rounds. In a round every
member reads the thread so far and its own reading, and posts once, with its current number.
Members post at the same time within a round, so nobody answers a post from the same round.
A member with a job (a challenger, swarmz.diversity) is told it each round; the thread does not
show the others who has one.

With each post a member may also like up to two other members' latest posts: the ones most useful
to it. Likes are kept with the thread for the replay and are never shown to the panel, so a count
of likes cannot pull members together.

Nothing else happens here: no simulated social network, no graph, no memory store. The private
final forecast is taken afterwards by the runner, which hands each member the thread returned here.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field, ValidationError, field_validator

from .bridge import SwarmAgent, SwarmRequest, SwarmResult
from .config import settings
from .llm import LLM, LLMOutputError

DISCUSS_SYSTEM = (
    "You are a member of a forecasting panel, in the middle of its discussion. Stay in character as the "
    "persona described. Each member has read a different part of the research, so others hold facts you "
    "have not seen and you hold facts they have not seen. In your post, do one of two things: bring a "
    "fact from your reading that nobody has mentioned and say which way it points, or answer the "
    "strongest point made against your view. Do not repeat what has been said and do not summarise the "
    "thread. Then give your current probability that the question resolves YES. Move your number only "
    "for a fact or an argument you had not weighed: other members agreeing with each other is not "
    "evidence. Commit to a side. Do not guess what any market thinks."
)
POST_CHARS = 900       # a post is cut here when shown to others
OPENING_CHARS = 600
MAX_LIKES = 2


class Said(BaseModel):
    post: str = Field(min_length=1, max_length=4000)
    prob: float = Field(ge=0.0, le=1.0)
    likes: list[str] = Field(default_factory=list)   # names, as the member wrote them

    @field_validator("likes", mode="before")
    @classmethod
    def _names_only(cls, v):
        """A like is optional: anything that is not a list of names counts as no likes, not a bad post."""
        if isinstance(v, str):
            v = [v]
        return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def liked(names: list[str], me: str, thread: list[dict]) -> list[str]:
    """The persona ids a member liked: others who have a post in the thread it read, at most MAX_LIKES."""
    by_name = {e["name"].strip().lower(): e["persona_id"] for e in thread}
    out: list[str] = []
    for n in names:
        pid = by_name.get(n.strip().lower())
        if pid and pid != me and pid not in out:
            out.append(pid)
    return out[:MAX_LIKES]


def stated(prob: float) -> str:
    """A probability the way members post it: "NO, 30%". The number is always the chance of YES."""
    return f"{'YES' if prob >= 0.5 else 'NO'}, {round(prob * 100)}%"


def _one_line(text: str, max_chars: int) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= max_chars else t[:max_chars].rsplit(" ", 1)[0] + " ..."


def render(thread: list[dict], me: str | None) -> str:
    """The thread as one member sees it. Its own entries are marked "(you)"."""
    lines = []
    for e in thread:
        when = "Opening" if e["round"] == 0 else f"Round {e['round']}"
        who = e["name"] + (" (you)" if e["persona_id"] == me else "")
        lines.append(f"[{when}] {who}: {stated(e['prob'])}. {e['text']}")
    return "\n".join(lines)


def _user(profile: str, question: str, reading: str, thread: list[dict], pid: str, round_no: int, rounds: int,
          role: str = "") -> str:
    talk = render(thread, pid) or "(nobody has spoken yet)"
    job = f"YOUR JOB IN THIS DISCUSSION:\n{role}\n\n" if role else ""
    return (f"PERSONA:\n{profile}\n\nQUESTION:\n{question}\n\nWHAT YOU HAVE READ:\n{reading or '(nothing)'}\n\n"
            f"THE DISCUSSION SO FAR (entries marked \"(you)\" are yours):\n{talk}\n\n{job}"
            f"This is round {round_no} of {rounds}. "
            'Return JSON: {"post": "<your post, at most 90 words>", "prob": <number between 0 and 1>, '
            '"likes": [<the names of up to two other members whose latest post was most useful to you: a fact '
            'you did not have, or an argument you had to take seriously whether or not you agree. Nobody sees '
            'your likes. [] if none>]}')


def _speak(llm: LLM, model: str, user: str) -> Said | None:
    """One post. Asked again once if the reply is invalid; a member that fails twice stays silent."""
    for attempt in range(2):
        try:
            raw = llm.json(DISCUSS_SYSTEM, user if attempt == 0 else
                           user + "\n\nYour previous reply was invalid. Reply with exactly that JSON object.",
                           model=model, max_tokens=400)
            return Said.model_validate(raw)
        except (ValidationError, LLMOutputError, TypeError):
            continue
    return None


def run(req: SwarmRequest, *, llm: LLM, workers: int = 8) -> SwarmResult:
    if not req.personas:
        raise ValueError("the panel needs registry personas; run `swarmz personas seed`")
    model = settings().agent_model
    names = {p.persona_id: p.name or f"Member {i + 1}" for i, p in enumerate(req.personas)}
    reading = {p.persona_id: p.reading or req.brief_text for p in req.personas}
    thread: list[dict] = []
    for p in req.personas:   # opening posts: the forecast each made alone
        if p.prior_prob is not None:
            thread.append({"round": 0, "persona_id": p.persona_id, "name": names[p.persona_id],
                           "prob": float(p.prior_prob),
                           "text": _one_line(p.prior_rationale or "No reason given.", OPENING_CHARS)})
    silent = 0
    rounds = max(0, int(req.max_rounds))
    for r in range(1, rounds + 1):
        seen = list(thread)   # everyone in a round reads the same thread

        def one(p):
            return _speak(llm, model, _user(p.profile, req.question, reading[p.persona_id], seen,
                                            p.persona_id, r, rounds, p.role))

        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            said = list(ex.map(one, req.personas))
        for p, s in zip(req.personas, said):
            if s is None:
                silent += 1
                continue
            thread.append({"round": r, "persona_id": p.persona_id, "name": names[p.persona_id],
                           "prob": s.prob, "text": _one_line(s.post, POST_CHARS),
                           "likes": liked(s.likes, p.persona_id, seen)})   # each is that member's latest post in `seen`
    agents = []
    for i, p in enumerate(req.personas):
        mine = [e for e in thread if e["persona_id"] == p.persona_id]
        agents.append(SwarmAgent(
            agent_id=i, persona_id=p.persona_id, persona_text=p.profile,
            posts=[f"{stated(e['prob'])}. {e['text']}" for e in mine],
            discussion=render(thread, p.persona_id),
            public_prob=mine[-1]["prob"] if mine else None))
    return SwarmResult(agents=agents, native_prob=None,
                       meta={"runner": "panel", "rounds": rounds, "silent": silent, "thread": thread})
