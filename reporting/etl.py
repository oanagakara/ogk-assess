#!/usr/bin/env python3
"""
NQF Assessment Platform — Reporting ETL
Oanagakara (Pty) Ltd

Extracts from app tables (public schema) into staging schema.
Reporting views in the reporting schema are then queryable by Power BI.

Usage:
    python etl.py [--dry-run]            # validate connections, print row counts, no writes (default)
    python etl.py --write                # full run — truncate and reload staging tables
    python etl.py --write --table attempts  # reload a single staging table only
    python etl.py --write --verify       # full run, then print reporting view row counts
    python etl.py --verify-only          # skip ETL; print reporting view row counts

Environment:
    DATABASE_URL   — ETL connection string (SELECT on public.*, write on staging.*)

Run cadence: on demand, or scheduled via cron / GitHub Actions.
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from textwrap import dedent

import psycopg2
import psycopg2.extras
from psycopg2 import sql

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("etl")

# ── Table definitions ─────────────────────────────────────────────────────────
# Each entry: (staging_table, source_query)
# Order matters — respect FK dependencies in staging.

TABLES = {
    "tenants": dedent("""
        SELECT
            id,
            name
        FROM public.assessment_tenant
    """),

    "templates": dedent("""
        SELECT
            id,
            name,
            version,
            moderation_mode,
            created_at
        FROM public.assessment_assessmenttemplate
    """),

    "sections": dedent("""
        SELECT
            id,
            title,
            "order",
            template_id
        FROM public.assessment_section
    """),

    "questions": dedent("""
        SELECT
            id,
            code,
            kind,
            max_marks,
            "order",
            section_id,
            is_active
        FROM public.assessment_question
    """),

    "sessions": dedent("""
        SELECT
            id,
            code,
            seat_limit,
            created_at,
            expires_at,
            template_id
        FROM public.assessment_examsession
    """),

    "attempts": dedent("""
    SELECT
        a.id,
        a.code,
        a.status,
        a.started_at,
        a.submitted_at,
        a.last_activity_at,
        a.finalised_at,
        a.moderated_at,
        a.timed_out,
        a.session_id,
        a.template_id,
        -- Pseudonymous learner identifier — hashed; not a direct FK to the learner table
        md5(a.learner_id::text)     AS learner_hash,
        -- Learner demographics
        l.gender,
        l.demographic,
        -- Transform layer
        CASE
            WHEN a.started_at IS NOT NULL AND a.submitted_at IS NOT NULL
            THEN ROUND(EXTRACT(EPOCH FROM (a.submitted_at - a.started_at)) / 60.0, 1)
            ELSE NULL
        END AS duration_minutes,
        CASE
            WHEN a.started_at IS NOT NULL AND a.submitted_at IS NOT NULL
            AND EXTRACT(EPOCH FROM (a.submitted_at - a.started_at)) / 60.0 BETWEEN 0.1 AND 240
            THEN TRUE
            ELSE FALSE
        END AS is_valid_duration
    FROM public.assessment_attempt a
    LEFT JOIN public.assessment_learner l ON l.id = a.learner_id
