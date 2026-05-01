#!/usr/bin/env bash
# verify_backup_environment.sh
# Confirms the off-Render backup environment is ready to run.
# Does NOT take a backup; only verifies. Safe to run any time.
#
# Usage: bash scripts/verify_backup_environment.sh
# Env:   DATABASE_URL (required), BACKUP_DIR (required)

set -u

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

fail=0
ok()   { echo -e "${GREEN}OK${NC}    $1"; }
warn() { echo -e "${YELLOW}WARN${NC}  $1"; }
err()  { echo -e "${RED}FAIL${NC}  $1"; fail=1; }

echo "== Off-Render backup environment verification =="
echo

# 1. Required commands
for cmd in pg_dump gpg sha256sum date find awk; do
  if command -v "$cmd" >/dev/null 2>&1; then
    ok "$cmd installed"
  else
    err "$cmd not installed"
  fi
done

# 2. Required env vars
if [[ -n "${DATABASE_URL:-}" ]]; then
  ok "DATABASE_URL is set"
else
  err "DATABASE_URL is not set"
fi

if [[ -n "${BACKUP_DIR:-}" ]]; then
  ok "BACKUP_DIR is set ($BACKUP_DIR)"
else
  err "BACKUP_DIR is not set"
fi

# 3. Backup directory exists, writable, has space
if [[ -n "${BACKUP_DIR:-}" ]]; then
  if [[ -d "$BACKUP_DIR" ]]; then
    ok "BACKUP_DIR exists"
  else
    err "BACKUP_DIR does not exist or is not mounted"
  fi

  if [[ -d "$BACKUP_DIR" && -w "$BACKUP_DIR" ]]; then
    ok "BACKUP_DIR is writable"
  elif [[ -d "$BACKUP_DIR" ]]; then
    err "BACKUP_DIR is not writable"
  fi

  if [[ -d "$BACKUP_DIR" ]]; then
    free_mb=$(df -m "$BACKUP_DIR" 2>/dev/null | awk 'NR==2 {print $4}')
    if [[ "${free_mb:-0}" -gt 500 ]]; then
      ok "BACKUP_DIR has ${free_mb}MB free (>=500MB required)"
    else
      err "BACKUP_DIR has only ${free_mb:-0}MB free (need >=500MB)"
    fi
  fi
fi

# 4. backup_log.txt exists and recency check
log="${BACKUP_DIR:-.}/backup_log.txt"
if [[ -f "$log" ]]; then
  ok "backup_log.txt exists ($log)"
  last_line=$(tail -n 1 "$log" 2>/dev/null)
  if [[ -n "$last_line" ]]; then
    echo "      last entry: $last_line"
    last_date=$(echo "$last_line" | awk '{print $1}')
    if [[ -n "$last_date" ]]; then
      last_epoch=$(date -d "$last_date" +%s 2>/dev/null || echo 0)
      if [[ "$last_epoch" -gt 0 ]]; then
        days_old=$(( ( $(date +%s) - last_epoch ) / 86400 ))
        if [[ "$days_old" -le 35 ]]; then
          ok "Last backup is ${days_old} days old (<=35d expected)"
        else
          warn "Last backup is ${days_old} days old — overdue (monthly cadence)"
        fi
      fi
    fi
  fi
else
  warn "backup_log.txt not found — first run? Will be created on first backup."
fi

# 5. Database reachable (read-only check)
if [[ -n "${DATABASE_URL:-}" ]] && command -v psql >/dev/null 2>&1; then
  if PGCONNECT_TIMEOUT=10 psql "$DATABASE_URL" -c "SELECT 1" >/dev/null 2>&1; then
    ok "Database reachable"
  else
    err "Cannot reach database with provided DATABASE_URL"
  fi
elif [[ -n "${DATABASE_URL:-}" ]]; then
  warn "psql not installed; skipping reachability check (pg_dump will still try)"
fi

# 6. GPG can encrypt (dry-run with throwaway data)
if command -v gpg >/dev/null 2>&1; then
  testfile=$(mktemp)
  echo "test" > "$testfile"
  if echo "test-passphrase" | gpg --batch --yes --pinentry-mode loopback \
       --passphrase-fd 0 --symmetric --cipher-algo AES256 -o /dev/null "$testfile" 2>/dev/null; then
    ok "GPG symmetric AES256 encryption working"
  else
    err "GPG encryption test failed"
  fi
  rm -f "$testfile"
fi

echo
if [[ "$fail" -eq 0 ]]; then
  echo -e "${GREEN}All checks passed. Safe to run off_render_backup.sh.${NC}"
else
  echo -e "${RED}One or more checks failed. Fix before running backup.${NC}"
fi
exit "$fail"
