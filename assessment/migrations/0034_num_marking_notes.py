import json
from django.db import migrations


# Brief assessor guidance for each numeracy question.
# These are objective questions — the correct answer is definitive.
# Working verification is the physical working sheet system.

NOTES = {
    "NUM-A-1": "Auto-marked. Correct answer: 72. Verify any discrepancy against the working sheet.",
    "NUM-A-2": "Auto-marked. Correct answer: 62. Verify any discrepancy against the working sheet.",
    "NUM-A-3": "Auto-marked. Correct answer: 42. Verify any discrepancy against the working sheet.",
    "NUM-A-4": "Auto-marked. Correct answer: 21. Verify any discrepancy against the working sheet.",
    "NUM-B-1": "Auto-marked. Correct answer: 30. ½ × 60 = 30. Verify any discrepancy against the working sheet.",
    "NUM-B-2": (
        "Auto-marked. Correct answer: 50.\n"
        "Working: 25% of 200 = 200 ÷ 4 = 50 (or 200 × 0.25).\n"
        "Verify working calculations against the physical working sheet."
    ),
    "NUM-B-3": (
        "Auto-marked. Correct answer: R360.\n"
        "Working: 10% of R400 = R40. Discount applied: R400 − R40 = R360.\n"
        "Verify working calculations against the physical working sheet."
    ),
    "NUM-C-1": (
        "Auto-marked. Correct answer: 8.5 hours (accept 8,5).\n"
        "Working: 16:30 − 08:00 = 8 hours 30 minutes = 8.5 hours.\n"
        "Verify working calculations against the physical working sheet."
    ),
    "NUM-C-2": "Auto-marked. Correct answer: 30 km. 15 km × 2 legs = 30 km. Verify any discrepancy against the working sheet.",
    "NUM-C-3": (
        "Auto-marked. Correct answer: R1,700.\n"
        "Working: R4,000 − (R800 + R1,200 + R300) = R4,000 − R2,300 = R1,700.\n"
        "Verify working calculations against the physical working sheet."
    ),
    "NUM-D-1": "Auto-marked. Correct answer: 10. Pattern: +2 each step (2, 4, 6, 8, 10). Verify any discrepancy against the working sheet.",
    "NUM-D-2": (
        "Auto-marked. Correct answer: x = 4.\n"
        "Working: 5x + 10 = 30 → 5x = 20 → x = 4.\n"
        "Verify working calculations against the physical working sheet."
    ),
}


def apply(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    for code, note in NOTES.items():
        Question.objects.filter(code=code).update(marking_notes=note)


def reverse(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    Question.objects.filter(code__in=list(NOTES.keys())).update(marking_notes="")


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0033_num_correct_answer_full_marks"),
    ]

    operations = [
        migrations.RunPython(apply, reverse),
    ]
