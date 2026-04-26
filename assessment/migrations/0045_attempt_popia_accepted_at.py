from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0044_set_moderation_modes"),
    ]

    operations = [
        migrations.AddField(
            model_name="attempt",
            name="popia_accepted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
