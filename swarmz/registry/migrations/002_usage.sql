-- Token usage and cost per run (or per learning/evolve step), split by stage and model.
CREATE TABLE IF NOT EXISTS usage (
    usage_id           VARCHAR PRIMARY KEY,
    context            VARCHAR NOT NULL,      -- run | learn | evolve
    run_id             VARCHAR,               -- set for context = 'run'
    question_id        VARCHAR,
    arm                VARCHAR,
    stage              VARCHAR NOT NULL,      -- setup | simulation | memory | elicit | report | learning | other
    provider           VARCHAR NOT NULL,      -- anthropic | openai (SDK used; host says which API)
    host               VARCHAR,
    model              VARCHAR NOT NULL,
    calls              INTEGER NOT NULL,
    input_tokens       BIGINT NOT NULL,
    output_tokens      BIGINT NOT NULL,
    cache_read_tokens  BIGINT NOT NULL,
    cache_write_tokens BIGINT NOT NULL,
    cost_usd           DOUBLE,                -- null when the model has no price in config/pricing.yaml
    recorded_at        TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_run ON usage (run_id);
