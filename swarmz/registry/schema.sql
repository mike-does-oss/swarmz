-- swarmz registry, migration 001. All timestamps are naive UTC.
-- Every table that feeds learning carries a timestamp so asof.py can filter
-- to what was known before a question's freeze time.

CREATE TABLE IF NOT EXISTS personas (
    persona_id   VARCHAR PRIMARY KEY,
    population   VARCHAR NOT NULL,          -- arm population that owns it: A2, A3, A4, A0
    generation   INTEGER NOT NULL DEFAULT 0,
    parent_id    VARCHAR,
    status       VARCHAR NOT NULL DEFAULT 'active',   -- active | elite | retired | system
    base_prompt  VARCHAR NOT NULL,
    model        VARCHAR NOT NULL,
    created_at   TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS questions (
    question_id          VARCHAR PRIMARY KEY,
    question_text        VARCHAR NOT NULL,
    resolution_criteria  VARCHAR NOT NULL DEFAULT '',
    category             VARCHAR NOT NULL DEFAULT 'other',
    freeze_ts            TIMESTAMP NOT NULL,
    resolve_ts           TIMESTAMP,
    outcome              INTEGER,               -- 0 / 1, NULL until resolved
    status               VARCHAR NOT NULL DEFAULT 'open',  -- open | resolved | void
    brief_sha256         VARCHAR NOT NULL,
    brief_path           VARCHAR NOT NULL,
    split                VARCHAR NOT NULL DEFAULT 'train', -- train | holdout | excluded (set aside: kept on record, never learned from or evaluated)
    source               VARCHAR NOT NULL DEFAULT 'manual' -- kalshi | manual | backtest
);

CREATE TABLE IF NOT EXISTS runs (
    run_id          VARCHAR PRIMARY KEY,
    question_id     VARCHAR NOT NULL,
    arm             VARCHAR NOT NULL,
    generation      INTEGER NOT NULL DEFAULT 0,
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    status          VARCHAR NOT NULL DEFAULT 'running', -- running | complete | failed | late (started after the question resolved; never scored)
    config_json     VARCHAR NOT NULL,
    seed            INTEGER NOT NULL,
    model_versions  VARCHAR NOT NULL,
    brief_sha256    VARCHAR NOT NULL,
    error           VARCHAR
);

CREATE TABLE IF NOT EXISTS forecasts (
    run_id       VARCHAR NOT NULL,
    question_id  VARCHAR NOT NULL,
    persona_id   VARCHAR NOT NULL,
    prob         DOUBLE NOT NULL,
    rationale    VARCHAR NOT NULL DEFAULT '',
    elicited_at  TIMESTAMP NOT NULL,
    PRIMARY KEY (run_id, persona_id)
);

CREATE TABLE IF NOT EXISTS aggregates (
    run_id       VARCHAR NOT NULL,
    question_id  VARCHAR NOT NULL,
    method       VARCHAR NOT NULL,   -- equal | weighted | calibrated | native
    prob         DOUBLE,             -- NULL when the method has no output for this arm
    computed_at  TIMESTAMP NOT NULL,
    PRIMARY KEY (run_id, method)
);

-- One row per scored persona forecast (persona_id set, method NULL)
-- or per scored aggregate (method set, persona_id NULL).
CREATE TABLE IF NOT EXISTS scores (
    question_id  VARCHAR NOT NULL,
    run_id       VARCHAR NOT NULL,
    arm          VARCHAR NOT NULL,
    persona_id   VARCHAR,
    method       VARCHAR,
    prob         DOUBLE NOT NULL,
    outcome      INTEGER NOT NULL,
    brier        DOUBLE NOT NULL,
    rel_brier    DOUBLE NOT NULL,
    category     VARCHAR NOT NULL,
    split        VARCHAR NOT NULL,
    resolve_ts   TIMESTAMP NOT NULL,
    scored_at    TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS lessons (
    lesson_id           VARCHAR PRIMARY KEY,
    persona_id          VARCHAR NOT NULL,
    source_question_id  VARCHAR,
    text                VARCHAR NOT NULL,
    valid_from          TIMESTAMP NOT NULL,   -- source question's resolve_ts
    status              VARCHAR NOT NULL,     -- active | merged | rejected | retired (its question was excluded)
    lint_reason         VARCHAR,
    merged_into         VARCHAR,
    created_at          TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS selection_events (
    event_id    VARCHAR PRIMARY KEY,
    population  VARCHAR NOT NULL,
    generation  INTEGER NOT NULL,
    persona_id  VARCHAR NOT NULL,
    action      VARCHAR NOT NULL,   -- keep | cull | mutate | promote | spawn
    reason      VARCHAR NOT NULL,
    as_of       TIMESTAMP NOT NULL,
    created_at  TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS calibration_fits (
    fit_id        VARCHAR PRIMARY KEY,
    arm           VARCHAR NOT NULL,
    method        VARCHAR NOT NULL,
    params_json   VARCHAR NOT NULL,
    fit_until_ts  TIMESTAMP NOT NULL,   -- latest resolve_ts in the fit's data window
    n_questions   INTEGER NOT NULL,
    created_at    TIMESTAMP NOT NULL
);

-- Tracks which resolved questions have had their learning hooks applied, so
-- refit / evolve counters ("every N newly resolved") are exact.
CREATE TABLE IF NOT EXISTS learning_log (
    question_id  VARCHAR NOT NULL,
    population   VARCHAR NOT NULL,
    step         VARCHAR NOT NULL,     -- lessons | counted
    done_at      TIMESTAMP NOT NULL,
    PRIMARY KEY (question_id, population, step)
);
