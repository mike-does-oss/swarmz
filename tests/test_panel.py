"""The panel: ten methods, each member reads part of the brief, forecasts alone, then the panel
discusses and each gives a final forecast. Spread, baselines and extremizing as outputs."""

from datetime import datetime, timedelta

from swarmz import panel as panel_mod
from swarmz.arms.runner import ARMS, LEARNING_ARMS, run_arm
from swarmz.bridge import PersonaSpec, SwarmAgent, SwarmRequest, SwarmResult
from swarmz.briefparts import LENSES, brief_slice, sections
from swarmz.capture import aggregate as agg
from swarmz.diversity import assign
from swarmz.registry import store
from swarmz.registry.db import rows
from swarmz.scoring import report
from swarmz.scoring.brier import score_question
from tests.conftest import BRIEF, T0, add_q

TEXT = "\n\n".join(f"## {t}\n\n- about {t.lower()}" for t in (
    "QUESTION & RESOLUTION CRITERIA", "AS-OF", "ENTITIES & RELATIONSHIPS", "CONFIRMED FACTS",
    "REPORTED BUT UNCONFIRMED", "CONFLICTING REPORTS", "QUANTITATIVE ANCHORS",
    "STAKEHOLDER POSITIONS & INCENTIVES", "RECENT TIMELINE", "OPEN UNCERTAINTIES", "AUDIT"))


def test_a_slice_is_the_question_plus_two_parts_of_the_evidence():
    assert [t for t, _ in sections(TEXT)][:2] == ["QUESTION & RESOLUTION CRITERIA", "AS-OF"]
    got = [t for t, _ in sections(brief_slice(TEXT, ("numbers", "doubts")))]
    assert got == ["QUESTION & RESOLUTION CRITERIA", "AS-OF", "CONFLICTING REPORTS", "QUANTITATIVE ANCHORS",
                   "RECENT TIMELINE", "OPEN UNCERTAINTIES"]
    assert brief_slice("free text, no headings", ("numbers",)) == "free text, no headings"


def test_seats_spread_the_evidence_over_the_panel_and_give_nobody_a_job():
    ids = [f"p{i}" for i in range(10)]
    seats = assign(TEXT, ids, "Q1")
    assert assign(TEXT, ids, "Q1") == seats                       # same question, same seating
    assert any(assign(TEXT, ids, q)["p0"] != seats["p0"] for q in ("Q2", "Q3", "Q4"))
    for lens in LENSES:                                            # every part of the evidence is held by several
        assert 4 <= sum(lens in s.lenses for s in seats.values()) <= 6
    assert len({s.lenses for s in seats.values()}) == 6            # and no two neighbours read the same pair
    one = seats["p0"]
    assert "QUESTION & RESOLUTION CRITERIA" in one.brief and len(one.lenses) == 2
    assert not any(s.challenger for s in seats.values())            # none unless asked for
    two = assign(TEXT, ids, "Q1", challengers=2)
    against = [i for i, p in enumerate(ids) if two[p].challenger]
    assert len(against) == 2 and against[1] - against[0] == 5      # spread round the table
    assert two[ids[against[0]]].lenses != two[ids[against[1]]].lenses
    assert len({tuple(p for p in ids if assign(TEXT, ids, q, 2)[p].challenger) for q in "abcdefgh"}) > 1
    assert sum(s.challenger for s in assign(TEXT, ids[:1], "Q1", 2).values()) == 1


def test_personas_are_methods_with_no_job_title():
    assert len(store.METHODS) == 10 and len(set(store.METHODS)) == 10
    prompts = [store.method_prompt(i) for i in range(10)]
    assert len(set(prompts)) == 10 and store.method_prompt(10) == prompts[0]
    for p in prompts:
        assert "Your method:" in p and "You are a" not in p
    assert set(ARMS) == {"A0", "PANEL", "A3", "A4"} and "PANEL" in LEARNING_ARMS
    spec = ARMS["PANEL"]
    assert spec.forecast_first and spec.diverse and spec.lessons and spec.population == "PANEL"


