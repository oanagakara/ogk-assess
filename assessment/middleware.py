from django.http import Http404
from django.core.exceptions import PermissionDenied, SuspiciousOperation


class ErrorHandlerMiddleware:
    """
    Catches all unhandled exceptions in views — including in DEBUG mode —
    sends a Slack notification and returns the friendly 500 page.
    Http404 and PermissionDenied are left to their own handlers.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, (Http404, PermissionDenied, SuspiciousOperation)):
            return None  # Django handles these via handler404/403/400

        from assessment.views import handler500, _slack_notify
        _slack_notify(request, type(exception).__name__, str(exception))
        return handler500(request)
