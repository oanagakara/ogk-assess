import json
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


SPEC = json.dumps({
    "kind_hint": "mcq_or_choice",
    "choices": ["Yes", "No"],
}, ensure_ascii=False)


def add_handwrite_question(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    Section = apps.get_model("assessment", "Section")

    if Question.objects.filter(code="GEN-G-HANDWRITE").exists():
        return

    try:
        section = Section.objects.get(pk=6)
    except Section.DoesNotExist:
        return

    Question.objects.create(
        pk=68,
        section=section,
        order=17,
        code="GEN-G-HANDWRITE",
        prompt="Did you write your essay on a page instead of typing it in the box?",
        kind="text",
        max_marks=0,
        spec_json=SPEC,
        answer_key_json=json.dumps({"auto_mark": False}),
        marking_notes="",
    )


def remove_handwrite_question(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    Question.objects.filter(code="GEN-G-HANDWRITE").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0051_add_gen_g_write_question"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WritingSubmission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("attempt", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="writing_submission",
                    to="assessment.attempt",
                )),
                ("uploaded_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL,
                )),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("content_type", models.CharField(max_length=50)),
                ("original_filename", models.CharField(blank=True, max_length=255)),
                ("data", models.TextField()),
            ],
        ),
        migrations.RunPython(add_handwrite_question, remove_handwrite_question),
    ]
