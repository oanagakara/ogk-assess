-- ============================================================
-- NQF Assessment Platform — Reporting Schema
-- Oanagakara (Pty) Ltd
-- Version 1.1 — 2026-06-04
--
-- Two schemas inside the same Neon database:
--   staging   — raw extracts from app tables, refreshed each run
--   reporting — transformed views, Power BI connects here only
--
-- Run this once to set up. Re-run is idempotent (DROP IF EXISTS).
-- ============================================================


-- ============================================================
-- ACCESS CONTROL (run once as superuser on each database)
-- Two dedicated roles — etl_writer for the ETL pipeline,
-- reporting_reader for Power BI. Neither uses the full-privilege
-- app credential.
--
-- ── etl_writer: reads app data, writes staging ───────────────
-- CREATE ROLE etl_writer LOGIN PASSWORD '<strong-password>';
-- GRANT CONNECT ON DATABASE neondb TO etl_writer;
-- GRANT USAGE ON SCHEMA public TO etl_writer;
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO etl_writer;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public
--     GRANT SELECT ON TABLES TO etl_writer;
-- GRANT USAGE, CREATE ON SCHEMA staging TO etl_writer;
-- GRANT ALL ON ALL TABLES IN SCHEMA staging TO etl_writer;
-- GRANT ALL ON ALL SEQUENCES IN SCHEMA staging TO etl_writer;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA staging
--     GRANT ALL ON TABLES TO etl_writer;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA staging
--     GRANT ALL ON SEQUENCES TO etl_writer;
-- GRANT USAGE ON SCHEMA reporting TO etl_writer;
-- GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO etl_writer;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA reporting
--     GRANT SELECT ON TABLES TO etl_writer;
--
-- ── reporting_reader: Power BI — reporting schema only ────────
-- (role already exists in Neon; apply these grants if not done)
-- GRANT CONNECT ON DATABASE neondb TO reporting_reader;
-- GRANT USAGE ON SCHEMA reporting TO reporting_reader;
-- GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO reporting_reader;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA reporting
--     GRANT SELECT ON TABLES TO reporting_reader;
-- REVOKE ALL ON SCHEMA public FROM reporting_reader;
-- REVOKE ALL ON SCHEMA staging FROM reporting_reader;
--
-- Store etl_writer connection string as ETL_DATABASE_URL in
-- GitHub Secrets (used by the reporting-etl.yml workflow).
-- Store reporting_reader connection string separately for Power BI.
-- ============================================================


-- ── SCHEMAS ──────────────────────────────────────────────────

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS reporting;


-- ============================================================
-- STAGING TABLES
-- Raw copies of app data. No business logic. Truncate and
-- reload on each ETL run.
-- ============================================================

DROP TABLE IF EXISTS staging.tenants CASCADE;
CREATE TABLE staging.tenants (
    id              bigint PRIMARY KEY,
    name            text
);

DROP TABLE IF EXISTS staging.templates CASCADE;
CREATE TABLE staging.templates (
    id              bigint PRIMARY KEY,
    name            text,
    version         text,
    moderation_mode text,
    created_at      timestamptz
);

DROP TABLE IF EXISTS staging.sections CASCADE;
CREATE TABLE staging.sections (
    id          bigint PRIMARY KEY,
    title       text,
    "order"     integer,
    template_id bigint
);

DROP TABLE IF EXISTS staging.questions CASCADE;
CREATE TABLE staging.questions (
    id          bigint PRIMARY KEY,
    code        text,
    kind        text,
    max_marks   smallint,
    "order"     integer,
    section_id  bigint,
    is_active   boolean
);

DROP TABLE IF EXISTS staging.sessions CASCADE;
CREATE TABLE staging.sessions (
    id             bigint PRIMARY KEY,
    code           text,
    seat_limit     smallint,
    created_at     timestamptz,
    expires_at     timestamptz,
    template_id    bigint
);

DROP TABLE IF EXISTS staging.attempts CASCADE;
CREATE TABLE staging.attempts (
    id                  bigint PRIMARY KEY,
    code                text,
    status              text,
    started_at          timestamptz,
    submitted_at        timestamptz,
    last_activity_at    timestamptz,
    finalised_at        timestamptz,
    moderated_at        timestamptz,
    timed_out           boolean,
    session_id          bigint,
    template_id         bigint,
    -- md5 hash of learner PK — pseudonymous; not a direct FK
    learner_hash        text,
    gender              text,
    demographic         text,
    duration_minutes    numeric(8,1),
    is_valid_duration   boolean
);

DROP TABLE IF EXISTS staging.responses CASCADE;
CREATE TABLE staging.responses (
    id           bigint PRIMARY KEY,
    attempt_id   bigint,
    question_id  bigint
);

DROP TABLE IF EXISTS staging.scores CASCADE;
CREATE TABLE staging.scores (
    id              bigint PRIMARY KEY,
    response_id     bigint,
    -- md5 hash of assessor user PK — pseudonymous; not a direct FK
    assessor_hash   text,
    points          double precision,
    max_points      double precision,
    needs_review    boolean,
    created_at      timestamptz
);


