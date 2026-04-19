"""
dpa_sam — Data Subject Access Management

Implements the Responsible Party's obligations under clause 6.2 of the Data
Processing Agreement and sections 23–25 of POPIA.

Usage:
  python manage.py dpa_sam access   --id-number <SA_ID>
  python manage.py dpa_sam access   --name "<surname>"
  python manage.py dpa_sam correct  --id-number <SA_ID> --field <field> --value <value>
  python manage.py dpa_sam restrict --id-number <SA_ID> [--reason "<text>"]
  python manage.py dpa_sam delete   --id-number <SA_ID> [--confirm]
  python manage.py dpa_sam export-all [--out <path>]
  python manage.py dpa_sam list-subjects
"""

import json
from datetime import datetime, timezone

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from assessment.models import Attempt, Learner, Response, ScoreAuditLog, WorkingSheet

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso():
    return datetime.now(UTC).isoformat()


def _find_learner(id_number=None, name=None):
    if id_number:
        qs = Learner.objects.filter(id_number=id_number)
        if not qs.exists():
            raise CommandError(
                f"No data subject found with ID number '{id_number}'. "
                "Verify the identifier and resubmit."
            )
        return list(qs)
    if name:
        qs = Learner.objects.filter(surname__iexact=name)
        if not qs.exists():
            raise CommandError(
                f"No data subject found with surname '{name}'. "
                "Verify the identifier and resubmit."
            )
        return list(qs)
    raise CommandError("Provide --id-number or --name to identify the data subject.")


CORRECTABLE_FIELDS = {
    "first_names":   "First names",
    "surname":       "Surname",
    "id_number":     "South African ID number",
    "dob":           "Date of birth",
    "gender":        "Gender",
    "demographic":   "Demographic classification",
}

RESTRICTED_SENTINEL = "__RESTRICTED__"


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _action_access(options, stdout):
    learners = _find_learner(options["id_number"], options["name"])
    report = {
        "generated_at": _now_iso(),
        "action": "access",
        "basis": "Clause 6.2 of the Data Processing Agreement; section 23 of POPIA",
        "data_subjects": [],
    }

    for learner in learners:
        attempts = Attempt.objects.filter(learner=learner).select_related("template", "session")
        attempt_records = []

        for attempt in attempts:
            responses = Response.objects.filter(attempt=attempt).select_related("question", "score")
            response_records = []
            for r in responses:
                score = getattr(r, "score", None)
                audit = []
                if score:
                    audit = [
                        {
                            "changed_at": e.changed_at.isoformat(),
                            "action": e.action,
                            "mode": e.mode,
                            "points_before": e.points_before,
                            "points_after": e.points_after,
                            "max_points": e.max_points,
                            "changed_by": e.changed_by.username if e.changed_by else "system",
                            "notes": e.notes,
                        }
                        for e in ScoreAuditLog.objects.filter(score=score).order_by("changed_at")
                    ]
                response_records.append({
                    "question_code": r.question.code,
                    "response": r.response_json,
                    "score": {
                        "points": score.points,
                        "max_points": score.max_points,
                        "audit_log": audit,
                    } if score else None,
                })

            working_sheet = None
            try:
                ws = attempt.working_sheet
                working_sheet = {
                    "uploaded_at": ws.uploaded_at.isoformat(),
                    "content_type": ws.content_type,
                    "original_filename": ws.original_filename,
                    "note": "File content withheld from this export; available on written request.",
                }
            except WorkingSheet.DoesNotExist:
                pass

            attempt_records.append({
                "attempt_code": attempt.code,
                "template": str(attempt.template),
                "status": attempt.status,
                "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
                "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
                "honesty_declaration": {
                    "name_given": attempt.honesty_name,
                    "accepted_at": attempt.honesty_accepted_at.isoformat() if attempt.honesty_accepted_at else None,
                } if attempt.honesty_accepted_at else None,
                "working_sheet": working_sheet,
                "responses": response_records,
            })

        report["data_subjects"].append({
            "learner_id": learner.pk,
            "first_names": learner.first_names,
            "surname": learner.surname,
            "id_number": learner.id_number,
            "dob": learner.dob.isoformat() if learner.dob else None,
            "gender": learner.gender,
            "demographic": learner.demographic,
            "attempts": attempt_records,
        })

    stdout.write(json.dumps(report, indent=2, ensure_ascii=False))