class Panel:
    """A swarm stand-in that records what it was asked and has every agent post a number."""

    def __init__(self):
        self.req = None

    def __call__(self, req):
        self.req = req
        agents = [SwarmAgent(i, p.persona_id, p.profile, posts=[f"YES, {55 + i}%: because."],
                             discussion="[Post 1] Wary Lynx: NO, 30%: two holdouts remain.",
                             public_prob=(55 + i) / 100)
                  for i, p in enumerate(req.personas)]
        return SwarmResult(agents=agents, native_prob=0.9)


def test_members_forecast_alone_first_and_open_with_that_number(con, llm):
    store.seed_population(con, 6, "m")
    add_q(con, "Q1", T0)
    asked = []
    orig = llm.json
    llm.json = lambda s, u, **k: (asked.append(u), orig(s, u, **k))[1]
    panel = Panel()
    out = run_arm(con, "Q1", "PANEL", llm=llm, swarm=panel, started_at=T0, workers=1)
    assert out.status == "complete", out.error
    # each persona was asked alone before the discussion, and the discussion was handed those numbers
    assert len(asked) == 12 and not any("THE PANEL'S DISCUSSION" in u for u in asked[:6])
    assert all(p.prior_prob is not None and p.prior_rationale for p in panel.req.personas)
    against = [p for p in panel.req.personas if p.role]
    assert len(against) == 2 and all("argue against whichever view" in p.role for p in against)
    assert panel.req.max_rounds == 2                # the suite's setting; the default is 5
    # the fixture brief has no fixed headings, so there is nothing to split: every member read it whole
    whole = BRIEF.read_text()
    assert all(p.reading == whole for p in panel.req.personas) and all(whole[:200] in u for u in asked)
    seats = __import__("json").loads(rows(con, "SELECT config_json FROM runs WHERE run_id = ?",
                                          [out.run_id])[0]["config_json"])["seats"]
    assert len(seats) == 6 and all(len(s["lenses"]) == 2 for s in seats.values())
    assert {pid for pid, s in seats.items() if s["challenger"]} == {p.persona_id for p in against}
    # afterwards each is reminded of its own number and reads the discussion
    assert all("YOUR FORECAST BEFORE THE DISCUSSION: 0." in u and "two holdouts remain" in u for u in asked[6:])
    # a challenger made its first forecast before it had the job, and is released from it at the end
    assert not any("That job is over" in u for u in asked[:6])
    assert sum("That job is over" in u for u in asked[6:]) == 2
    stages = rows(con, "SELECT * FROM swarm_stages WHERE run_id = ? ORDER BY persona_id", [out.run_id])
    assert len(stages) == 6 and all(s["prior_prob"] is not None and s["public_prob"] is not None for s in stages)
    aggs = {r["method"]: r["prob"] for r in rows(con, "SELECT method, prob FROM aggregates WHERE run_id = ?", [out.run_id])}
    assert abs(aggs["prior"] - agg.equal([s["prior_prob"] for s in stages])) < 1e-9
    assert abs(aggs["public"] - agg.equal([s["public_prob"] for s in stages])) < 1e-9
    assert out.spread["n"] == 6 and out.spread["prior_sd"] > 0 and out.spread["low"] <= out.spread["high"]


def _request(n=3, rounds=2, priors=True):
    return SwarmRequest(
        question_id="Q1", question="Will it happen?", brief_text="the whole brief", freeze_ts=T0,
        personas=[PersonaSpec(f"p{i}", f"persona {i}", f"Name {i}", prior_prob=0.2 + 0.3 * i if priors else None,
                              prior_rationale=f"reason {i}" if priors else "", reading=f"slice {i}")
                  for i in range(n)],
        max_rounds=rounds)


