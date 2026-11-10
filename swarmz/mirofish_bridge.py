"""run_swarm: drive the vendored MiroFish swarm for one question run (arms A1/A2).

Pipeline (mirrors vendor/mirofish/app/services/prediction/run_prediction.py::_run_swarm):
  backend for req.graph_name/req.freeze_ts -> seed graph from the frozen brief ->
  SimulationManager.create/prepare -> [A2: overwrite personas] ->
  every agent is told the question, the brief's deciding facts and to take a side in each
  post (commit_in_public); an agent that already forecast alone is told to open with that
  number, and an agent with its own reading or a job (arm A5) is told those ->
  SimulationRunner.start (OASIS subprocess, MIROFISH_SIM_PYTHON) -> poll ->
  gather posts -> the last number each agent posted (public_prob) -> the discussion thread
  per agent (swarmz.discussion, read from the simulation's own SQLite file) -> native_prob
  (stance extraction + ReportAgent structured report + reducer; any failure there -> None).

The brief's graph is built once per question and brief: the first swarm arm builds
it and keeps a copy (``q_<question>__brief__<hash>``), later arms copy that
instead of paying for the same extraction again (about $0.19 and 3.5 minutes).

Agent actions are no longer written into the graph (it cost about half of a run
and barely reached the final answer). SWARMZ_AGENT_GRAPH_MEMORY=1 turns that
back on and adds the retrieved facts (memory_context) to each agent.

vendor/mirofish is imported lazily inside the function, so ``import swarmz``
never needs MiroFish's dependencies.

Environment (read here, never required at import time):
  MIROFISH_SIM_PYTHON  python with camel-oasis (<3.12). Defaults to
                       ~/Desktop/projects/shoals-new/backend/.venv/bin/python if present.
  LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_NAME   MiroFish's OpenAI-format LLM. If unset
                       and ANTHROPIC_API_KEY is set, they default to Anthropic's
                       OpenAI-compatible endpoint with claude-haiku-4-5 (today's
                       original MiroFish setting).
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from . import usage
from .bridge import SwarmAgent, SwarmRequest, SwarmResult

logger = logging.getLogger("swarmz.mirofish_bridge")

VENDOR_DIR = Path(__file__).resolve().parents[1] / "vendor" / "mirofish"
_DEFAULT_SIM_PYTHON = Path.home() / "Desktop/projects/shoals-new/backend/.venv/bin/python"
MEMORY_CONTEXT_CHARS = 2000

MIN_SIDED_SHARE = 0.5   # of the agents who spoke; below this MiroFish's own number is left empty

_TERMINAL_OK = {"completed"}
_TERMINAL_BAD = {"failed", "stopped"}


def _prepare_env() -> None:
    if not os.environ.get("MIROFISH_SIM_PYTHON") and _DEFAULT_SIM_PYTHON.exists():
        os.environ["MIROFISH_SIM_PYTHON"] = str(_DEFAULT_SIM_PYTHON)
    anth = os.environ.get("ANTHROPIC_API_KEY")
    if not os.environ.get("LLM_API_KEY") and anth:
        os.environ["LLM_API_KEY"] = anth
        os.environ.setdefault("LLM_BASE_URL", "https://api.anthropic.com/v1/")
        os.environ.setdefault("LLM_MODEL_NAME", "claude-haiku-4-5")


def ensure_vendor_path() -> None:
    p = str(VENDOR_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)


def _status_str(run_state) -> str:
    raw = getattr(run_state, "runner_status", None)
    value = getattr(raw, "value", raw)
    return str(value or "").lower()


def memory_context(backend, agent_id: int, question: str,
                   max_chars: int = MEMORY_CONTEXT_CHARS) -> str:
    """Short text of facts from the agent's own group, then the shared seed graph."""
    from .memory import SHARED_GROUP, agent_group

    lines: list[str] = []
    seen: set[str] = set()
    for groups, limit, tag in (([agent_group(agent_id)], 10, "own"), ([SHARED_GROUP], 6, "brief")):
        try:
            res = backend.search(question, group_ids=groups, limit=limit, scope="edges")
        except Exception as e:  # memory is best-effort context, never fatal
            logger.warning(f"memory search failed for agent {agent_id} ({tag}): {e}")
            continue
        for f in res.facts:
            text = (f.fact or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            lines.append(f"- [{tag}] {text}")
    out = ""
    for line in lines:
        if len(out) + len(line) + 1 > max_chars:
            break
        out += line + "\n"
    return out.rstrip()


def every_round_live(sim_dir: str) -> int:
    """Make every round a working hour for every agent. Returns how many agents were changed.

    MiroFish simulates a day: the clock starts at midnight, one round is one hour, and agents
    are only active during their waking hours. swarmz caps a run at a handful of rounds, so
    eight of ten rounds fell between midnight and 8am and nobody spoke (found 2026-11-10; a
    "ten round" discussion was two rounds). A forecasting panel has no time of day, so the
    daily rhythm is switched off; agents still differ in how often they speak (activity_level).
    """
    import json

    path = Path(sim_dir) / "simulation_config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    tc = config.setdefault("time_config", {})
    tc["peak_hours"], tc["off_peak_hours"] = [], []
    hours, n = list(range(24)), 0
    for c in config.get("agent_configs", []):
        if c.get("active_hours") != hours:
            c["active_hours"] = hours
            n += 1
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return n


def _wait_monitor_done(runner_cls, sid: str, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while sid in getattr(runner_cls, "_processes", {}) and time.monotonic() < deadline:
        time.sleep(0.5)


def brief_graph_name(question_id: str, question: str, brief_text: str) -> str:
    """Where the graph built from one question's brief is kept for the question's other arms."""
    safe = re.sub(r"[^A-Za-z0-9_]", "_", question_id)
    sha = hashlib.sha256(f"{question}\0{brief_text}".encode("utf-8")).hexdigest()[:12]
    return f"q_{safe}__brief__{sha}"


def _copy_graph(src: str, dest: str) -> bool:
    """Best effort: sharing the brief's graph saves money, it must never fail a run."""
    from .memory import falkor

    try:
        return falkor.copy_graph(src, dest)
    except Exception as e:
        logger.warning(f"could not copy graph {src} to {dest}: {e}")
        return False


def stance_counts(predictions: list[dict]) -> dict[str, int]:
    counts = {"yes": 0, "no": 0, "uncertain": 0}
    for p in predictions:
        side = p.get("predicted_outcome")
        counts[side if side in ("yes", "no") else "uncertain"] += 1
    return counts


def enough_stances(counts: dict[str, int], n_speaking: int) -> bool:
    """MiroFish's number is the YES share of the agents who took a side. With few of them it is
    noise: one NO among seven undecided agents came out as 0.000 (2026-11-10)."""
    return counts["yes"] + counts["no"] >= max(2, math.ceil(MIN_SIDED_SHARE * n_speaking))


def _native_prob(agents_payload: list[dict], question: str, graph_id: str, sid: str):
    """(MiroFish's YES probability or None, the per-agent stances it was built from)."""
    from app.services.prediction.agent_prediction_extractor import extract_agent_predictions
    from app.services.prediction.swarm_reducer import reduce_to_yes_prob
    from app.services.report_agent import ReportAgent
    from app.utils.llm_client import LLMClient

    predictions = extract_agent_predictions(agents_payload, question, LLMClient())
    if not enough_stances(stance_counts(predictions), len(agents_payload)):
        return None, predictions
    report = ReportAgent(graph_id, sid, question)
    structured = report.generate_structured_report(predictions, topic_tags=None)
    return reduce_to_yes_prob(structured.to_dict()), predictions


PUBLIC_STANCE = (
    "THE QUESTION THIS PANEL IS FORECASTING\n{question}\n\n"
    "{facts}{prior}{role}"
    "HOW YOU POST\n"
    "Every post and comment you write takes a side. Begin it with YES or NO and your probability "
    "that the question resolves YES, like \"NO, 30%:\" or \"YES, 65%:\". Then give the one fact that "
    "moves you most, in a sentence or two. Give your honest number: do not park at 50% to stay "
    "safe and do not inflate it to sound sure. If you are close to 50%, say which way you lean "
    "and what would tip you. When a post changes your mind, post your new number and say what "
    "changed it. Never write a post that only describes how one would go about answering."
)
FACTS_SHARED = "KEY FACTS FROM THE PANEL'S RESEARCH BRIEF\n{facts}\n\n"
FACTS_OWN = ("WHAT YOU HAVE READ\nYou were given part of the panel's research; other members were given other "
             "parts. Share what yours says and ask for what you lack.\n{facts}\n\n")
PRIOR = ("YOUR FORECAST BEFORE THE DISCUSSION\n"
         "You read the research alone and committed to this: {stated}. Your reason: {why}\n"
         "Your first post states this number. Move from it only when a post gives you a fact or an "
         "argument you had not weighed, and say which one.\n\n")
ROLE = "YOUR JOB ON THIS PANEL\n{role} {note}\n\n"
ROLE_NOTE = ("Your job decides what you argue for, not your number: the probability you post is still "
             "your honest one.")

_STATED = re.compile(r"\b(YES|NO)\b[\s,:\-]*?(\d{1,3}(?:\.\d+)?)\s*%", re.I)


def stated(prob: float) -> str:
    """A probability the way agents are told to post it: "NO, 30%"."""
    return f"{'YES' if prob >= 0.5 else 'NO'}, {round(prob * 100)}%"


def public_prob(posts: list[str]) -> float | None:
    """The last probability an agent stated in public ("NO, 30%:"), or None if it never did.
    The number is always the chance of YES, whichever side the agent named."""
    for text in reversed(posts or []):
        found = _STATED.findall(text or "")
        if found:
            pct = float(found[-1][1])
            if 0 <= pct <= 100:
                return pct / 100
    return None


def commit_in_public(sim_dir: str, question: str, brief_text: str, platform: str = "reddit",
                     seats: dict[int, Any] | None = None) -> int:
    """Tell every simulated agent the question, the brief's key facts, and to take a side in
    each post. Returns how many agents were told.

    An agent in the simulation is shown only its persona and the feed: not the question, not the
    brief. In the first six questions (2026-11-10) agents therefore traded remarks on method
    ("I'd check the current price first") and at most 2 of 20 ever said YES or NO, so MiroFish's
    vote share was empty every time. The persona is the one text every agent reads on every
    turn, so the instruction goes there. The persona as it was is kept as ``persona_plain``:
    that is what the run reports and what the private forecast is asked with.

    ``seats`` maps an agent's id to its PersonaSpec. An agent whose spec has a forecast made
    before the discussion is told to open with it; one with its own reading is shown that
    instead of the shared facts; one with a job on the panel is told the job.
    """
    import json

    from .briefparts import digest

    path = Path(sim_dir) / f"{platform}_profiles.json"
    if not path.exists():
        logger.warning(f"{path.name} not found: agents were not told to take a side")
        return 0
    shared = digest(brief_text)
    profiles = json.loads(path.read_text(encoding="utf-8"))
    for i, prof in enumerate(profiles):
        seat = (seats or {}).get(int(prof.get("user_id", i)))
        own = digest(seat.reading) if seat is not None and seat.reading else ""
        facts = (FACTS_OWN.format(facts=own) if own else
                 FACTS_SHARED.format(facts=shared) if shared else "")
        prior = role = ""
        if seat is not None and seat.prior_prob is not None:
            prior = PRIOR.format(stated=stated(seat.prior_prob),
                                 why=" ".join((seat.prior_rationale or "not recorded.").split())[:500])
        if seat is not None and seat.role:
            role = ROLE.format(role=seat.role, note=ROLE_NOTE)
        block = PUBLIC_STANCE.format(question=question.strip(), facts=facts, prior=prior, role=role)
        plain = prof.get("persona_plain", prof.get("persona") or "")
        prof["persona_plain"] = plain
        prof["persona"] = f"{plain}\n\n{block}"
    path.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(profiles)


def run_swarm(req: SwarmRequest, *, backend: Any = None, platform: str = "reddit",
              poll_interval: float = 10, poll_timeout: float = 3600,
              keep_backend_open: bool = False) -> SwarmResult:
    _prepare_env()
    ensure_vendor_path()

    from app import memory_compat
    from app.services.persona_injection import apply_personas
    from app.services.prediction.graph_from_text import build_graph_from_text
    from app.services.simulation_manager import SimulationManager, SimulationStatus
    from app.services.simulation_runner import SimulationRunner
    from app.services.zep_graph_memory_updater import ZepGraphMemoryManager

    from .memory import make_backend

    from .config import settings
    graph_memory = settings().agent_graph_memory
    graph_id = req.graph_name
    # Share the brief's graph between this question's arms. Not when agents write into the graph:
    # those writes need the ontology, which only a fresh build puts on the backend.
    kept_name = brief_graph_name(req.question_id, req.question, req.brief_text)
    share = backend is None and not graph_memory
    copied = False
    if backend is None:
        copied = share and _copy_graph(kept_name, graph_id)   # before the backend creates graph_id
        backend = make_backend(req.graph_name, req.freeze_ts)
    memory_compat.register_backend(graph_id, backend)
    meta: dict[str, Any] = {"platform": platform, "seed": req.seed, "max_rounds": req.max_rounds,
                            "arm_personas": req.personas is not None}
    t0 = time.monotonic()
    sid = None
    try:
        # 1. seed graph from the frozen brief (shared group, reference time < freeze)
        if copied:
            meta["brief_graph"] = f"copied from {kept_name}"
        else:
            build_graph_from_text(req.brief_text, req.question, name=req.graph_name, graph_id=graph_id)
            meta["brief_graph"] = "built"
            if share and _copy_graph(graph_id, kept_name):
                meta["brief_graph"] = f"built, kept as {kept_name}"
        meta["seed_built_s"] = round(time.monotonic() - t0, 1)

        # 2. simulation + profiles
        manager = SimulationManager()
        state = manager.create_simulation(project_id=None, graph_id=graph_id,
                                          enable_twitter=(platform == "twitter"),
                                          enable_reddit=(platform == "reddit"))
        sid = state.simulation_id
        meta["simulation_id"] = sid
        n_agents = len(req.personas) if req.personas else req.num_agents
        prepared = manager.prepare_simulation(
            sid, simulation_requirement=req.question, document_text=req.brief_text,
            num_agents=n_agents, use_llm_for_profiles=req.personas is None,
        )
        if getattr(prepared, "status", None) == SimulationStatus.FAILED:
            raise RuntimeError(f"prepare_simulation failed: {getattr(prepared, 'error', '')}")

        # 3. A2: exactly the given personas
        persona_by_agent: dict[int, str] = {}
        if req.personas:
            sim_dir = manager._get_simulation_dir(sid)
            persona_by_agent = apply_personas(
                sim_dir, [(p.persona_id, p.profile, p.name, p.handle) for p in req.personas])
            # the seed posts were drafted as the brief's entities; re-voice them as the personas
            from .llm import default_llm
            from .seedposts import rewrite_seed_posts
            meta["seed_posts_rewritten"] = rewrite_seed_posts(
                sim_dir, {i: (p.name or f"Agent {i}", p.profile) for i, p in enumerate(req.personas)},
                default_llm(), settings().agent_model, question=req.question)
        meta["persona_map"] = {str(k): v for k, v in persona_by_agent.items()}
        meta["agents_made_live_every_round"] = every_round_live(manager._get_simulation_dir(sid))
        spec_by_id = {p.persona_id: p for p in req.personas or []}
        seats = {aid: spec_by_id[pid] for aid, pid in persona_by_agent.items() if pid in spec_by_id}
        meta["agents_told_to_take_a_side"] = commit_in_public(
            manager._get_simulation_dir(sid), req.question, req.brief_text, platform, seats)
        meta["agents_opening_with_their_own_forecast"] = sum(p.prior_prob is not None for p in seats.values())
        meta["agents_with_a_job"] = sum(bool(p.role) for p in seats.values())

        # 4. run OASIS. Agents read each other through the simulation's own feed; writing their
        # actions into the graph as well is off unless SWARMZ_AGENT_GRAPH_MEMORY is set.
        # the OASIS subprocess logs its own LLM calls here (see swarmz/usage_hook)
        meta["agent_graph_memory"] = graph_memory
        usage_file = Path(manager._get_simulation_dir(sid)) / "usage.jsonl"
        with usage.child_env(usage_file):
            SimulationRunner.start_simulation(sid, platform=platform, max_rounds=req.max_rounds,
                                              enable_graph_memory_update=graph_memory, graph_id=graph_id)
        try:
            deadline = time.monotonic() + poll_timeout
            while True:
                status = _status_str(SimulationRunner.get_run_state(sid))
                if status in _TERMINAL_OK:
                    break
                if status in _TERMINAL_BAD:
                    raise RuntimeError(f"simulation ended {status}")
                if time.monotonic() > deadline:
                    raise TimeoutError("simulation poll timeout")
                time.sleep(poll_interval)
        except Exception:
            try:
                SimulationRunner.stop_simulation(sid)
            except Exception as stop_err:
                logger.error(f"failed to stop simulation {sid}: {stop_err}")
            raise
        # The OASIS script stays alive in "wait for commands" mode after the last
        # round; close it so the monitor thread finishes reading the log, then make
        # sure the last round has been written to memory before reading it.
        try:
            SimulationRunner.close_simulation_env(sid)
        except Exception as e:
            logger.warning(f"close_simulation_env({sid}) failed: {e}")
        _wait_monitor_done(SimulationRunner, sid)
        if sid in getattr(SimulationRunner, "_processes", {}):
            try:
                SimulationRunner.stop_simulation(sid)
            except Exception as e:
                logger.warning(f"stop_simulation({sid}) failed: {e}")
        ZepGraphMemoryManager.stop_updater(sid)
        meta["simulation_s"] = round(time.monotonic() - t0, 1)
        meta["simulation_llm_calls"] = usage.merge_file(usage_file)

        # 5. posts, personas and the discussion thread per agent
        from app.services.prediction.agent_posts import gather_agents

        from . import discussion

        posted = {a["agent_id"]: a for a in gather_agents(sid, platform=platform)}
        profiles = manager.get_profiles(sid, platform=platform) or []
        thread = discussion.load(Path(manager._get_simulation_dir(sid)) / f"{platform}_simulation.db")
        names = {int(p.get("user_id")): p.get("name") or p.get("username") or "" for p in profiles}
        meta["discussion"] = {"posts": len(thread), "replies": sum(len(t.comments) for t in thread)}
        # everything an agent wrote, replies included: most of a discussion is replies, and on
        # the first run that read posts only, 12 of 20 agents had "no posted number"
        said = discussion.said(thread)
        agents: list[SwarmAgent] = []
        for p in profiles:
            aid = int(p.get("user_id"))
            agents.append(SwarmAgent(
                agent_id=aid,
                persona_id=persona_by_agent.get(aid),
                persona_text=p.get("persona_plain") or p.get("persona") or "",
                posts=list(posted.get(aid, {}).get("texts", [])),
                public_prob=public_prob(said.get(aid) or list(posted.get(aid, {}).get("texts", []))),
                discussion=discussion.render(thread, aid, names),
                memory_context=memory_context(backend, aid, req.question) if graph_memory else "",
            ))

        # 6. MiroFish's own swarm probability (failures -> None). Stances are read from
        # everything an agent wrote, replies included: most of a discussion is replies.
        native = None
        payload = [{"agent_id": a.agent_id, "persona": a.persona_text, "texts": said.get(a.agent_id) or a.posts}
                   for a in agents if said.get(a.agent_id) or a.posts]
        if payload:
            try:
                native, predictions = _native_prob(payload, req.question, graph_id, sid)
                counts = stance_counts(predictions)
                meta["n_predictions"], meta["native_stances"] = len(predictions), counts
                if not predictions:
                    logger.warning(f"MiroFish extracted 0 agent predictions from {len(payload)} posting agents")
                if native is None:
                    meta["native_error"] = (f"only {counts['yes'] + counts['no']} of {len(payload)} agents who spoke "
                                            f"took a YES or NO side")
            except Exception as e:
                logger.error(f"native prob failed for {req.question_id}: {e}")
                meta["native_error"] = str(e)
        else:
            meta["native_error"] = "no agent posts"
        meta["wall_clock_s"] = round(time.monotonic() - t0, 1)
        return SwarmResult(agents=agents, native_prob=native, graph_name=graph_id, meta=meta)
    finally:
        if sid is not None:
            try:
                ZepGraphMemoryManager.stop_updater(sid)
            except Exception:
                pass
        if not keep_backend_open:
            memory_compat.unregister_backend(graph_id)
            try:
                backend.close()
            except Exception:
                pass
