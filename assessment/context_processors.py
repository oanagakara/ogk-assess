from django.db.models import Exists, OuterRef

from .models import Attempt, Response, Score
from .tenant import get_active_tenant


def tenant_branding(request):
    tenant = get_active_tenant()
    if tenant is None:
        return {}
    return {
        "tenant": tenant,
        "brand_css_vars": _css_vars(tenant),
    }


def _css_vars(tenant):
    return (
        f"--brand-primary:{tenant.color_primary};"
        f"--brand-secondary:{tenant.color_secondary};"
        f"--brand-accent:{tenant.color_accent};"
        f"--brand-text:{tenant.color_text};"
        f"--brand-bg:{tenant.color_bg};"
        f"--brand-font:'{tenant.font_family_primary}';"
    )


def assessor_nav_counts(request):
    if not request.user.is_authenticated:
        return {}
    user_groups = set(request.user.groups.values_list("name", flat=True)) if not request.user.is_staff else set()
    if not (request.user.is_staff or user_groups & {"assessor", "moderator", "auditor"}):
        return {}

    real_is_moderator = request.user.is_staff or bool(user_groups & {"moderator", "auditor"})
    user_is_auditor = request.user.is_staff or "auditor" in user_groups

    # Session-based role downgrade: a moderator/auditor can choose to operate as assessor.
    active_role = request.session.get("active_role", "moderator" if real_is_moderator else "assessor")
    if not real_is_moderator:
        active_role = "assessor"  # can't self-assign upwards
    user_is_moderator = real_is_moderator and active_role != "assessor"

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
    submitted    = Attempt.objects.filter(status=Attempt.SUBMITTED).filter(Exists(has_unscored_markable)).count()
    marked       = Attempt.objects.filter(status=Attempt.SUBMITTED, finalised_at__isnull=True).filter(~Exists(has_unscored_markable)).count()
    incomplete   = Attempt.objects.filter(status=Attempt.INCOMPLETE).count()
    needs_review = (
        Attempt.objects
        .filter(status=Attempt.SUBMITTED)
        .filter(Exists(has_review_score) | Exists(has_unscored_markable))
        .count()
    )
    finalised = Attempt.objects.filter(finalised_at__isnull=False).count()

    return {
        "user_is_moderator": user_is_moderator,
        "user_is_auditor": user_is_auditor,
        "active_role": active_role,
        "can_switch_role": real_is_moderator,
        "nav_counts": {
            "in_progress":  in_progress,
            "submitted":    submitted,
            "marked":       marked,
            "incomplete":   incomplete,
            "needs_review": needs_review,
            "finalised":    finalised,
            "total":        in_progress + submitted + marked + incomplete,
        }
    }
