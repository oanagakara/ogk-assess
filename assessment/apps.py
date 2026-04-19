from django.apps import AppConfig


class AssessmentConfig(AppConfig):
    name = 'assessment'

    def ready(self):
        import assessment.tenant    # noqa: F401 — registers tenant cache-invalidation signal
        import assessment.security  # noqa: F401 — registers login_failed rate-limit signal
        import assessment.signals   # noqa: F401 — registers Score audit-log signals
