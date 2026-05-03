from datetime import date, timedelta
import csv
import json
import logging
import os
import re
import sys
import uuid
from typing import NamedTuple

logger = logging.getLogger(__name__)

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator 

from django.db.models import Count, Prefetch, Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from .forms import (
    AttemptForm,
    ExamSessionForm,
    HonestyForm,
    LearnerForm,
    StartForm,
)
from .auto_mark import auto_mark_attempt
from .models import AssessmentTemplate, Attempt, ExamSession, Learner, Question, Response, Score, ScoreAuditLog, Section, WorkingSheet
from .nqf import NQF_DISPLAY_GROUPS, build_question_metadata, compute_nqf_placement
from .renderers import get_renderer
from .services import claim_seat, claim_session_seat


ASSESSMENT_DURATION = timedelta(hours=2)   # hard cap: two 60-min section slots
SECTION_DURATION = timedelta(minutes=60)   # each section: questions + review fits in 60 min
REVIEW_MAX_SECONDS = 600                   # global review timer per section: ≤10 minutes

# ── Learner session ownership ─────────────────────────────────────────────────
# H-5: Bind each attempt to the browser session that created it. Prevents one
# learner from navigating to another learner's attempt by guessing the code.
_LEARNER_SESSION_KEY = "learner_attempt_code"


def _bind_attempt_to_session(request, code: str) -> None:
    request.session[_LEARNER_SESSION_KEY] = code
    request.session.modified = True


def _owns_attempt(request, code: str) -> bool:
    return request.session.get(_LEARNER_SESSION_KEY) == code


class MarkingTotals(NamedTuple):
    available: float
    awarded: float
    scored_count: int

# Question codes that display the Thandi passage.
# TODO: move to spec_json["passage_source"] in a data migration.
_PASSAGE_CODES = frozenset({"LIT-B-1", "LIT-B-2", "LIT-B-3", "LIT-B-4", "LIT-B-READ"})


def _attempt_expires_at(attempt):
    if not attempt.started_at:
        return None
    return attempt.started_at + ASSESSMENT_DURATION


def _section_expires_at(attempt, section_pk):
    started = attempt.section_started_at(section_pk)
    if not started:
        return None
    return started + SECTION_DURATION


def _first_n_of_next_section(qs_section_ids, current_section_id):
    """Return 1-based position of the first question in the section after current_section_id."""
    in_current = False
    for i, (_, sec_id) in enumerate(qs_section_ids):
        if sec_id == current_section_id:
            in_current = True
        elif in_current:
            return i + 1
    return None


def _is_last_section(qs_section_ids, current_section_id):
    return _first_n_of_next_section(qs_section_ids, current_section_id) is None


def _section_review_seconds(attempt, section_pk: int) -> int:
    """Return the section's review window in seconds: min(10min, 60min − question_time_used).

    Question time = (review_started − section_started). Returns 0 if either timestamp is missing.
    """
    section_started = attempt.section_started_at(section_pk)
    review_started = attempt.get_section_review_started_at(section_pk)
    if not (section_started and review_started):
        return 0
    question_seconds = max(0, int((review_started - section_started).total_seconds()))
    remaining = int(SECTION_DURATION.total_seconds()) - question_seconds
    return max(0, min(REVIEW_MAX_SECONDS, remaining))


def _section_review_expires_at(attempt, section_pk: int):
    review_started = attempt.get_section_review_started_at(section_pk)
    if not review_started:
        return None
    return review_started + timedelta(seconds=_section_review_seconds(attempt, section_pk))


def _projected_section_review_seconds(attempt, section_pk: int) -> int:
    """Projected review window if review were started right now (for the review_info preview)."""
    section_started = attempt.section_started_at(section_pk)
    if not section_started:
        return 0
    question_seconds = max(0, int((timezone.now() - section_started).total_seconds()))
    remaining = int(SECTION_DURATION.total_seconds()) - question_seconds
    return max(0, min(REVIEW_MAX_SECONDS, remaining))


def _section_questions(template, section_pk: int):
    """Return ordered list of non-layout-only questions in a specific section."""
    qs = (
        Question.objects.filter(section__template=template, section_id=section_pk, is_active=True)
        .select_related("section")
        .order_by("order", "code")
    )
    return [q for q in qs if not _is_layout_only_question(q)]


def _next_section_first_n(template, section_pk: int):
    """Global 1-based question index of the first question in the section after section_pk.

    Returns None if section_pk is the last section.
    """
    qs_section_ids = list(
        Question.objects.filter(section__template=template, is_active=True)
        .order_by("section__order", "order", "code")
        .values_list("pk", "section_id")
    )
    return _first_n_of_next_section(qs_section_ids, section_pk)


def _finalize_attempt(attempt, when=None):
    attempt.submit(when=when)
    auto_mark_attempt(attempt)


def _expire_attempt_if_needed(attempt, now=None):
    now = now or timezone.now()

    if attempt.status == Attempt.SUBMITTED:
        return True

    expires_at = _attempt_expires_at(attempt)
    if expires_at and now >= expires_at:
        _finalize_attempt(attempt, when=now)
        return True

    return False


def _expire_overdue_attempts():
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


def _extract_inline_choices(prompt: str) -> list[str]:
    if not prompt:
        return []

    matches = re.findall(r"\(([^()]*\/[^()]*)\)", prompt)
    if not matches:
        return []

    raw = matches[-1]
    return [part.strip() for part in raw.split("/") if part.strip()]


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


def _find_rubric_candidates(question):
    for container in (_question_spec(question), _question_answer_key(question)):
        if not isinstance(container, dict):
            continue
        for key in ("rubric", "rubric_criteria", "criteria"):
            value = container.get(key)
            if isinstance(value, list):
                return value
    return []


def _normalise_rubric_item(item, idx):
    label = (
        item.get("label") or item.get("criterion") or item.get("name") or f"Criterion {idx}"
    ).strip()
    raw_max = item.get("max_points", item.get("points", item.get("max", 0)))
    try:
        max_points = float(raw_max)
    except (TypeError, ValueError):
        max_points = 0.0
    key = slugify(str(item.get("key") or label)) or f"criterion_{idx}"
    return {"key": key, "label": label, "max_points": max_points}


def _extract_rubric(question):
    return [
        _normalise_rubric_item(item, idx)
        for idx, item in enumerate(_find_rubric_candidates(question), start=1)
        if isinstance(item, dict)
    ]


def _is_layout_only_question(question):
    layout = _question_spec(question).get("layout", "")
    return layout in {"info_only", "info-only", "passage_only"}


def _is_markable_question(question):
    if _is_layout_only_question(question):
        return False
    if _extract_rubric(question):
        return True
    return float(question.max_marks or 0) > 0


def _render_form_fill(spec, data):
    fields = spec.get("fields", [])
    if fields:
        return "\n".join(
            f"{field.get('label') or field.get('name', '')}: {data.get(field.get('name', ''), '')}"
            for field in fields
        ).strip()
    return "\n".join(f"{k}: {v}" for k, v in data.items()).strip()


def _render_match(spec, data):
    targets = {
        str(t.get("id")): t.get("text", "")
        for t in spec.get("targets", [])
        if isinstance(t, dict)
    }
    return "\n".join(
        f"{targets.get(str(tid), str(tid))}: {word}"
        for tid, word in data.items()
    ).strip()


def _render_text(data):
    if isinstance(data, dict):
        return str(data.get("answer", "")).strip()
    return str(data).strip()


def _render_response_for_marking(question, response):
    if not response or not response.response_json:
        return ""

    spec = _question_spec(question)
    data = _safe_json_loads(response.response_json, {})

    if spec.get("layout") == "form_fill":
        return _render_form_fill(spec, data)

    if question.kind == Question.MATCH:
        return _render_match(spec, data)

    return _render_text(data)


def _clamped_float(value, upper_bound):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    return max(0.0, min(parsed, float(upper_bound)))


