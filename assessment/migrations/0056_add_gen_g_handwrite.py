"""
Ensure GEN-G-HANDWRITE exists on existing production databases.
This question was added to the lit_nqf_general fixture but fixtures are only
loaded once (on first deploy), so existing databases need this migration.
"""
from django.db import migrations


def add_gen_g_handwrite(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    if Question.objects.filter(code="GEN-G-HANDWRITE").exists():
        return
    Section = apps.get_model("assessment", "Section")
    if not Section.objects.filter(pk=6).exists():
        return  # section not loaded yet (fresh deploy or test DB) — fixture will supply it
    Question.objects.create(
        section_id=6,
        order=17,
        code="GEN-G-HANDWRITE",
        prompt="Did you write your essay on a page instead of typing it in the box?",
        kind="text",
        max_marks=0,
        spec_json='{"kind_hint": "mcq_or_choice", "choices": ["Yes", "No"]}',
        answer_key_json='{"auto_mark": false}',
        marking_notes="",
    )


def remove_gen_g_handwrite(apps, schema_editor):
    apps.get_model("assessment", "Question").objects.filter(code="GEN-G-HANDWRITE").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0055_demo_request"),
    ]

    operations = [
        migrations.RunPython(add_gen_g_handwrite, remove_gen_g_handwrite),
    ]
