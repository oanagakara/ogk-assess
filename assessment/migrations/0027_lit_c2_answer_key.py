import json
from django.db import migrations


def update_lit_c2_answer_key(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    new_key = {
        "auto_mark": True,
        "keyword_answer": ["existing", "condition"],
        "partial_marks": 0,
        "flag_always": False,
    }
    Question.objects.filter(code="LIT-C-2").update(answer_key_json=json.dumps(new_key))


def reverse_lit_c2_answer_key(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    old_key = {
        "auto_mark": True,
        "keyword_answer": ["exist"],
        "partial_marks": 0,
        "flag_always": True,
    }
    Question.objects.filter(code="LIT-C-2").update(answer_key_json=json.dumps(old_key))


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0026_working_sheet"),
    ]

    operations = [
        migrations.RunPython(update_lit_c2_answer_key, reverse_lit_c2_answer_key),
    ]
