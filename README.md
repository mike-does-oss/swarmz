# swarmz

A local forecasting panel that learns from resolved questions. Ten forecasters keep a persistent
identity across questions, get scored when questions resolve, and improve through track-record
weighting, Platt calibration, selection/mutation and reflection lessons.

For each question the panel does four things:

1. **Reads.** Each member reads the question and two of the brief's four evidence slices
   (`swarmz/diversity.py`), so no member has all the evidence and every slice is held by several.
2. **Forecasts alone.** Each member commits to a probability before anyone speaks.
3. **Discusses.** Each member's forecast is its opening post. Then five rounds: every member reads the
   thread and posts once, with its current number (`swarmz/panel.py`). Two members, different ones on
   each question, are challengers: they argue against whichever view is leading.
4. **Forecasts again, in private.** That final number is the one that is aggregated and scored.

Members differ in three ways, none of which costs more per call: a forecasting method each (ten of
them, `METHODS` in `swarmz/registry/store.py`; no job titles or biographies), the part of the brief
each reads, and the lessons each has collected from its own scored forecasts. All run on
`claude-haiku-5-5`.

Everything runs on one machine, as plain model calls plus one DuckDB file (`data/swarmz.duckdb`):
persona registry, forecasts, scores, lessons, calibration fits and the selection audit trail. The
registry is the **only** channel between questions.

The brief builder (Decomposer, Assembler, Scrubber) lives in `briefbuilder/`. swarmz starts it as a
child process and only consumes its frozen briefs; its model and keys are the `BRIEF_*` settings.

## Try it with Claude Code, no API key

If you have Claude Code and a Claude subscription, swarmz can make its model calls through Claude Code
and needs no keys at all:

```
git clone https://github.com/mike-whypred/swarmz && cd swarmz
uv sync
uv run swarmz health                 # says which way model calls will go
uv run swarmz ask KXFEDDECISION-26DEC-H25
```

`swarmz ask` takes any open Kalshi market ticker (or a link to the market). It sets up the registry and
the ten-agent panel on first use, builds a research brief from the web, makes the single call, runs the
panel's discussion, and prints the forecasts beside the market price. Everything is kept in
`data/swarmz.duckdb`.

How it differs from running on an API key:

- It is chosen when `ANTHROPIC_API_KEY` is not set and the `claude` command is. `SWARMZ_LLM=claude-code`
  forces it, `SWARMZ_LLM=api` forces the API.
- Each call starts `claude -p` with tools, connectors, skills and settings switched off (see
  `swarmz/claude_code.py`), so it is slower: expect a question to take ten minutes or more.
- Nothing is billed per call, but a question reads several hundred thousand tokens, and that comes out of
  your subscription's usage allowance. Usage is still counted in the `usage` table, at a cost of zero.
- Web search goes through Claude Code's own search unless `BRIEF_BRAVE_API_KEY` is set. Its results are
  thinner than Brave's, so briefs are more likely to be rejected for being too short or missing the
  latest figure; `--min-brief-words` lowers the bar.
- The scheduled cloud runs cannot use a subscription. They always use API keys.

## Running in the cloud

Nothing has to stay switched on. A Vercel project (`swarmz-engine`, this folder) has one scheduled
function, `api/tick.py`, that Vercel Cron calls on the even hours UTC. Each call runs `swarmz.cloud.tick()`:

1. take the lock, and unpack the registry and briefs from the `engine_files` table in Postgres;
2. run the oldest queued Kalshi market from the Supercast queue (brief, single call, panel) and mark
   its queue row `done` or `failed` with the reason;
3. settle, score and learn from markets that have resolved;
4. export the site's data to the `site_data` table and call the site's deploy hook, which rebuilds the page;
5. pack the registry back and release the lock.

The registry is still one DuckDB file; Postgres only stores it between runs. `swarmz cloud status`
shows what is stored, `swarmz cloud pull` copies it to this machine (the cloud copy is the one that
counts now), and `swarmz cloud push` replaces it with this machine's. Settings on the Vercel project:
`DATABASE_URL`, `CRON_SECRET`, `SITE_DEPLOY_HOOK_URL`, `ANTHROPIC_API_KEY` and the `BRIEF_*` ones
(`scripts/push_keys.py` copies the keys up). `SWARMZ_QUEUE_MAX_PER_DAY` caps paid runs (default 12).

## Setup

```bash
uv sync
cp .env.example .env         # ANTHROPIC_API_KEY is the only key needed
uv run swarmz health
uv run swarmz init
uv run swarmz personas seed  # the ten panel members (plus A3 and the A0/A4 baselines)
```

## Daily loop

