"""Error handlers and error notification helpers."""
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils.html import escape

from ._common import is_staff


_SUPPORT_EMAIL = "support@oanagakara.co.za"
_SILENT_404_PATHS = {"/favicon.ico", "/robots.txt", "/apple-touch-icon.png"}


def _notify(error_type, error_msg, url="", method="", user=""):
    logger.warning(
        "PLATFORM ERROR | %s | %s | %s %s | user:%s",
        error_type, error_msg, method, url, user,
    )
    _send_error_email(error_type, error_msg, url, method, user)


def _send_error_email(error_type, error_msg, url, method, user):
    import threading
    from django.conf import settings
    sender = getattr(settings, "EMAIL_HOST_USER", "")
    if not sender or not getattr(settings, "EMAIL_HOST_PASSWORD", ""):
        return

    def _send():
        from django.core.mail import send_mail
        try:
            send_mail(
                subject=f"[Assessment Platform] {error_type}",
                message=(
                    f"Error type: {error_type}\n"
                    f"Detail:     {error_msg}\n"
                    f"Request:    {method} {url}\n"
                    f"User:       {user or 'anonymous'}\n"
                ),
                from_email=sender,
                recipient_list=[_SUPPORT_EMAIL],
                fail_silently=False,
            )
            logger.warning("ERROR EMAIL sent to %s", _SUPPORT_EMAIL)
        except Exception as exc:
            logger.warning("ERROR EMAIL failed: %s", exc)

    threading.Thread(target=_send, daemon=True).start()


def handler500(request):
    import traceback
    exc_type, exc_value, exc_tb = sys.exc_info()
    error_type = exc_type.__name__ if exc_type else "Error"
    error_msg = str(exc_value) if exc_value else ""
    if exc_tb:
        print("SERVER ERROR TRACEBACK:\n" + "".join(traceback.format_exception(exc_type, exc_value, exc_tb)), file=sys.stderr, flush=True)
    _notify(
        error_type, error_msg,
        url=request.build_absolute_uri(),
        method=request.method,
        user=str(request.user) if request.user.is_authenticated else "anonymous",
    )
    try:
        return render(request, "500.html", {
            "error_type": error_type,
            "error_msg": error_msg,
        }, status=500)
    except Exception:
        return HttpResponse(
            f"<h1>System error</h1><p>{escape(error_type)}: {escape(error_msg)}</p>"
            "<p>Please contact the administrator.</p>",
            status=500,
        )


def error_report(request):
    import hmac
    from django.http import JsonResponse
    if request.method != "POST":
        return HttpResponse(status=405)
    expected = os.environ.get("ERROR_REPORT_SECRET", "")
    provided = request.headers.get("X-Error-Token", "")
    if not expected or not hmac.compare_digest(expected, provided):
        return HttpResponse(status=403)
    try:
        data = json.loads(request.body)
    except Exception:
        data = {}
    _notify(
        f"[Learner Report] {data.get('error_type', 'Unknown')}",
        data.get("error_msg", ""),
        url=data.get("url", ""),
        method="—",
        user="learner (manual report)",
    )
    from django.http import JsonResponse
    return JsonResponse({"ok": True})


def handler400(request, exception=None):
    msg = str(exception) if exception else "The request could not be understood."
    _notify("BadRequest", msg, url=request.build_absolute_uri(), method=request.method)
    return render(request, "400.html", {
        "error_type": "Bad Request",
        "error_msg": msg,
    }, status=400)


def handler403(request, exception=None):
    msg = str(exception) if exception else "Access denied."
    _notify("PermissionDenied", msg, url=request.build_absolute_uri(), method=request.method)
    return render(request, "403.html", {
        "error_type": "Access Denied",
        "error_msg": msg,
    }, status=403)


def handler404(request, exception=None):
    if request.path not in _SILENT_404_PATHS:
        _notify("NotFound", request.path, url=request.build_absolute_uri(), method=request.method)
    return render(request, "404.html", {
        "error_type": "Page Not Found",
        "error_msg": request.path,
    }, status=404)


@login_required
@user_passes_test(is_staff)
def dev_doc_view(request, name: str):
    from django.conf import settings
    if not settings.DEBUG:
        raise Http404
    doc_path = settings.BASE_DIR / "docs" / f"{name}.html"
    if not doc_path.exists():
        raise Http404
    return HttpResponse(doc_path.read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")


_REPORT_CONTENT_TYPES = {
    ".pdf":  "application/pdf",
    ".html": "text/html; charset=utf-8",
    ".htm":  "text/html; charset=utf-8",
}

@login_required
@user_passes_test(is_staff)
def dev_report_file(request):
    from django.conf import settings
    if not settings.DEBUG:
        raise Http404
    filename = request.GET.get("name", "")
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        raise Http404
    report_dir = settings.BASE_DIR / "reporting" / "powerbi"
    file_path = report_dir / filename
    if not file_path.exists() or not file_path.is_file():
        raise Http404
    ext = file_path.suffix.lower()
    content_type = _REPORT_CONTENT_TYPES.get(ext, "application/octet-stream")
    response = HttpResponse(file_path.read_bytes(), content_type=content_type)
    response["Content-Disposition"] = "inline"
    response["Content-Security-Policy"] = "frame-ancestors 'self'"
    return response


@login_required
@user_passes_test(is_staff)
def dev_reporting_dashboard(request):
    from django.conf import settings
    if not settings.DEBUG:
        raise Http404
    report_dir = settings.BASE_DIR / "reporting" / "powerbi"
    files = sorted(
        [f.name for f in report_dir.iterdir()
         if f.is_file() and f.suffix.lower() in _REPORT_CONTENT_TYPES],
        key=str.lower,
    ) if report_dir.exists() else []
    selected = request.GET.get("file", files[0] if files else "")
    return render(request, "assessment/dev_reporting_dashboard.html", {
        "report_files": files,
        "selected_file": selected,
    })


@login_required
@user_passes_test(is_staff)
def error_preview(request, code: int):
    templates = {
        400: ("400.html", "Bad Request", "The request could not be understood by the server."),
        403: ("403.html", "Access Denied", "You do not have permission to access this resource."),
        404: ("404.html", "Page Not Found", "/attempt/XXXXXXXX/q/99/"),
        500: ("500.html", "DatabaseError", "no such table: assessment_example"),
    }
    template, error_type, error_msg = templates.get(code, ("404.html", "Not Found", ""))
    return render(request, template, {
        "error_type": error_type,
        "error_msg": error_msg,
    }, status=code)
