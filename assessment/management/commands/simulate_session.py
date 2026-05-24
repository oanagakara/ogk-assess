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
from django.db.models import Count
from django.utils import timezone

from assessment.auto_mark import auto_mark_attempt
from assessment.models import (
    AssessmentTemplate,
    Attempt,
    ExamSession,
    Learner,
    Question,
    Response,
    Score,
)

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

MATCH_FILLERS = ["Apply", "Salary", "Contract", "Interview", "Training", "Leave"]

ESSAY_TEXTS = {
    "above_average": (
        "I want to join this learnership because I am committed to building a sustainable career "
        "in this industry. I aim to develop specific skills in communication, customer service, and "
        "problem-solving. These skills will make me a valuable employee and open doors for advancement."
    ),
    "good": (
        "I want to join the learnership to gain work experience and improve my career. "
        "I would like to learn communication and customer service skills. "
        "This will help me find a good job and support my family."
    ),
    "fair": (
        "I want to join the learnership to learn new skills and get a job. "
        "I will work hard and do my best every day."
    ),
    "poor": (
        "I want to learn and get job. Learnership is good for me."
    ),
}


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
        template = (
            AssessmentTemplate.objects
            .annotate(q_count=Count("section__question"))
            .order_by("-q_count", "-created_at")
            .first()
        )
        if not template or not Question.objects.filter(section__template=template).exists():
            self.stderr.write(self.style.ERROR("No template with questions found."))
            return

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

        questions = list(
            Question.objects.filter(section__template=template)
            .order_by("section__order", "order", "code")
        )
        total_q = len(questions)
        layout_only = {
            q.pk for q in questions
            if json.loads(q.spec_json or "{}").get("layout", "")
            in {"info_only", "info-only", "passage_only"}
        }
        essay_q = next((q for q in questions if q.code == "GEN-G-WRITE"), None)

        profiles = []
        for label, ratio, count in COMPETENCY_DISTRIBUTION:
            for _ in range(count):
                profiles.append((label, ratio))
        random.shuffle(profiles)

        now = timezone.now()

        self.stdout.write(f"\n{'Learner':<25} {'Level':<15} {'Status':<12} {'Q':<6} {'Auto score'}")
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
            else:  # poor — always in progress, set stale so they appear abandoned on the monitor
                status = Attempt.IN_PROGRESS
                current_q = random.randint(1, max(1, int(total_q * 0.4)))
                submitted_at = None

            if level == "poor":
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

            if status == Attempt.SUBMITTED:
                qs_to_answer = questions
            else:
                qs_to_answer = questions[: max(0, current_q - 1)]

            for question in qs_to_answer:
                if question.pk in layout_only:
                    continue
                resp_json = self._make_response_json(question, ratio, level)
                Response.objects.create(
                    attempt=attempt,
                    question=question,
                    response_json=resp_json,
                )

            # Auto-mark all scoreable responses (respects auto_mark:false — skips essay)
            auto_mark_attempt(attempt)

            # Essay: only score for above_average (simulates completed marking workflow)
            if essay_q and level == "above_average" and status == Attempt.SUBMITTED:
                try:
                    essay_resp = Response.objects.get(attempt=attempt, question=essay_q)
                    Score.objects.update_or_create(
                        response=essay_resp,
                        defaults={
                            "assessor": None,
                            "points": round(ratio * 8),
                            "max_points": 8,
                            "rubric_json": {
                                "mode": "manual",
                                "auto_marked": False,
                                "needs_review": False,
                                "notes": f"Simulated — {ratio * 100:.0f}% competency band.",
                            },
                        },
                    )
                except Response.DoesNotExist:
                    pass

            # Finalise above_average submitted attempts
            if level == "above_average" and status == Attempt.SUBMITTED and submitted_at:
                attempt.finalised_at = submitted_at + timedelta(minutes=random.randint(10, 30))
                attempt.save(update_fields=["finalised_at"])

            # Report auto-scored totals (essay excluded — pending assessor)
            from assessment.models import Score as S
            scores = S.objects.filter(response__attempt=attempt)
            awarded = sum(float(s.points or 0) for s in scores)
            available = sum(float(s.max_points or 0) for s in scores)
            pct = (awarded / available * 100) if available else 0
            essay_note = " +essay" if (essay_q and level == "above_average") else " essay=pending" if status == Attempt.SUBMITTED else ""
            self.stdout.write(
                f"{first + ' ' + last:<25} {level:<15} {status:<12} "
                f"Q{current_q}/{total_q:<4} "
                f"{awarded:.1f}/{available:.1f} ({pct:.0f}%){essay_note}"
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Monitor: /assessor/sessions/{session.code}/monitor/"
        ))

    def _make_response_json(self, question, ratio: float, level: str) -> str:
        spec = json.loads(question.spec_json or "{}")
        key = json.loads(question.answer_key_json or "{}")

        if question.kind == "match":
            return self._make_match_response(key, ratio)

        if spec.get("layout") == "form_fill":
            return self._make_form_fill_response(spec)

        if spec.get("layout") == "writing" or question.code == "GEN-G-WRITE":
            return json.dumps({"answer": ESSAY_TEXTS.get(level, ESSAY_TEXTS["fair"])})

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
                wrong = [w for w in words if w != correct_word]
                data[tid] = random.choice(wrong) if wrong else ""
        return json.dumps(data)

    def _make_form_fill_response(self, spec: dict) -> str:
        fields = spec.get("fields", [])
        sample = {
            "name": "Simulated Learner",
            "id_number": "0001010000000",
            "cell_number": "0821234567",
            "highest_grade": "Grade 11",
            "disability": "",
        }
        return json.dumps({f.get("name", ""): sample.get(f.get("name", ""), "Simulated value") for f in fields})

    def _make_text_response(self, key: dict, ratio: float) -> str:
        if key.get("sentence_word"):
            word = key["sentence_word"]
            if random.random() < ratio:
                return json.dumps({"answer": f"I enjoy learning new {word}s every day at work."})
            return json.dumps({"answer": "This is a sentence."})

        if key.get("keyword_answer"):
            keywords = key["keyword_answer"]
            if random.random() < ratio:
                return json.dumps({"answer": " ".join(keywords) + " and more context here."})
            partial = keywords[: max(1, round(len(keywords) * ratio))]
            return json.dumps({"answer": " ".join(partial)})

        answers = key.get("answers", [])
        if answers:
            if random.random() < ratio:
                return json.dumps({"answer": str(answers[0])})
            return json.dumps({"answer": random.choice(WRONG_ANSWERS["numeric"])})

        # Open-ended with no key — leave a plausible answer
        if random.random() < ratio:
            return json.dumps({"answer": (
                "This skill is important because it helps people communicate effectively "
                "in the workplace and shows professionalism."
            )})
        return json.dumps({"answer": "I think this is useful."})
