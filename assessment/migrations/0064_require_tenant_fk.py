import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0063_backfill_tenant_ican"),
    ]

    operations = [
        migrations.AlterField(
            model_name="assessmenttemplate",
            name="tenant",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="templates", to="assessment.tenant"),
        ),
        migrations.AlterField(
            model_name="attempt",
            name="tenant",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attempts_all", to="assessment.tenant"),
        ),
        migrations.AlterField(
            model_name="examsession",
            name="tenant",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sessions", to="assessment.tenant"),
        ),
        migrations.AlterField(
            model_name="learner",
            name="tenant",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="learners", to="assessment.tenant"),
        ),
    ]
