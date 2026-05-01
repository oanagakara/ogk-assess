#!/usr/bin/env bash
# off_render_backup.sh — Monthly off-Render encrypted backup
#
# Usage: bash scripts/off_render_backup.sh [--dry-run] [--keep-plaintext] [--rotate-only]
# Env:   DATABASE_URL          (required) — production Render Postgres URL
#        BACKUP_DIR            (required) — mounted backup medium (USB / external SSD)
#        GPG_PASSPHRASE_FILE   (optional) — file containing the symmetric passphrase
#                                            (one line, no trailing newline). If unset,
#                                            GPG prompts interactively.
#
# Flags:
#   --dry-run         Print planned actions, run nothing. No files written.
#   --keep-plaintext  Skip plaintext deletion (NOT recommended; warned loudly).
#   --rotate-only     Run rotation pass only (no new backup).
#
# Output: encrypted dump in $BACKUP_DIR; SHA-256 line appended to $BACKUP_DIR/backup_log.txt.
# Rotation: encrypted dumps older than 365 days are deleted.

set -u

DRY_RUN=0
KEEP_PLAINTEXT=0
ROTATE_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --dry-run)         DRY_RUN=1 ;;
    --keep-plaintext)  KEEP_PLAINTEXT=1 ;;
    --rotate-only)     ROTATE_ONLY=1 ;;
    -h|--help)
      sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

# ---- Environment validation ----
if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERROR: DATABASE_URL is not set. Export it before running." >&2
  exit 1
fi
if [[ -z "${BACKUP_DIR:-}" ]]; then
  echo "ERROR: BACKUP_DIR is not set. Export it before running." >&2
  exit 1
fi
if [[ ! -d "$BACKUP_DIR" ]]; then
  echo "ERROR: BACKUP_DIR ($BACKUP_DIR) does not exist or is not mounted." >&2
  exit 1
fi
if [[ ! -w "$BACKUP_DIR" ]]; then
  echo "ERROR: BACKUP_DIR ($BACKUP_DIR) is not writable." >&2
  exit 1
fi
for cmd in pg_dump gpg sha256sum; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR: $cmd is not installed." >&2; exit 1; }
done

LOG="$BACKUP_DIR/backup_log.txt"
TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
DUMP_FILE="$BACKUP_DIR/oanagakara_${TIMESTAMP}.dump"
ENCRYPTED_FILE="${DUMP_FILE}.gpg"

# ---- Rotation-only mode ----
if [[ "$ROTATE_ONLY" -eq 1 ]]; then
  echo "Rotation-only mode: removing encrypted dumps older than 365 days..."
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Would remove:"
    find "$BACKUP_DIR" -name "*.dump.gpg" -mtime +365 -print
  else
    removed=$(find "$BACKUP_DIR" -name "*.dump.gpg" -mtime +365 -print -delete | wc -l)
    echo "Removed: $removed file(s)"
  fi
  exit 0
fi

# ---- Dry-run mode ----
if [[ "$DRY_RUN" -eq 1 ]]; then
  redacted_url=$(echo "$DATABASE_URL" | sed -E 's|(postgres(ql)?://[^:]+):[^@]+@|\1:****@|')
  echo "DRY RUN — no files will be written, no commands run."
  echo "Source: $redacted_url"
  echo "Backup dir: $BACKUP_DIR"
  echo "Would:"
  echo "  1. pg_dump → $DUMP_FILE"
  echo "  2. gpg --symmetric --cipher-algo AES256 → $ENCRYPTED_FILE"
  echo "  3. Compute SHA-256 of $ENCRYPTED_FILE"
  echo "  4. Append entry to $LOG"
  if [[ "$KEEP_PLAINTEXT" -eq 1 ]]; then
    echo "  5. KEEP plaintext $DUMP_FILE  (--keep-plaintext set)"
  else
    echo "  5. Delete plaintext $DUMP_FILE"
  fi
  echo "  6. Rotate encrypted dumps older than 365 days"
  exit 0
fi

# ---- Confirmation prompt ----
redacted_url=$(echo "$DATABASE_URL" | sed -E 's|(postgres(ql)?://[^:]+):[^@]+@|\1:****@|')
echo "Source database: $redacted_url"
echo "Backup directory: $BACKUP_DIR"
echo "Will write: $ENCRYPTED_FILE"
read -r -p "Proceed? [y/N] " reply
if [[ ! "$reply" =~ ^[Yy]$ ]]; then
  echo "Aborted."
  exit 0
