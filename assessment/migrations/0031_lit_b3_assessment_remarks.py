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
            "note": (
                "The response identifies a purposive relationship between the learner and "
                "her family as the stated motivation for joining the programme. "
                "Both required elements are present and correctly oriented."
            ),
        },
    ],
    "no_match_note": (
        "The response does not establish the learner's motivational intent toward "
        "joining the programme in relation to family responsibility. "
        "Full comprehension of the question requires identifying the learner as the "
        "agent acting toward a stated purpose — not as the recipient of another's action."
    ),
    "flag_always": False,
}

OLD_KEY = {
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


def apply(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    Question.objects.filter(code="LIT-B-3").update(answer_key_json=json.dumps(NEW_KEY))


def reverse(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    Question.objects.filter(code="LIT-B-3").update(answer_key_json=json.dumps(OLD_KEY))


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0030_lit_b3_require_not"),
    ]

    operations = [
        migrations.RunPython(apply, reverse),
    ]
