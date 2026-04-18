import json
from django.db import migrations


CLEAR_REVIEW_CODES = {
    "LIT-A-3A", "LIT-A-3B", "LIT-A-3C",
    "LIT-B-4",
    "NUM-A-4", "NUM-B-1", "NUM-B-2", "NUM-B-3",
    "NUM-C-1", "NUM-C-3", "NUM-D-2",
}


def clear_needs_review(apps, schema_editor):
    Score = apps.get_model("assessment", "Score")
    for score in Score.objects.filter(
        response__question__code__in=CLEAR_REVIEW_CODES,
    ).select_related("response__question"):
        rubric = score.rubric_json if isinstance(score.rubric_json, dict) else {}
        if rubric.get("needs_review") or rubric.get("verify_working"):
            rubric["needs_review"] = False
            rubric["verify_working"] = False
            score.rubric_json = rubric
            score.save(update_fields=["rubric_json"])


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0038_remove_flag_always_from_automark"),
    ]

    operations = [
        migrations.RunPython(clear_needs_review, migrations.RunPython.noop),
    ]