def home(request):
    return render(request, "index.html")


def start(request):
    form = StartForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        code = form.cleaned_data["code"].strip().upper()

        # Existing per-attempt flow
        attempt = Attempt.objects.filter(code=code).first()
        if attempt is not None:
            ok, msg = claim_seat(attempt)
            if ok:
                _bind_attempt_to_session(request, attempt.code)
                return redirect("assessment:attempt_details", code=attempt.code)
            form.add_error("code", msg)
            return render(request, "assessment/start.html", {"form": form})

        # Session flow
        session = ExamSession.objects.filter(code=code).select_related("template").first()
        if session is not None:
            return redirect("assessment:session_join", code=session.code)

        form.add_error("code", "Invalid code. Please check and try again.")

    return render(request, "assessment/start.html", {"form": form})


def is_assessor(user):
    return user.is_authenticated and (
        user.is_staff or user.groups.filter(name="assessor").exists()
    )


def is_staff(user):
    return user.is_authenticated and user.is_staff


def is_moderator(user):
    return user.is_authenticated and (
        user.is_staff or user.groups.filter(name="moderator").exists()
    )


def is_auditor(user):
    return user.is_authenticated and (
        user.is_staff
        or user.groups.filter(name__in=["assessor", "auditor", "moderator"]).exists()
    )


@login_required
@user_passes_test(is_assessor)
def assessor_dashboard(request):
    _expire_overdue_attempts()

    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    active_cutoff = now - timedelta(minutes=30)

    total_attempts = Attempt.objects.count()
    submitted_today = Attempt.objects.filter(
        status=Attempt.SUBMITTED,
        submitted_at__gte=today_start,
    ).count()
    active_now = Attempt.objects.filter(
        status=Attempt.IN_PROGRESS,
        last_activity_at__gte=active_cutoff,
    ).count()

    q_code    = (request.GET.get("q_code") or "").strip()
    q_learner = (request.GET.get("q_learner") or "").strip()
    q_status  = (request.GET.get("q_status") or "").strip()
    q_date    = (request.GET.get("q_date") or "").strip()

    recent_qs = (
        Attempt.objects.select_related("learner", "template")
        .annotate(response_count=Count("response"))
        .order_by("-last_activity_at", "-started_at")
    )

    if q_code:
        recent_qs = recent_qs.filter(code__icontains=q_code)
    if q_learner:
        recent_qs = recent_qs.filter(
            Q(learner__first_names__icontains=q_learner)
            | Q(learner__surname__icontains=q_learner)
            | Q(learner__id_number__icontains=q_learner)
        )
    if q_status:
        recent_qs = recent_qs.filter(status=q_status)
    if q_date:
        try:
            recent_qs = recent_qs.filter(
                last_activity_at__date=date.fromisoformat(q_date)
            )
        except ValueError:
            pass

    try:
        per_page = int(request.GET.get("per_page", 25))
    except (ValueError, TypeError):
        per_page = 25
    if per_page not in (10, 25, 50, 100):
        per_page = 25

    # filter_qs carries only search params (not page/per_page) so
    # "Clear filters" only appears when a real filter is active.
    search_params = request.GET.copy()
    search_params.pop("page", None)
    search_params.pop("per_page", None)
    filter_qs = search_params.urlencode()

    paginator = Paginator(recent_qs, per_page)
    recent_page = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "assessment/assessor_dashboard.html",
        {
            "total_attempts": total_attempts,
            "submitted_today": submitted_today,
            "active_now": active_now,
            "recent_page": recent_page,
            "filter_qs": filter_qs,
            "per_page": per_page,
            "q_code": q_code,
            "q_learner": q_learner,
            "q_status": q_status,
            "q_date": q_date,
        },
    )


@login_required
@user_passes_test(is_auditor)
def assessor_attempts(request):
    _expire_overdue_attempts()

    from django.db.models import Exists, OuterRef
    has_unscored_markable = Response.objects.filter(
        attempt_id=OuterRef("pk"),
        score__isnull=True,
        question__is_active=True,
        question__max_marks__gt=0,
    )

    base_qs = (
        Attempt.objects.select_related("learner", "template")
        .annotate(response_count=Count("response", distinct=True))
        .order_by("-last_activity_at", "-started_at")
    )

    active_tab = request.GET.get("tab", "in_progress")
    try:
        per_page = int(request.GET.get("per_page", 25))
    except ValueError:
        per_page = 25
    if per_page not in (10, 25, 50, 100):
        per_page = 25

    tab_qs = {
        "in_progress": base_qs.filter(status=Attempt.IN_PROGRESS),
        "submitted":   base_qs.filter(status=Attempt.SUBMITTED).filter(Exists(has_unscored_markable)),
        "marked":      base_qs.filter(status=Attempt.SUBMITTED).filter(~Exists(has_unscored_markable)),
        "incomplete":  base_qs.filter(status=Attempt.INCOMPLETE),
    }
    current_qs = tab_qs.get(active_tab, tab_qs["in_progress"])

    paginator = Paginator(current_qs, per_page)
    page_obj = paginator.get_page(request.GET.get("page"))

    params = request.GET.copy()
    params.pop("page", None)
    filter_qs = params.urlencode()

    return render(request, "assessment/assessor_attempts.html", {
        "page_obj":   page_obj,
        "active_tab": active_tab,
        "per_page":   per_page,
        "filter_qs":  filter_qs,
    })


def _score_for_response(response):
    try:
        return response.score
    except Score.DoesNotExist:
        return None


def _max_points_for_question(question, rubric):
    if rubric:
        return sum(item["max_points"] for item in rubric)
    return float(question.max_marks or 0)


def _saved_criteria_from_score(score) -> dict:
    """Parse the saved per-criterion points/feedback from a score's rubric_json."""
    if not (score and isinstance(score.rubric_json, dict)):
        return {}
    return {
        str(item["key"]): item
        for item in score.rubric_json.get("criteria", [])
        if isinstance(item, dict) and item.get("key")
    }


def _rubric_display_rows(rubric, saved_criteria) -> list:
    return [
        {
            "key": c["key"],
            "label": c["label"],
            "max_points": c["max_points"],
            "value": saved_criteria.get(c["key"], {}).get("points", ""),
            "feedback": saved_criteria.get(c["key"], {}).get("feedback", ""),
        }
        for c in rubric
    ]


def _score_notes(score) -> str:
    if score and isinstance(score.rubric_json, dict):
        return score.rubric_json.get("notes", "")
    return ""


def _build_marking_row(question, index, responses_by_qid):
    response = responses_by_qid[question.pk]
    score = _score_for_response(response)
    rubric = _extract_rubric(question)
    saved_criteria = _saved_criteria_from_score(score)
    rubric_rows = _rubric_display_rows(rubric, saved_criteria)
    max_points = _max_points_for_question(question, rubric)
    awarded = float(score.points) if score else 0.0

    return {
        "index": index,
        "question": question,
        "response": response,
        "response_text": _render_response_for_marking(question, response),
        "has_rubric": bool(rubric_rows),
        "rubric": rubric_rows,
        "manual_value": awarded if score and not rubric_rows else "",
        "notes": _score_notes(score),
        "score": score,
        "awarded": awarded,
        "max_points": max_points,
    }