fi

# ---- Step 1: pg_dump ----
echo "Step 1/6: pg_dump..."
if ! pg_dump "$DATABASE_URL" -Fc -f "$DUMP_FILE"; then
  echo "ERROR: pg_dump failed." >&2
  rm -f "$DUMP_FILE"
  exit 1
fi
echo "  Plaintext dump: $(du -h "$DUMP_FILE" | cut -f1)"

# ---- Step 2: GPG AES-256 encrypt ----
echo "Step 2/6: Encrypt with GPG AES-256..."
if [[ -n "${GPG_PASSPHRASE_FILE:-}" ]]; then
  if [[ ! -f "$GPG_PASSPHRASE_FILE" ]]; then
    echo "ERROR: GPG_PASSPHRASE_FILE ($GPG_PASSPHRASE_FILE) does not exist." >&2
    rm -f "$DUMP_FILE"
    exit 1
  fi
  gpg --batch --yes --pinentry-mode loopback \
      --passphrase-file "$GPG_PASSPHRASE_FILE" \
      --symmetric --cipher-algo AES256 \
      -o "$ENCRYPTED_FILE" "$DUMP_FILE"
else
  # Interactive — GPG prompts (via agent or terminal)
  gpg --symmetric --cipher-algo AES256 -o "$ENCRYPTED_FILE" "$DUMP_FILE"
fi

if [[ ! -f "$ENCRYPTED_FILE" ]]; then
  echo "ERROR: encryption failed; encrypted file not produced." >&2
  rm -f "$DUMP_FILE"
  exit 1
fi
echo "  Encrypted dump: $(du -h "$ENCRYPTED_FILE" | cut -f1)"

# ---- Step 3: SHA-256 ----
echo "Step 3/6: Compute SHA-256..."
HASH=$(sha256sum "$ENCRYPTED_FILE" | awk '{print $1}')
echo "  $HASH"

# ---- Step 4: Append to log ----
echo "Step 4/6: Append to backup_log.txt..."
echo "$(date +%Y-%m-%d) $HASH $(basename "$ENCRYPTED_FILE")" >> "$LOG"

# ---- Step 5: Plaintext handling ----
if [[ "$KEEP_PLAINTEXT" -eq 1 ]]; then
  echo "Step 5/6: --keep-plaintext set; plaintext dump retained at $DUMP_FILE"
  echo "  WARNING: plaintext dump contains learner PII."
  echo "  Do not commit, do not cloud-sync, delete when done."
else
  echo "Step 5/6: Delete plaintext..."
  rm -f "$DUMP_FILE"
fi

# ---- Step 6: Rotation ----
echo "Step 6/6: Rotate encrypted dumps older than 365 days..."
removed=$(find "$BACKUP_DIR" -name "*.dump.gpg" -mtime +365 -print -delete | wc -l)
echo "  Removed: $removed file(s)"

# ---- Verification reminder ----
LAST_RESTORE=$(grep "RESTORE-VERIFIED" "$LOG" 2>/dev/null | tail -n 1 | awk '{print $2}')
if [[ -n "$LAST_RESTORE" ]]; then
  last_restore_epoch=$(date -d "$LAST_RESTORE" +%s 2>/dev/null || echo 0)
  if [[ "$last_restore_epoch" -gt 0 ]]; then
    days_since=$(( ( $(date +%s) - last_restore_epoch ) / 86400 ))
    if [[ "$days_since" -gt 90 ]]; then
      echo
      echo "REMINDER: Last verified restore was $days_since days ago (cadence: quarterly)."
      echo "Run a test restore against a scratch database when convenient,"
      echo "and append 'RESTORE-VERIFIED YYYY-MM-DD' to backup_log.txt."
    fi
  fi
else
  echo
  echo "REMINDER: No restore verification recorded yet."
  echo "At first quarter-end, run a test restore and append"
  echo "'RESTORE-VERIFIED YYYY-MM-DD' to backup_log.txt."
fi

echo
echo "BACKUP COMPLETE — hash logged to $LOG"
