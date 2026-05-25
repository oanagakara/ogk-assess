from django.contrib.auth.signals import user_login_failed
from django.core.cache import cache
from django.dispatch import receiver
from django.http import HttpResponseForbidden


def _get_client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


@receiver(user_login_failed)
def on_login_failure(sender, credentials, request, **kwargs):
    from .metrics import login_failures_total
    login_failures_total.inc()
    if request:
        ip = _get_client_ip(request)
        key = f"login_fail:{ip}"
        count = cache.get(key, 0) + 1
        cache.set(key, count, 600)  # 10-minute sliding window


class LoginRateLimitMiddleware:
    """Block IPs that exceed 10 failed login attempts in 10 minutes (H-4)."""
    MAX_ATTEMPTS = 10

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and "/accounts/login" in request.path:
            ip = _get_client_ip(request)
            if cache.get(f"login_fail:{ip}", 0) >= self.MAX_ATTEMPTS:
                return HttpResponseForbidden(
                    "Too many failed login attempts. Please wait 10 minutes before trying again."
                )
        return self.get_response(request)


class SecurityHeadersMiddleware:
    """
    Inject Content-Security-Policy (M-2) and Permissions-Policy (L-2) on every response.

    unsafe-inline is required because the base template injects tenant brand CSS variables
    and theme-toggle scripts inline. A nonce-based CSP would require template refactoring.
    """
    _CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "frame-ancestors 'none';"
    )
    _PERMISSIONS = "camera=(), microphone=(), geolocation=()"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", self._CSP)
        response.setdefault("Permissions-Policy", self._PERMISSIONS)
        return response
