import json
from django.db import migrations


SPEC = json.dumps({
    "layout": "writing",
    "prompt": "Why I want to join a learnership and what skills I want to develop.",
    "instructions": [
        "Write 6 to 8 sentences in English.",
        "Spell-check and grammar assist are disabled.",
        "Do not use notes or outside help.",
    ],
}, ensure_ascii=False)

ANSWER_KEY = json.dumps({
    "auto_mark": True,
    "ai_rubric": True,
    "criteria": [
        {
            "key": "motivation",
            "label": "MOTIVATION",
            "max_points": 3,
            "description": (
                "3=clearly stated and specific, elaborated with personal context; "
                "2=stated with some elaboration but lacking depth; "
                "1=generic or unexplained; 0=none or off-topic"
            ),
        },
        {
            "key": "skills",
            "label": "SKILLS",
            "max_points": 3,
            "description": (
                "3=two or more specific skills named with personal relevance explanation; "
                "2=skills named with limited elaboration; "
                "1=vague skills; 0=none"
            ),
        },
        {
            "key": "language",
            "label": "LANGUAGE",
            "max_points": 2,
            "description": (
                "2=complete coherent sentences meeting 6 to 8 requirement; "
                "1=some coherence issues; 0=too brief or incoherent"
            ),
        },
    ],
}, ensure_ascii=False)

MARKING_NOTES = "AI-suggested scores are pre-filled. Review each criterion and adjust as needed before saving."


def add_question(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    Section = apps.get_model("assessment", "Section")

    if Question.objects.filter(code="GEN-G-WRITE").exists():
        return

    try:
        section = Section.objects.get(pk=6)
    except Section.DoesNotExist:
        return

    Question.objects.create(
        pk=67,
        section=section,
        order=16,
        code="GEN-G-WRITE",
        prompt="PART G: WRITING TASK",
        kind="text",
        max_marks=8,
        spec_json=SPEC,
        answer_key_json=ANSWER_KEY,
        marking_notes=MARKING_NOTES,
    )


def remove_question(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    Question.objects.filter(code="GEN-G-WRITE").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0050_assessorinvite_role"),
    ]

    operations = [
        migrations.RunPython(add_question, remove_question),
    ]
