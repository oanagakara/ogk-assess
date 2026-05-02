from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0045_attempt_popia_accepted_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="attempt",
            name="section_review_started_at",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
