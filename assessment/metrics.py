from prometheus_client import Counter, Histogram

score_writes_total = Counter(
    "assessment_score_writes_total",
    "Score records written by assessors",
    ["action"],  # created / updated
)

platform_errors_total = Counter(
    "assessment_platform_errors_total",
    "Platform errors routed through _notify()",
    ["error_type"],
)

login_failures_total = Counter(
    "assessment_login_failures_total",
    "Failed login attempts",
)

attempt_submissions_total = Counter(
    "assessment_attempt_submissions_total",
    "Learner attempts submitted",
)

request_duration_seconds = Histogram(
    "assessment_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "status"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)