def _save_question_score(request, attempt, question):
    response, _ = Response.objects.get_or_create(attempt=attempt, question=question)
    rubric = _extract_rubric(question)

    if rubric:
        criteria_payload = []
        total_points = 0.0
        max_points = 0.0
        for criterion in rubric:
            max_points += criterion["max_points"]
            points = _clamped_float(
                request.POST.get(f"rubric__{question.pk}__{criterion['key']}", ""),
                criterion["max_points"],
            )
            feedback = request.POST.get(
                f"rubric_feedback__{question.pk}__{criterion['key']}", ""
            ).strip()
            criteria_payload.append({
                "key": criterion["key"],
                "label": criterion["label"],
                "max_points": criterion["max_points"],
                "points": points,
                "feedback": feedback,
            })
            total_points += points
        points = total_points
        rubric_json = {
            "mode": "rubric",
            "criteria": criteria_payload,
            "notes": request.POST.get(f"notes__{question.pk}", "").strip(),
        }
    else:
        max_points = float(question.max_marks or 0)
        points = _clamped_float(request.POST.get(f"manual__{question.pk}", ""), max_points)
        rubric_json = {
            "mode": "manual",
            "notes": request.POST.get(f"notes__{question.pk}", "").strip(),
        }

    Score.objects.update_or_create(
        response=response,
        defaults={
            "assessor": request.user,
            "points": points,
            "max_points": max_points,
            "rubric_json": rubric_json,
        },
    )


def _requires_assessor_attention(score) -> bool:
    """True if the question still needs assessor input."""
    if score is None:
        return True
    if not isinstance(score.rubric_json, dict):
        return True
    return bool(score.rubric_json.get("needs_review", False))


def _review_type(score) -> str:
    """
    Classify why a question is in the review queue.

    process      — answer correct, working evidence absent; assessor verifies the working sheet.
    comprehension — auto-flagged; assessor adjudicates meaning or quality.
    manual       — no auto-mark; assessor scores from scratch.
    """
    if score is None:
        return "manual"
    rubric = score.rubric_json if isinstance(score.rubric_json, dict) else {}
    if rubric.get("auto_marked"):
        if rubric.get("verify_working"):
            return "process"
        if rubric.get("answer_found") and rubric.get("working_found") is False:
            return "process"
        return "comprehension"
    return "manual"


def _build_review_queue(markable_questions, responses_by_qid) -> list:
    """Return the subset of markable questions that still need assessor attention."""
    return [
        q for q in markable_questions
        if _requires_assessor_attention(_score_for_response(responses_by_qid[q.pk]))
    ]


def _compute_marking_totals(markable_questions, responses_by_qid) -> MarkingTotals:
    available = 0.0
    awarded = 0.0
    scored_count = 0
    for question in markable_questions:
        score = _score_for_response(responses_by_qid[question.pk])
        rubric = _extract_rubric(question)
        available += _max_points_for_question(question, rubric)
        awarded += float(score.points) if score else 0.0
        if score is not None:
            scored_count += 1
    return MarkingTotals(available=available, awarded=awarded, scored_count=scored_count)


@login_required
@user_passes_test(is_auditor)
def assessor_mark_attempt(request, code: str):
    _expire_overdue_attempts()

    attempt = get_object_or_404(
        Attempt.objects.select_related("learner", "template"),
        code=code,
    )

    markable_questions = [
        q for q in
        Question.objects.filter(section__template=attempt.template, is_active=True)
        .select_related("section")
        .order_by("section__order", "order", "code")
        if _is_markable_question(q)
    ]

    question_ids = [q.pk for q in markable_questions]
    Response.objects.bulk_create(
        [Response(attempt=attempt, question_id=qid, response_json="") for qid in question_ids],
        ignore_conflicts=True,
    )
    responses_by_qid = {
        r.question_id: r
        for r in Response.objects.filter(
            attempt=attempt, question_id__in=question_ids
        ).select_related("score")
    }

    # Defensively auto-mark any responses the engine missed (edge cases in
    # submission flow). Keeps auto-markable questions out of the review queue.
    has_unscored = any(
        _score_for_response(responses_by_qid[q.pk]) is None
        for q in markable_questions
    )
    if has_unscored:
        auto_mark_attempt(attempt)
        responses_by_qid = {
            r.question_id: r
            for r in Response.objects.filter(
                attempt=attempt, question_id__in=question_ids
            ).select_related("score")
        }

    pending_questions = _build_review_queue(markable_questions, responses_by_qid)
    questions_by_pk = {q.pk: q for q in markable_questions}

    # Resolve which question to show from ?qid=, falling back to first pending.
    # Audit-only questions (auto_done) are restricted to staff.
    try:
        requested_qid = int(request.GET.get("qid", 0))
    except (TypeError, ValueError):
        requested_qid = 0

    def _is_audit_only(q) -> bool:
        score = _score_for_response(responses_by_qid.get(q.pk))
        if score is None or _requires_assessor_attention(score):
            return False
        rubric = score.rubric_json if isinstance(score.rubric_json, dict) else {}
        return bool(rubric.get("auto_marked"))

    requested_question = questions_by_pk.get(requested_qid)
    if requested_question and _is_audit_only(requested_question) and not request.user.is_staff:
        requested_qid = 0  # silently drop the request — fall through to first pending

    current_question = (
        questions_by_pk.get(requested_qid)
        or (pending_questions[0] if pending_questions else None)
    )

    finalised = bool(attempt.finalised_at)
    can_mark = (not finalised and is_assessor(request.user)) or is_moderator(request.user)

    if request.method == "POST":
        if not can_mark:
            return HttpResponseForbidden("This attempt has been finalised.")
        action = request.POST.get("action", "save")
        if action == "finalise":
            attempt.finalised_at = timezone.now()
            attempt.finalised_by = request.user
            attempt.save(update_fields=["finalised_at", "finalised_by"])
            return redirect(reverse("assessment:assessor_attempts") + "?tab=marked")

    if request.method == "POST" and current_question:
        _save_question_score(request, attempt, current_question)
        action = request.POST.get("action", "save")
        if action == "done":
            return redirect("assessment:assessor_review_queue")
        url = reverse("assessment:assessor_mark_attempt", kwargs={"code": code})
        # After save, go to next pending question (stable by pk, not position)
        remaining = _build_review_queue(markable_questions, {
            r.question_id: r
            for r in Response.objects.filter(
                attempt=attempt, question_id__in=question_ids
            ).select_related("score")
        })
        next_question = remaining[0] if remaining else None
        if next_question and action != "summary":
            return redirect(f"{url}?qid={next_question.pk}&saved=1")
        return redirect(f"{url}?summary=1")

    totals = _compute_marking_totals(markable_questions, responses_by_qid)
    show_summary = request.GET.get("summary") == "1" or (not pending_questions and not current_question)

    summary_rows = None
    if show_summary:
        summary_rows = [
            _build_marking_row(q, idx + 1, responses_by_qid)
            for idx, q in enumerate(markable_questions)
        ]

    current_row = (
        _build_marking_row(current_question, markable_questions.index(current_question) + 1, responses_by_qid)
        if current_question and not show_summary
        else None
    )

    # Sidebar: all markable questions with pending/done status.
    # auto_done — correctly auto-marked, no review required; shown in spot-check section only.
    def _is_auto_done(score) -> bool:
        """Scored and needs no further assessor action — goes to audit log."""
        if score is None or _requires_assessor_attention(score):
            return False
        return True

    def _sidebar_item(q):
        score = _score_for_response(responses_by_qid[q.pk])
        return {
            "question": q,
            "pending": _requires_assessor_attention(score),
            "auto_done": _is_auto_done(score),
            "active": current_question and q.pk == current_question.pk,
            "url": reverse("assessment:assessor_mark_attempt", kwargs={"code": code}) + f"?qid={q.pk}",
            "review_type": _review_type(score),
        }

    all_sidebar = [_sidebar_item(q) for q in markable_questions]
    # Questions needing assessor attention — shown in the main sidebar list.
    sidebar_questions = [i for i in all_sidebar if not i["auto_done"]]
    # Auto-marked, no review — hidden by default; accessible for spot-checking.
    sidebar_spot = [i for i in all_sidebar if i["auto_done"]]

    return render(
        request,
        "assessment/assessor_mark_attempt.html",
        {
            "attempt": attempt,
            "row": current_row,
            "saved": request.GET.get("saved") == "1",
            "total_awarded": totals.awarded,
            "total_available": totals.available,
            "scored_count": totals.scored_count,
            "pending_count": len(pending_questions),
            "total_questions": len(markable_questions),
            "show_summary": show_summary,
            "summary_rows": summary_rows,
            "sidebar_questions": sidebar_questions,
            "sidebar_spot": sidebar_spot,
            "working_sheet": _get_working_sheet(attempt),
            "is_finalised": finalised,
            "can_mark": can_mark,
            "can_unlock": finalised and is_moderator(request.user) and attempt.template.moderation_mode == AssessmentTemplate.MODERATION_FULL,
        },
    )


