#!/usr/bin/env bash
# render_upgrade.sh
#
# Migrates data from a free Render PostgreSQL instance to a new paid instance.
# Run locally. One-shot, not idempotent.
#
# Required env vars:
#   DATABASE_URL      — current free database connection string
#   NEW_DATABASE_URL  — newly-provisioned paid database connection string
#
# Flags:
#   --dry-run     Print planned actions; touch nothing
#   --keep-dump   Skip the dump-deletion step (warns about PII risk)
#
# See ~/.claude/skills/disaster-recovery/references/free-to-paid-migration.md

set -euo pipefail

DRY_RUN=0
KEEP_DUMP=0

for arg in "$@"; do
  case "$arg" in
    --dry-run)   DRY_RUN=1 ;;
    --keep-dump) KEEP_DUMP=1 ;;
    *)           echo "ERROR: unknown flag: $arg" >&2; exit 2 ;;
  esac
done

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERROR: DATABASE_URL is not set. Export the FREE database URL first." >&2
  exit 1
fi

if [[ -z "${NEW_DATABASE_URL:-}" ]]; then
  echo "ERROR: NEW_DATABASE_URL is not set. Export the PAID database URL first." >&2
  exit 1
fi

TIMESTAMP="$(date +%Y-%m-%d_%H%M)"
DUMP_FILE="tmp/render_upgrade_${TIMESTAMP}.dump"

mask_db_url() {
  # Strip credentials: postgresql://user:pass@host:port/db -> postgresql://[redacted]@host:port/db
  echo "$1" | sed -E 's|://[^@]+@|://[redacted]@|'
}

echo "=== Render Free → Paid Migration ==="
echo "Source (DATABASE_URL):     $(mask_db_url "$DATABASE_URL")"
echo "Target (NEW_DATABASE_URL): $(mask_db_url "$NEW_DATABASE_URL")"
echo "Dump file:                 ${DUMP_FILE}"
echo "Dry run:                   $([[ $DRY_RUN -eq 1 ]] && echo yes || echo no)"
echo "Keep dump after success:   $([[ $KEEP_DUMP -eq 1 ]] && echo yes || echo no)"
echo ""

if [[ $DRY_RUN -eq 1 ]]; then
  echo "[DRY RUN] Would create tmp/ if missing"
  echo "[DRY RUN] Would run: pg_dump \"\$DATABASE_URL\" -Fc -f ${DUMP_FILE}"
  echo "[DRY RUN] Would run: pg_restore --no-owner --no-acl -d \"\$NEW_DATABASE_URL\" ${DUMP_FILE}"
  if [[ $KEEP_DUMP -eq 0 ]]; then
    echo "[DRY RUN] Would delete: ${DUMP_FILE}"
  else
    echo "[DRY RUN] Would keep: ${DUMP_FILE} (--keep-dump set)"
  fi
  echo ""
  echo "[DRY RUN] No files written, no network calls made."
  exit 0
fi

read -r -p "Proceed with migration? (y/N) " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  echo "Aborted."
  exit 1
fi

mkdir -p tmp

echo ""
echo "[1/3] Dumping free database..."
pg_dump "$DATABASE_URL" -Fc -f "$DUMP_FILE"
echo "      Dump complete: ${DUMP_FILE} ($(du -h "$DUMP_FILE" | cut -f1))"

echo ""
echo "[2/3] Restoring to paid database..."
pg_restore --no-owner --no-acl -d "$NEW_DATABASE_URL" "$DUMP_FILE"
echo "      Restore complete."

echo ""
echo "[3/3] Migration finished. Next steps (manual):"
echo ""
echo "      1. Render dashboard → web service → Environment"
echo "         Edit DATABASE_URL to:"
echo "         ${NEW_DATABASE_URL}"
echo "      2. Save (triggers a redeploy automatically)"
echo "      3. After redeploy completes, run:"
echo "         python manage.py check --deploy"
echo "      4. Smoke-test the live URL: log in, view a recent attempt"
echo "      5. Render dashboard → old free database → Settings → Delete"
echo "         (only after verifying the new database is healthy)"
echo ""

if [[ $KEEP_DUMP -eq 0 ]]; then
  rm "$DUMP_FILE"
  echo "Dump file deleted: ${DUMP_FILE} (contained PII — never commit, never cloud-sync)"
else
  echo "WARNING: Dump file retained at ${DUMP_FILE}"
  echo "         The dump contains learner records, password hashes, session tokens."
  echo "         Treat as personal-information artefact under POPIA."
  echo "         Do NOT commit. Do NOT place in Dropbox/iCloud/Google Drive."
  echo "         Encrypt if persisting:"
  echo "           gpg --symmetric --cipher-algo AES256 ${DUMP_FILE}"
  echo "           rm ${DUMP_FILE}"
  echo "         Delete with: rm ${DUMP_FILE}"
fi

echo ""
echo "Done."