def test_the_discussion_opens_with_each_forecast_and_runs_in_rounds(llm):
    asked = []
    orig = llm.json
    llm.json = lambda s, u, **k: (asked.append((s, u)), orig(s, u, **k))[1]
    res = panel_mod.run(_request(), llm=llm, workers=1)
    thread = res.meta["thread"]
    # three opening posts at no cost, then two rounds of three posts
    assert len(asked) == 6 and all(s.startswith("You are a member of a forecasting panel") for s, _ in asked)
    assert [e["round"] for e in thread] == [0] * 3 + [1] * 3 + [2] * 3
    assert thread[0]["text"] == "reason 0" and thread[0]["prob"] == 0.2 and thread[0]["name"] == "Name 0"
    assert res.meta["runner"] == "panel" and res.meta["rounds"] == 2 and res.meta["silent"] == 0
    assert res.native_prob is None
    # in round 1 a member reads its own slice and the three openings, with its own marked
    first = asked[0][1]
    assert "slice 0" in first and "slice 1" not in first and "the whole brief" not in first
    assert "[Opening] Name 0 (you): NO, 20%. reason 0" in first and "[Opening] Name 2: YES, 80%. reason 2" in first
    assert "[Round 1]" not in first and "This is round 1 of 2" in first
    # in round 2 it also reads round 1, but nothing posted in round 2 itself
    assert asked[3][1].count("[Round 1]") == 3 and "[Round 2]" not in asked[5][1]
    a0 = res.agents[0]
    assert a0.persona_id == "p0" and len(a0.posts) == 3 and a0.public_prob == thread[6]["prob"]
    assert a0.discussion.count("(you)") == 3 and a0.discussion.count("\n") == 8
    assert not any("YOUR JOB IN THIS DISCUSSION" in u for _, u in asked)


def test_a_challenger_is_told_its_job_every_round_and_the_thread_does_not_name_it(llm):
    from swarmz.config import Settings
    from swarmz.diversity import CHALLENGER
    assert Settings.__dataclass_fields__["discussion_rounds"].default == 5
    assert Settings.__dataclass_fields__["challengers"].default == 2
    req = _request()
    req.personas[1].role = CHALLENGER
    asked = []
    orig = llm.json
    llm.json = lambda s, u, **k: (asked.append(u), orig(s, u, **k))[1]
    res = panel_mod.run(req, llm=llm, workers=1)
    told = [u for u in asked if "YOUR JOB IN THIS DISCUSSION" in u]
    assert len(told) == 2 and all("PERSONA:\npersona 1" in u and CHALLENGER in u for u in told)
    assert all("challenger" not in a.discussion.lower() for a in res.agents)
    assert all("challenger" not in str(e).lower() for e in res.meta["thread"])


def test_likes_are_kept_with_the_thread_and_never_shown_to_the_panel(llm):
    asked = []
    orig = llm.json

    def liking(s, u, **k):
        asked.append(u)
        out = orig(s, u, **k)
        if "PERSONA:\npersona 0" in u:      # itself, a stranger, one name twice, then a third: two real likes kept
            out["likes"] = ["Name 0", "Nobody", "name 2", "Name 2", "Name 1"]
        elif "PERSONA:\npersona 1" in u:
            out["likes"] = "Name 0"          # a bare name is one like
        else:
            out["likes"] = {"not": "a list"}  # nonsense is no likes, and the post still counts
        return out
    llm.json = liking
    res = panel_mod.run(_request(), llm=llm, workers=1)
    thread = res.meta["thread"]
    assert res.meta["silent"] == 0 and all("likes" not in e for e in thread if e["round"] == 0)
    by = {(e["round"], e["persona_id"]): e["likes"] for e in thread if e["round"]}
    assert by[(1, "p0")] == ["p2", "p1"] and by[(1, "p1")] == ["p0"] and by[(2, "p2")] == []
    assert all('"likes": [' in u for u in asked)                      # every member is asked for them
    assert all("likes" not in u.split("THE DISCUSSION SO FAR")[1].split("This is round")[0] for u in asked)
    assert all("likes" not in a.discussion for a in res.agents)


