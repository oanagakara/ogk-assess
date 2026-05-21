"""
Simulate a full exam session with 15 learners at varying competency levels.

Competency distribution:
  Poor          20%  (3 learners)   ~25% of marks
  Fair          40%  (6 learners)   ~50% of marks
  Good          30%  (4 learners)   ~72% of marks
  Above-average 10%  (2 learners)   ~90% of marks

Usage:
  python manage.py simulate_session
  python manage.py simulate_session --session-code ABCD123
"""

import json
import random
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from django.db.models import Count

from assessment.models import (
    AssessmentTemplate,
    Attempt,
    ExamSession,
    Learner,
    Question,
    Response,
    Score,
)

# South African names with demographics, gender, and plausible age
# (first, last, demographic, gender, age)
SA_LEARNERS = [
    ("Thabo",        "Nkosi",          "African",  "male",   34),
    ("Nomvula",      "Dlamini",        "African",  "female", 28),
    ("Sipho",        "Mthembu",        "African",  "male",   42),
    ("Zanele",       "Khumalo",        "African",  "female", 31),
    ("Lungelo",      "Zulu",           "African",  "male",   23),
    ("Ayanda",       "Ndlovu",         "African",  "female", 19),
    ("Bongani",      "Sithole",        "African",  "male",   38),
    ("Nompumelelo",  "Maharaj",        "Indian",   "female", 27),
    ("Kefilwe",      "Mokoena",        "African",  "female", 45),
    ("Jacques",      "van der Merwe",  "White",    "male",   52),
    ("Priya",        "Govender",       "Indian",   "female", 33),
    ("Luyanda",      "Ntuli",          "African",  "male",   29),
    ("Amahle",       "Cele",           "African",  "female", 22),
    ("Riaan",        "Botha",          "White",    "male",   47),
    ("Thandeka",     "Mkhize",         "African",  "female", 36),
]

# (level_label, score_ratio, count)
COMPETENCY_DISTRIBUTION = [
    ("above_average", 0.90, 2),
    ("good",          0.72, 4),
    ("fair",          0.50, 6),
    ("poor",          0.25, 3),
]

WRONG_ANSWERS = {
    "numeric": ["15", "100", "999", "0", "45", "7"],
    "text":    ["I don't know", "Not sure", "No answer", "Maybe"],
}

# Plausible wrong words for match questions
MATCH_FILLERS = ["Apply", "Salary", "Contract", "Interview", "Training", "Leave"]


