import json
from django.db import migrations


LIT_A_3_GUIDANCE = (
    "Award the mark if the target word is used correctly in a grammatically complete sentence "
    "that demonstrates the learner understands what the word means — not merely that the word appears.\n\n"
    "Do not award the mark if:\n"
    "• The sentence is grammatically incoherent.\n"
    "• The word appears but contradicts its own meaning in context.\n"
    "• The response is a fragment and does not constitute a complete thought.\n\n"
    "The distinction is between a learner who knows the word exists and a learner who can use it. "
    "These are different levels of competence."
)

LIT_B_4_GUIDANCE = (
    "The question asks for the main MESSAGE — a theme or principle drawn from the story — "
    "not a summary of events.\n\n"
    "2 marks: The learner identifies a theme (e.g. the possibility of adult learning, perseverance "
    "through hardship, family responsibility as a motivator for self-improvement) and relates it to "
    "the passage. The response moves from what happened to what it means.\n\n"
    "1 mark: The learner identifies a relevant aspect of the story but frames it as content rather "
    "than message. Partial comprehension is demonstrated — the learner understood the passage but "
    "did not abstract from it.\n\n"
    "0 marks: The learner recites facts without drawing meaning, or the response is unrelated to "
    "the passage.\n\n"
    "A factually accurate response that answers 'What happens in the story?' instead of "
    "'What is the message?' is a comprehension failure and scores zero on this question."
)

LIT_D_WRITE_GUIDANCE = (
    "This is a two-part question. Both parts must be addressed for full marks.\n\n"
    "MOTIVATION — Why I want to join (3 marks):\n"
    "3 — Motivation is clearly stated, specific, and elaborated. References personal circumstances, "
    "career goals, or responsibility toward others.\n"
    "2 — Motivation is stated with some elaboration but lacks specificity or depth.\n"
    "1 — Motivation is mentioned but remains generic and unexplained (e.g. 'I want a better job').\n"
    "0 — No motivation stated, or the response addresses a different question entirely.\n\n"
    "SKILLS — What I want to develop (3 marks):\n"
    "3 — At least two specific skills are named and the learner explains why they are personally relevant.\n"
    "2 — Skills are named with limited elaboration.\n"
    "1 — Skills are mentioned vaguely without specificity (e.g. 'I want to learn things').\n"
    "0 — No skills mentioned.\n\n"
    "LANGUAGE — Quality and coherence (2 marks):\n"
    "2 — Sentences are complete and coherent. Response meets the 6–8 sentence requirement.\n"
    "1 — Some coherence issues but meaning is recoverable. May fall short of the sentence requirement.\n"
    "0 — Response is difficult to follow or too brief to constitute a paragraph.\n\n"
    "A learner who writes fluently about motivation but says nothing about skills has answered "
    "half the question. Score each component independently."
)

LIT_D_WRITE_RUBRIC = {
    "criteria": [
        {"key": "motivation", "label": "Motivation — Why I want to join the learnership", "max_points": 3},
        {"key": "skills",     "label": "Skills — What skills I want to develop",          "max_points": 3},
        {"key": "language",   "label": "Language quality and coherence",                   "max_points": 2},
    ]
}


UPDATES = [
    ("LIT-A-3A",   {"marking_notes": LIT_A_3_GUIDANCE}),
    ("LIT-A-3B",   {"marking_notes": LIT_A_3_GUIDANCE}),
    ("LIT-A-3C",   {"marking_notes": LIT_A_3_GUIDANCE}),
    ("LIT-B-4",    {"marking_notes": LIT_B_4_GUIDANCE}),
    ("LIT-D-WRITE", {
        "marking_notes": LIT_D_WRITE_GUIDANCE,
        "spec_json": json.dumps(LIT_D_WRITE_RUBRIC),
    }),
]


def apply(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    for code, fields in UPDATES:
        Question.objects.filter(code=code).update(**fields)


def reverse(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    codes = [code for code, _ in UPDATES]
    Question.objects.filter(code__in=codes).update(marking_notes="")
    Question.objects.filter(code="LIT-D-WRITE").update(spec_json="")


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0031_lit_b3_assessment_remarks"),
    ]

    operations = [
        migrations.RunPython(apply, reverse),
    ]
