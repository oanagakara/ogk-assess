from django.db.models import Exists, OuterRef

from .models import Attempt, Score


def assessor_nav_counts(request):
    if not request.user.is_authenticated:
        return {}
    if not (request.user.is_staff or request.user.groups.filter(name="assessor").exists()):
        return {}

    has_score = Score.objects.filter(response__attempt_id=OuterRef("pk"))

    in_progress = Attempt.objects.filter(status=Attempt.IN_PROGRESS).count()
    submitted   = Attempt.objects.filter(status=Attempt.SUBMITTED).filter(~Exists(has_score)).count()
    marked      = Attempt.objects.filter(status=Attempt.SUBMITTED).filter(Exists(has_score)).count()
    abandoned   = Attempt.objects.filter(status=Attempt.ABANDONED).count()

    return {
        "nav_counts": {
            "in_progress": in_progress,
            "submitted":   submitted,
            "marked":      marked,
            "abandoned":   abandoned,
            "total":       in_progress + submitted + marked + abandoned,
        }
    }