@login_required
@user_passes_test(is_moderator)
def assessor_unlock_attempt(request, code: str):
    if request.method != "POST":
        return HttpResponseForbidden()
    attempt = get_object_or_404(Attempt, code=code)
    if attempt.template.moderation_mode != AssessmentTemplate.MODERATION_FULL:
        return HttpResponseForbidden("This template is audit-only and cannot be unlocked.")
    attempt.finalised_at = None
    attempt.finalised_by = None
    attempt.save(update_fields=["finalised_at", "finalised_by"])
    return redirect(reverse("assessment:assessor_mark_attempt", kwargs={"code": code}))


@login_required
@user_passes_test(is_moderator)
def assessor_moderation(request):
    attempts = (
        Attempt.objects
        .filter(finalised_at__isnull=False)
        .select_related("learner", "template", "finalised_by")
        .order_by("-finalised_at")
    )
    return render(request, "assessment/assessor_moderation.html", {
        "attempts": attempts,
    })


def _get_working_sheet(attempt):
    try:
        return attempt.working_sheet
    except WorkingSheet.DoesNotExist:
        return None


@login_required
@user_passes_test(is_auditor)
def assessor_auto_marked_attempt(request, code: str):
    """Read-only view of all scored questions not requiring assessor input."""
    attempt = get_object_or_404(
        Attempt.objects.select_related("learner", "template"),
        code=code,
    )

    markable_questions = [
        q for q in
        Question.objects.filter(section__template=attempt.template, is_active=True)
        .select_related("section")
        .order_by("section__order", "order", "code")
        if _is_markable_question(q)
    ]

    question_ids = [q.pk for q in markable_questions]
    Response.objects.bulk_create(
        [Response(attempt=attempt, question_id=qid, response_json="") for qid in question_ids],
        ignore_conflicts=True,
    )
    responses_by_qid = {
        r.question_id: r
        for r in Response.objects.filter(
            attempt=attempt, question_id__in=question_ids
        ).select_related("score")
    }

    rows = []
    for question in markable_questions:
        score = _score_for_response(responses_by_qid[question.pk])
        if score is None or _requires_assessor_attention(score):
            continue
        rubric = _extract_rubric(question)
        rows.append({
            "question": question,
            "response_text": _render_response_for_marking(question, responses_by_qid[question.pk]),
            "score": score,
            "awarded": float(score.points),
            "max_points": _max_points_for_question(question, rubric),
            "notes": _score_notes(score),
            "mode": score.rubric_json.get("mode", "") if isinstance(score.rubric_json, dict) else "",
        })

    review_count = len(markable_questions) - len(rows)

    return render(request, "assessment/assessor_auto_marked_attempt.html", {
        "attempt": attempt,
        "rows": rows,
        "review_count": review_count,
    })


@login_required
@user_passes_test(is_assessor)
def assessor_new_attempt(request):
    latest_template = AssessmentTemplate.objects.order_by("-created_at").first()

    if latest_template is None:
        return render(
            request,
            "assessment/assessor_new_attempt.html",
            {"error": "No assessment template exists yet."},
        )

    if request.method == "POST":
        form = AttemptForm(request.POST)

        if form.is_valid():
            learner = Learner.objects.create(
                first_names="Temp",
                surname="Learner",
                id_number=uuid.uuid4().hex[:13],
            )

            attempt = form.save(commit=False)
            attempt.learner = learner
            attempt.save()

            return render(
                request,
                "assessment/assessor_new_attempt.html",
                {
                    "attempt": attempt,
                    "form": form,
                    "template": attempt.template,
                },
            )
    else:
        form = AttemptForm(initial={"template": latest_template})

    return render(
        request,
        "assessment/assessor_new_attempt.html",
        {
            "form": form,
            "template": latest_template,
        },
    )


def _load_passage(question, spec) -> str:
    if spec.get("passage"):
        return spec["passage"]
    source = spec.get("passage_source")
    if not source and question.code in _PASSAGE_CODES:
        source = "LIT-B-READ"
    if not source:
        return ""
    try:
        return Question.objects.get(code=source).prompt
    except Question.DoesNotExist:
        return ""


def _navigate(request, attempt, n, total):
    """Return a redirect based on the posted navigation button, or None if none was posted."""
    if "next" in request.POST:
        if n >= total:
            return redirect("assessment:attempt_submitted", code=attempt.code)
        return redirect("assessment:attempt_question", code=attempt.code, n=n + 1)
    if "prev" in request.POST:
        return redirect("assessment:attempt_question", code=attempt.code, n=max(1, n - 1))
    return None


def _base_context(attempt, question, spec, n, total, expires_at, passage="", form=None):
    return {
        "attempt": attempt,
        "question": question,
        "spec": spec,
        "n": n,
        "total": total,
        "expires_at": expires_at,
        "layout": spec.get("layout", "default"),
        "passage": passage,
        "form": form,
        "form_fill_values": {},
        "current_answer": "",
    }


def _handle_display_only(request, attempt, question, spec, n, total, expires_at, *, passage="", extra_context=None):
    """Handle a question that shows content but collects no response (info or passage screens)."""
    if request.method == "POST" and "next" in request.POST:
        attempt.touch()
        if n >= total:
            return redirect("assessment:attempt_submit", code=attempt.code)
        return redirect("assessment:attempt_question", code=attempt.code, n=n + 1)
    context = _base_context(attempt, question, spec, n, total, expires_at, passage=passage)
    if extra_context:
        context.update(extra_context)
    return render(request, "assessment/question.html", context)


def _handle_info_only(request, attempt, question, spec, n, total, expires_at, *, extra_context=None):
    return _handle_display_only(request, attempt, question, spec, n, total, expires_at, extra_context=extra_context)


def _handle_passage_only(request, attempt, question, spec, n, total, expires_at, *, extra_context=None):
    passage = _load_passage(question, spec)
    return _handle_display_only(request, attempt, question, spec, n, total, expires_at, passage=passage, extra_context=extra_context)


def _handle_with_response(request, attempt, question, spec, n, total, expires_at, *, end_redirect=None, extra_context=None):
    passage = _load_passage(question, spec)
    response, _ = Response.objects.get_or_create(attempt=attempt, question=question)
    renderer = get_renderer(question, spec, response)
    form = renderer.get_form(request)

    if request.method == "POST":
        renderer.save(request, form)
        attempt.touch()
        if "next" in request.POST and n >= total:
            if end_redirect:
                return end_redirect
            _finalize_attempt(attempt)
            return redirect("assessment:attempt_submitted", code=attempt.code)
        nav = _navigate(request, attempt, n, total)
        if nav:
            return nav

    context = _base_context(attempt, question, spec, n, total, expires_at, passage=passage, form=form)
    context.update(renderer.get_context())
    if extra_context:
        context.update(extra_context)
    return render(request, "assessment/question.html", context)


