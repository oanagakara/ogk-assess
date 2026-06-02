# DR Tabletop Runbook
## NQF Learner Placement Assessment Platform
### Database Migration: Personal Neon → Oanagakara (Pty) Ltd Neon Org

**Version:** 1.0  
**Date:** 2026-06-02  
**Author:** Platform operator — Donavan Bugan  
**Relates to:** DR Plan v1.0 Section 8 — Verification Cadence

---

## Prerequisites

- WSL2 (Ubuntu) with sudo access
- Access to source `DATABASE_URL` (Render environment variables)
- Access to target `COMPANY_DATABASE_URL` (Neon org connection string)
- Git repo cloned locally

---

## Step 1 — Create tabletop branch

```bash
git checkout -b migrate/neon-company-dr-tabletop
```

---

## Step 2 — Create artefacts directory

```bash
mkdir -p dr-artefacts
cd dr-artefacts
```

---

## Step 3 — Install PostgreSQL 17 client tools

The client version must match the server version. Verify server version first:

```bash
psql "$DATABASE_URL" -c "SELECT version();"
```

Then install matching client tools:

```bash
sudo apt install -y postgresql-common
sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh
sudo apt update
sudo apt install -y postgresql-client-17
```

Verify:

```bash
pg_dump --version
# Expected: pg_dump (PostgreSQL) 17.x
```

---

## Step 4 — Set connection strings in shell

Never write connection strings to files. Export to shell only — they evaporate on terminal close.

```bash
export DATABASE_URL="postgresql://..."           # source (personal/old)
export COMPANY_DATABASE_URL="postgresql://..."   # target (company Neon org)
```

---

## Step 5 — Dump source database

Run from inside `dr-artefacts/`:

```bash
pg_dump "$DATABASE_URL" \
  --no-owner \
  --no-acl \
  --format=custom \
  --file=ogk_assess_migration.dump
```

Flags explained:
- `--no-owner` — strips Render-specific role bindings that would fail on restore
- `--no-acl` — strips access control that doesn't transfer between accounts
- `--format=custom` — compressed, supports selective restore

---

## Step 6 — Verify dump integrity

```bash
pg_restore --list ogk_assess_migration.dump | head -20
```

Expected output includes:
- Archive creation timestamp
- Dump/server version match (both 17.x)
- TOC entry count
- Table names visible in listing

---

## Step 7 — Ensure dump is gitignored

```bash
echo "dr-artefacts/*.dump" >> ../.gitignore
git add ../.gitignore
git commit -m "dr-tabletop: ignore dump files in dr-artefacts"
```

Dump files contain real data. They must never enter git history.

---

## Step 8 — Create company Neon organisation and project

1. Go to neon.tech → create organisation under company name
2. Create project inside the org
3. Region: **AWS Europe West 2 (London)** — lowest latency from SA
4. Copy connection string from: Dashboard → Project → Connection Details

---

## Step 9 — Test company connection

```bash
psql "$COMPANY_DATABASE_URL" -c "SELECT version();"
```

Expected: PostgreSQL 17.x — must match source.

---

## Step 10 — Restore to company instance

```bash
pg_restore \
  --no-owner \
  --no-acl \
  --dbname="$COMPANY_DATABASE_URL" \
  ogk_assess_migration.dump
```

Expected: clean exit, no errors. Takes ~42 seconds for this database size.

---

## Step 11 — Verify restore

Do not use `\dt` — it is unreliable on Neon due to search path. Use the catalog query directly:

```bash
psql "$COMPANY_DATABASE_URL" -c "
SELECT schemaname, tablename 
FROM pg_tables 
WHERE schemaname NOT IN ('pg_catalog', 'information_schema', 'neon_auth') 
ORDER BY tablename;"
```

Expected: 25 tables in `public` schema including `assessment_*`, `auth_*`, `django_*`.

---

## Step 12 — Update DATABASE_URL on Render

1. Render dashboard → your service → Environment
2. Update `DATABASE_URL` to the company Neon connection string
3. **Save only** — do not trigger a manual redeploy, Render applies env changes on next deploy

---

## Step 13 — Smoke test live app

Open the app in browser. Verify:
- Loads without error
- Login works
- Existing data visible

Then verify live writes are going to the company instance:

```bash
psql "$COMPANY_DATABASE_URL" -c "SELECT COUNT(*) FROM django_session;"
```

Session count should be non-zero and increasing if the app is receiving traffic.

---

## Step 14 — Run full test suite

```bash
.venv/bin/python -m pytest assessment/tests/ -v
```

Expected: all tests pass against the company Neon instance.

---

## Step 15 — Keep Claude context local only

Claude project files must not appear in git history or on GitHub. Use `.git/info/exclude`, not `.gitignore`:

```bash
echo ".claude/" >> .git/info/exclude
echo "about.md" >> .git/info/exclude
echo "CLAUDE.md" >> .git/info/exclude
```

Verify:
```bash
cat .git/info/exclude
```

---

## Step 16 — Write verification log

Create `dr-artefacts/tabletop-YYYY-MM-DD.md` with:
- pg_dump result (TOC count)
- pg_restore result (duration, exit status)
- Table count verification
- Render update confirmation
- Smoke test result
- Session count confirmation
- Test suite result (pass count, duration)
- RTO achieved

---

## Step 17 — Commit and push

```bash
git add dr-artefacts/tabletop-YYYY-MM-DD.md
git commit -m "dr-tabletop: verification log - N tests passed, RTO under X hours"
git push origin main
```

---

## Notes

- The dump file is the last-resort recovery artefact. Store a copy on encrypted USB per DR Plan Section 5.
- This runbook covers the **free tier** migration path. When upgrading to Neon Launch (paid), the procedure is identical but PITR is available from that point forward.
- RTO achieved in this exercise: under 2 hours end-to-end including environment setup.
- Next tabletop: per DR Plan Section 8 cadence — before each new client cohort, or quarterly.

---

*NQF Learner Placement Assessment Platform — DR Tabletop Runbook v1.0 · 2026-06-02*
