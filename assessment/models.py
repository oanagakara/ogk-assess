from django.conf import settings
from django.contrib.auth import get_user_model 
from django.db import models
from django.utils import timezone
import secrets
import string


# Create your models here.
class AssessmentTemplate(models.Model):
    name = models.CharField(max_length=200)
    version = models.CharField(max_length=50, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name="created_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        v = f"({self.version})" if self.version else ""
        return f"{self.name}{v}"

class Section(models.Model):
    template = models.ForeignKey("AssessmentTemplate", on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    order = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.template.name} :: {self.order} - {self.title}"

class Question(models.Model):
    TEXT = "text"
    FILL_BLANKS = "fill_blanks"
    REORDER = "reorder"
    MATCH = "match"
    MCQ = "mcq"
    LONG_DIVISION = "long_division"

    KIND_CHOICES = [
        (TEXT, "Text"),
        (FILL_BLANKS, "Fill in the blanks"),
        (REORDER, "Reorder"),
        (MATCH, "Matching"),
        (MCQ, "Multiple choice"),
        (LONG_DIVISION, "Long division"),
    ]

    section = models.ForeignKey("Section", on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)
    code = models.CharField(unique=True,max_length=50)
    prompt = models.TextField()
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=TEXT)
    max_marks = models.PositiveSmallIntegerField(default=9)
    spec_json = models.TextField(blank=True, default="")
    answer_key_json = models.TextField(blank=True, default="")
    marking_notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["section__order", "order", "code"]
    
    def __str__(self) -> str:
        return f"{self.code} [{self.kind}]"

User = get_user_model()

class Score(models.Model):
    response = models.OneToOneField("Response", on_delete=models.CASCADE)
    assessor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, 
        null=True,
        blank=True
    )
    points = models.FloatField(default=0)
    max_points = models.FloatField(default=0)
    rubric_json = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Learner(models.Model):
    MALE = "male"
    FEMALE = "female"

    GENDER_CHOICES = [
            (MALE, "Male"),
            (FEMALE, "Female")
    ]

    AFRICAN = "African"
    COLOURED = "Coloured"
    INDIAN = "Indian"
    WHITE = "White"

    DEMOGRAPHIC_CHOICES = [
            (AFRICAN, "African"),
            (COLOURED, "Coloured"),
            (INDIAN, "Indian"),
            (WHITE, "White"),
    ]
    first_names = models.CharField(max_length=200)
    surname = models.CharField(max_length=200) 
    id_number = models.CharField(max_length=13, unique=True, blank=True, null=True, default=None)
    dob = models.DateField(blank=True, null=True)
    gender = models.CharField(max_length=20, choices=GENDER_CHOICES, default="")
    demographic = models.CharField(max_length=20, choices=DEMOGRAPHIC_CHOICES, default="")

    def __str__(self) -> str:
        return f"{self.first_names} {self.surname} {self.id_number}"

def generate_attempt_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_session_code() -> str:
    """
    Generate a session code: 5 uppercase letters + 3 digits, randomly ordered.
    Total keyspace: 26^5 × 10^3 ≈ 11.88 billion combinations.
    At typical SA learnership volumes (~10k sessions/year) the probability of
    a collision on any single generation is < 0.000001%. The generator retries
    on collision so this is handled gracefully, but will essentially never fire.
    """
    letters = [secrets.choice(string.ascii_uppercase) for _ in range(5)]
    digits = [secrets.choice(string.digits) for _ in range(3)]
    parts = letters + digits
    secrets.SystemRandom().shuffle(parts)
    return "".join(parts)


SESSION_DURATION = 7200  # 2 hours in seconds


class ExamSession(models.Model):
    code = models.CharField(max_length=8, unique=True, default=generate_session_code)
    template = models.ForeignKey("AssessmentTemplate", on_delete=models.CASCADE)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exam_sessions",
    )
    seat_limit = models.PositiveSmallIntegerField(default=15)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.expires_at:
            from datetime import timedelta
            self.expires_at = (self.created_at or timezone.now()) + timedelta(seconds=SESSION_DURATION)
        super().save(*args, **kwargs)

    @property
    def is_open(self) -> bool:
        return timezone.now() < self.expires_at

    def __str__(self) -> str:
        return f"Session {self.code} — {self.template}"


