from django.db import migrations, models


def abandoned_to_incomplete(apps, schema_editor):
    Attempt = apps.get_model("assessment", "Attempt")
    Attempt.objects.filter(status="abandoned").update(status="incomplete")


def incomplete_to_abandoned(apps, schema_editor):
    Attempt = apps.get_model("assessment", "Attempt")
    Attempt.objects.filter(status="incomplete").update(status="abandoned")


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0041_audit_log"),
    ]

    operations = [
        migrations.RunPython(abandoned_to_incomplete, incomplete_to_abandoned),
        migrations.AlterField(
            model_name="attempt",
            name="status",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("in_progress", "In Progress"),
                    ("submitted", "Submitted"),
                    ("incomplete", "Incomplete"),
                ],
                default="in_progress",
            ),
        ),
    ]
