"""What the runner hands a discussion and what it gets back.

The panel's own discussion (``swarmz.panel``) takes a SwarmRequest and returns a SwarmResult.
The shapes date from the MiroFish simulation, which is no longer run by any arm; its bridge
(``swarmz.mirofish_bridge``) still speaks them, so a few fields here are only read there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class PersonaSpec:
    persona_id: str
    profile: str          # base_prompt + lessons block, ready to use as the agent's persona text
    name: str = ""        # public pseudonym the agent posts under (registry display_name)
    handle: str = ""
    prior_prob: float | None = None   # its own forecast, made alone before the discussion
    prior_rationale: str = ""
    reading: str = ""     # the part of the brief this agent was given; "" = the whole brief
    role: str = ""        # a job in the discussion, as the instruction text (a challenger); "" = none


@dataclass
class SwarmRequest:
    question_id: str
    question: str          # resolution criteria / question text
    brief_text: str        # frozen brief bytes, decoded
    freeze_ts: datetime
    graph_name: str        # q_<question_id>__<arm>__<run_id>
    personas: list[PersonaSpec] | None   # the panel always passes them; None only for the old MiroFish runner
    num_agents: int | None = None        # used when personas is None
    max_rounds: int = 10   # rounds of discussion after the opening forecasts
    seed: int = 0


@dataclass
class SwarmAgent:
    agent_id: int
    persona_id: str | None   # None for MiroFish-generated agents (A1)
    persona_text: str
    posts: list[str] = field(default_factory=list)
    discussion: str = ""      # the run's thread as this agent reads it, for elicitation
    memory_context: str = ""  # graph facts; only filled when SWARMZ_AGENT_GRAPH_MEMORY is on
    native_prob: float | None = None  # per-agent belief if MiroFish exposes one
    public_prob: float | None = None  # the last probability the agent stated in a post


@dataclass
class SwarmResult:
    agents: list[SwarmAgent]
    native_prob: float | None    # MiroFish's own swarm output (report agent + reducer)
    graph_name: str
    meta: dict = field(default_factory=dict)


class SwarmRunner(Protocol):
    def __call__(self, req: SwarmRequest) -> SwarmResult: ...
