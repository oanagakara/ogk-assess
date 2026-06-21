"""
Tests for KPI-4 role-check gaps:
- set_active_role requires is_assessor (was @login_required only)
- generate_invite requires is_moderator (was is_assessor)
"""
import pytest
from django.contrib.auth.models import Group, User
from django.test import Client
from django.urls import reverse


def _make_user(username, groups=(), is_staff=False):
    u = User.objects.create_user(username=username, password="Pw1234567890!", is_staff=is_staff)
    for g in groups:
        grp, _ = Group.objects.get_or_create(name=g)
        u.groups.add(grp)
    return u


@pytest.fixture
def assessor_user(db):
    return _make_user("test_assessor", groups=["assessor"])


@pytest.fixture
def moderator_user(db):
    return _make_user("test_moderator", groups=["moderator"])


@pytest.fixture
def plain_user(db):
    return _make_user("test_plain")


# ── set_active_role ───────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_set_active_role_blocks_unauthenticated():
    client = Client()
    resp = client.post(reverse("assessment:set_active_role"), {"role": "assessor"})
    assert resp.status_code in (302, 403)
    if resp.status_code == 302:
        assert "/login" in resp["Location"]


@pytest.mark.django_db
def test_set_active_role_blocks_plain_user(plain_user):
    client = Client()
    client.force_login(plain_user)
    resp = client.post(reverse("assessment:set_active_role"), {"role": "assessor"})
    assert resp.status_code in (302, 403)
    if resp.status_code == 302:
        assert "/login" in resp["Location"]


@pytest.mark.django_db
def test_set_active_role_allows_assessor(assessor_user):
    client = Client()
    client.force_login(assessor_user)
    resp = client.post(
        reverse("assessment:set_active_role"),
        {"role": "assessor"},
        follow=False,
    )
    assert resp.status_code in (302,), "Assessor should be able to call set_active_role"


# ── generate_invite ───────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_generate_invite_blocks_plain_assessor(assessor_user):
    """Assessors can no longer generate invites — requires moderator+."""
    client = Client()
    client.force_login(assessor_user)
    resp = client.post(reverse("assessment:generate_invite"), {"role": "assessor"})
    assert resp.status_code in (302, 403)
    if resp.status_code == 302:
        assert "/login" in resp["Location"]


@pytest.mark.django_db
def test_generate_invite_allows_moderator(moderator_user):
    client = Client()
    client.force_login(moderator_user)
    resp = client.post(reverse("assessment:generate_invite"), {"role": "assessor"})
    assert resp.status_code in (200, 302), "Moderator should be able to generate an invite"
