import json
from django.db import migrations


# Correct answer = full marks, no review.
# Working verification is the physical working sheet system.
# The digital response field is not a reliable working detector —
# learners may write working by hand and enter only the final answer.

NEW_KEYS = {
    "NUM-B-2": {"auto_mark": True, "answers": ["50"]},
    "NUM-B-3": {"auto_mark": True, "answers": ["360"]},
    "NUM-C-1": {"auto_mark": True, "answers": ["8.5", "8,5"]},
    "NUM-C-3": {"auto_mark": True, "answers": ["1700"]},
    "NUM-D-2": {"auto_mark": True, "answers": ["4"]},
}

OLD_KEYS = {
    "NUM-B-2": {"auto_mark": True, "answers": ["50"],         "working_keywords": ["200", "25", "100"], "partial_marks": 1, "flag_if_no_working": True},
    "NUM-B-3": {"auto_mark": True, "answers": ["360"],        "working_keywords": ["40"],               "partial_marks": 1},
    "NUM-C-1": {"auto_mark": True, "answers": ["8.5", "8,5"],"working_keywords": ["16:30", "08:00"],   "partial_marks": 1},
    "NUM-C-3": {"auto_mark": True, "answers": ["1700"],       "working_keywords": ["2300"],             "partial_marks": 1},
    "NUM-D-2": {"auto_mark": True, "answers": ["4"],          "working_keywords": ["20"],               "partial_marks": 1, "flag_always": True},
}


def apply(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    for code, key in NEW_KEYS.items():
        Question.objects.filter(code=code).update(answer_key_json=json.dumps(key))


def reverse(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    for code, key in OLD_KEYS.items():
        Question.objects.filter(code=code).update(answer_key_json=json.dumps(key))


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0032_marking_notes_and_rubrics"),
    ]

    operations = [
        migrations.RunPython(apply, reverse),
    ]