class Command(BaseCommand):
    help = "Simulate a full exam session with 15 learners at varying competency levels."

    def add_arguments(self, parser):
        parser.add_argument(
            "--session-code",
            type=str,
            default=None,
            help="Use an existing session code instead of creating a new one.",
        )

    def handle(self, *args, **options):
        # Use the template with the most questions (not necessarily the latest by date)
        template = (
            AssessmentTemplate.objects
            .annotate(q_count=Count("section__question"))
            .order_by("-q_count", "-created_at")
            .first()
        )
        if not template or not Question.objects.filter(section__template=template).exists():
            self.stderr.write(self.style.ERROR("No template with questions found."))
            return

        # --- Session ---
        session_code = options.get("session_code")
        if session_code:
            try:
                session = ExamSession.objects.get(code=session_code.upper())
                self.stdout.write(f"Using existing session: {session.code}")
            except ExamSession.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"Session {session_code} not found."))
                return
        else:
            session = ExamSession.objects.create(template=template, seat_limit=15)
            self.stdout.write(self.style.SUCCESS(f"Created session: {session.code}"))

        # --- Questions ---
        questions = list(
            Question.objects.filter(section__template=template)
            .order_by("section__order", "order", "code")
        )
        total_q = len(questions)
        layout_only = {
            q.pk for q in questions
            if json.loads(q.spec_json or "{}").get("layout", "") in
               {"info_only", "info-only", "passage_only"}
        }

        # --- Build learner profiles ---
        profiles = []
        for label, ratio, count in COMPETENCY_DISTRIBUTION:
            for _ in range(count):
                profiles.append((label, ratio))
        random.shuffle(profiles)

        now = timezone.now()

        self.stdout.write(f"\n{'Learner':<25} {'Level':<15} {'Status':<12} {'Q':<6} {'Score'}")
        self.stdout.write("-" * 75)

        for (first, last, demographic, gender, age), (level, ratio) in zip(SA_LEARNERS, profiles):
            import uuid as _uuid
            fake_id = f"S{_uuid.uuid4().hex[:12]}"
            today = timezone.now().date()
            dob = date(today.year - age, today.month, today.day)
            learner = Learner.objects.create(
                first_names=first,
                surname=last,
                id_number=fake_id,
                dob=dob,
                gender=gender,
                demographic=demographic,
            )

            started = now - timedelta(minutes=random.randint(10, 100))

            # Determine status and progress based on level
            if level == "above_average":
                status = Attempt.SUBMITTED
                current_q = total_q
                submitted_at = started + timedelta(minutes=random.randint(70, 105))
            elif level == "good":
                if random.random() < 0.75:
                    status = Attempt.SUBMITTED
                    current_q = total_q
                    submitted_at = started + timedelta(minutes=random.randint(80, 115))
                else:
                    status = Attempt.IN_PROGRESS
                    current_q = random.randint(max(1, int(total_q * 0.8)), total_q)
                    submitted_at = None
            elif level == "fair":
                if random.random() < 0.33:
                    status = Attempt.SUBMITTED
                    current_q = total_q
                    submitted_at = started + timedelta(minutes=random.randint(95, 118))
                else:
                    status = Attempt.IN_PROGRESS
                    current_q = random.randint(max(1, int(total_q * 0.4)), max(1, int(total_q * 0.85)))
                    submitted_at = None
            else:  # poor
                if random.random() < 0.34:
                    status = Attempt.INCOMPLETE
                    current_q = random.randint(1, max(1, int(total_q * 0.25)))
                    submitted_at = None
                else:
                    status = Attempt.IN_PROGRESS
                    current_q = random.randint(1, max(1, int(total_q * 0.4)))
                    submitted_at = None

            # Poor in-progress learners are set 4h stale so they appear as abandoned
            if level == "poor" and status == Attempt.IN_PROGRESS:
                last_activity = now - timedelta(hours=4, minutes=random.randint(0, 60))
            else:
                last_activity = submitted_at or (started + timedelta(minutes=random.randint(5, 95)))

            attempt = Attempt.objects.create(
                template=template,
                learner=learner,
                session=session,
                status=status,
                current_question=current_q,
                started_at=started,
                submitted_at=submitted_at,
                last_activity_at=last_activity,
                honesty_name=f"{first} {last}",
                honesty_accepted_at=started,
            )

            # Responses: for submitted → all questions; in-progress → up to current_q - 1
            if status == Attempt.SUBMITTED:
                qs_to_answer = questions
            else:
                qs_to_answer = questions[: max(0, current_q - 1)]

            total_awarded = 0.0
            total_available = 0.0

            for question in qs_to_answer:
                if question.pk in layout_only:
                    continue  # no response for display-only questions

                response_json = self._make_response_json(question, ratio)
                response = Response.objects.create(
                    attempt=attempt,
                    question=question,
                    response_json=response_json,
                )

                max_marks = float(question.max_marks or 0)
                if max_marks == 0:
                    continue

                points, rubric_json = self._make_score(question, ratio, max_marks)
                Score.objects.create(
                    response=response,
                    points=points,
                    max_points=max_marks,
                    rubric_json=rubric_json,
                )
                total_awarded += points
                total_available += max_marks

            # Finalise above_average submitted attempts (simulates completed marking)
            if level == "above_average" and status == Attempt.SUBMITTED and submitted_at:
                attempt.finalised_at = submitted_at + timedelta(minutes=random.randint(10, 30))
                attempt.save(update_fields=["finalised_at"])

            pct = (total_awarded / total_available * 100) if total_available else 0
            self.stdout.write(
                f"{first + ' ' + last:<25} {level:<15} {status:<12} "
                f"Q{current_q}/{total_q:<4} "
                f"{total_awarded:.1f}/{total_available:.1f} ({pct:.0f}%)"
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Monitor: /assessor/sessions/{session.code}/monitor/"
        ))

    # ------------------------------------------------------------------
    # Response generators
    # ------------------------------------------------------------------

    def _make_response_json(self, question, ratio: float) -> str:
        spec = json.loads(question.spec_json or "{}")
        key = json.loads(question.answer_key_json or "{}")
        layout = spec.get("layout", "")

        if question.kind == "match":
            return self._make_match_response(key, ratio)

        if layout == "form_fill":
            return self._make_form_fill_response(spec, question)

        return self._make_text_response(key, ratio)

    def _make_match_response(self, key: dict, ratio: float) -> str:
        expected = key.get("match", {})
        if not expected:
            return json.dumps({})

        items = list(expected.items())
        n_correct = round(len(items) * ratio)
        random.shuffle(items)
        words = [w for _, w in items]

        data = {}
        for i, (tid, correct_word) in enumerate(items):
            if i < n_correct:
                data[tid] = correct_word
            else:
                # Use a wrong word from the same bank
                wrong = [w for w in words if w != correct_word]
                data[tid] = random.choice(wrong) if wrong else ""
        return json.dumps(data)

    def _make_form_fill_response(self, spec: dict, question) -> str:
        fields = spec.get("fields", [])
        data = {}
        sample_values = {
            "name": "Simulated Learner",
            "id_number": "0001010000000",
            "cell_number": "0821234567",
            "highest_grade": "Grade 11",
            "disability": "",
        }
        for f in fields:
            name = f.get("name", "")
            data[name] = sample_values.get(name, "Simulated value")
        return json.dumps(data)

    def _make_text_response(self, key: dict, ratio: float) -> str:
        """Return correct answer at `ratio` probability, otherwise a wrong answer."""
        # sentence_word question
        if key.get("sentence_word"):
            word = key["sentence_word"]
            if random.random() < ratio:
                return json.dumps({"answer": f"I enjoy learning new {word}s every day at work."})
            return json.dumps({"answer": "This is a sentence."})

        # keyword_answer question
        if key.get("keyword_answer"):
            keywords = key["keyword_answer"]
            if random.random() < ratio:
                return json.dumps({"answer": " ".join(keywords) + " and more context here."})
            # Include only some keywords
            partial = keywords[: max(1, round(len(keywords) * ratio))]
            return json.dumps({"answer": " ".join(partial)})

        # Standard answer list
        answers = key.get("answers", [])
        if answers:
            if random.random() < ratio:
                return json.dumps({"answer": str(answers[0])})
            # Provide wrong answer with some working text
            wrong = random.choice(WRONG_ANSWERS["numeric"])
            return json.dumps({"answer": wrong})

        # No key — open-ended (e.g. LIT-D-WRITE)
        if random.random() < ratio:
            return json.dumps({"answer": (
                "In my opinion, this is an important skill because it helps people "
                "communicate effectively in the workplace. Writing clearly shows "
                "professionalism and makes it easier for colleagues to understand you."
            )})
        return json.dumps({"answer": "I think writing is useful."})

    # ------------------------------------------------------------------
    # Score generator
    # ------------------------------------------------------------------

    def _make_score(self, question, ratio: float, max_marks: float):
        noise = random.uniform(0.88, 1.12)
        raw = min(1.0, max(0.0, ratio * noise))
        # Round to nearest 0.5
        points = round(raw * max_marks * 2) / 2
        points = max(0.0, min(points, max_marks))

        rubric_json = {
            "mode": "manual",
            "auto_marked": False,
            "notes": f"Simulated — {ratio * 100:.0f}% competency band.",
        }
        return points, rubric_json
