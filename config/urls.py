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
        import time
        from datetime import datetime, timezone
        from django.shortcuts import render
        groups = []
        cards = {}
        domain = {"login_failures": 0, "errors": 0, "score_writes": 0,
                  "submissions": 0, "requests": 0, "avg_ms": None}
        rss_bytes = vms_bytes = 0
        gc = {"0": 0, "1": 0, "2": 0}
        req_count = req_sum = 0.0
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
            n = metric.name
            if n == "assessment_login_failures_total":
                domain["login_failures"] = int(sum(s.value for s in metric.samples))
            elif n == "assessment_platform_errors_total":
                domain["errors"] = int(sum(s.value for s in metric.samples))
            elif n == "assessment_score_writes_total":
                domain["score_writes"] = int(sum(s.value for s in metric.samples))
            elif n == "assessment_attempt_submissions_total":
                domain["submissions"] = int(sum(s.value for s in metric.samples))
            elif n == "assessment_request_duration_seconds":
                for s in metric.samples:
                    if s.name.endswith("_count"):
                        req_count += s.value
                    elif s.name.endswith("_sum"):
                        req_sum += s.value
            elif n == "process_resident_memory_bytes" and metric.samples:
                rss_bytes = metric.samples[0].value
                cards["rss"] = f"{rss_bytes / 1_048_576:.1f} MB"
            elif n == "process_virtual_memory_bytes" and metric.samples:
                vms_bytes = metric.samples[0].value
                cards["vms"] = f"{vms_bytes / 1_048_576:.0f} MB"
            elif n == "process_cpu_seconds_total" and metric.samples:
                cards["cpu"] = f"{metric.samples[0].value:.1f} s"
            elif n == "process_open_fds" and metric.samples:
                cards["fds"] = int(metric.samples[0].value)
            elif n == "process_max_fds" and metric.samples:
                cards["max_fds"] = int(metric.samples[0].value)
            elif n == "process_start_time_seconds" and metric.samples:
                elapsed = int(time.time() - metric.samples[0].value)
                h, rem = divmod(elapsed, 3600)
                m, s = divmod(rem, 60)
                cards["uptime"] = f"{h}h {m}m" if h else f"{m}m {s}s"
            elif n == "python_gc_objects_collected_total":
                for s in metric.samples:
                    gen = s.labels.get("generation", "")
                    if gen in gc:
                        gc[gen] = int(s.value)
        domain["requests"] = int(req_count)
        if req_count:
            domain["avg_ms"] = round(req_sum / req_count * 1000, 1)
        cards["gc_gen0"] = gc["0"]
        rss_mb = round(rss_bytes / 1_048_576, 1)
        mapped_mb = round((vms_bytes - rss_bytes) / 1_048_576, 1)
        return render(request, "assessment/prometheus_metrics.html", {
            "groups": groups,
            "cards": cards,
            "domain": domain,
            "gc_data": [gc["0"], gc["1"], gc["2"]],
            "rss_mb": rss_mb,
            "mapped_mb": mapped_mb,
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
