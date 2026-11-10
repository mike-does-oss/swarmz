# Ideas backlog: better self-improvement

Parked 2026-11-05. The bottleneck is slow, noisy feedback: one binary outcome per question can't
separate skill from luck until there are hundreds of questions. Detecting a 0.01 Brier difference
between two arms needs roughly 200 resolved questions, so ideas 1-3 matter most.

**Suggested first:** 1 (market-price scoring), 4 (lesson A/B tests), 7 (marginal-contribution selection).
Cheap, fit the current code, and each can be compared against today's version. 10 (internal market) is the
most novel and a good candidate for an A5 arm.

## More signal per question
1. **Score against the later market price, not just the outcome.** The Kalshi price a few days later, or
   just before close, is a far less noisy target than 0/1 and is available before settlement. Agents still
   never see prices; it's only a grading target. Risk: selects for personas that track the market. Blend with
   the real outcome.
2. **Pick questions the crowd disagrees on.** Run a cheap A3 round first and favour questions where personas
   disagree most; those separate good forecasters from bad fastest (active learning).
3. **Leak-free backtests from dated news.** Build briefs only from articles published before the freeze date
   (e.g. GDELT's timestamped news) on events after the models' training cutoffs. Hundreds of questions without
   waiting.

## Lessons that earn their place
4. **A/B-test each lesson.** Give a new lesson to a random half of personas and measure the score difference
   before keeping it. Causal estimate per lesson; lessons become shareable across personas.
5. **Replay lessons on past questions.** Before activating, re-run a few resolved train questions (as of their
   freeze, same briefs) with the lesson; keep it only if it helps. Filters out hindsight lessons.
6. **Critique the reasoning, not the result.** Write lessons from process (base rates, timelines, resolution
   rules), not from whether one outcome went the persona's way.

## A better crowd
7. **Select by marginal contribution.** Keep personas that are right when the crowd is wrong (leave-one-out
   impact on the aggregate's Brier), not just the most accurate. Accuracy-only selection makes everyone alike.
8. **Specialists by tag.** Keep the best persona per niche (Economics x short horizon, Sports x long, ...),
   quality-diversity style, and weight by category track record shrunk toward the global one. Uses question
   tags; `market_bucket` must stay excluded.
9. **Retire on evidence.** Replace "cull bottom 20%" with a skill estimate with error bars: retire only when
   probably worse than median (e.g. 90%), and sample new personas into the crowd so they get tested.

## Different interaction (new arms vs A2)
10. **Internal prediction market.** Agents trade with play money; price = forecast; wealth carries over between
    questions. Weighting and selection become automatic (rich agents count more, broke ones are replaced).
11. **Structured rounds instead of a feed.** Delphi style: estimate, exchange reasons, revise. Record before and
    after; score who updates well and whose arguments move others correctly.

## Learning to learn
12. **Population-based training.** Run several A2 populations with different weighting strength, cull rate and
    lesson cap; periodically copy the best population's settings to the rest.
