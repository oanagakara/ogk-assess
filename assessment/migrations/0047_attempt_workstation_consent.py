from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0046_attempt_section_review_started_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="attempt",
            name="workstation_number",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="attempt",
            name="consent_signature_png",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="attempt",
            name="consent_signed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="attempt",
            name="consent_signed_name",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
    ]
