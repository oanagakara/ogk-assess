from django.db import migrations, models
from django.utils import timezone


def convert_incomplete_to_submitted(apps, schema_editor):
    Attempt = apps.get_model("assessment", "Attempt")
    now = timezone.now()
    Attempt.objects.filter(status="incomplete").update(
        status="submitted",
        timed_out=True,
        submitted_at=now,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0056_add_gen_g_handwrite"),
    ]

    operations = [
        migrations.AddField(
            model_name="attempt",
            name="timed_out",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(convert_incomplete_to_submitted, migrations.RunPython.noop),
    ]
