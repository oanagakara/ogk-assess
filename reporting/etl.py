#!/usr/bin/env python3
"""
NQF Assessment Platform — Reporting ETL
Oanagakara (Pty) Ltd

Extracts from app tables (public schema) into staging schema.
Reporting views in the reporting schema are then queryable by Power BI.

Usage:
    python etl.py                    # full run
    python etl.py --dry-run          # validate connections, print row counts, no writes
    python etl.py --table attempts   # reload a single staging table only

Environment:
    DATABASE_URL   — Neon connection string (same DB contains app, staging, reporting)

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
            id,
            code,
            status,
            started_at,
            submitted_at,
            last_activity_at,
            finalised_at,
            moderated_at,
            timed_out,
            session_id,
            template_id,
            learner_id
        FROM public.assessment_attempt
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
            assessor_id,
            points,
            max_points,
            rubric_json,
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
    # Count source rows first
    cursor.execute(f"SELECT COUNT(*) FROM ({source_query}) AS src")
    source_count = cursor.fetchone()[0]

    if dry_run:
        log.info("  [dry-run] staging.%-20s  source rows: %d", table_name, source_count)
        return source_count

    # Truncate staging table
    cursor.execute(f"TRUNCATE staging.{table_name} CASCADE")

    # Copy source → staging using server-side INSERT SELECT
    cursor.execute(f"""
        INSERT INTO staging.{table_name}
        {source_query}
    """)

    loaded = cursor.rowcount
    log.info("  staging.%-20s  loaded: %d rows", table_name, loaded)
    return loaded


def run_etl(database_url: str, dry_run: bool, only_table: str | None) -> dict:
    """
    Main ETL routine.
    Returns a summary dict suitable for the run log.
    """
    env = os.environ.get("REPORTING_ENV", "").lower()
    if env != "production" and not dry_run:
        log.warning("REPORTING_ENV is not set to 'production'. Forcing dry-run.")
        log.warning("Set REPORTING_ENV=production to write to staging tables.")
        dry_run = True

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
                    raise  # re-raise to abort the whole run on any failure

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
    ]
    log.info("Verifying reporting views:")
    conn = get_connection(database_url)
    try:
        with conn.cursor() as cur:
            for view in views:
                cur.execute(f"SELECT COUNT(*) FROM {view}")
                count = cur.fetchone()[0]
                log.info("  %-40s  %d rows", view, count)
    finally:
        conn.rollback()
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NQF Assessment Reporting ETL")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate connections and print row counts without writing to staging.",
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
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        log.error("DATABASE_URL environment variable is not set.")
        sys.exit(1)

    try:
        summary = run_etl(database_url, dry_run=args.dry_run, only_table=args.table)
    except Exception as e:
        log.error("ETL failed: %s", e)
        sys.exit(1)

    if args.verify and not args.dry_run:
        verify_views(database_url)

    if summary["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
