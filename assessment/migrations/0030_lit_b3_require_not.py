import json
from django.db import migrations


NEW_KEY = {
    "auto_mark": True,
    "tiered_keyword": [
        {
            "marks": 2,
            "require_all": ["family"],
            "require_any": ["support", "help", "provide", "care", "look after"],
            "require_not": [
                "family supported",
                "family helped",
                "family provided",
                "family cared",
                "supported her",
                "helped her",
            ],
        },
    ],
    "flag_always": False,
}

OLD_KEY = {
    "auto_mark": True,
    "tiered_keyword": [
        {
            "marks": 2,
            "require_all": ["family"],
            "require_any": ["support", "help", "provide", "care", "look after"],
        },
    ],
    "flag_always": False,
}


def apply(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    Question.objects.filter(code="LIT-B-3").update(answer_key_json=json.dumps(NEW_KEY))


def reverse(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    Question.objects.filter(code="LIT-B-3").update(answer_key_json=json.dumps(OLD_KEY))


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0029_lit_b3_answer_key"),
    ]

    operations = [
        migrations.RunPython(apply, reverse),
    ]
