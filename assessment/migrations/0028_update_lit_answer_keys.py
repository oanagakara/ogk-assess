import json
from django.db import migrations


UPDATES = {
    "LIT-B-2": {
        "max_marks": 2,
        "answer_key_json": {
            "auto_mark": True,
            "keyword_per_mark": ["Office Administration", "learnership"],
            "marks_per_keyword": 1,
            "flag_always": False,
        },
    },
    "LIT-C-1": {
        "max_marks": 3,
        "answer_key_json": {
            "auto_mark": True,
            "tiered_keyword": [
                {
                    "marks": 3,
                    "require_all": ["full name", "ID number", "contact", "grade", "disability"],
                },
                {
                    "marks": 2,
                    "require_all": ["basic personal information"],
                },
                {
                    "marks": 1,
                    "require_any": ["basic information"],
                },
            ],
            "flag_always": False,
        },
    },
    "LIT-C-2": {
        "max_marks": 2,
        "answer_key_json": {
            "auto_mark": True,
            "tiered_keyword": [
                {
                    "marks": 2,
                    "require_any": ["exist", "exists", "existing", "existence"],
                    "min_words": 4,
                },
                {
                    "marks": 1,
                    "require_any": ["exist", "exists", "existing", "existence"],
                },
            ],
            "flag_always": False,
        },
    },
}

ROLLBACK = {
    "LIT-B-2": {
        "max_marks": 1,
        "answer_key_json": {
            "auto_mark": True,
            "keyword_answer": ["Office Administration", "learnership"],
            "partial_marks": 0,
            "flag_always": False,
        },
    },
    "LIT-C-1": {
        "max_marks": 2,
        "answer_key_json": {
            "auto_mark": True,
            "keyword_answer": ["assess", "basic information"],
            "partial_marks": 1,
            "flag_always": True,
        },
    },
    "LIT-C-2": {
        "max_marks": 1,
        "answer_key_json": {
            "auto_mark": True,
            "keyword_answer": ["existing", "condition"],
            "partial_marks": 0,
            "flag_always": False,
        },
    },
}


def apply_updates(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    for code, fields in UPDATES.items():
        Question.objects.filter(code=code).update(
            max_marks=fields["max_marks"],
            answer_key_json=json.dumps(fields["answer_key_json"]),
        )


def reverse_updates(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    for code, fields in ROLLBACK.items():
        Question.objects.filter(code=code).update(
            max_marks=fields["max_marks"],
            answer_key_json=json.dumps(fields["answer_key_json"]),
        )


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0027_lit_c2_answer_key"),
    ]

    operations = [
        migrations.RunPython(apply_updates, reverse_updates),
    ]
