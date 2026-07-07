from django.db import migrations


def backfill_tenant(apps, schema_editor):
    Tenant = apps.get_model("assessment", "Tenant")
    AssessmentTemplate = apps.get_model("assessment", "AssessmentTemplate")
    Learner = apps.get_model("assessment", "Learner")
    Attempt = apps.get_model("assessment", "Attempt")
    ExamSession = apps.get_model("assessment", "ExamSession")

    tenant, _ = Tenant.objects.get_or_create(
        slug="ican",
        defaults={"name": "iCan", "is_active": True},
    )

    AssessmentTemplate.objects.filter(tenant__isnull=True).update(tenant_id=tenant.pk)
    Learner.objects.filter(tenant__isnull=True).update(tenant_id=tenant.pk)
    Attempt.objects.filter(tenant__isnull=True).update(tenant_id=tenant.pk)
    ExamSession.objects.filter(tenant__isnull=True).update(tenant_id=tenant.pk)


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0062_tenant_membership_and_nullable_fks"),
    ]

    operations = [
        migrations.RunPython(backfill_tenant, migrations.RunPython.noop),
    ]
