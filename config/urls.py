import os

from django.contrib import admin
from django.db import connection, OperationalError
from django.http import HttpResponse, JsonResponse
from django.urls import path, include
from django.views.generic import RedirectView
from assessment import views as assessment_views

handler400 = assessment_views.handler400
handler403 = assessment_views.handler403
handler404 = assessment_views.handler404
handler500 = assessment_views.handler500

_ADMIN_PATH = os.environ.get("ADMIN_URL_PREFIX", "_platform-admin") + "/"

# Only Prometheus (127.0.0.1) may scrape /metrics/.
_METRICS_ALLOWED_IPS = {"127.0.0.1", "::1"}


def _health(request):
    try:
        connection.ensure_connection()
        db_ok = True
    except OperationalError:
        db_ok = False
    status = 200 if db_ok else 503
    return JsonResponse({"db": "ok" if db_ok else "error"}, status=status)


def _metrics(request):
    if request.META.get("REMOTE_ADDR") not in _METRICS_ALLOWED_IPS:
        return HttpResponse(status=403)
    from prometheus_client import REGISTRY, generate_latest, CONTENT_TYPE_LATEST
    if "text/html" in request.META.get("HTTP_ACCEPT", ""):
        from datetime import datetime, timezone
        from django.shortcuts import render
        groups = []
        for metric in REGISTRY.collect():
            samples = [
                {
                    "labels": ", ".join(f'{k}="{v}"' for k, v in s.labels.items()),
                    "value": f"{s.value:g}",
                }
                for s in metric.samples
            ]
            groups.append({
                "name": metric.name,
                "type": metric.type,
                "help": metric.documentation,
                "samples": samples,
            })
        return render(request, "assessment/prometheus_metrics.html", {
            "groups": groups,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        })
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)


urlpatterns = [
    path("health/", _health, name="health"),
    path("metrics/", _metrics, name="metrics"),
    path("favicon.ico", lambda request: HttpResponse(status=204), name="favicon"),
    path(_ADMIN_PATH, admin.site.urls),
    path("accounts", RedirectView.as_view(url="/accounts/login/", permanent=False)),
    path("accounts/logout", RedirectView.as_view(url="accounts/login/", permanent=False)),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("assessment.urls")),
]
