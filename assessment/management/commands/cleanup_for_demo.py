"""
Cleanup production data for demo.

Usage:
  python manage.py cleanup_for_demo --keep IGWNXK6U O7RI56IS CTCDRW62 TWOM1SXJ 2TS77VCR
  python manage.py cleanup_for_demo --keep ... --dry-run   # preview only, no changes
  python manage.py cleanup_for_demo --keep ... --no-backup # skip dumpdata backup

Run on Render shell, or locally with DATABASE_URL set.
"""
import sys
from django.core.management.base import BaseCommand
from django.core.management import call_command


class Command(BaseCommand):
    help = "Wipe test data, keep specified attempts, re-mark auto-scored responses."

    def add_arguments(self, parser):
        parser.add_argument("--keep", nargs="+", required=True, metavar="CODE",
                            help="Attempt codes to preserve (space-separated)")
        parser.add_argument("--dry-run", action="store_true",
                            help="Print what would happen without making changes")
        parser.add_argument("--no-backup", action="store_true",
                            help="Skip the dumpdata backup step")

    def handle(self, *args, **options):
        from assessment.models import Attempt, Learner, Score
        from assessment.auto_mark import auto_mark_attempt

        keep_codes = [c.upper().strip() for c in options["keep"]]
        dry = options["dry_run"]
        prefix = "[DRY RUN] " if dry else ""

        # ── 1. Verify keep list ───────────────────────────────────────────────
        self.stdout.write("\n── Verifying keep list ──────────────────────────────")
        keep_attempts = []
        for code in keep_codes:
            a = Attempt.objects.filter(code=code).first()
            if a:
                self.stdout.write(f"  ✓ {code}  {a.learner.first_names} {a.learner.surname}  [{a.status}]")
                keep_attempts.append(a)
            else:
                self.stdout.write(self.style.ERROR(f"  ✗ {code}  NOT FOUND"))

        if len(keep_attempts) != len(keep_codes):
            self.stdout.write(self.style.ERROR("\nAborted — one or more codes not found."))
            sys.exit(1)

        # ── 2. Count what will be deleted ────────────────────────────────────
        keep_ids = [a.pk for a in keep_attempts]
        keep_learner_ids = list(
            Attempt.objects.filter(pk__in=keep_ids).values_list("learner_id", flat=True)
        )

        delete_attempts = Attempt.objects.exclude(pk__in=keep_ids)
        delete_learners = Learner.objects.exclude(
            pk__in=keep_learner_ids
        ).filter(
            attempt__isnull=True
        ) | Learner.objects.exclude(
            pk__in=keep_learner_ids
        ).filter(
            attempt__pk__in=delete_attempts.values("pk")
        )
        # Simpler: learners with no kept attempts
        delete_learners = Learner.objects.exclude(pk__in=keep_learner_ids)

        self.stdout.write(f"\n── What will be deleted ────────────────────────────")
        self.stdout.write(f"  Attempts to delete:  {delete_attempts.count()}")
        self.stdout.write(f"  Learners to delete:  {delete_learners.count()}")
        self.stdout.write(f"  Attempts to keep:    {len(keep_attempts)}")

        if dry:
            self.stdout.write(self.style.WARNING("\nDry run — no changes made."))
            return

        # ── 3. Backup ────────────────────────────────────────────────────────
        if not options["no_backup"]:
            backup_path = "demo_cleanup_backup.json"
            self.stdout.write(f"\n── Backing up to {backup_path} ─────────────────────")
            with open(backup_path, "w") as f:
                call_command("dumpdata", "--natural-foreign", "--natural-primary",
                             "--indent", "2", stdout=f)
            self.stdout.write(self.style.SUCCESS(f"  Backup saved to {backup_path}"))

        # ── 4. Delete ────────────────────────────────────────────────────────
        self.stdout.write("\n── Deleting ────────────────────────────────────────")
        deleted_attempts, _ = delete_attempts.delete()
        self.stdout.write(f"  Deleted {deleted_attempts} attempt records (responses + scores cascade)")

        deleted_learners, _ = delete_learners.delete()
        self.stdout.write(f"  Deleted {deleted_learners} learner records")

        # ── 5. Re-mark ───────────────────────────────────────────────────────
        self.stdout.write("\n── Re-marking ──────────────────────────────────────")
        for attempt in keep_attempts:
            # Clear auto-marked scores (assessor=None) so we get a clean re-mark
            auto_scores = Score.objects.filter(
                response__attempt=attempt,
                assessor__isnull=True,
            )
            cleared = auto_scores.count()
            auto_scores.delete()

            marked = auto_mark_attempt(attempt)
            self.stdout.write(f"  {attempt.code}  cleared {cleared} auto-scores, re-marked {marked} responses")

        # ── 6. Summary ───────────────────────────────────────────────────────
        self.stdout.write(self.style.SUCCESS(
            f"\n✓ Done. {Attempt.objects.count()} attempts remain, "
            f"{Learner.objects.count()} learners remain."
        ))
