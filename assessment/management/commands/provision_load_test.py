"""
Create N learners + attempts against a template for load testing.

Usage:
    python manage.py provision_load_test --template-id 3 --count 500
    python manage.py provision_load_test --template-id 3 --count 500 --out perf/locust/codes.json
    python manage.py provision_load_test --purge   # delete all load-test attempts/learners
"""

import json
import random
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from assessment.models import AssessmentTemplate, Attempt, Learner


_FIRST_NAMES = [
    "Thabo", "Sipho", "Lerato", "Nomvula", "Kagiso", "Zanele", "Tshepo", "Bongi",
    "Sifiso", "Ayanda", "Lindiwe", "Mpho", "Thandeka", "Bongani", "Nkosi", "Palesa",
    "Themba", "Dikeledi", "Lwazi", "Naledi", "Sibusiso", "Refilwe", "Lungelo", "Khanya",
]
_SURNAMES = [
    "Dlamini", "Nkosi", "Mokoena", "Sithole", "Mthembu", "Khumalo", "Ntuli", "Molefe",
    "Motsepe", "Zulu", "Ndlovu", "Mabunda", "Radebe", "Shabalala", "Mahlangu", "Buthelezi",
    "Cele", "Mthethwa", "Gumede", "Hadebe", "Mnguni", "Mkhize", "Khoza", "Mbatha",
]

_LOAD_TEST_MARKER = "LOAD_TEST"


def _fake_id(rng: random.Random) -> str:
    # SA-style 13-digit placeholder: YYMMDD + 7 digits
    yy = rng.randint(80, 99)
    mm = rng.randint(1, 12)
    dd = rng.randint(1, 28)
    tail = "".join(str(rng.randint(0, 9)) for _ in range(7))
    return f"{yy:02d}{mm:02d}{dd:02d}{tail}"


class Command(BaseCommand):
    help = "Provision learners + attempts for load testing"

    def add_arguments(self, parser):
        parser.add_argument("--template-id", type=int, default=3)
        parser.add_argument("--count", type=int, default=500)
        parser.add_argument("--out", default="perf/locust/codes.json",
                            help="Path to write attempt codes JSON")
        parser.add_argument("--purge", action="store_true",
                            help="Delete all load-test data and exit")

    def handle(self, **options):
        if options["purge"]:
            self._purge()
            return

        template_id = options["template_id"]
        count = options["count"]
        out_path = Path(options["out"])

        try:
            template = AssessmentTemplate.objects.get(pk=template_id)
        except AssessmentTemplate.DoesNotExist:
            raise CommandError(f"Template {template_id} not found")

        self.stdout.write(f"Provisioning {count} attempts against '{template.name}' …")

        # Unseeded: a fixed seed made every invocation generate identical
        # id_numbers for the same index, colliding with any prior batch
        # that hadn't been purged yet.
        rng = random.Random()
        codes = []

        with transaction.atomic():
            for i in range(count):
                fn = rng.choice(_FIRST_NAMES)
                sn = rng.choice(_SURNAMES)
                id_num = _fake_id(rng)
                # Ensure id_number uniqueness by appending index
                id_num = id_num[:12] + str(i % 10)

                learner = Learner.objects.create(
                    tenant=template.tenant,
                    first_names=fn,
                    surname=f"{sn}-{_LOAD_TEST_MARKER}-{i}",
                    id_number=id_num,
                    gender=random.choice(["male", "female"]),
                    demographic=random.choice(["African", "Coloured", "Indian", "White"]),
                )
                attempt = Attempt.objects.create(
                    learner=learner,
                    template=template,
                    # last_activity_at=None so claim_seat doesn't count against seat limit
                )
                codes.append({
                    "code": attempt.code,
                    "learner": f"{fn} {sn}",
                    "id_number": id_num,
                    "dob": "1995-06-15",
                })

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(codes, indent=2))

        self.stdout.write(self.style.SUCCESS(
            f"Created {count} attempts. Codes written to {out_path}"
        ))
        self.stdout.write(
            f"Run with: SEAT_LIMIT=500 python manage.py runserver\n"
            f"Then: cd perf/locust && locust --host http://localhost:8000"
        )

    def _purge(self):
        learners = Learner.objects.filter(surname__contains=_LOAD_TEST_MARKER)
        attempts = Attempt.objects.filter(learner__in=learners)
        a_count = attempts.count()
        l_count = learners.count()
        attempts.delete()
        learners.delete()
        self.stdout.write(self.style.SUCCESS(
            f"Purged {a_count} attempts and {l_count} learners."
        ))