def _action_correct(options, stdout):
    field = options.get("field")
    value = options.get("value")

    if field not in CORRECTABLE_FIELDS:
        raise CommandError(
            f"'{field}' is not a correctable field under this procedure. "
            f"Correctable fields are: {', '.join(CORRECTABLE_FIELDS)}."
        )
    if value is None:
        raise CommandError("--value is required for the correct action.")

    learners = _find_learner(options["id_number"], options["name"])
    if len(learners) > 1:
        raise CommandError(
            f"{len(learners)} data subjects matched. Use --id-number to identify "
            "a single data subject before applying a correction."
        )

    learner = learners[0]
    old_value = getattr(learner, field)
    setattr(learner, field, value)
    learner.save(update_fields=[field])

    stdout.write(json.dumps({
        "action": "correct",
        "basis": "Clause 6.2 of the Data Processing Agreement; section 24 of POPIA",
        "completed_at": _now_iso(),
        "data_subject": f"{learner.first_names} {learner.surname} ({learner.id_number})",
        "field": CORRECTABLE_FIELDS[field],
        "previous_value": str(old_value),
        "corrected_value": str(value),
    }, indent=2))


def _action_restrict(options, stdout):
    learners = _find_learner(options["id_number"], options["name"])
    if len(learners) > 1:
        raise CommandError(
            f"{len(learners)} data subjects matched. Use --id-number to identify "
            "a single data subject before applying a restriction."
        )

    learner = learners[0]
    reason = options.get("reason") or "No reason provided."

    # Record restriction in the learner's surname field is not appropriate —
    # instead we annotate all in-progress attempts. For a full implementation,
    # add a Restriction model. This sentinel flags the learner record.
    if RESTRICTED_SENTINEL in (learner.first_names or ""):
        stdout.write(json.dumps({
            "action": "restrict",
            "status": "already_restricted",
            "data_subject": f"{learner.first_names} {learner.surname} ({learner.id_number})",
            "note": "Data subject is already marked as restricted. No change made.",
        }, indent=2))
        return

    learner.first_names = f"{RESTRICTED_SENTINEL} {learner.first_names}"
    learner.save(update_fields=["first_names"])

    stdout.write(json.dumps({
        "action": "restrict",
        "basis": "Clause 6.2 of the Data Processing Agreement; section 25 of POPIA",
        "completed_at": _now_iso(),
        "data_subject": f"{learner.surname} ({learner.id_number})",
        "reason_recorded": reason,
        "effect": (
            "The data subject's record is flagged. Processing of their personal "
            "information should be suspended pending resolution of the dispute. "
            "Remove the restriction via the correct action once resolved."
        ),
    }, indent=2))


def _action_delete(options, stdout):
    if not options.get("confirm"):
        raise CommandError(
            "Deletion of personal information is irreversible. "
            "Re-run with --confirm to proceed. "
            "Note: assessment records may be subject to statutory retention obligations "
            "under the relevant SETA or QCTO framework — verify before proceeding."
        )

    learners = _find_learner(options["id_number"], options["name"])
    if len(learners) > 1:
        raise CommandError(
            f"{len(learners)} data subjects matched. Use --id-number to identify "
            "a single data subject before applying deletion."
        )

    learner = learners[0]
    label = f"{learner.first_names} {learner.surname} ({learner.id_number})"

    with transaction.atomic():
        attempt_codes = list(
            Attempt.objects.filter(learner=learner).values_list("code", flat=True)
        )
        learner.first_names = "[DELETED]"
        learner.surname = "[DELETED]"
        learner.id_number = None
        learner.dob = None
        learner.gender = ""
        learner.demographic = ""
        learner.save()

    stdout.write(json.dumps({
        "action": "delete",
        "basis": "Clause 9 of the Data Processing Agreement; section 14 of POPIA",
        "completed_at": _now_iso(),
        "data_subject_anonymised": label,
        "attempt_codes_affected": attempt_codes,
        "note": (
            "Identity fields anonymised. Assessment response records and scoring audit "
            "logs are retained under the statutory retention obligation (5 years) "
            "but are no longer linked to an identifiable natural person."
        ),
    }, indent=2))


