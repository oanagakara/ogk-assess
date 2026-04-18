import json
from django.db import migrations


# LIT-B-4: "What does 'support her family' mean?"
# 2 marks for thematic understanding (responsibility, providing for family, motivation).
# 1 mark for basic comprehension (mentions family or provision without abstraction).
# Always flagged for assessor confirmation — the distinction between 1 and 2 marks
# requires human judgement about whether the learner extracted meaning vs. restated facts.

NEW_KEY = {
    "auto_mark": True,
    "flag_always": True,
    "tiered_keyword": [
        {
            "marks": 2,
            "require_any": [
                "provide", "responsible", "responsibility", "persever",
                "motivat", "look after", "care for", "financially",
                "earn", "better life", "take care",
            ],
            "note": "Response demonstrates thematic understanding — flagged for assessor confirmation.",
        },
        {
            "marks": 1,
            "require_any": [
                "family", "help", "support", "money", "needs", "children",
                "home", "house", "food", "education",
            ],
            "note": "Response shows basic comprehension — flagged for assessor confirmation.",
        },
    ],
    "no_match_note": "No relevant terms detected — likely 0 marks, flagged for assessor confirmation.",
}

PREV_KEY = {}


def apply(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    Question.objects.filter(code="LIT-B-4").update(answer_key_json=json.dumps(NEW_KEY))


def reverse(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    Question.objects.filter(code="LIT-B-4").update(answer_key_json=json.dumps(PREV_KEY))


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0036_num_review_questions"),
    ]

    operations = [
        migrations.RunPython(apply, reverse),
    ]
