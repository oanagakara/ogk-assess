"""
Regression tests for multi-tenant data isolation (PathTenantMiddleware,
require_same_tenant, and the tenant-scoped querysets across assessor.py /
marking.py / learner.py).

These run with MULTI_TENANT_MODE forced on via override_settings, since
that's the only mode where request.tenant is ever set — iCan's deployment
never turns this on, so every code path exercised here is inert there.
"""
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client

from assessment.models import (
    AssessmentTemplate, Attempt, Learner, Tenant, TenantMembership,
)

User = get_user_model()


@pytest.fixture(autouse=True)
def multi_tenant_mode(settings):
    settings.MULTI_TENANT_MODE = True


@pytest.fixture
def tenant_a(tenant):
    """The 'tenant' fixture from conftest.py — reused as tenant A."""
    return tenant


@pytest.fixture
def tenant_b(db):
    return Tenant.objects.create(name="Tenant B", slug="tenant-b")


@pytest.fixture
def attempt_a(tenant_a):
    template = AssessmentTemplate.objects.create(tenant=tenant_a, name="Template A", version="v1")
    learner = Learner.objects.create(tenant=tenant_a, first_names="A", surname="Learner", id_number="1111111111111")
    return Attempt.objects.create(template=template, learner=learner)


@pytest.fixture
def attempt_b(tenant_b):
    template = AssessmentTemplate.objects.create(tenant=tenant_b, name="Template B", version="v1")
    learner = Learner.objects.create(tenant=tenant_b, first_names="B", surname="Learner", id_number="2222222222222")
    return Attempt.objects.create(template=template, learner=learner)


@pytest.fixture
def assessor_a(tenant_a):
    """An assessor belonging to tenant A only (not is_staff)."""
    user = User.objects.create_user(username="assessor_a", password="pass1234", is_staff=False)
    group, _ = Group.objects.get_or_create(name="assessor")
    user.groups.add(group)
    TenantMembership.objects.create(user=user, tenant=tenant_a)
    return user


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(username="admin_user", password="pass1234", is_staff=True, is_superuser=True)


@pytest.mark.django_db
class TestPathTenantMiddleware:
    def test_unknown_tenant_slug_404s(self, client):
        resp = client.get("/no-such-tenant/start/")
        assert resp.status_code == 404

    def test_bare_root_redirects_to_default_tenant(self, client, settings):
        settings.DEFAULT_TENANT_SLUG = "demo"
        resp = client.get("/")
        assert resp.status_code == 302
        assert resp["Location"] == "/demo/"

    def test_reserved_prefix_passes_through_unprefixed(self, client):
        resp = client.get("/health/")
        assert resp.status_code == 200

    def test_login_page_shows_no_tenant_branding(self, client, tenant_a):
        resp = client.get("/accounts/login/")
        assert resp.status_code == 200
        assert tenant_a.name.encode() not in resp.content

    def test_valid_tenant_path_resolves(self, client, tenant_a):
        resp = client.get(f"/{tenant_a.slug}/start/")
        assert resp.status_code == 200


@pytest.mark.django_db
class TestRequireSameTenant:
    def test_own_tenant_dashboard_is_accessible(self, tenant_a, assessor_a):
        client = Client()
        client.force_login(assessor_a)
        resp = client.get(f"/{tenant_a.slug}/assessor/")
        assert resp.status_code == 200

    def test_cross_tenant_dashboard_is_forbidden(self, tenant_a, tenant_b, assessor_a):
        client = Client()
        client.force_login(assessor_a)
        resp = client.get(f"/{tenant_b.slug}/assessor/")
        assert resp.status_code == 403

    def test_admin_has_cross_tenant_access(self, tenant_a, tenant_b, admin_user):
        client = Client()
        client.force_login(admin_user)
        resp_a = client.get(f"/{tenant_a.slug}/assessor/")
        resp_b = client.get(f"/{tenant_b.slug}/assessor/")
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200


@pytest.mark.django_db
class TestCrossTenantQuerysetIsolation:
    def test_mark_attempt_404s_for_wrong_tenant_code(self, tenant_a, assessor_a, attempt_b):
        """The single most important case: attempt_b belongs to tenant_b, but an
        assessor_a request via tenant_a's path must not be able to reach it —
        not even to confirm it exists (404, not 403)."""
        client = Client()
        client.force_login(assessor_a)
        resp = client.get(f"/{tenant_a.slug}/assessor/attempts/{attempt_b.code}/mark/")
        assert resp.status_code == 404

    def test_mark_attempt_succeeds_for_own_tenant_code(self, tenant_a, assessor_a, attempt_a):
        client = Client()
        client.force_login(assessor_a)
        resp = client.get(f"/{tenant_a.slug}/assessor/attempts/{attempt_a.code}/mark/")
        assert resp.status_code == 200

    def test_attempts_list_excludes_other_tenants(self, tenant_a, assessor_a, attempt_a, attempt_b):
        client = Client()
        client.force_login(assessor_a)
        resp = client.get(f"/{tenant_a.slug}/assessor/attempts/?tab=in_progress")
        assert resp.status_code == 200
        assert attempt_a.code.encode() in resp.content
        assert attempt_b.code.encode() not in resp.content

    def test_learner_start_rejects_code_from_other_tenant(self, tenant_a, attempt_b):
        """A learner using tenant A's URL with tenant B's attempt code must be
        told it's invalid, not routed into tenant B's attempt."""
        client = Client()
        resp = client.post(f"/{tenant_a.slug}/start/", {"code": attempt_b.code})
        assert resp.status_code == 200
        assert b"Invalid code" in resp.content

    def test_new_attempt_form_only_offers_own_tenant_templates(self, tenant_a, tenant_b, assessor_a, attempt_a, attempt_b):
        client = Client()
        client.force_login(assessor_a)
        resp = client.get(f"/{tenant_a.slug}/assessor/attempts/new/")
        assert resp.status_code == 200
        assert attempt_a.template.name.encode() in resp.content
        assert attempt_b.template.name.encode() not in resp.content


@pytest.mark.django_db
def test_claim_seat_does_not_count_other_tenants_attempts(tenant_a, tenant_b):
    from assessment.services import claim_seat, SEAT_LIMIT

    template_a = AssessmentTemplate.objects.create(tenant=tenant_a, name="Seat Template A", version="v1")
    template_b = AssessmentTemplate.objects.create(tenant=tenant_b, name="Seat Template B", version="v1")

    from django.utils import timezone
    now = timezone.now()

    # Fill tenant B with SEAT_LIMIT in-progress attempts.
    for i in range(SEAT_LIMIT):
        learner = Learner.objects.create(
            tenant=tenant_b, first_names="B", surname=f"Learner{i}", id_number=f"300000000{i:04d}"
        )
        Attempt.objects.create(
            template=template_b, learner=learner, status=Attempt.IN_PROGRESS, last_activity_at=now,
        )

    # A fresh tenant A attempt should still be able to claim a seat.
    learner_a = Learner.objects.create(tenant=tenant_a, first_names="A", surname="Learner", id_number="4000000000001")
    attempt_a = Attempt.objects.create(template=template_a, learner=learner_a)
    ok, msg = claim_seat(attempt_a)
    assert ok, msg