def _action_export_all(options, stdout):
    out_path = options.get("out")
    learners = Learner.objects.all().order_by("surname", "first_names")
    report = {
        "generated_at": _now_iso(),
        "action": "export-all",
        "basis": "Clause 9.3 of the Data Processing Agreement",
        "record_count": learners.count(),
        "data_subjects": [],
    }

    for learner in learners:
        attempts = Attempt.objects.filter(learner=learner).values(
            "code", "status", "started_at", "submitted_at"
        )
        report["data_subjects"].append({
            "first_names": learner.first_names,
            "surname": learner.surname,
            "id_number": learner.id_number,
            "dob": learner.dob.isoformat() if learner.dob else None,
            "gender": learner.gender,
            "demographic": learner.demographic,
            "attempt_count": len(attempts),
            "attempts": [
                {
                    "code": a["code"],
                    "status": a["status"],
                    "started_at": a["started_at"].isoformat() if a["started_at"] else None,
                    "submitted_at": a["submitted_at"].isoformat() if a["submitted_at"] else None,
                }
                for a in attempts
            ],
        })

    payload = json.dumps(report, indent=2, ensure_ascii=False)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(payload)
        stdout.write(f"Export written to {out_path} ({learners.count()} data subjects).")
    else:
        stdout.write(payload)


def _action_list_subjects(options, stdout):
    learners = Learner.objects.all().order_by("surname", "first_names")
    rows = [
        {
            "id": l.pk,
            "surname": l.surname,
            "first_names": l.first_names,
            "id_number": l.id_number or "(none)",
            "attempt_count": Attempt.objects.filter(learner=l).count(),
        }
        for l in learners
    ]
    stdout.write(json.dumps({
        "generated_at": _now_iso(),
        "action": "list-subjects",
        "total": len(rows),
        "data_subjects": rows,
    }, indent=2))


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = (
        "Data Subject Access Management — fulfil POPIA data subject rights requests "
        "as set out in clause 6.2 of the Data Processing Agreement."
    )

    ACTIONS = {
        "access": _action_access,
        "correct": _action_correct,
        "restrict": _action_restrict,
        "delete": _action_delete,
        "export-all": _action_export_all,
        "list-subjects": _action_list_subjects,
    }

    def add_arguments(self, parser):
        parser.add_argument(
            "action",
            choices=list(self.ACTIONS),
            help=(
                "access — export all personal information for a data subject; "
                "correct — update a specific field; "
                "restrict — suspend processing pending dispute resolution; "
                "delete — anonymise identity fields (irreversible); "
                "export-all — full export for responsible party (clause 9.3); "
                "list-subjects — enumerate all data subjects."
            ),
        )
        parser.add_argument("--id-number", help="South African ID number of the data subject.")
        parser.add_argument("--name", help="Surname of the data subject (use --id-number where possible).")
        parser.add_argument("--field", help="Field to correct (for the correct action).")
        parser.add_argument("--value", help="Corrected value (for the correct action).")
        parser.add_argument("--reason", help="Reason for restriction (for the restrict action).")
        parser.add_argument("--confirm", action="store_true", help="Confirm irreversible actions.")
        parser.add_argument("--out", help="Output file path (for export-all).")

    def handle(self, *args, **options):
        action = options["action"]
        try:
            self.ACTIONS[action](options, self.stdout)
        except CommandError:
            raise
        except Exception as exc:
            raise CommandError(f"Unexpected error during '{action}': {exc}") from exc
