"""Error handlers and error notification helpers."""
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils.html import escape

_ERROR_REPORT_RL_MAX = 3
_ERROR_REPORT_RL_WINDOW = 3600  # 1 hour


def _error_report_get_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")

from ._common import is_staff


_SUPPORT_EMAIL = "support@oanagakara.co.za"
_SILENT_404_PATHS = {
    # Browser / SEO conventions
    "/favicon.ico", "/favicon.png", "/robots.txt", "/humans.txt", "/security.txt",
    "/llms.txt", "/sitemap.xml", "/ads.txt", "/sellers.json", "/apple-touch-icon.png",
    "/service-worker.js",
    # Framework fingerprinting — JS/config roots
    "/app.js", "/main.js", "/bot-connect.js", "/next.config.js",
    "/vite.config.js", "/nuxt.config.js",
    "/firebase.json", "/web.config", "/composer.json",
    "/docker-compose.yml", "/docker-compose.yaml",
    "/Dockerfile", "/Jenkinsfile",
    # Python / Django config probes
    "/settings.py", "/local_settings.py", "/env",
    # .NET / Java config probes
    "/appsettings.json", "/local.settings.json",
    "/config.json", "/config.yaml", "/config.yml",
    "/application.properties", "/application.yaml", "/application.yml",
    # Infra / CI probes
    "/amplify.yml",
    # Auth / admin route probes
    "/debug", "/phpinfo", "/signin", "/signup", "/logout",
    "/api/login", "/api/logout",
}
_SILENT_404_PREFIXES = (
    # Standards / conventions
    "/.well-known/",
    # Dot-file probes
    "/.env", "/.git", "/.github/", "/.gitlab", "/.idea/", "/.vscode/",
    "/.npm", "/.yarn", "/.pypirc", "/.firebase/",
    # Cloud / framework config probes
    "/appsettings.", "/config/", "/application-",
    # WordPress probes (wlwmanifest.xml caught by suffix below)
    "/wp", "/blog/", "/cms/", "/media/", "/news/", "/site/",
    "/sito/", "/test/", "/web/", "/website/", "/wordpress/",
    "/2018/", "/2019/", "/2020/", "/2021/", "/2022/", "/2023/",
    # Log / storage probes
    "/logs/", "/var/", "/storage/",
    # Asset path probes (Django serves from /static/, not these)
    "/assets/", "/js/", "/static/style/",
    # API probe paths
    "/api/",
    # Hash / token probes
    "/curl/",
    # Misc CMS / legacy paths
    "/services/", "/sessions/", "/shop/", "/shared/", "/shibboleth/",
)
_SILENT_404_SUFFIXES = (
    # Secrets / config files
    ".env", ".cfg", ".bak", ".sql",
    # Web framework files
    ".php", ".asp", ".aspx", ".cgi",
    # Config formats
    ".yml", ".yaml", ".properties", ".json",
    # Log files
    ".log",
    # Source map
    ".js.map",
    # WordPress manifest
    "wlwmanifest.xml",
)


def _notify(error_type, error_msg, url="", method="", user=""):
    from assessment.metrics import platform_errors_total
    platform_errors_total.labels(error_type=error_type).inc()
    logger.warning(
        "PLATFORM ERROR | %s | %s | %s %s | user:%s",
        error_type, error_msg, method, url, user,
    )
    _send_error_email(error_type, error_msg, url, method, user)


