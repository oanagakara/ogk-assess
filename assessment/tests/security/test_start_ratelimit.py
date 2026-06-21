"""Rate-limit tests for the /start/ learner code-entry endpoint."""
import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def _post_start(client, ip="5.6.7.8"):
    return client.post(
        reverse("assessment:start"),
        {"code": "INVALID1"},
        REMOTE_ADDR=ip,
        HTTP_X_FORWARDED_FOR=ip,
    )


@pytest.mark.django_db
def test_start_allows_up_to_limit():
    client = Client()
    for i in range(20):
        resp = _post_start(client, ip="20.0.0.1")
        assert resp.status_code != 429, f"Request {i+1} should not be rate-limited"


@pytest.mark.django_db
def test_start_blocks_after_limit():
    client = Client()
    for _ in range(20):
        _post_start(client, ip="20.0.0.2")
    resp = _post_start(client, ip="20.0.0.2")
    assert resp.status_code == 429


@pytest.mark.django_db
def test_start_rate_limit_is_per_ip():
    client = Client()
    for _ in range(21):
        _post_start(client, ip="20.0.0.3")
    resp = _post_start(client, ip="20.0.0.4")
    assert resp.status_code != 429, "Different IP should not be affected"
