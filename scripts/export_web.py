"""Snapshot of the swarmz registry for the Supercast site. Read-only.

    uv run python scripts/export_web.py web/src/admin_costs.json > web/src/app_data.json

Public JSON on stdout: finished panel runs (for the tables and the replay), scores, agents. It carries no
dollar figures. Costs go to the file named in argv[1], which only the signed-in admin API serves."""
import json, re, statistics, sys
from datetime import datetime, timezone
import duckdb

con = duckdb.connect("data/swarmz.duckdb", read_only=True)


def rows(sql, args=()):
    cur = con.execute(sql, list(args))
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


questions = {q["question_id"]: q for q in rows("select * from questions")}
personas = {p["persona_id"]: p for p in rows("select * from personas")}
a0 = {}                                                   # latest single call per question
for r in rows("select r.question_id, f.prob from runs r join forecasts f using (run_id) "
              "where r.arm = 'A0' and r.status = 'complete' order by r.started_at"):
    a0[r["question_id"]] = r["prob"]


def method(pid):
    return (personas[pid].get("base_prompt") or "").split("Your method:")[-1].strip()


def sd(xs):
    xs = [x for x in xs if x is not None]
    return statistics.pstdev(xs) if len(xs) > 1 else None


def panel_run(run):
    rid, q = run["run_id"], questions[run["question_id"]]
    cfg = json.loads(run["config_json"])
    seats, meta = cfg["seats"], cfg["swarm_meta"]
    thread = meta["thread"]
    fc = {f["persona_id"]: f for f in rows("select * from forecasts where run_id = ?", [rid])}
    agg = {a["method"]: a["prob"] for a in rows("select method, prob from aggregates where run_id = ?", [rid])}
    usage = [[u["stage"], u["calls"], u["tin"], u["tout"]] for u in rows(
        "select stage, sum(calls) calls, sum(input_tokens) tin, sum(output_tokens) tout "
        "from usage where run_id = ? group by 1 order by 1 desc", [rid])]
    score = rows("select brier from scores where run_id = ? and persona_id is null and method = 'equal'", [rid])
    order = [e["persona_id"] for e in thread if e["round"] == 0]
    names = {pid: next(e["name"] for e in thread if e["persona_id"] == pid) for pid in order}
    opening = {e["persona_id"]: e["prob"] for e in thread if e["round"] == 0}
    agents = [{"id": pid, "name": names[pid], "method": method(pid), "challenger": bool(seats[pid].get("challenger")),
               "lenses": seats[pid]["lenses"], "alone": opening.get(pid), "final": fc[pid]["prob"],
               "why": fc[pid].get("rationale") or ""} for pid in order]
    posts = []
    for i, e in enumerate(thread):
        said = [pid for pid, nm in names.items() if pid != e["persona_id"] and (
            re.search(r"\b" + re.escape(nm) + r"\b", e["text"]) or re.search(r"\b" + re.escape(nm.split()[-1]) + r"\b", e["text"]))]
        posts.append({"i": i, "round": e["round"], "agent": e["persona_id"], "prob": e["prob"], "text": e["text"],
                      "answers": said, "likes": e.get("likes", [])})
    return {"run_id": rid, "question_id": q["question_id"], "question": q["question_text"], "split": q["split"],
            "market": q["market_prob"], "outcome": q["outcome"], "brier": score[0]["brier"] if score else None,
            "rounds": meta["rounds"], "silent": meta.get("silent"), "a0": a0.get(q["question_id"]),
            "aggregates": agg, "usage": usage, "has_likes": any("likes" in e for e in thread),
            "started": str(run["started_at"])[:19], "finished": str(run["finished_at"])[:19],
            "secs": round((run["finished_at"] - run["started_at"]).total_seconds()),
            "sd_alone": sd([a["alone"] for a in agents]), "sd_final": sd([a["final"] for a in agents]),
            "agents": agents, "posts": posts}


runs = [panel_run(r) for r in rows("select * from runs where arm = 'PANEL' and status = 'complete' order by started_at desc")]
runs = [r for r in runs if r["has_likes"]]                # the final design keeps likes; runs made before it are left out
latest = {r["question_id"]: r for r in reversed(runs)}

# ---- scoring: mean Brier over finished runs whose question has resolved, beside the cheap answers on the same questions
done = [r for r in latest.values() if r["brier"] is not None and r["outcome"] in (0, 1)]


def mean(xs):
    xs = [x for x in xs if x is not None]
    return (len(xs), sum(xs) / len(xs)) if xs else (0, None)


sq = lambda p, o: None if p is None else (p - o) ** 2
scored = [dict(zip(("key", "label", "n", "brier"), (k, label) + mean(v))) for k, label, v in [
    ("PANEL", "Panel", [r["brier"] for r in done]),
    ("A0", "Single call", [sq(r["a0"], r["outcome"]) for r in done]),
    ("market", "Market price at freeze", [sq(r["market"], r["outcome"]) for r in done]),
    ("fifty", "Always 50%", [0.25 for r in done])]]

# ---- agents, across the finished runs
agents = []
for a in runs[0]["agents"] if runs else []:
    pid, mine = a["id"], []
    for r in runs:
        me = next((x for x in r["agents"] if x["id"] == pid), None)
        if me:
            mine.append((r, me))
    moves = [abs(me["final"] - me["alone"]) for _, me in mine if me["final"] is not None and me["alone"] is not None]
    likes = sum(p["likes"].count(pid) for r, _ in mine for p in r["posts"])
    ids = [r["run_id"] for r, _ in mine]
    sc = rows(f"select count(*) n, avg(brier) brier from scores where persona_id = ? and run_id in ({','.join('?' * len(ids))})", [pid] + ids)[0]
    agents.append({"id": pid, "name": a["name"], "method": a["method"], "runs": len(mine),
                   "challenger": sum(1 for _, me in mine if me["challenger"]),
                   "move": sum(moves) / len(moves) if moves else None, "likes": likes,
                   "scored": sc["n"], "brier": sc["brier"]})

generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
json.dump({"generated": generated, "runs": runs, "scored": scored, "n_scored": len(done), "agents": agents},
          sys.stdout, ensure_ascii=False, default=str)

# ---- costs, for the owner-only admin view
if len(sys.argv) > 1:
    one = lambda sql, args: con.execute(sql, args).fetchone()
    costs = []
    for r in runs:
        st = rows("select stage, sum(calls) calls, sum(input_tokens) tin, sum(output_tokens) tout, sum(cost_usd) cost "
                  "from usage where run_id = ? group by 1", [r["run_id"]])
        by = {x["stage"]: x for x in st}
        brief = one("select coalesce(sum(cost_usd), 0), coalesce(sum(calls), 0) from usage where context = 'brief' and question_id = ?", [r["question_id"]])
        single = one("select coalesce(sum(cost_usd), 0), coalesce(sum(calls), 0) from usage where arm = 'A0' and question_id = ?", [r["question_id"]])
        g = lambda k, f: (by.get(k) or {}).get(f) or 0
        costs.append({"run_id": r["run_id"], "ticker": r["question_id"], "question": r["question"], "finished": r["finished"],
                      "brief": round(brief[0], 4), "single": round(single[0], 4),
                      "forecasts": round(g("elicit", "cost"), 4), "discussion": round(g("discussion", "cost"), 4),
                      "calls": int(brief[1] + single[1] + sum(x["calls"] for x in st)),
                      "tokens_read": int(sum(x["tin"] for x in st))})
    with open(sys.argv[1], "w") as f:
        json.dump({"generated": generated, "runs": costs}, f, ensure_ascii=False)
