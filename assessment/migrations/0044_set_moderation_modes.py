from django.db import migrations


def set_modes(apps, schema_editor):
    AssessmentTemplate = apps.get_model("assessment", "AssessmentTemplate")
    AssessmentTemplate.objects.filter(
        name="NQF Learner Placement Assessment"
    ).update(moderation_mode="full")
    # NQF Literacy stays at default "audit" — no update needed


class Migration(migrations.Migration):
    dependencies = [
        ("assessment", "0043_add_moderation_mode_and_finalisation"),
    ]

    operations = [
        migrations.RunPython(set_modes, migrations.RunPython.noop),
    ]
