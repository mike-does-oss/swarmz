-- Swarm agents forecast alone before the discussion; that forecast and the last number each agent
-- posted in public are kept beside its final forecast (forecasts.prob).
CREATE TABLE IF NOT EXISTS swarm_stages (
    run_id           VARCHAR NOT NULL,
    question_id      VARCHAR NOT NULL,
    persona_id       VARCHAR NOT NULL,
    prior_prob       DOUBLE,              -- alone, before the discussion; NULL for arms without that step
    prior_rationale  VARCHAR NOT NULL DEFAULT '',
    public_prob      DOUBLE,              -- last probability stated in a post; NULL if it never stated one
    recorded_at      TIMESTAMP NOT NULL,
    PRIMARY KEY (run_id, persona_id)
);

-- The market's price for YES when the brief froze, 0..1. A baseline to score against: it is never
-- shown to an agent and nothing in learning reads it.
ALTER TABLE questions ADD COLUMN IF NOT EXISTS market_prob DOUBLE;
ALTER TABLE questions ADD COLUMN IF NOT EXISTS market_ts TIMESTAMP;