def attempt_question(request, code: str, n: int):
    attempt = get_object_or_404(Attempt, code=code)
    if not _owns_attempt(request, code):
        return HttpResponseForbidden()

    if _expire_attempt_if_needed(attempt):
        return redirect("assessment:attempt_submitted", code=code)

    if not attempt.has_honesty_declaration:
        return redirect("assessment:attempt_details", code=code)

    if not attempt.started_at:
        attempt.start()

    expires_at = _attempt_expires_at(attempt)

    qs = (
        Question.objects.filter(section__template=attempt.template, is_active=True)
        .select_related("section")
        .order_by("section__order", "order", "code")
    )
    total = qs.count()
    if total == 0:
        return render(request, "assessment/no_questions.html", {"attempt": attempt})

    if n < 1 or n > total:
        return redirect("assessment:attempt_question", code=code, n=1)

    question = qs[n - 1]

    if attempt.current_question != n:
        Attempt.objects.filter(pk=attempt.pk).update(current_question=n)
        attempt.current_question = n

    # ── Section timer logic ──────────────────────────────────────────────────
    qs_section_ids = list(qs.values_list("pk", "section_id"))
    section_pk = qs_section_ids[n - 1][1]

    # Record first entry into this section (no-op on subsequent visits)
    attempt.record_section_entry(section_pk)

    now = timezone.now()
    sec_expires = _section_expires_at(attempt, section_pk)

    if sec_expires and now >= sec_expires:
        # Section's 60-min slot exhausted — go to that section's review screen.
        # If review time is also 0, that screen will auto-advance to the next section / submit.
        return redirect("assessment:attempt_section_review_info", code=code, section_id=section_pk)

    # When this section's clock hits zero, JS redirects to that section's review screen.
    section_timeout_url = reverse(
        "assessment:attempt_section_review_info",
        kwargs={"code": code, "section_id": section_pk},
    )

    # After the last question of EACH section, offer that section's review screen.
    end_redirect = None
    is_last_q_in_section = (n == total) or (qs_section_ids[n][1] != section_pk)
    if is_last_q_in_section:
        end_redirect = redirect(
            "assessment:attempt_section_review_info", code=code, section_id=section_pk
        )
    # ────────────────────────────────────────────────────────────────────────

    spec = _question_spec(question)
    layout = spec.get("layout", "default")

    if spec.get("kind_hint") == "mcq_or_choice" and not spec.get("choices"):
        spec["choices"] = _extract_inline_choices(question.prompt)

    if question.kind == Question.LONG_DIVISION:
        spec["digit_range"] = list(range(spec.get("num_digits", 2)))
        spec["digit_bank"] = list(range(10))

    extra_context = {
        "section_expires_at": sec_expires,
        "section_timeout_url": section_timeout_url,
    }

    if layout in {"info_only", "info-only"}:
        return _handle_info_only(request, attempt, question, spec, n, total, expires_at, extra_context=extra_context)

    if layout == "passage_only":
        return _handle_passage_only(request, attempt, question, spec, n, total, expires_at, extra_context=extra_context)

    return _handle_with_response(
        request, attempt, question, spec, n, total, expires_at,
        end_redirect=end_redirect,
        extra_context=extra_context,
    )


def attempt_submit(request, code: str):
    attempt = get_object_or_404(Attempt, code=code)
    if not _owns_attempt(request, code):
        return HttpResponseForbidden()

    if _expire_attempt_if_needed(attempt):
        return redirect("assessment:attempt_submitted", code=code)

    if attempt.status == Attempt.SUBMITTED:
        return redirect("assessment:attempt_submitted", code=code)

    if not attempt.has_honesty_declaration:
        return redirect("assessment:attempt_details", code=code)

    if request.method == "POST":
        _finalize_attempt(attempt)
        return redirect("assessment:attempt_submitted", code=code)

    answered = Response.objects.filter(attempt=attempt).exclude(response_json="").count()
    return render(
        request,
        "assessment/submitted.html",
        {"attempt": attempt, "answered": answered},
    )


def attempt_details(request, code: str):
    attempt = get_object_or_404(Attempt, code=code)
    if not _owns_attempt(request, code):
        return HttpResponseForbidden()
    learner = attempt.learner

    if request.method == "POST":
        learner_form = LearnerForm(request.POST, instance=learner)
        honesty_form = HonestyForm(request.POST)

        if learner_form.is_valid() and honesty_form.is_valid():
            learner_form.save()

            attempt.accept_honesty_declaration(
                honesty_form.cleaned_data["honesty_name"]
            )

            return redirect("assessment:attempt_instructions", code=code)
    else:
        learner_form = LearnerForm(instance=learner)
        honesty_form = HonestyForm(initial={"honesty_name": attempt.honesty_name})

    return render(
        request,
        "assessment/details.html",
        {
            "attempt": attempt,
            "learner_form": learner_form,
            "honesty_form": honesty_form,
        },
    )


def attempt_instructions(request, code: str):
    attempt = get_object_or_404(Attempt, code=code)
    if not _owns_attempt(request, code):
        return HttpResponseForbidden()

    if not attempt.has_honesty_declaration:
        return redirect("assessment:attempt_details", code=code)

    if request.method == "POST":
        attempt.start()
        return redirect("assessment:attempt_question", code=code, n=1)

    return render(request, "assessment/instructions.html", {"attempt": attempt})


def attempt_submitted(request, code: str):
    attempt = get_object_or_404(Attempt, code=code)
    return render(request, "assessment/submitted.html", {"attempt": attempt})


def session_join(request, code: str):
    """Step 1 of session entry: learner particulars. On success renders the consent modal."""
    session = get_object_or_404(
        ExamSession.objects.select_related("template"),
        code=code,
    )

    if not session.is_open:
        return render(request, "assessment/session_expired.html", {"session": session})

    # If this browser already completed step 1, skip straight to consent (or assessment).
    existing_code = request.session.get(_LEARNER_SESSION_KEY)
    if existing_code:
        try:
            existing = Attempt.objects.get(code=existing_code, session=session)
            if existing.consent_signed_at:
                return redirect("assessment:attempt_question", code=existing.code, n=1)
            learner = existing.learner
            return render(request, "assessment/session_join.html", {
                "session": session,
                "show_consent": True,
                "workstation_number": existing.workstation_number,
                "learner_name": f"{learner.first_names} {learner.surname}".strip(),
            })
        except Attempt.DoesNotExist:
            del request.session[_LEARNER_SESSION_KEY]

    learner_form = LearnerForm(request.POST or None)

    if request.method == "POST" and learner_form.is_valid():
        learner = learner_form.save()
        ok, msg, attempt = claim_session_seat(session, learner)
        if not ok:
            learner.delete()
            return render(request, "assessment/session_join.html", {
                "session": session,
                "learner_form": learner_form,
                "error": msg,
            })
        _bind_attempt_to_session(request, attempt.code)
        return render(request, "assessment/session_join.html", {
            "session": session,
            "show_consent": True,
            "workstation_number": attempt.workstation_number,
            "learner_name": f"{learner.first_names} {learner.surname}".strip(),
        })

    return render(request, "assessment/session_join.html", {
        "session": session,
        "learner_form": learner_form,
    })


def session_consent(request, code: str):
    """Step 2 of session entry: learner scrolled and accepted — record consent."""
    if request.method != "POST":
        return redirect("assessment:session_join", code=code)

    attempt_code = request.session.get(_LEARNER_SESSION_KEY)
    if not attempt_code:
        return redirect("assessment:session_join", code=code)

    attempt = get_object_or_404(
        Attempt.objects.select_related("learner"), code=attempt_code
    )

    if attempt.consent_signed_at:
        return redirect("assessment:attempt_question", code=attempt.code, n=1)

    full_name = f"{attempt.learner.first_names} {attempt.learner.surname}".strip()
    attempt.accept_consent(signature_png="", name=full_name)
    return redirect("assessment:attempt_question", code=attempt.code, n=1)


@login_required
@user_passes_test(is_assessor)
def assessor_sessions(request):
    sessions = (
        ExamSession.objects
        .select_related("template", "created_by")
        .annotate(attempt_count=Count("attempts"))
        .order_by("-created_at")
    )
    rows = [
        {"session": s, "is_open": s.is_open, "attempt_count": s.attempt_count}
        for s in sessions
    ]
    return render(request, "assessment/assessor_sessions.html", {"rows": rows})