DROP TABLE IF EXISTS staging.etl_run_log CASCADE;
CREATE TABLE staging.etl_run_log (
    id              bigserial PRIMARY KEY,
    started_at      timestamptz NOT NULL,
    finished_at     timestamptz,
    elapsed_seconds numeric(8,2),
    dry_run         boolean NOT NULL,
    table_counts    jsonb,
    error_count     integer,
    errors          jsonb
);


-- ============================================================
-- REPORTING VIEWS
-- Power BI connects to these only. Never to staging or the
-- live app schema directly.
-- ============================================================

-- ── DIMENSIONS ───────────────────────────────────────────────

CREATE OR REPLACE VIEW reporting.dim_template AS
SELECT
    id                          AS template_id,
    name                        AS template_name,
    version,
    moderation_mode,
    created_at
FROM staging.templates;


CREATE OR REPLACE VIEW reporting.dim_session AS
SELECT
    s.id                        AS session_id,
    s.code                      AS session_code,
    s.seat_limit,
    s.created_at                AS session_created_at,
    s.expires_at                AS session_expires_at,
    s.template_id,
    t.name                      AS template_name
FROM staging.sessions s
LEFT JOIN staging.templates t ON t.id = s.template_id;


CREATE OR REPLACE VIEW reporting.dim_question AS
SELECT
    q.id                        AS question_id,
    q.code                      AS question_code,
    q.kind,
    q.max_marks,
    q."order"                   AS question_order,
    q.is_active,
    q.section_id,
    sec.title                   AS section_title,
    sec.template_id,
    t.name                      AS template_name
FROM staging.questions q
LEFT JOIN staging.sections sec  ON sec.id = q.section_id
LEFT JOIN staging.templates t   ON t.id = sec.template_id;


-- ── FACTS ────────────────────────────────────────────────────

CREATE OR REPLACE VIEW reporting.fact_attempt AS
SELECT
    a.id                        AS attempt_id,
    a.code                      AS attempt_code,
    a.status,
    a.timed_out,
    a.session_id,
    a.template_id,
    a.learner_hash,
    a.started_at,
    a.submitted_at,
    a.finalised_at,
    a.moderated_at,
    -- Duration in minutes from start to submission
    a.duration_minutes,
    a.is_valid_duration,
    COALESCE(a.timed_out, FALSE) AS did_time_out,
    -- Convenience flags
    (a.status = 'submitted')    AS is_submitted,
    (a.finalised_at IS NOT NULL) AS is_finalised,
    (a.moderated_at IS NOT NULL) AS is_moderated,
    t.name                      AS template_name,
    s.code                      AS session_code
FROM staging.attempts a
LEFT JOIN staging.templates t   ON t.id = a.template_id
LEFT JOIN staging.sessions s    ON s.id = a.session_id;


CREATE OR REPLACE VIEW reporting.fact_score AS
SELECT
    sc.id                       AS score_id,
    sc.response_id,
    sc.points,
    sc.max_points,
    sc.assessor_hash,
    sc.created_at               AS scored_at,
    -- Derived flags
    (sc.points = 0)             AS is_zero,
    (sc.points = sc.max_points) AS is_full_marks,
    sc.needs_review,
    -- Response joins
    r.attempt_id,
    r.question_id,
    -- Question context
    q.code                      AS question_code,
    q.kind                      AS question_kind,
    q.section_id,
    sec.title                   AS section_title,
    sec.template_id,
    t.name                      AS template_name
FROM staging.scores sc
LEFT JOIN staging.responses r   ON r.id = sc.response_id
LEFT JOIN staging.questions q   ON q.id = r.question_id
LEFT JOIN staging.sections sec  ON sec.id = q.section_id
LEFT JOIN staging.templates t   ON t.id = sec.template_id;


-- ── AGGREGATES ───────────────────────────────────────────────

-- Backlog snapshot — current state of marking queue
CREATE OR REPLACE VIEW reporting.agg_backlog AS
SELECT
    COUNT(*)
        FILTER (WHERE a.status = 'submitted' AND a.finalised_at IS NULL)
                                AS unfinalised_submitted,
    COUNT(*)
        FILTER (WHERE a.status = 'in_progress')
                                AS in_progress,
    COUNT(*)
        FILTER (WHERE a.finalised_at IS NOT NULL AND a.moderated_at IS NULL)
                                AS finalised_not_moderated,
    COUNT(*)
        FILTER (WHERE a.moderated_at IS NOT NULL)
                                AS moderated,
    COUNT(*)                    AS total_attempts
FROM staging.attempts a;


-- Unscored responses — responses with no score record
CREATE OR REPLACE VIEW reporting.agg_unscored AS
SELECT
    COUNT(*)                    AS unscored_responses
FROM staging.responses r
LEFT JOIN staging.scores sc     ON sc.response_id = r.id
WHERE sc.id IS NULL;