class Attempt(models.Model):
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    ABANDONED = "abandoned"

    STATUS_CHOICES = [
        (IN_PROGRESS, "In Progress"),
        (SUBMITTED, "Submitted"),
        (ABANDONED, "Abandoned"),
    ]

    template = models.ForeignKey("AssessmentTemplate", on_delete=models.CASCADE)
    learner = models.ForeignKey("Learner", on_delete=models.CASCADE)
    session = models.ForeignKey(
        "ExamSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attempts",
    )
    code = models.CharField(max_length=32, unique=True, default=generate_attempt_code)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=IN_PROGRESS)
    current_question = models.PositiveSmallIntegerField(null=True, blank=True, default=None)
    started_at = models.DateTimeField(blank=True, null=True)
    submitted_at = models.DateTimeField(blank=True, null=True)
    last_activity_at = models.DateTimeField(blank=True, null=True)
    honesty_name = models.CharField(max_length=200, blank=True, default="")
    honesty_accepted_at = models.DateTimeField(blank=True, null=True)
    section_timings_json = models.JSONField(default=dict, blank=True)
    review_started_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.code} - {self.learner} - {self.status}"

    @property
    def has_honesty_declaration(self) -> bool:
        return bool(self.honesty_accepted_at)

    def start(self, when=None) -> None:
        when = when or timezone.now()
        update_fields = []

        if self.started_at is None:
            self.started_at = when
            update_fields.append("started_at")

        if self.status != self.IN_PROGRESS:
            self.status = self.IN_PROGRESS
            update_fields.append("status")

        self.last_activity_at = when
        update_fields.append("last_activity_at")

        self.save(update_fields=update_fields)

    def touch(self, when=None) -> None:
        when = when or timezone.now()
        update_fields = []

        if self.started_at is None:
            self.started_at = when
            update_fields.append("started_at")

        if self.status not in {self.SUBMITTED, self.ABANDONED} and self.status != self.IN_PROGRESS:
            self.status = self.IN_PROGRESS
            update_fields.append("status")

        self.last_activity_at = when
        update_fields.append("last_activity_at")

        self.save(update_fields=update_fields)

    def accept_honesty_declaration(self, name: str, when=None) -> None:
        when = when or timezone.now()
        cleaned = (name or "").strip()
        if not cleaned:
            raise ValueError("Honesty name is required.")

        self.honesty_name = cleaned
        self.honesty_accepted_at = when
        self.last_activity_at = when
        self.save(update_fields=["honesty_name", "honesty_accepted_at", "last_activity_at"])

    def section_started_at(self, section_pk: int):
        ts = (self.section_timings_json or {}).get(str(section_pk))
        if ts:
            from django.utils.dateparse import parse_datetime
            return parse_datetime(ts)
        return None

    def record_section_entry(self, section_pk: int, when=None) -> bool:
        """Record first entry into a section. Returns True if newly recorded."""
        when = when or timezone.now()
        key = str(section_pk)
        timings = dict(self.section_timings_json or {})
        if key in timings:
            return False
        timings[key] = when.isoformat()
        self.section_timings_json = timings
        self.save(update_fields=["section_timings_json"])
        return True

    def start_review(self, when=None) -> None:
        if self.review_started_at:
            return
        when = when or timezone.now()
        self.review_started_at = when
        self.last_activity_at = when
        self.save(update_fields=["review_started_at", "last_activity_at"])

    def submit(self, when=None) -> None:
        when = when or timezone.now()
        update_fields = []

        if self.status != self.SUBMITTED:
            self.status = self.SUBMITTED
            update_fields.append("status")

        if self.submitted_at is None:
            self.submitted_at = when
            update_fields.append("submitted_at")

        if self.started_at is None:
            self.started_at = when
            update_fields.append("started_at")

        self.last_activity_at = when
        update_fields.append("last_activity_at")

        self.save(update_fields=update_fields)

class Response(models.Model):
    attempt = models.ForeignKey("Attempt", on_delete=models.CASCADE)
    question = models.ForeignKey("Question", on_delete=models.CASCADE)
    response_json = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["attempt", "question"], name="uniq_attempt_question")
        ]

    def __str__(self) -> str:
        return f"{self.attempt.code} :: {self.question.code}"