@login_required
@user_passes_test(is_auditor)
def assessor_results(request):
    q_code    = (request.GET.get("q_code")    or "").strip()
    q_learner = (request.GET.get("q_learner") or "").strip()
    q_date    = (request.GET.get("q_date")    or "").strip()

    attempts = (
        Attempt.objects
        .filter(
            status__in=[Attempt.SUBMITTED, Attempt.INCOMPLETE],
            response__score__isnull=False,
        )
        .select_related("learner", "template")
        .distinct()
        .order_by("-submitted_at")
    )

    if q_code:
        attempts = attempts.filter(code__icontains=q_code)
    if q_learner:
        attempts = attempts.filter(
            Q(learner__first_names__icontains=q_learner)
            | Q(learner__surname__icontains=q_learner)
        )
    if q_date:
        attempts = attempts.filter(submitted_at__date=q_date)

    try:
        per_page = int(request.GET.get("per_page", 25))
    except (ValueError, TypeError):
        per_page = 25
    if per_page not in (10, 25, 50, 100):
        per_page = 25

    paginator = Paginator(attempts, per_page)
    page_obj  = paginator.get_page(request.GET.get("page"))

    sections = Section.objects.filter(template__attempt__in=page_obj.object_list).distinct()
    all_questions = (
        Question.objects
        .filter(section__in=sections)
        .select_related("section")
    )
    q_meta = build_question_metadata(all_questions)

    rows = [compute_nqf_placement(attempt, q_meta) for attempt in page_obj.object_list]

    params = request.GET.copy()
    params.pop("page", None)

    return render(request, "assessment/assessor_results.html", {
        "rows":       rows,
        "page_obj":   page_obj,
        "filter_qs":  params.urlencode(),
        "per_page":   per_page,
        "lit_groups": NQF_DISPLAY_GROUPS["literacy"],
        "num_groups": NQF_DISPLAY_GROUPS["numeracy"],
        "q_code":     q_code,
        "q_learner":  q_learner,
        "q_date":     q_date,
    })


@login_required
@user_passes_test(is_auditor)
def assessor_results_export(request):
    attempts = (
        Attempt.objects
        .filter(
            status__in=[Attempt.SUBMITTED, Attempt.INCOMPLETE],
            response__score__isnull=False,
        )
        .select_related("learner", "template")
        .distinct()
        .order_by("learner__surname", "learner__first_names")
    )

    sections = Section.objects.filter(template__attempt__in=attempts).distinct()
    all_questions = Question.objects.filter(section__in=sections).select_related("section")
    q_meta = build_question_metadata(all_questions)

    lit_groups = NQF_DISPLAY_GROUPS["literacy"]
    num_groups = NQF_DISPLAY_GROUPS["numeracy"]

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="nqf_results.csv"'

    writer = csv.writer(response)

    header = [
        "First Name", "Surname", "ID Number",
        "Attempt Code", "Template", "Submitted At",
    ]
    for g in lit_groups:
        header += [f"Lit: {g['label']} Awarded", f"Lit: {g['label']} Max", f"Lit: {g['label']} %"]
    header += ["Literacy Level"]
    for g in num_groups:
        header += [f"Num: {g['label']} Awarded", f"Num: {g['label']} Max", f"Num: {g['label']} %"]
    header += ["Numeracy Level", "NQF Placement"]
    writer.writerow(header)

    for attempt in attempts:
        placement = compute_nqf_placement(attempt, q_meta)
        learner = attempt.learner
        submitted_at = attempt.submitted_at.strftime("%Y-%m-%d %H:%M") if attempt.submitted_at else ""

        row = [
            learner.first_names,
            learner.surname,
            learner.id_number,
            attempt.code,
            str(attempt.template),
            submitted_at,
        ]
        for g in placement.literacy_groups:
            row += [g["awarded"], g["max"], g["pct"]]
        row += [placement.lit_level]
        for g in placement.numeracy_groups:
            row += [g["awarded"], g["max"], g["pct"]]
        row += [placement.num_level, placement.comment]

        writer.writerow(row)

    return response


@login_required
@user_passes_test(is_assessor)
def assessor_new_session(request):
    latest_template = AssessmentTemplate.objects.order_by("-created_at").first()

    if latest_template is None:
        return render(request, "assessment/assessor_new_session.html",
                      {"error": "No assessment template exists yet."})

    if request.method == "POST":
        form = ExamSessionForm(request.POST)
        if form.is_valid():
            session = form.save(commit=False)
            session.created_by = request.user
            session.save()
            return render(request, "assessment/assessor_new_session.html",
                          {"session": session, "form": form})
    else:
        form = ExamSessionForm(initial={"template": latest_template})

    return render(request, "assessment/assessor_new_session.html", {"form": form})


@login_required
@user_passes_test(is_assessor)
def session_monitor(request, code: str):
    session = get_object_or_404(
        ExamSession.objects.select_related("template"),
        code=code,
    )

    total_questions = Question.objects.filter(
        section__template=session.template, is_active=True
    ).count()

    now = timezone.now()
    attempts = (
        session.attempts
        .select_related("learner")
        .annotate(response_count=Count("response"))
        .order_by("started_at")
    )

    rows = []
    for a in attempts:
        if a.started_at:
            end = a.submitted_at or now
            elapsed_display = str(end - a.started_at).split(".")[0]
        else:
            elapsed_display = "—"

        pct = int((a.current_question or 0) / total_questions * 100) if total_questions else 0

        rows.append({
            "attempt": a,
            "learner_name": f"{a.learner.first_names} {a.learner.surname}",
            "current_question": a.current_question or 0,
            "responses_count": a.response_count,
            "total_questions": total_questions,
            "elapsed_display": elapsed_display,
            "status": a.status,
            "has_started": a.started_at is not None,
            "pct": pct,
            "mark_url": reverse("assessment:assessor_mark_attempt", kwargs={"code": a.code}),
            "workstation_number": a.workstation_number,
        })

    # Pad to seat_limit — empty slots carry their expected workstation number
    occupied_count = len(rows)
    empty_slots = [
        {"is_empty": True, "workstation_number": session.seat_limit - occupied_count - i}
        for i in range(max(0, session.seat_limit - occupied_count))
    ]
    slots = rows + empty_slots

    return render(request, "assessment/session_monitor.html", {
        "session": session,
        "slots": slots,
        "rows": rows,
        "total_questions": total_questions,
        "seat_limit": session.seat_limit,
        "seats_taken": len(rows),
    })


@login_required
@user_passes_test(is_staff)
def assessor_questions(request):
    templates = AssessmentTemplate.objects.order_by("name", "-created_at")

    selected_template_id = None
    try:
        selected_template_id = int(request.GET.get("template", ""))
    except (TypeError, ValueError):
        pass

    if selected_template_id:
        questions = (
            Question.objects
            .filter(section__template_id=selected_template_id)
            .select_related("section", "section__template")
            .order_by("section__order", "order", "code")
        )
    else:
        questions = (
            Question.objects
            .select_related("section", "section__template")
            .order_by("section__template__name", "section__order", "order", "code")
        )

    return render(request, "assessment/assessor_questions.html", {
        "templates": templates,
        "selected_template_id": selected_template_id,
        "questions": questions,
    })


@login_required
@user_passes_test(is_staff)
def assessor_toggle_question(request, pk: int):
    if request.method != "POST":
        return redirect("assessment:assessor_questions")

    question = get_object_or_404(Question, pk=pk)
    question.is_active = not question.is_active
    question.save(update_fields=["is_active"])

    template_id = question.section.template_id
    url = reverse("assessment:assessor_questions")
    return redirect(f"{url}?template={template_id}")


