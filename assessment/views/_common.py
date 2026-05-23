"""Shared constants, auth predicates, and utility helpers used across view modules."""
import json
import logging
import re
from datetime import timedelta
from typing import NamedTuple

logger = logging.getLogger(__name__)

from django.utils import timezone

from ..models import Attempt


ASSESSMENT_DURATION = timedelta(hours=2)
SECTION_DURATION = timedelta(minutes=60)
REVIEW_MAX_SECONDS = 600


class MarkingTotals(NamedTuple):
    available: float
    awarded: float
    scored_count: int


# ── Auth predicates ───────────────────────────────────────────────────────────

def is_assessor(user):
    return user.is_authenticated and (
        user.is_staff
        or user.groups.filter(name__in=["assessor", "moderator", "auditor"]).exists()
    )


def is_staff(user):
    return user.is_authenticated and user.is_staff


def is_moderator(user):
    return user.is_authenticated and (
        user.is_staff
        or user.groups.filter(name__in=["moderator", "auditor"]).exists()
    )


def is_auditor(user):
    return user.is_authenticated and (
        user.is_staff
        or user.groups.filter(name="auditor").exists()
    )


def effective_is_moderator(request) -> bool:
    """is_moderator, but respects the user's active-role session downgrade."""
    if not is_moderator(request.user):
        return False
    return request.session.get("active_role", "moderator") != "assessor"


# ── JSON helpers ──────────────────────────────────────────────────────────────

def _safe_json_loads(value, default=None):
    if default is None:
        default = {}
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return default


def _question_spec(question):
    return _safe_json_loads(question.spec_json, {})


def _question_answer_key(question):
    return _safe_json_loads(question.answer_key_json, {})


# ── Question classification ───────────────────────────────────────────────────

def _extract_inline_choices(prompt: str) -> list[str]:
    if not prompt:
        return []
    matches = re.findall(r"\(([^()]*\/[^()]*)\)", prompt)
    if not matches:
        return []
    raw = matches[-1]
    return [part.strip() for part in raw.split("/") if part.strip()]


def _is_layout_only_question(question):
    layout = _question_spec(question).get("layout", "")
    return layout in {"info_only", "info-only", "passage_only"}


# ── Attempt expiry ────────────────────────────────────────────────────────────

def _expire_overdue_attempts():
    from django.core.cache import cache
    if cache.get("_expire_ran"):
        return
    now = timezone.now()
    cutoff = now - ASSESSMENT_DURATION
    Attempt.objects.filter(
        status=Attempt.IN_PROGRESS,
        started_at__isnull=False,
        started_at__lte=cutoff,
    ).update(
        status=Attempt.INCOMPLETE,
        last_activity_at=now,
    )
    cache.set("_expire_ran", True, 60)
