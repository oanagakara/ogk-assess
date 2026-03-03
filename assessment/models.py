from django.db import models
import secrets
import string


# Create your models here.
class AssessmentTemplate(models.Model):
    name = models.CharField(max_length=200)
    version = models.CharField(max_length=50, blank=True, default="")
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

    KIND_CHOICES = [
        (TEXT, "Text"),
        (FILL_BLANKS, "Fill in the blanks"),
        (REORDER, "Reorder"),
        (MATCH, "Matching"),
        (MCQ, "Multiple choice"),

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

    class Meta:
        ordering = [
                "section__order",
                "order",
                "code"
                ]
    
    def __str__(self) -> str:
        return f"{self.code} [{self.kind}]"


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
    id_number = models.CharField(max_length=13, unique=True)
    dob = models.DateField(blank=True, null=True)
    gender = models.CharField(max_length=20, choices=GENDER_CHOICES, default="")
    demographic = models.CharField(max_length=20, choices=DEMOGRAPHIC_CHOICES, default="")

    def __str__(self) -> str:
        return f"{self.first_names} {self.surname} {self.id_number}"

def generate_attempt_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))
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
    code = models.CharField(max_length=32, unique=True, default=generate_attempt_code())
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=IN_PROGRESS)
    started_at = models.DateTimeField(blank=True, null=True)
    submitted_at = models.DateTimeField(blank=True, null=True)
    last_activity_at = models.DateTimeField(blank=True, null=True)
    honesty_name = models.CharField(max_length=200, blank=True, default="") 
    honesty_accepted_at = models.DateTimeField(blank=True, null=True)

    def __str__(self) -> str:
        return f"{self.code} - {self.learner} - {self.status}"

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