def test_a_member_that_fails_twice_is_silent_and_keeps_its_last_number(llm):
    orig = llm.json

    def flaky(s, u, **k):
        if "PERSONA:\npersona 1" in u:
            return {"post": "", "prob": 7}          # invalid both times
        return orig(s, u, **k)
    llm.json = flaky
    res = panel_mod.run(_request(), llm=llm, workers=1)
    assert res.meta["silent"] == 2 and len(res.meta["thread"]) == 3 + 2 + 2
    assert res.agents[1].public_prob == 0.5 and len(res.agents[1].posts) == 1      # its opening forecast
    quiet = panel_mod.run(_request(rounds=0, priors=False), llm=llm)
    assert quiet.meta["thread"] == [] and all(a.public_prob is None and a.discussion == "" for a in quiet.agents)


def test_a_full_panel_run_uses_no_stand_in(con, llm):
    store.seed_population(con, 6, "m")
    add_q(con, "Q1", T0)
    out = run_arm(con, "Q1", "PANEL", llm=llm, started_at=T0)
    assert out.status == "complete", out.error
    kinds = [c[0] for c in llm.calls]
    assert sum(k.startswith("You are one forecaster") for k in kinds) == 12        # alone, then final
    assert sum(k.startswith("You are a member of a fore") for k in kinds) == 12    # two rounds of six
    meta = __import__("json").loads(rows(con, "SELECT config_json FROM runs WHERE run_id = ?",
                                         [out.run_id])[0]["config_json"])["swarm_meta"]
    assert meta["runner"] == "panel" and len(meta["thread"]) == 18
    assert out.aggregates["prior"] is not None and out.aggregates["public"] is not None
    assert out.aggregates["native"] is None


def test_a_quiet_panel_has_no_public_number():
    def quiet(req):
        return SwarmResult(agents=[SwarmAgent(i, p.persona_id, p.profile) for i, p in enumerate(req.personas)],
                           native_prob=None)
    from swarmz.registry.db import connect
    con = connect(":memory:")
    from tests.conftest import FakeLLM
    store.seed_population(con, 6, "m")
    add_q(con, "Q1", T0)
    out = run_arm(con, "Q1", "PANEL", llm=FakeLLM(), swarm=quiet, started_at=T0)
    assert out.aggregates["public"] is None and out.aggregates["prior"] is not None


def test_spread_counts_sides_and_shows_whether_talk_pulled_the_panel_together():
    s = agg.spread([0.48, 0.5, 0.52, 0.52], priors=[0.2, 0.4, 0.6, 0.8])
    assert (s["n"], s["low"], s["high"], s["yes_side"], s["no_side"]) == (4, 0.48, 0.52, 2, 1)
    assert s["sd"] < 0.02 < s["prior_sd"]
    assert "prior_sd" not in agg.spread([0.3, 0.7])
    assert agg.extremized([0.6, 0.6], 1.0) == agg.equal([0.6, 0.6])
    assert agg.extremized([0.6, 0.6], 2.5) > 0.7 and agg.extremized([0.4], 2.5) < 0.3


def _resolved(con, llm, qid, outcome, market):
    add_q(con, qid, T0)
    store.set_market_prob(con, qid, market, T0)
    for arm in ("A0", "PANEL", "A3"):
        assert run_arm(con, qid, arm, llm=llm, swarm=Panel(), started_at=T0).status == "complete"
    store.set_outcome(con, qid, outcome, T0 + timedelta(days=1))
    score_question(con, qid)


