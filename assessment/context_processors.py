from django.db.models import Exists, OuterRef, Q

from .models import Attempt, Response, Score


def assessor_nav_counts(request):
    if not request.user.is_authenticated:
        return {}
    if not (request.user.is_staff or request.user.groups.filter(name="assessor").exists()):
        return {}

    has_score = Score.objects.filter(response__attempt_id=OuterRef("pk"))
    has_review_score = Score.objects.filter(
        response__attempt_id=OuterRef("pk"),
        rubric_json__needs_review=True,
    )
    has_unscored_markable = Response.objects.filter(
        attempt_id=OuterRef("pk"),
        score__isnull=True,
        question__is_active=True,
        question__max_marks__gt=0,
    )

    in_progress  = Attempt.objects.filter(status=Attempt.IN_PROGRESS).count()
    submitted    = Attempt.objects.filter(status=Attempt.SUBMITTED).filter(~Exists(has_score)).count()
    marked       = Attempt.objects.filter(status=Attempt.SUBMITTED).filter(Exists(has_score)).count()
    abandoned    = Attempt.objects.filter(status=Attempt.ABANDONED).count()
    needs_review = (
        Attempt.objects
        .filter(status=Attempt.SUBMITTED)
        .filter(Exists(has_review_score) | Exists(has_unscored_markable))
        .count()
    )

    return {
        "nav_counts": {
            "in_progress":  in_progress,
            "submitted":    submitted,
            "marked":       marked,
            "abandoned":    abandoned,
            "needs_review": needs_review,
            "total":        in_progress + submitted + marked + abandoned,
        }
    }
