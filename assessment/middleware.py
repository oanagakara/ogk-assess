import time

from django.http import Http404
from django.core.exceptions import PermissionDenied, SuspiciousOperation

from .metrics import request_duration_seconds


class ErrorHandlerMiddleware:
    """
    Catches all unhandled exceptions in views — including in DEBUG mode —
    sends a Slack notification and returns the friendly 500 page.
    Http404 and PermissionDenied are left to their own handlers.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        t0 = time.monotonic()
        response = self.get_response(request)
        request_duration_seconds.labels(
            method=request.method,
            status=str(response.status_code),
        ).observe(time.monotonic() - t0)
        return response

    def process_exception(self, request, exception):
        if isinstance(exception, (Http404, PermissionDenied, SuspiciousOperation)):
            return None  # Django handles these via handler404/403/400

        from assessment.views import handler500, _notify
        _notify(type(exception).__name__, str(exception),
                url=request.build_absolute_uri(), method=request.method,
                user=str(request.user) if request.user.is_authenticated else "anonymous")
        return handler500(request)