def notify_attempt_activity(event, attempt):
    """Backup email to support on learner start/finish — INFO tier, not an error."""
    import threading
    from django.conf import settings
    sender = getattr(settings, "EMAIL_HOST_USER", "")
    if not sender or not getattr(settings, "EMAIL_HOST_PASSWORD", ""):
        return

    def _send():
        from django.core.mail import send_mail
        from django.utils import timezone
        learner_name = f"{attempt.learner.first_names} {attempt.learner.surname}".strip()
        try:
            send_mail(
                subject=f"[Assessment Platform] Learner {event} — {attempt.code}",
                message=(
                    f"Learner:  {learner_name}\n"
                    f"Attempt:  {attempt.code}\n"
                    f"Event:    {event}\n"
                    f"Time:     {timezone.now():%Y-%m-%d %H:%M:%S}\n"
                ),
                from_email=sender,
                recipient_list=[_SUPPORT_EMAIL],
                fail_silently=False,
            )
            logger.info("ACTIVITY EMAIL sent: %s %s", event, attempt.code)
        except Exception as exc:
            logger.warning("ACTIVITY EMAIL failed: %s", exc)

    threading.Thread(target=_send, daemon=True).start()


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

    ip = _error_report_get_ip(request)
    rl_key = f"err_report_rl:{ip}"
    count = cache.get(rl_key, 0) + 1
    cache.set(rl_key, count, _ERROR_REPORT_RL_WINDOW)
    if count > _ERROR_REPORT_RL_MAX:
        return HttpResponse(status=429)

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


def _classify_csrf_reason(reason):
    """Return (heading, detail, show_reload) for a Django CSRF rejection reason string."""
    r = reason.lower()
    if "from post incorrect" in r or ("incorrect" in r and "http header" in r):
        return (
            "Your form session has expired",
            "This happens when you log in or out in another tab, use the browser "
            "back button, or your session times out. Reloading the form gives you "
            "a fresh security token.",
            True,
        )
    if "csrf cookie not set" in r:
        return (
            "Your session cookie is missing",
            "This usually means cookies were disabled or cleared in your browser. "
            "Enable cookies for this site and reload to continue.",
            True,
        )
    if "csrf token missing" in r:
        return (
            "A required security field was missing",
            "The form was submitted without a required security field — this can "
            "happen if the page did not load fully. Reloading should fix it.",
            True,
        )
    if "incorrect length" in r or "invalid characters" in r:
        return (
            "Your session data appears corrupted",
            "The security token stored in your browser is invalid, possibly due to "
            "partial cookie data. Clearing cookies for this site and reloading should fix it.",
            True,
        )
    if "origin checking failed" in r:
        return (
            "Request blocked — unexpected source",
            "Your request came from an address this site does not recognise. "
            "Navigate directly to the site rather than following an external link.",
            False,
        )
    if "insecure while host is secure" in r:
        return (
            "Insecure connection detected",
            "Your request was sent over an insecure connection while this site "
            "requires HTTPS. Please use the secure address for this site.",
            False,
        )
    if "referer" in r:
        return (
            "Request source could not be verified",
            "Your browser did not send the information needed to verify this request. "
            "This can happen in private/incognito mode or with strict privacy settings.",
            True,
        )
    return (
        "Security verification failed",
        "Your request could not be verified. Please reload the page and try again.",
        True,
    )


def csrf_failure(request, reason=""):
    try:
        from assessment.tenant import get_active_tenant
        tenant = get_active_tenant()
        support_email = (tenant.support_email if tenant else "") or _SUPPORT_EMAIL
    except Exception:
        support_email = _SUPPORT_EMAIL

    retry_url = request.META.get("HTTP_REFERER") or "/"
    heading, detail, show_reload = _classify_csrf_reason(reason)
    return render(request, "csrf_failure.html", {
        "heading": heading,
        "detail": detail,
        "show_reload": show_reload,
        "retry_url": retry_url,
        "support_email": support_email,
    }, status=403)


def handler403(request, exception=None):
    msg = str(exception) if exception else "Access denied."
    _notify("PermissionDenied", msg, url=request.build_absolute_uri(), method=request.method)
    return render(request, "403.html", {
        "error_type": "Access Denied",
        "error_msg": msg,
    }, status=403)


def handler404(request, exception=None):
    if (request.path not in _SILENT_404_PATHS
            and not request.path.startswith(_SILENT_404_PREFIXES)
            and not request.path.endswith(_SILENT_404_SUFFIXES)):
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