```bash
uv run swarmz questions import-briefs   # frozen briefs from the brief builder (or: swarmz questions add ...)
uv run swarmz run KXRECSSNBER-26            # the single-call baseline (A0) and the panel
uv run swarmz resolve          # Kalshi lookups for open questions -> score -> learn
uv run swarmz resolve --set QID=1 --at 2026-11-01   # manual outcome (1, 0 or void)
uv run swarmz report
uv run swarmz backtest questions.jsonl --arms A0,PANEL
make backup                    # the DuckDB registry into backups/<date>/
uv run swarmz questions exclude QID --reason "..."   # set a question aside (see Guardrails)
```

**Short-dated questions.** `swarmz questions pick` finds Kalshi markets expected to resolve in 4-36h
(by expected resolution, not close; events whose date has passed are skipped), one per event and per
game/day, 10-90c, mixed across categories. `--build` freezes a brief for each with the
brief builder (run as a child process via `scripts/build_brief.py`) and imports it; `--run A0,PANEL`
then runs the arms. Example: `swarmz questions pick --n 5 --max-per-category 1 --min-volume 300 --build --run A0,PANEL`.
`--min-brief-words` (default 800) relaxes the builder's 1,500-word minimum, which daily price markets rarely reach.

**Automatic resolution.** `make watch` starts a detached watcher (`swarmz resolve --watch --forever`) that polls
Kalshi every 10 minutes, scores questions as they settle and runs learning. `make watch-status` / `make unwatch`;
log in `logs/resolve-watch.log`. It survives closing the terminal but not a reboot. It opens the registry only
briefly per poll, and other swarmz commands wait for the lock instead of failing.

