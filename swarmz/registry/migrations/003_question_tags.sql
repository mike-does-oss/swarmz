-- Free-form question tags for analysis (which personas/arms do well on which kinds of question).
-- key examples: category, topic, series, frequency, horizon, strike_type, market_bucket, or anything manual.
-- Tags are reporting metadata only: no prompt, lesson or learning read uses them.
CREATE TABLE IF NOT EXISTS question_tags (
    question_id VARCHAR NOT NULL,
    key         VARCHAR NOT NULL,
    value       VARCHAR NOT NULL,
    source      VARCHAR NOT NULL,        -- kalshi | brief_builder | manual
    tagged_at   TIMESTAMP NOT NULL,
    PRIMARY KEY (question_id, key, value)
);
