import json
from django.db import migrations


CLEAR_FLAG_ALWAYS = [
    "LIT-B-4",
    "NUM-A-4",
    "NUM-B-1",
    "NUM-B-2",
    "NUM-B-3",
    "NUM-C-1",
    "NUM-C-3",
    "NUM-D-2",
]


def remove_flag_always(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    for code in CLEAR_FLAG_ALWAYS:
        try:
            q = Question.objects.get(code=code)
        except Question.DoesNotExist:
            continue
        key = json.loads(q.answer_key_json or "{}")
        if key.get("flag_always"):
            key["flag_always"] = False
            q.answer_key_json = json.dumps(key)
            q.save(update_fields=["answer_key_json"])


def restore_flag_always(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    for code in CLEAR_FLAG_ALWAYS:
        try:
            q = Question.objects.get(code=code)
        except Question.DoesNotExist:
            continue
        key = json.loads(q.answer_key_json or "{}")
        key["flag_always"] = True
        q.answer_key_json = json.dumps(key)
        q.save(update_fields=["answer_key_json"])


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0037_lit_b4_answer_key"),
    ]

    operations = [
        migrations.RunPython(remove_flag_always, restore_flag_always),
    ]
