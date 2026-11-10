"""The arms a question can be run on.

PANEL is the model: ten registry personas, each with its own forecasting method and its own
lessons. Each reads part of the brief (swarmz.diversity) and commits to a forecast alone. Then the
panel discusses (swarmz.panel), each member opening with its own number and two of them told to
argue against whichever view is leading, and afterwards each gives a private final forecast. The forecasts made before the discussion are kept (swarm_stages,
aggregate "prior") and scored beside the final ones, so a discussion that only pulls everyone to
the middle shows up as a worse score, not as agreement.

A0 is the yardstick: one plain model call on the whole brief. A3 (the same personas forecasting
alone on the whole brief) and A4 (one call with lessons and calibration) are still defined for
comparisons but nothing runs them by default.

Every arm on a question receives the identical brief bytes (checked against the question's
brief_sha256). Each run writes one forecast row per agent and one aggregate row per method.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from datetime import datetime

from ..bridge import PersonaSpec, SwarmRequest, SwarmResult, SwarmRunner
from .. import usage
from ..capture import aggregate as agg
from ..capture.elicit import elicit
from ..scoring.brier import score_question
from ..capture.personas import build_profile
from ..config import settings
from ..diversity import CHALLENGER, CHALLENGER_AFTER
from ..llm import LLM, default_llm
from ..registry import asof, store
from ..timeutil import now


@dataclass(frozen=True)
class ArmSpec:
    name: str
    kind: str                 # single | swarm | independent
    population: str           # registry population the agents come from
    lessons: bool
    weighting: bool
    calibration: bool
    learns: bool              # its own scores feed lessons / calibration / selection
    learn_from: str | None = None  # read track records + calibration of another arm
    elite_only: bool = False
    forecast_first: bool = False   # agents commit to a forecast alone, then open the discussion with it
    diverse: bool = False          # each agent reads its own part of the brief


ARMS: dict[str, ArmSpec] = {
    "A0": ArmSpec("A0", "single", "A0", lessons=False, weighting=False, calibration=False, learns=False),
    "PANEL": ArmSpec("PANEL", "swarm", "PANEL", lessons=True, weighting=True, calibration=True, learns=True,
                     forecast_first=True, diverse=True),
    "A3": ArmSpec("A3", "independent", "A3", lessons=True, weighting=True, calibration=True, learns=True),
    "A4": ArmSpec("A4", "single", "A4", lessons=True, weighting=False, calibration=True, learns=True),
}
LEARNING_ARMS = [a for a, s in ARMS.items() if s.learns]


@dataclass
class AgentForecast:
    persona_id: str
    prob: float
    rationale: str


@dataclass
class AgentStage:
    """One swarm agent on the way to its forecast: alone before the discussion, and in public."""
    persona_id: str
    prior_prob: float | None
    prior_rationale: str
    public_prob: float | None


@dataclass
class RunOutcome:
    run_id: str
    arm: str
    question_id: str
    n_forecasts: int
    aggregates: dict
    status: str
    error: str | None = None
    cost_usd: float | None = None
    spread: dict | None = None


def run_arm(con, question_id: str, arm: str, *, llm: LLM | None = None,
            swarm: SwarmRunner | None = None, seed: int = 0, started_at: datetime | None = None,
            workers: int = 8) -> RunOutcome:
    spec = ARMS[arm]
    s = settings()
    llm = llm or default_llm()
    q = store.get_question(con, question_id)
    if q is None:
        raise KeyError(f"unknown question {question_id}")
    brief = store.read_brief(q).decode("utf-8")   # raises if bytes differ from brief_sha256
    freeze = q["freeze_ts"]
    started_at = started_at or now()
    run_id = store.new_run_id(question_id, arm, seed, started_at)
    lcfg = s.learning
    generation = store.current_generation(con, spec.population)
    config = {"arm": asdict(spec), "learning": lcfg, "population_size": s.population_size,
              "discussion_rounds": s.discussion_rounds, "challengers": s.challengers}
    models = {"agent": s.agent_model, "elicit": s.elicit_model}
    store.start_run(con, run_id=run_id, question_id=question_id, arm=arm, generation=generation, seed=seed,
                    config=config, model_versions=models, brief_sha256=q["brief_sha256"], started_at=started_at)
    question = q["resolution_criteria"] or q["question_text"]
    with usage.track() as u:
        out = _run_tracked(con, spec, q, question, brief, run_id, llm, swarm, seed, workers, freeze, lcfg)
    out.cost_usd = usage.save(con, u, context="run", run_id=run_id, question_id=question_id, arm=arm)
    return out


def _run_tracked(con, spec, q, question, brief, run_id, llm, swarm, seed, workers, freeze, lcfg) -> RunOutcome:
    arm, question_id = spec.name, q["question_id"]
    try:
        forecasts, native, stages = _ARM_IMPL[spec.kind](con, spec, q, question, brief, run_id, llm, swarm, seed, workers)
        if not forecasts:
            raise RuntimeError("no agent forecasts captured")
        lo, hi = lcfg["probability"]["clip_low"], lcfg["probability"]["clip_high"]
        t = now()
        for f in forecasts:
            f.prob = agg.clip(f.prob, lo, hi)
            store.write_forecast(con, run_id=run_id, question_id=question_id, persona_id=f.persona_id,
                                 prob=f.prob, rationale=f.rationale, elicited_at=t)
        aggs = compute_aggregates(con, spec, freeze, forecasts, native)
        for s in stages:   # what each swarm agent said before the discussion and in it
            store.write_stage(con, run_id=run_id, question_id=question_id, persona_id=s.persona_id,
                              prior_prob=None if s.prior_prob is None else agg.clip(s.prior_prob, lo, hi),
                              prior_rationale=s.prior_rationale, public_prob=s.public_prob, at=t)
        priors = [agg.clip(s.prior_prob, lo, hi) for s in stages if s.prior_prob is not None]
        public = [agg.clip(s.public_prob, lo, hi) for s in stages if s.public_prob is not None]
        if spec.kind == "swarm":
            aggs["prior"] = agg.equal(priors) if priors else None
            # the panel's public number needs most of the panel to have stated one
            aggs["public"] = agg.equal(public) if public and 2 * len(public) >= len(forecasts) else None
        for method, p in aggs.items():
            store.write_aggregate(con, run_id=run_id, question_id=question_id, method=method,
                                  prob=None if p is None else agg.clip(p, lo, hi) if method != "native" else p,
                                  computed_at=t)
        store.finish_run(con, run_id, "complete")
        if q["status"] == "resolved":   # the outcome was already known: mark the run late, never score it
            score_question(con, question_id)
        return RunOutcome(run_id, arm, question_id, len(forecasts), aggs, "complete",
                          spread=agg.spread([f.prob for f in forecasts], priors))
    except Exception as e:
        store.finish_run(con, run_id, "failed", str(e)[:2000])
        return RunOutcome(run_id, arm, question_id, 0, {}, "failed", str(e))


def compute_aggregates(con, spec: ArmSpec, freeze_ts, forecasts: list[AgentForecast],
                       native: float | None) -> dict[str, float | None]:
    lcfg = settings().learning
    probs = [f.prob for f in forecasts]
    eq = agg.equal(probs)
    source_arm = spec.learn_from or spec.name
    if spec.weighting:
        records = asof.track_records(con, source_arm, freeze_ts)
        w = agg.track_record_weights([f.persona_id for f in forecasts], records,
                                     lcfg["weighting"]["min_n"], lcfg["weighting"]["k"])
        weighted = agg.weighted(probs, w, lcfg["weighting"]["extremize_a"])
    else:
        weighted = eq
    calibrated = weighted
    if spec.calibration:
        fit = asof.calibration_fit(con, source_arm, lcfg["calibration"]["source_method"], freeze_ts)
        if fit:
            calibrated = agg.apply_platt(weighted, fit["params"])
    return {"equal": eq, "weighted": weighted, "calibrated": calibrated, "native": native}


# ---------------------------------------------------------------- arm kinds

def _personas(con, spec: ArmSpec, freeze_ts) -> list[tuple[dict, str]]:
    from ..registry.names import ensure_names
    ensure_names(con)
    ps = store.active_personas(con, spec.population, elite_only=spec.elite_only)
    if spec.kind != "single":
        ps = [p for p in ps if p["status"] != "system"]
    if not ps:
        raise RuntimeError(f"no active personas for population {spec.population}; run `swarmz personas seed`")
    return [(p, build_profile(con, p, freeze_ts, spec.lessons)[0]) for p in ps]


def _elicit_many(llm, jobs: list[tuple[str, str, str, str]], question: str, workers: int) -> list[AgentForecast]:
    """jobs: (persona_id, profile, context, context_label)."""
    model = settings().elicit_model

    def one(job):
        pid, profile, ctx, label = job
        e = elicit(llm, model=model, persona=profile, question=question, context=ctx, context_label=label)
        return AgentForecast(pid, e.prob, e.rationale)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        return list(ex.map(one, jobs))


def _single(con, spec, q, question, brief, run_id, llm, swarm, seed, workers):
    (persona, profile), = _personas(con, spec, q["freeze_ts"])[:1]
    f = _elicit_many(llm, [(persona["persona_id"], profile, brief, "RESEARCH BRIEF")], question, 1)
    return f, f[0].prob, []  # native = the plain single-LLM forecast


def _independent(con, spec, q, question, brief, run_id, llm, swarm, seed, workers):
    jobs = [(p["persona_id"], prof, brief, "RESEARCH BRIEF") for p, prof in _personas(con, spec, q["freeze_ts"])]
    return _elicit_many(llm, jobs, question, workers), None, []


def _swarm(con, spec, q, question, brief, run_id, llm, swarm, seed, workers):
    if swarm is None:   # the panel's own discussion; tests pass a stand-in
        from .. import panel

        def swarm(req):
            return panel.run(req, llm=llm, workers=workers)
    s = settings()
    personas = _personas(con, spec, q["freeze_ts"])
    seats, priors = {}, {}
    if spec.diverse:
        from ..diversity import assign
        seats = assign(brief, [p["persona_id"] for p, _ in personas], q["question_id"], s.challengers)
    read = {p["persona_id"]: seats[p["persona_id"]].brief if seats else brief for p, _ in personas}
    if spec.forecast_first:   # alone, on what each has read, before anyone speaks
        alone = _elicit_many(llm, [(p["persona_id"], prof, read[p["persona_id"]], "RESEARCH BRIEF")
                                   for p, prof in personas], question, workers)
        priors = {f.persona_id: f for f in alone}
    req = SwarmRequest(
        question_id=q["question_id"], question=question, brief_text=brief, freeze_ts=q["freeze_ts"],
        personas=[PersonaSpec(
            p["persona_id"], prof, p.get("display_name") or "", p.get("handle") or "",
            prior_prob=priors[p["persona_id"]].prob if priors else None,
            prior_rationale=priors[p["persona_id"]].rationale if priors else "",
            reading=seats[p["persona_id"]].brief if seats else "",
            role=CHALLENGER if seats and seats[p["persona_id"]].challenger else "")
            for p, prof in personas],
        max_rounds=s.discussion_rounds, seed=seed,
    )
    result = swarm(req)
    # keep the thread and who read what with the run: for debugging, and for the app to replay
    extra = {"swarm_meta": result.meta}
    if seats:
        extra["seats"] = {pid: {"lenses": list(st.lenses), "challenger": st.challenger} for pid, st in seats.items()}
    con.execute("UPDATE runs SET config_json = json_merge_patch(config_json, ?) WHERE run_id = ?",
                [json.dumps(extra, default=str), run_id])
    by_pid = {a.persona_id: a for a in result.agents if a.persona_id}
    jobs, stages = [], []
    for p, prof in personas:   # every member gives a final forecast, even one that stayed silent
        pid = p["persona_id"]
        a, prior = by_pid.get(pid), priors.get(pid)
        jobs.append((pid, prof, _swarm_context(read[pid], a, prior, bool(seats) and seats[pid].challenger),
                     "RESEARCH BRIEF"))
        stages.append(AgentStage(pid, prior.prob if prior else None, prior.rationale if prior else "",
                                 a.public_prob if a else None))
    return _elicit_many(llm, jobs, question, workers), result.native_prob, stages


def _swarm_context(brief: str, a, prior: AgentForecast | None = None, challenger: bool = False) -> str:
    """What a swarm agent sees when asked for its probability: the research it read before the
    discussion (its own part of the brief, or the whole brief), the forecast it committed to then,
    and the discussion. Without the brief (the first five questions, 2026-11-10) swarm agents
    missed facts the other arms had, so the arms differed in information, not just in whether
    agents talked."""
    talk = _agent_context(a)
    before = ""
    if prior is not None:
        before = (f"\n\nYOUR FORECAST BEFORE THE DISCUSSION: {prior.prob:.2f}. Your reason then: {prior.rationale}\n"
                  "Keep it, or move it if the discussion gave you a fact or an argument you had not weighed.")
    after = f"\n\n{CHALLENGER_AFTER}" if challenger else ""
    return (f"{brief}{before}\n\nTHE PANEL'S DISCUSSION (entries marked \"(you)\" are yours):\n"
            f"{talk or '(there was no discussion)'}{after}")


def _agent_context(a) -> str:
    if a is None:
        return ""
    parts = []
    if a.discussion:
        parts.append(a.discussion)
    elif a.posts:  # a swarm runner that returns no thread: at least the agent's own posts
        parts.append("What you posted:\n" + "\n".join(f"- {p}" for p in a.posts[-15:]))
    return "\n\n".join(parts)


_ARM_IMPL = {"single": _single, "independent": _independent, "swarm": _swarm}