@login_required
@user_passes_test(is_assessor)
def assessor_review_queue(request):
    """Submitted attempts that still have questions flagged for assessor review."""
    from django.db.models import Exists, OuterRef

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
    attempts = (
        Attempt.objects
        .filter(status=Attempt.SUBMITTED)
        .filter(Exists(has_review_score) | Exists(has_unscored_markable))
        .select_related("learner", "template")
        .order_by("-submitted_at")
    )
    return render(request, "assessment/assessor_review_queue.html", {"attempts": attempts})


def _advance_after_section(attempt, section_pk: int):
    """Move on after a section's review screen is dismissed: next section's first question, or finalise."""
    next_n = _next_section_first_n(attempt.template, section_pk)
    if next_n:
        return redirect("assessment:attempt_question", code=attempt.code, n=next_n)
    _finalize_attempt(attempt)
    return redirect("assessment:attempt_submitted", code=attempt.code)


def attempt_section_review_info(request, code: str, section_id: int):
    """Offer the learner a choice to review (or skip) the section they just finished."""
    attempt = get_object_or_404(Attempt, code=code)
    if not _owns_attempt(request, code):
        return HttpResponseForbidden()

    if attempt.status == Attempt.SUBMITTED:
        return redirect("assessment:attempt_submitted", code=code)

    if not attempt.has_honesty_declaration:
        return redirect("assessment:attempt_details", code=code)

    section = get_object_or_404(Section, pk=section_id, template=attempt.template)
    section_questions = _section_questions(attempt.template, section.pk)

    # If review already started for this section, jump straight into it
    if attempt.get_section_review_started_at(section.pk):
        return redirect(
            "assessment:attempt_section_review_question",
            code=code,
            section_id=section.pk,
            n=1,
        )

    projected_review_seconds = _projected_section_review_seconds(attempt, section.pk)

    if request.method == "POST":
        if request.POST.get("action") == "review" and projected_review_seconds > 0 and section_questions:
            attempt.start_section_review(section.pk)
            return redirect(
                "assessment:attempt_section_review_question",
                code=code,
                section_id=section.pk,
                n=1,
            )
        return _advance_after_section(attempt, section.pk)

    # Auto-skip if there is nothing to review (no time left or no questions)
    if projected_review_seconds == 0 or not section_questions:
        return _advance_after_section(attempt, section.pk)

    return render(request, "assessment/review_info.html", {
        "attempt": attempt,
        "section": section,
        "total_questions": len(section_questions),
        "review_seconds": projected_review_seconds,
    })


def attempt_section_review_question(request, code: str, section_id: int, n: int):
    """Review phase for a single section, with one global timer (≤10 min) for the whole phase."""
    attempt = get_object_or_404(Attempt, code=code)
    if not _owns_attempt(request, code):
        return HttpResponseForbidden()

    if attempt.status == Attempt.SUBMITTED:
        return redirect("assessment:attempt_submitted", code=code)

    section = get_object_or_404(Section, pk=section_id, template=attempt.template)

    if not attempt.get_section_review_started_at(section.pk):
        return redirect(
            "assessment:attempt_section_review_info", code=code, section_id=section.pk
        )

    questions = _section_questions(attempt.template, section.pk)
    total = len(questions)
    if total == 0:
        return _advance_after_section(attempt, section.pk)

    # If the global section-review timer has expired, advance off this section
    review_expires = _section_review_expires_at(attempt, section.pk)
    if review_expires and timezone.now() >= review_expires:
        return _advance_after_section(attempt, section.pk)

    n = max(1, min(n, total))
    question = questions[n - 1]
    spec = _question_spec(question)

    if spec.get("kind_hint") == "mcq_or_choice" and not spec.get("choices"):
        spec["choices"] = _extract_inline_choices(question.prompt)

    if request.method == "POST":
        learner_response, _ = Response.objects.get_or_create(attempt=attempt, question=question)
        renderer = get_renderer(question, spec, learner_response)
        form = renderer.get_form(request)
        renderer.save(request, form)
        attempt.touch()
        if n >= total:
            return redirect(
                "assessment:attempt_section_review_done", code=code, section_id=section.pk
            )
        return redirect(
            "assessment:attempt_section_review_question",
            code=code,
            section_id=section.pk,
            n=n + 1,
        )

    learner_response, _ = Response.objects.get_or_create(attempt=attempt, question=question)
    renderer = get_renderer(question, spec, learner_response)
    form = renderer.get_form(request)

    timeout_url = reverse(
        "assessment:attempt_section_review_done",
        kwargs={"code": code, "section_id": section.pk},
    )

    passage = _load_passage(question, spec) if spec.get("layout") == "passage_split" else ""
    context = _base_context(
        attempt, question, spec, n, total, expires_at=review_expires, form=form, passage=passage
    )
    context.update(renderer.get_context())
    context.update({
        "is_review": True,
        "section": section,
        "slot_expires_at": review_expires,
        "review_timeout_url": timeout_url,
        "has_prev": n > 1,
        "has_next": n < total,
    })
    return render(request, "assessment/review_question.html", context)


def attempt_section_review_done(request, code: str, section_id: int):
    """Section review finished or timed out — advance to the next section, or finalise."""
    attempt = get_object_or_404(Attempt, code=code)
    if not _owns_attempt(request, code):
        return HttpResponseForbidden()
    if attempt.status == Attempt.SUBMITTED:
        return redirect("assessment:attempt_submitted", code=code)
    section = get_object_or_404(Section, pk=section_id, template=attempt.template)
    return _advance_after_section(attempt, section.pk)


# ── Working sheet ──────────────────────────────────────────────────────────────

def _valid_file_magic(header: bytes) -> bool:
    return (
        header[:3] == b'\xff\xd8\xff'               # JPEG
        or header[:8] == b'\x89PNG\r\n\x1a\n'       # PNG
        or (header[:4] == b'RIFF' and header[8:12] == b'WEBP')  # WebP
        or header[:5] == b'%PDF-'                   # PDF
    )

@login_required
@user_passes_test(is_assessor)
def assessor_working_sheet_upload(request, code: str):
    """Upload or replace the working sheet scan for an attempt."""
    import base64

    attempt = get_object_or_404(Attempt.objects.select_related("learner"), code=code)

    if request.method != "POST":
        return redirect("assessment:assessor_mark_attempt", code=code)

    uploaded_file = request.FILES.get("working_sheet")
    if not uploaded_file:
        return redirect(f"{reverse('assessment:assessor_mark_attempt', kwargs={'code': code})}?ws_error=1")

    allowed_types = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
    if uploaded_file.content_type not in allowed_types:
        return redirect(f"{reverse('assessment:assessor_mark_attempt', kwargs={'code': code})}?ws_error=2")

    # M-5: Validate magic bytes — reject files that lie about their content-type.
    header = uploaded_file.read(12)
    uploaded_file.seek(0)
    if not _valid_file_magic(header):
        return redirect(f"{reverse('assessment:assessor_mark_attempt', kwargs={'code': code})}?ws_error=2")

    encoded = base64.b64encode(uploaded_file.read()).decode("utf-8")

    WorkingSheet.objects.update_or_create(
        attempt=attempt,
        defaults={
            "uploaded_by": request.user,
            "content_type": uploaded_file.content_type,
            "original_filename": uploaded_file.name,
            "data": encoded,
        },
    )

    url = reverse("assessment:assessor_mark_attempt", kwargs={"code": code})
    return redirect(f"{url}?ws_saved=1")


@login_required
@user_passes_test(is_auditor)
def assessor_working_sheet_image(request, code: str):
    """Serve the stored working sheet image/PDF."""
    import base64
    from django.http import HttpResponse

    attempt = get_object_or_404(Attempt, code=code)
    sheet = get_object_or_404(WorkingSheet, attempt=attempt)
    data = base64.b64decode(sheet.data)
    response = HttpResponse(data, content_type=sheet.content_type)
    safe_name = re.sub(r'["\r\n\\]', '', sheet.original_filename or f"working_sheet_{code}")
    response["Content-Disposition"] = f'inline; filename="{safe_name}"'
    return response


