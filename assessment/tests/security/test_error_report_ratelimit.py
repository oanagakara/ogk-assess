"""Rate-limit tests for POST /error-report/."""
import pytest
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def _post_report(client, ip="9.8.7.6", token="test-secret"):
    return client.post(
        reverse("assessment:error_report"),
        data=b'{"error_type":"test","error_msg":"msg"}',
        content_type="application/json",
        REMOTE_ADDR=ip,
        HTTP_X_FORWARDED_FOR=ip,
        HTTP_X_ERROR_TOKEN=token,
    )


@pytest.mark.django_db
@override_settings(ERROR_REPORT_SECRET="test-secret")
def test_error_report_allows_up_to_limit():
    client = Client()
    for i in range(3):
        resp = _post_report(client, ip="30.0.0.1")
        assert resp.status_code != 429, f"Request {i+1} should not be rate-limited"


@pytest.mark.django_db
@override_settings(ERROR_REPORT_SECRET="test-secret")
def test_error_report_blocks_after_limit():
    client = Client()
    for _ in range(3):
        _post_report(client, ip="30.0.0.2")
    resp = _post_report(client, ip="30.0.0.2")
    assert resp.status_code == 429


@pytest.mark.django_db
@override_settings(ERROR_REPORT_SECRET="test-secret")
def test_error_report_rate_limit_per_ip():
    client = Client()
    for _ in range(4):
        _post_report(client, ip="30.0.0.3")
    resp = _post_report(client, ip="30.0.0.4")
    assert resp.status_code != 429, "Different IP should not be rate-limited"


@pytest.mark.django_db
def test_error_report_invalid_token_returns_403():
    """Token check still enforced regardless of rate limit (limit not yet reached)."""
    client = Client()
    resp = _post_report(client, ip="30.0.0.5", token="wrong-token")
    assert resp.status_code == 403