**Tags.** Kalshi questions are tagged on import (`category`, `topic`, `series`, `frequency`, `horizon`,
`strike_type`, plus `market_bucket` from the brief builder's market mid at freeze). Add your own with
`swarmz questions tag QID key=value ...`, refresh with `swarmz questions retag`, and see which arms and
personas do best per tag with `swarmz report --section tags --tag topic`. Persona scores per tag are
shrunk toward 0 by 10 pseudo-questions so small samples don't top the table. Tags are reporting-only:
agents, lessons and learning never see them (enforced by a test), so the market-price tag can't leak odds.

**Cost.** Every model call is counted (tokens by stage: elicit, discussion, learning, brief) and priced
from `config/pricing.yaml` into the `usage` table. `swarmz run` prints each run's cost;
`swarmz report --section cost` shows cost per arm, per stage, and outside runs. Failed runs are counted
too. Prices are list prices; update the YAML if they change. One panel run at the default five rounds is
70 calls (10 forecasts alone, 50 discussion posts, 10 final forecasts). Each post reads the whole
thread so far, so cost grows faster than the number of rounds. Measured, one run each: two rounds
(40 calls) $0.038, five rounds (70 calls) $0.095, ten rounds (120 calls) $0.223, against $0.001 for
the single call. `swarmz run` prints the figure for every run. In the ten-round run the panel's mean
stopped moving after round 4, which is why the default is five. Freezing a brief costs about $0.22.

Other commands: `swarmz learn` (run lesson/calibration/selection hooks now),
`swarmz evolve [--dry-run]`, `swarmz personas list`, `swarmz questions list`.

## Arms

| Arm | Agents | Learning | Run by default |
|---|---|---|---|
| PANEL | Ten registry personas: read part of the brief, forecast alone, discuss, forecast again | weighting, calibration, selection, lessons | yes |
| A0 | One plain model call on the whole brief | none | yes: the yardstick the report scores against |
| A3 | Registry personas forecasting alone on the whole brief, no discussion | same as PANEL, its own population | no |
| A4 | One model call with lessons and calibration | lessons, calibration | no |

**What is stored per run.** Four aggregates: `equal` (log-odds mean of the final forecasts), `weighted`
(by track record), `calibrated` (weighted, then the latest Platt fit) and `native` (the raw single forecast
for A0/A4; empty otherwise). The panel adds `prior` (the mean before the discussion) and `public` (the mean
of the last number each member posted). Each member's numbers before and during the discussion are in
`swarm_stages`. The whole thread, with each post's likes, and who read which slices are in the run's
`config_json` (`swarm_meta.thread`, `seats`). Scoring `prior` beside the final number shows whether talking helped: a
discussion that only pulls everyone to the middle shows up as a worse score, not as agreement.

**The discussion.** Members post at the same time within a round, so nobody answers a post from the same
round. Each is told to bring a fact from its own reading that nobody has mentioned or to answer the
strongest point against its view, and to move its number only for a fact or argument it had not weighed.
A member whose reply is invalid twice stays silent for that round. `SWARMZ_DISCUSSION_ROUNDS` (default 5)
sets the number of rounds; each round is one call per member.

**Likes.** With each post a member may name up to two other members whose latest post was most useful
to it: a fact it did not have, or an argument it had to take seriously. This is part of the same call,
so it costs no extra calls. Likes are never shown to the panel, so a count of likes cannot pull members
together. They are kept for the replay and are not used in aggregation or learning. Each thread entry
after the opening posts has a `likes` list of persona ids; a like points at that member's latest post
in the thread the liker read.

**Challengers.** A panel left alone settles fast: in the first live run one argument pulled all ten
members into a one-point range. So on each question `SWARMZ_CHALLENGERS` members (default 2) are told
to argue against whichever view is leading, even if they share it, and to go after the new leader if
the panel moves. Who they are changes with the question, they sit apart so they read different slices,
and the others are not told. The job covers arguments only. A challenger's forecast alone is made before
it has the job, the number in each post is its honest one, and before the final forecast it is told the
job is over. Which members challenged is in the run's `config_json` (`seats.<persona>.challenger`).

PANEL, A3 and A4 each own their personas and calibration fits, so their learning never mixes.

**What the report scores against.** `swarmz report --section baselines` puts every arm and aggregate next to
three cheap answers on the same questions: always 50%, the single call (A0), and the market's price when the
brief was frozen. Negative means better. The market price is read from Kalshi's price history on import
(`swarmz questions market` fills in any that are missing) and is never shown to an agent.
`--section spread` shows, per question and arm, how far apart the agents were, how many stood on each side,
the spread before the discussion and how far the panel's mean moved. `--section extremize` rescores each
multi-agent arm with its mean log-odds multiplied by 1.0 to 3.0, to show whether the panel sits too close
to 50%. `weighting.extremize_a` stays 1.0 until that table has enough resolved questions to choose from;
the `calibrated` aggregate fits the same multiplier by itself once 50 questions resolve.

## Guardrails (all covered by tests)

- **Same brief for every arm**: every arm works from the same frozen brief. A0, A3 and A4 read it whole;
  a panel member reads its own part before the discussion and again, with the thread, at the final forecast.
- **Late runs**: a run that starts after its question resolved gets status `late` and is never scored,
  reported or used for learning.
- **Temporal hygiene**: every learning read goes through `swarmz/registry/asof.py`, which
  only returns scores/lessons/fits from questions resolved strictly before the current freeze time.
- **Holdout**: `asof.py` filters to `split = 'train'`; holdout questions are only scored and reported.
  Split defaults to a stable hash of the question id (20% holdout) unless given.
- **Excluded questions**: `swarmz questions exclude QID --reason "..."` sets a question aside (split
  `excluded`). Its runs, forecasts and scores stay on record and show as their own group in the arm
  comparison, but nothing learns from it and it counts in neither train nor holdout results. Its lessons
  are retired and calibration fits that could have used it are dropped. The six pilot questions run
  before 2026-11-10 are excluded this way.
- **Resolution time**: Kalshi's `settlement_ts`; if Kalshi reports a result before filling that in, the
  close time, and never a time in the future. (The old fallback was the market's expiration backstop, a
  week out, which kept two questions out of learning until that date.)
- **Question isolation**: the DuckDB registry is the only cross-question channel.
- **Same brief bytes**: each arm re-hashes the brief file and refuses to run on a mismatch.
- **Lesson lint**: rejects lessons mentioning odds/markets/prices, outcomes as fact, dates, or
  named entities and numbers from the source question. The first 20 resolved questions'
  lessons go to `reports/lessons_review.md` for manual review.
- **Training cutoff**: the backtest refuses questions that resolved on or before the latest training
  cutoff of any model in the run (`config/models.yaml`), and refuses to start if a model has no cutoff recorded.
- **Reproducibility**: each run stores config, seed, model versions and brief hash. Selection is
  seeded and deterministic for a given registry state.

## What was removed

Until 2026-11-10 the swarm arms (A1, A2, A2E, A5) ran a simulated social network with a graph
database as its memory. That code and its dependencies have been deleted; the panel's own discussion
(`swarmz/panel.py`) replaced it. Runs of those arms stay in the registry as history, and their usage
rows keep the old stage names (setup, simulation, memory, report). The personas of the old populations
(job titles such as "a regulatory lawyer") are still in the registry under A2 and A3; the panel does not
use them.

## Tests

```bash
uv run pytest            # fake LLM, no network
```

## Open decisions (defaults in use until decided)

| Decision | Default now |
|---|---|
| Outcome source | Kalshi public API (the imported questions are Kalshi tickers; the handoff doc says Polymarket) + manual `--set` |
| Category taxonomy | Kalshi's category, set on import (`swarmz questions retag`) |
| Panel size / rounds / model | 10 members, 2 of them challengers, 5 rounds, `claude-haiku-5-5` for forecasts, discussion and lessons, `claude-sonnet-5-5` for mutation |
| Learning parameters | `config/learning.yaml` (handoff doc defaults) |
| Elicitation and discussion prompts | `swarmz/capture/elicit.py`, `swarmz/panel.py` |
| Backtest question set and model cutoffs | cutoffs in `config/models.yaml` are placeholders to confirm |
| When A3/A4 start | they run whenever you include them in `--arms` |
| Brief builder | lives in `briefbuilder/`, copied from the `shoals-new` project on 2026-11-10 |