@login_required
@user_passes_test(is_auditor)
def assessor_working_sheet_print(request, code: str):
    """Printable working sheet for a specific attempt."""
    attempt = get_object_or_404(
        Attempt.objects.select_related("learner", "template"),
        code=code,
    )
    _WORKING_SHEET_CODES = {
        "NUM-A-4", "NUM-B-1", "NUM-B-2", "NUM-B-3",
        "NUM-C-1", "NUM-C-3", "NUM-D-2",
    }
    working_questions = list(
        Question.objects.filter(
            section__template=attempt.template,
            is_active=True,
            code__in=_WORKING_SHEET_CODES,
        ).order_by("section__order", "order")
    )
    return render(request, "assessment/working_sheet_print.html", {
        "attempt": attempt,
        "working_questions": working_questions,
    })


def _needs_working_space(question) -> bool:
    """True if this question has expected working evidence in its answer key."""
    import json as _json
    key = _json.loads(question.answer_key_json or "{}")
    return bool(
        key.get("working_keywords")
        or key.get("flag_if_no_working")
        or key.get("flag_always")
    )


# ---------------------------------------------------------------------------
# Scoring transparency helpers
# ---------------------------------------------------------------------------

def _key_to_criteria_lines(key: dict) -> list[str]:
    """
    Convert a parsed answer_key_json into a list of plain-English criterion
    strings that a client can read to understand how the question is scored.
    """
    lines = []

    if key.get("match"):
        expected = key["match"]
        lines.append(f"Match question — {len(expected)} pair(s).")
        lines.append(f"Marks per correct match: {key.get('marks_per_match', 1)}.")
        for target, word in expected.items():
            lines.append(f"  {target} → \"{word}\"")
        return lines

    if key.get("sentence_word"):
        word = key["sentence_word"]
        min_w = key.get("min_words", 4)
        lines.append(f"Learner must use the word \"{word}\" (or a common inflection) in a sentence.")
        lines.append(f"Minimum sentence length: {min_w} words.")
        lines.append("Always flagged for assessor review.")
        return lines

    if key.get("keyword_answer"):
        kws = key["keyword_answer"]
        partial = key.get("partial_marks", 0)
        lines.append(f"All of the following must appear in the response:")
        for kw in kws:
            lines.append(f"  • \"{kw}\"")
        if partial:
            lines.append(f"Partial marks ({partial}) if only some keywords found.")
        if key.get("flag_always"):
            lines.append("Always flagged for assessor review.")
        return lines

    if key.get("keyword_per_mark"):
        kws = key["keyword_per_mark"]
        mpp = key.get("marks_per_keyword", 1)
        lines.append(f"Each keyword found independently awards {mpp} mark(s):")
        for kw in kws:
            lines.append(f"  • \"{kw}\"")
        if key.get("flag_always"):
            lines.append("Always flagged for assessor review.")
        return lines

    if key.get("tiered_keyword"):
        lines.append("Tiered scoring — first matching tier wins:")
        for i, tier in enumerate(key["tiered_keyword"], 1):
            tier_marks = tier.get("marks", 0)
            parts = [f"Tier {i} → {tier_marks} mark(s)"]
            if tier.get("require_all"):
                parts.append(f"require ALL of: {', '.join(repr(k) for k in tier['require_all'])}")
            if tier.get("require_any"):
                parts.append(f"require ANY of: {', '.join(repr(k) for k in tier['require_any'])}")
            if tier.get("require_not"):
                parts.append(f"exclude if: {', '.join(repr(k) for k in tier['require_not'])}")
            if tier.get("min_words"):
                parts.append(f"min words: {tier['min_words']}")
            lines.append("  " + " | ".join(parts))
        no_match = key.get("no_match_note", "No criteria met → 0 marks.")
        lines.append(f"No tier matched: {no_match}")
        if key.get("flag_always"):
            lines.append("Always flagged for assessor review.")
        return lines

    # Standard numeric/text answer
    answers = key.get("answers", [])
    if answers:
        lines.append(f"Accepted answer(s): {', '.join(repr(str(a)) for a in answers)}")
    working_kws = key.get("working_keywords", [])
    if working_kws:
        lines.append(f"Working evidence required (all must appear): {', '.join(repr(k) for k in working_kws)}")
        partial = key.get("partial_marks", 0)
        if partial:
            lines.append(f"Partial marks ({partial}) if answer correct but working absent.")
    if key.get("flag_always"):
        lines.append("Always flagged for assessor review.")
    elif key.get("flag_if_no_working"):
        lines.append("Flagged for review if working evidence absent.")
    return lines


@login_required
@user_passes_test(is_staff)
def assessor_scoring_breakdown(request, code: str):
    """
    Staff-only view showing how each question in an attempt was scored:
    the marking criteria, the learner's response, and the auto-marker's decision.
    """
    attempt = get_object_or_404(
        Attempt.objects.select_related("learner", "template"),
        code=code,
    )

    questions = (
        Question.objects
        .filter(section__template=attempt.template, is_active=True)
        .select_related("section")
        .order_by("section__order", "order", "code")
    )

    question_ids = [q.pk for q in questions]
    responses_by_qid = {
        r.question_id: r
        for r in Response.objects.filter(
            attempt=attempt, question_id__in=question_ids
        ).select_related("score")
    }

    rows = []
    for question in questions:
        response = responses_by_qid.get(question.pk)
        key = json.loads(question.answer_key_json or "{}")
        auto_markable = bool(key.get("auto_mark"))

        score = None
        rubric = {}
        if response:
            score = getattr(response, "score", None)
            if score:
                rubric = score.rubric_json if isinstance(score.rubric_json, dict) else {}

        criteria_lines = _key_to_criteria_lines(key) if auto_markable else []

        rows.append({
            "question": question,
            "response_text": _render_response_for_marking(question, response) if response else "",
            "auto_markable": auto_markable,
            "criteria_lines": criteria_lines,
            "score": score,
            "awarded": float(score.points) if score else None,
            "max_marks": float(question.max_marks or 0),
            "notes": rubric.get("notes", ""),
            "needs_review": rubric.get("needs_review", False),
            "mode": rubric.get("mode", ""),
            "marking_notes": question.marking_notes or "",
        })

    return render(request, "assessment/assessor_scoring_breakdown.html", {
        "attempt": attempt,
        "rows": rows,
    })


@login_required
@user_passes_test(is_staff)
def assessor_score_audit_log(request, code: str):
    """Per-attempt score audit log — every score creation and change."""
    attempt = get_object_or_404(
        Attempt.objects.select_related("learner", "template"),
        code=code,
    )
    entries = (
        ScoreAuditLog.objects
        .filter(score__response__attempt=attempt)
        .select_related("score__response__question", "changed_by")
        .order_by("changed_at")
    )
    return render(request, "assessment/assessor_score_audit_log.html", {
        "attempt": attempt,
        "entries": entries,
    })


# ── Error handling ────────────────────────────────────────────────────────────

def _notify(error_type, error_msg, url="", method="", user=""):
    """
    Central error notification. Currently logs to Render's log stream.
    Wire up email here once KAIgaba Google Workspace is set up.
    """
    logger.warning(
        "PLATFORM ERROR | %s | %s | %s %s | user:%s",
        error_type, error_msg, method, url, user,
    )


def handler500(request):
    from django.utils.html import escape
    exc_type, exc_value, _ = sys.exc_info()
    error_type = exc_type.__name__ if exc_type else "Error"
    error_msg = str(exc_value) if exc_value else ""
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
    _notify("NotFound", request.path, url=request.build_absolute_uri(), method=request.method)
    return render(request, "404.html", {
        "error_type": "Page Not Found",
        "error_msg": request.path,
    }, status=404)


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