def test_reports_score_against_the_cheap_baselines_and_show_the_spread(con, llm):
    store.seed_population(con, 6, "m")
    _resolved(con, llm, "Q1", 1, 0.8)
    _resolved(con, llm, "Q2", 0, 0.3)
    base = {(r["arm"], r["method"]): r for r in report.against_baselines(con)}
    a0, prior = base[("A0", "equal")], base[("PANEL", "prior")]
    assert a0["n"] == 2 and a0["vs_one_call"] == 0 and a0["n_with_market"] == 2
    market_brier = ((0.8 - 1) ** 2 + (0.3 - 0) ** 2) / 2
    assert abs(a0["vs_market"] - (a0["mean_brier"] - market_brier)) < 1e-3
    assert abs(prior["vs_always_50"] - (prior["mean_brier"] - 0.25)) < 1e-3
    assert ("PANEL", "public") in base and ("A3", "equal") in base

    sp = {(r["question_id"], r["arm"]): r for r in report.spread_by_question(con)}
    assert ("Q1", "A0") not in sp                                  # one forecast has no spread
    a2 = sp[("Q1", "PANEL")]
    assert a2["n"] == 6 and a2["sd_before"] > 0 and a2["moved"] is not None and a2["market"] == 0.8
    assert a2["yes_side"] + a2["no_side"] <= 6 and a2["outcome"] == 1
    assert sp[("Q1", "A3")]["sd_before"] is None                   # A3 has no discussion to be before

    grid = {r["arm"]: r for r in report.extremize_grid(con)}
    assert grid["PANEL"]["n"] == 2 and grid["PANEL"]["best_a"] in report.EXTREMIZE_GRID
    q1 = rows(con, "SELECT list(prob) AS p FROM forecasts f JOIN runs r USING (run_id) WHERE r.arm='A3' AND r.question_id='Q1'")[0]["p"]
    q2 = rows(con, "SELECT list(prob) AS p FROM forecasts f JOIN runs r USING (run_id) WHERE r.arm='A3' AND r.question_id='Q2'")[0]["p"]
    want = ((agg.extremized(q1, 2.0) - 1) ** 2 + (agg.extremized(q2, 2.0) - 0) ** 2) / 2
    assert abs(grid["A3"]["a=2"] - want) < 1e-3
    report.print_report(con, ("baselines", "spread", "extremize"))  # renders, None cells included


class Candles:
    def __init__(self, candles):
        self.candles, self.asked = candles, None

    def get(self, url, params=None, timeout=None):
        self.asked = (url, params)
        return self

    def raise_for_status(self):
        pass

    def json(self):
        return {"candlesticks": self.candles}


def test_market_price_at_freeze_is_read_from_the_price_history():
    from swarmz.picker import market_prob_at
    freeze = datetime(2026, 11, 10, 0, 15, 50)
    end = 1794269750   # that moment as a Unix time
    quote = lambda t, bid, ask, **price: {"end_period_ts": t, "yes_bid": {"close_dollars": bid},
                                          "yes_ask": {"close_dollars": ask}, "price": price}
    s = Candles([quote(end - 120, "0.5000", "0.6000"), quote(end - 50, "0.5800", "0.6700"),
                 quote(end + 600, "0.9000", "0.9500")])            # a quote after the freeze is not used
    p, at = market_prob_at("KXNASDAQ100U-26NOV10H1600-T27899.99", freeze, session=s)
    assert p == 0.625 and at == datetime(2026, 11, 10, 0, 15)
    assert s.asked[0].endswith("/series/KXNASDAQ100U/markets/KXNASDAQ100U-26NOV10H1600-T27899.99/candlesticks")
    assert s.asked[1]["end_ts"] == end + 60 and s.asked[1]["period_interval"] == 1
    # an empty side of the book is not a price: fall back to the last trade
    one_sided = Candles([quote(end - 60, "0.0000", "1.0000", previous_dollars="0.4400")])
    assert market_prob_at("KXA-1-B", freeze, session=one_sided)[0] == 0.44
    assert market_prob_at("KXA-1-B", freeze, session=Candles([])) is None
