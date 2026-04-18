import json
from django.db import migrations


# Multi-step NUM questions require assessor verification against the physical
# working sheet. flag_always=True causes the engine to award the correct score
# but hold the response in the review queue for working sheet confirmation.
#
# Single-operation questions (NUM-A-1/2/3, NUM-C-2, NUM-D-1) are objectively
# verifiable from the digit alone — no working sheet check required.

REVIEW_KEYS = {
    "NUM-A-4": {"auto_mark": True, "answers": ["21"],          "flag_always": True},
    "NUM-B-1": {"auto_mark": True, "answers": ["30"],          "flag_always": True},
    "NUM-B-2": {"auto_mark": True, "answers": ["50"],          "flag_always": True},
    "NUM-B-3": {"auto_mark": True, "answers": ["360"],         "flag_always": True},
    "NUM-C-1": {"auto_mark": True, "answers": ["8.5", "8,5"],  "flag_always": True},
    "NUM-C-3": {"auto_mark": True, "answers": ["1700"],        "flag_always": True},
    "NUM-D-2": {"auto_mark": True, "answers": ["4"],           "flag_always": True},
}

PREV_KEYS = {
    "NUM-A-4": {"auto_mark": True, "answers": ["21"]},
    "NUM-B-1": {"auto_mark": True, "answers": ["30"]},
    "NUM-B-2": {"auto_mark": True, "answers": ["50"]},
    "NUM-B-3": {"auto_mark": True, "answers": ["360"]},
    "NUM-C-1": {"auto_mark": True, "answers": ["8.5", "8,5"]},
    "NUM-C-3": {"auto_mark": True, "answers": ["1700"]},
    "NUM-D-2": {"auto_mark": True, "answers": ["4"]},
}


def apply(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    for code, key in REVIEW_KEYS.items():
        Question.objects.filter(code=code).update(answer_key_json=json.dumps(key))


def reverse(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    for code, key in PREV_KEYS.items():
        Question.objects.filter(code=code).update(answer_key_json=json.dumps(key))


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0035_automark_curriculum_intent"),
    ]

    operations = [
        migrations.RunPython(apply, reverse),
    ]
