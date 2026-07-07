"""
One-shot, idempotent bootstrap for the free-tier multi-tenant service's
first tenant ("demo"). Every subsequent prospect tenant is onboarded via
the normal invite flow (see assessment/views/auth.py:register) — this
command exists only because the very first tenant has no one yet to send
an invite from.

Usage:
    python manage.py bootstrap_demo_tenant
    python manage.py bootstrap_demo_tenant --username demo_assessor --password ...
"""
import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand

from assessment.models import Tenant, TenantMembership

User = get_user_model()


class Command(BaseCommand):
    help = "Create (or reuse) the 'demo' tenant, an assessor account for it, and seed a template."

    def add_arguments(self, parser):
        parser.add_argument("--slug", default="demo", help="Tenant slug (default: demo)")
        parser.add_argument("--username", default="demo_assessor", help="Assessor account to create/attach")
        parser.add_argument("--password", default=None, help="Password for the assessor account (random if omitted)")

    def handle(self, *args, **opts):
        slug = opts["slug"]
        username = opts["username"]

        tenant, tenant_created = Tenant.objects.get_or_create(
            slug=slug,
            defaults={"name": slug.capitalize(), "is_active": True},
        )
        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if tenant_created else 'Reusing'} tenant: {tenant.slug}"
        ))

        password = opts["password"] or secrets.token_urlsafe(12)
        user, user_created = User.objects.get_or_create(username=username, defaults={"is_staff": False})
        if user_created:
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Created assessor account: {username} / {password}"))
        else:
            self.stdout.write(f"Reusing existing account: {username}")

        group, _ = Group.objects.get_or_create(name="assessor")
        user.groups.add(group)

        TenantMembership.objects.get_or_create(user=user, defaults={"tenant": tenant})

        call_command("seed_questions", tenant=slug)
        self.stdout.write(self.style.SUCCESS(f"Seeded assessment template for tenant '{slug}'."))
