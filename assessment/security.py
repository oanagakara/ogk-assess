import secrets

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
    """Inject Content-Security-Policy and Permissions-Policy on every response."""
    _CSP_TEMPLATE = (
        "default-src 'self'; "
        "script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "form-action 'self'; "
        "base-uri 'self'; "
        "frame-ancestors 'none';"
    )
    _PERMISSIONS = "camera=(), microphone=(), geolocation=()"
    _COEP = "require-corp"
    _CORP = "same-origin"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(16)
        response = self.get_response(request)
        csp = self._CSP_TEMPLATE.format(nonce=request.csp_nonce)
        response.setdefault("Content-Security-Policy", csp)
        response.setdefault("Permissions-Policy", self._PERMISSIONS)
        response.setdefault("Cross-Origin-Embedder-Policy", self._COEP)
        response.setdefault("Cross-Origin-Resource-Policy", self._CORP)
        response.setdefault("Cache-Control", "no-store, no-cache")
        return response