"""),

    "responses": dedent("""
        SELECT
            id,
            attempt_id,
            question_id
        FROM public.assessment_response
    """),

    "scores": dedent("""
        SELECT
            id,
            response_id,
            -- Pseudonymous assessor identifier — hashed; not a direct FK to auth_user
            md5(assessor_id::text)                  AS assessor_hash,
            points,
            max_points,
            (rubric_json->>'needs_review')::boolean AS needs_review,
            created_at
        FROM public.assessment_score
    """),
}

# ── Core ETL ──────────────────────────────────────────────────────────────────

def get_connection(database_url: str):
    """Return a psycopg2 connection with autocommit off."""
    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    return conn


def load_table(cursor, table_name: str, source_query: str, dry_run: bool) -> int:
    """
    Truncate staging.<table_name> and reload from source_query.
    Returns row count loaded.
    """
    cursor.execute(f"SELECT COUNT(*) FROM ({source_query}) AS src")
    source_count = cursor.fetchone()[0]

    if dry_run:
        log.info("  [dry-run] staging.%-20s  source rows: %d", table_name, source_count)
        return source_count

    cursor.execute(
        sql.SQL("TRUNCATE {} CASCADE").format(
            sql.Identifier("staging", table_name)
        )
    )
    cursor.execute(
        sql.SQL("INSERT INTO {} {}").format(
            sql.Identifier("staging", table_name),
            sql.SQL(source_query),
        )
    )
    loaded = cursor.rowcount
    log.info("  staging.%-20s  loaded: %d rows", table_name, loaded)
    return loaded


def _write_run_log(database_url: str, summary: dict) -> None:
    """Persist a run summary to staging.etl_run_log in its own connection."""
    conn = get_connection(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO staging.etl_run_log
                    (started_at, finished_at, elapsed_seconds, dry_run,
                     table_counts, error_count, errors)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                summary["started_at"],
                summary.get("finished_at"),
                summary.get("elapsed_seconds"),
                summary["dry_run"],
                psycopg2.extras.Json(summary["tables"]),
                len(summary["errors"]),
                psycopg2.extras.Json(summary["errors"]),
            ))
        conn.commit()
    except Exception as e:
        log.warning("Could not write run log: %s", e)
        conn.rollback()
    finally:
        conn.close()


def run_etl(database_url: str, dry_run: bool, only_table: str | None) -> dict:
    """
    Main ETL routine.
    Returns a summary dict suitable for the run log.
    """
    started = datetime.now(timezone.utc)
    log.info("ETL started at %s%s", started.isoformat(), "  [DRY RUN]" if dry_run else "")

    tables_to_run = (
        {only_table: TABLES[only_table]}
        if only_table
        else TABLES
    )

    summary = {
        "started_at": started.isoformat(),
        "dry_run": dry_run,
        "tables": {},
        "errors": [],
    }

    conn = get_connection(database_url)
    try:
        with conn.cursor() as cur:
            for table, query in tables_to_run.items():
                try:
                    count = load_table(cur, table, query, dry_run)
                    summary["tables"][table] = count
                except Exception as e:
                    log.error("  FAILED staging.%s: %s", table, e)
                    summary["errors"].append({"table": table, "error": str(e)})
                    conn.rollback()
                    raise

        if not dry_run:
            conn.commit()
            log.info("Transaction committed.")
        else:
            conn.rollback()
            log.info("Dry run — rolled back.")

    except Exception:
        conn.rollback()
        log.error("ETL aborted — transaction rolled back.")
        raise
    finally:
        conn.close()

    finished = datetime.now(timezone.utc)
    elapsed = (finished - started).total_seconds()
    summary["finished_at"] = finished.isoformat()
    summary["elapsed_seconds"] = round(elapsed, 2)

    log.info(
        "ETL finished in %.1fs. Tables: %d. Errors: %d.",
        elapsed,
        len(summary["tables"]),
        len(summary["errors"]),
    )

    if not dry_run:
        _write_run_log(database_url, summary)

    return summary


def verify_views(database_url: str):
    """
    Quick sanity check — query each reporting view and print row counts.
    Useful after a full run to confirm the views are working.
    """
    views = [
        "reporting.dim_template",
        "reporting.dim_session",
        "reporting.dim_question",
        "reporting.fact_attempt",
        "reporting.fact_score",
        "reporting.agg_backlog",
        "reporting.agg_unscored",
        "reporting.agg_question_fail",
        "reporting.agg_duration",
        "reporting.agg_candidate_score",
        "reporting.agg_completion",
    ]
    log.info("Verifying reporting views:")
    conn = get_connection(database_url)
    try:
        with conn.cursor() as cur:
            for view in views:
                cur.execute(f"SELECT COUNT(*) FROM {view}")
                row = cur.fetchone()
                log.info("  %-40s  %d rows", view, row[0] if row else 0)
    finally:
        conn.rollback()
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NQF Assessment Reporting ETL")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--write",
        action="store_true",
        help="Write to staging tables. Without this flag, runs as a dry-run.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate connections and print row counts without writing (default behaviour).",
    )

    parser.add_argument(
        "--table",
        choices=list(TABLES.keys()),
        help="Reload a single staging table only.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="After ETL, print row counts for all reporting views.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Skip ETL load. Just print row counts for all reporting views.",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        log.error("DATABASE_URL environment variable is not set.")
        sys.exit(1)

    if args.verify_only:
        verify_views(database_url)
        sys.exit(0)

    dry_run = not args.write
    if dry_run:
        log.info("Running in dry-run mode. Pass --write to commit changes.")

    try:
        summary = run_etl(database_url, dry_run=dry_run, only_table=args.table)
    except Exception as e:
        log.error("ETL failed: %s", e)
        sys.exit(1)

    if args.verify and not dry_run:
        verify_views(database_url)

    if summary["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
