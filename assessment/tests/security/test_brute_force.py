"""
Brute-force protection tests for django-axes.

Config: AXES_FAILURE_LIMIT=5 locks on the 5th failure itself (axes 8 behaviour).
AXES_LOCKOUT_PARAMETERS=["username","ip_address"] uses OR semantics — a username
OR an IP that exceeds the limit is independently locked.

Each test uses a unique IP prefix to avoid interference from the existing
LoginRateLimitMiddleware (MAX_ATTEMPTS=10, tracked in cache by IP).
"""
import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from axes.models import AccessAttempt


@pytest.fixture(autouse=True)
def reset_axes_state(db):
    AccessAttempt.objects.all().delete()
    cache.clear()
    yield
    AccessAttempt.objects.all().delete()
    cache.clear()


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        username="brute_test_user",
        password="CorrectPassword99!",
        is_staff=True,
    )


def _post_login(client, username, password, ip="1.2.3.4"):
    return client.post(
        reverse("login"),
        {"username": username, "password": password},
        REMOTE_ADDR=ip,
        HTTP_X_FORWARDED_FOR=ip,
    )


@pytest.mark.django_db
def test_lockout_fires_on_5th_failure(staff_user):
    """The 5th consecutive failure triggers 429; first 4 receive 200."""
    client = Client()

    for i in range(4):
        resp = _post_login(client, staff_user.username, "WrongPassword1!", ip="11.0.0.1")
        assert resp.status_code in (200, 302), f"Attempt {i+1} should not yet be locked"

    resp = _post_login(client, staff_user.username, "WrongPassword1!", ip="11.0.0.1")
    assert resp.status_code == 429, "5th failure must return 429"


@pytest.mark.django_db
def test_correct_credentials_succeed_before_lockout(staff_user):
    """4 failures then correct credentials succeeds (no lockout yet)."""
    client = Client()

    for _ in range(4):
        _post_login(client, staff_user.username, "WrongPassword1!", ip="11.0.0.2")

    resp = _post_login(client, staff_user.username, "CorrectPassword99!", ip="11.0.0.2")
    assert resp.status_code in (200, 302)
    assert resp.status_code != 429


@pytest.mark.django_db
def test_attempt_count_tracked_per_username(staff_user, db):
    """A different username on a different IP is not affected by another user's lockout."""
    other = User.objects.create_user(
        username="other_user", password="OtherPassword99!", is_staff=True
    )
    client = Client()

    for _ in range(5):
        _post_login(client, staff_user.username, "WrongPassword1!", ip="11.0.0.3")

    resp = _post_login(client, other.username, "WrongPassword1!", ip="11.0.0.4")
    assert resp.status_code in (200, 302), (
        "Different username on different IP should not be locked"
    )


@pytest.mark.django_db
def test_locked_username_blocked_from_all_ips(staff_user):
    """Once a username is locked (via username OR), a new IP gets the same lockout."""
    client = Client()

    for _ in range(5):
        _post_login(client, staff_user.username, "WrongPassword1!", ip="11.0.0.5")

    resp = _post_login(client, staff_user.username, "WrongPassword1!", ip="11.0.0.6")
    assert resp.status_code == 429, "Locked username must be blocked from any IP"