-- Question failure rates — based on finalised attempts only
CREATE OR REPLACE VIEW reporting.agg_question_fail AS
SELECT
    q.code                      AS question_code,
    q.kind,
    sec.title                   AS section_title,
    t.name                      AS template_name,
    COUNT(sc.id)                AS n,
    COUNT(*) FILTER (WHERE sc.points = 0)
                                AS zero_count,
    COUNT(*) FILTER (WHERE sc.points = sc.max_points)
                                AS full_marks_count,
    ROUND(AVG(sc.points)::numeric, 1)
                                AS avg_points,
    MAX(sc.max_points)          AS max_points,
    CASE
        WHEN COUNT(sc.id) = 0 THEN NULL
        ELSE ROUND(
            COUNT(*) FILTER (WHERE sc.points = 0)::numeric
            / COUNT(sc.id) * 100, 1
        )
    END                         AS fail_pct
FROM staging.scores sc
JOIN staging.responses r        ON r.id = sc.response_id
JOIN staging.attempts a         ON a.id = r.attempt_id
JOIN staging.questions q        ON q.id = r.question_id
JOIN staging.sections sec       ON sec.id = q.section_id
JOIN staging.templates t        ON t.id = sec.template_id
WHERE a.finalised_at IS NOT NULL   -- finalised attempts only
GROUP BY q.id, q.code, q.kind, sec.title, t.name
ORDER BY fail_pct DESC NULLS LAST;


-- Duration distribution buckets
CREATE OR REPLACE VIEW reporting.agg_duration AS
SELECT
    template_name,
    COUNT(*)                    AS total,
    COUNT(*) FILTER (WHERE duration_minutes < 60)
                                AS under_1hr,
    COUNT(*) FILTER (WHERE duration_minutes BETWEEN 60 AND 90)
                                AS hr1_to_1hr30,
    COUNT(*) FILTER (WHERE duration_minutes BETWEEN 90 AND 120)
                                AS hr1_30_to_2hr,
    COUNT(*) FILTER (WHERE duration_minutes BETWEEN 120 AND 240)
                                AS over_2hr,
    COUNT(*) FILTER (WHERE did_time_out = TRUE)
                                AS timed_out,
    ROUND(AVG(duration_minutes)::numeric, 1)
                                AS avg_duration_minutes,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_minutes)::numeric, 1)
                                AS median_duration_minutes
FROM reporting.fact_attempt
WHERE is_submitted = TRUE
  AND is_valid_duration = TRUE
GROUP BY template_name;

-- Candidate score summary — per attempt, no PII
CREATE OR REPLACE VIEW reporting.agg_candidate_score AS
SELECT
    a.id                        AS attempt_id,
    a.template_id,
    t.name                      AS template_name,
    a.session_id,
    a.gender,
    a.demographic,
    a.duration_minutes,
    a.is_valid_duration,
    SUM(sc.points)              AS total_points,
    SUM(sc.max_points)          AS max_possible,
    ROUND((SUM(sc.points) / NULLIF(SUM(sc.max_points), 0) * 100)::numeric, 1)
                                AS pct_score
FROM staging.attempts a
JOIN staging.responses r        ON r.attempt_id = a.id
JOIN staging.scores sc          ON sc.response_id = r.id
LEFT JOIN staging.templates t   ON t.id = a.template_id
WHERE a.status = 'submitted'
GROUP BY a.id, a.template_id, t.name, a.session_id,
         a.gender, a.demographic, a.duration_minutes, a.is_valid_duration;


-- Session completion rates
CREATE OR REPLACE VIEW reporting.agg_completion AS
SELECT
    s.id                        AS session_id,
    s.code                      AS session_code,
    t.name                      AS template_name,
    s.created_at                AS session_date,
    s.seat_limit,
    COUNT(a.id)                 AS total_attempts,
    COUNT(*) FILTER (WHERE a.status = 'submitted')
                                AS submitted,
    COUNT(*) FILTER (WHERE a.status = 'incomplete')
                                AS incomplete,
    COUNT(*) FILTER (WHERE a.status = 'in_progress')
                                AS in_progress,
    COUNT(*) FILTER (WHERE a.timed_out = TRUE)
                                AS timed_out,
    ROUND(COUNT(*) FILTER (WHERE a.status = 'submitted')::numeric
        / NULLIF(COUNT(a.id), 0) * 100, 1)
                                AS completion_rate_pct
FROM staging.sessions s
LEFT JOIN staging.attempts a    ON a.session_id = s.id
LEFT JOIN staging.templates t   ON t.id = s.template_id
GROUP BY s.id, s.code, t.name, s.created_at, s.seat_limit
ORDER BY s.created_at DESC;

-- ============================================================
-- COMMENTS
-- ============================================================

COMMENT ON SCHEMA staging   IS 'Raw extracts from app tables. Truncate and reload on each ETL run. etl_writer role only.';
COMMENT ON SCHEMA reporting IS 'Transformed views for Power BI. reporting_reader role. Never grant public or staging access to Power BI credentials.';
COMMENT ON TABLE  staging.etl_run_log IS 'One row per ETL write run. Written in a separate connection after commit so it survives a partial rollback.';
COMMENT ON VIEW   reporting.agg_question_fail IS 'Finalised attempts only. NQF placement level not yet a structured field — pending Attempt model change.';
