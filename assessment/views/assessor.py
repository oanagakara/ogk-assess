"""Assessor management views: dashboard, metrics, attempts, results, sessions, questions."""
import csv
import json
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from ..forms import ExamSessionForm
from ..models import (
    AssessmentTemplate, Attempt, ExamSession, Learner, Question, Response, Score, Section,
)
from ..nqf import NQF_DISPLAY_GROUPS, build_question_metadata, compute_nqf_placement

from ._common import _expire_overdue_attempts, is_assessor, is_auditor, is_moderator, is_staff


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
@user_passes_test(is_staff)
def assessor_metrics_simulate(request):
    if request.method != "POST":
        return redirect("assessment:assessor_metrics")
    from django.core.management import call_command
    call_command("simulate_session")
    logger.info("Simulation triggered manually by staff user: %s", request.user.username)
    return redirect("assessment:assessor_metrics")


@login_required
@user_passes_test(is_assessor)
def assessor_metrics(request):
    from django.db.models import Sum
    from django.db.models.functions import TruncDate

    now = timezone.now()
    today = now.date()

    status_qs = Attempt.objects.values("status").annotate(count=Count("id"))
    status_map = {row["status"]: row["count"] for row in status_qs}
    in_progress  = status_map.get(Attempt.IN_PROGRESS, 0)
    submitted    = status_map.get(Attempt.SUBMITTED, 0)
    incomplete   = status_map.get(Attempt.INCOMPLETE, 0)
    finalised    = Attempt.objects.filter(finalised_at__isnull=False).count()
    total        = in_progress + submitted + incomplete

    abandoned_cutoff = now - timedelta(hours=3)
    abandoned = Attempt.objects.filter(
        status=Attempt.IN_PROGRESS,
        last_activity_at__lt=abandoned_cutoff,
    ).count()

    thirty_ago = now - timedelta(days=30)
    daily_qs = (
        Attempt.objects
        .filter(submitted_at__gte=thirty_ago)
        .annotate(day=TruncDate("submitted_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    daily_labels = [str(row["day"]) for row in daily_qs]
    daily_counts = [row["count"] for row in daily_qs]

    score_qs = (
        Score.objects
        .filter(response__attempt__status=Attempt.SUBMITTED, max_points__gt=0)
        .values("response__attempt_id")
        .annotate(
            awarded=Sum("points"),
            available=Sum("max_points"),
        )
    )
    bands = [0] * 5
    scored_count = 0
    total_pct_sum = 0.0
    for row in score_qs:
        if row["available"]:
            pct = row["awarded"] / row["available"] * 100
            total_pct_sum += pct
            scored_count += 1
            idx = min(int(pct // 20), 4)
            bands[idx] += 1
    avg_score = round(total_pct_sum / scored_count, 1) if scored_count else None

    unique_learners = Attempt.objects.values("learner_id").distinct().count()

    nqf_attempts = (
        Attempt.objects
        .filter(status=Attempt.SUBMITTED)
        .prefetch_related(
            Prefetch("response_set", queryset=Response.objects.select_related("score"))
        )
    )
    questions = Question.objects.select_related("section").filter(
        section__template__attempt__status=Attempt.SUBMITTED
    ).distinct()
    q_meta = build_question_metadata(questions)

    lit_level_order = ["L1", "L2", "L3", "L4", "Post L4"]
    num_level_order = ["L1", "L2", "L3", "L4", "Post L4"]
    nqf_lit_counts = {lv: 0 for lv in lit_level_order}
    nqf_num_counts = {lv: 0 for lv in num_level_order}
    for attempt in nqf_attempts:
        placement = compute_nqf_placement(attempt, q_meta)
        lit = str(placement.lit_level)
        num = str(placement.num_level)
        if lit in nqf_lit_counts:
            nqf_lit_counts[lit] += 1
        if num in nqf_num_counts:
            nqf_num_counts[num] += 1

    nqf_labels = lit_level_order
    nqf_lit_data = [nqf_lit_counts[lv] for lv in lit_level_order]
    nqf_num_data = [nqf_num_counts[lv] for lv in num_level_order]

    age_labels = ["18-25", "26-35"]
    age_counts = [0] * 2
    demographics = ["African", "Coloured", "Indian", "White"]
    demo_colors = ["#efbbff", "#d896ff", "#be29ec", "#800080"]
    age_demo_counts = {d: [0] * 2 for d in demographics}

    learner_qs = Learner.objects.filter(
        attempt__isnull=False, dob__isnull=False
    ).values("dob", "demographic").distinct()

    for row in learner_qs:
        dob = row["dob"]
        age = (today - dob).days // 365
        if 18 <= age <= 25:
            bucket = 0
        elif 26 <= age <= 35:
            bucket = 1
        else:
            continue
        age_counts[bucket] += 1
        demo = row.get("demographic", "")
        if demo in age_demo_counts:
            age_demo_counts[demo][bucket] += 1

    return render(request, "assessment/assessor_metrics.html", {
        "total": total,
        "in_progress": in_progress,
        "submitted": submitted,
        "incomplete": incomplete,
        "finalised": finalised,
        "abandoned": abandoned,
        "unique_learners": unique_learners,
        "avg_score": avg_score,
        "daily_labels": json.dumps(daily_labels),
        "daily_counts": json.dumps(daily_counts),
        "score_bands": json.dumps(bands),
        "nqf_labels": json.dumps(nqf_labels),
        "nqf_lit_data": json.dumps(nqf_lit_data),
        "nqf_num_data": json.dumps(nqf_num_data),
        "age_labels": json.dumps(age_labels),
        "age_counts": json.dumps(age_counts),
        "age_demo_labels": json.dumps(age_labels),
        "age_demo_datasets": json.dumps([
            {"label": d, "data": age_demo_counts[d], "backgroundColor": demo_colors[i], "borderWidth": 0}
            for i, d in enumerate(demographics)
        ]),
    })


@login_required
@user_passes_test(is_assessor)
def assessor_guide(request):
    return render(request, "assessment/assessor_guide.html")


@login_required
@user_passes_test(is_assessor)
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
        "submitted":   base_qs.filter(status=Attempt.SUBMITTED, finalised_at__isnull=True).filter(Exists(has_unscored_markable)),
        "marked":      base_qs.filter(status=Attempt.SUBMITTED, finalised_at__isnull=True).filter(~Exists(has_unscored_markable)),
        "incomplete":  base_qs.filter(status=Attempt.INCOMPLETE, finalised_at__isnull=True),
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


@login_required
@user_passes_test(is_assessor)
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

    template_ids = {a.template_id for a in page_obj.object_list}
    all_questions = (
        Question.objects
        .filter(section__template_id__in=template_ids)
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
        num_filled = len(placement.numeracy_groups)
        for g in placement.numeracy_groups:
            row += [g["awarded"], g["max"], g["pct"]]
        for _ in range(len(num_groups) - num_filled):
            row += ["", "", ""]
        num_level_val = "" if placement.num_level == "N/A" else placement.num_level
        row += [num_level_val, placement.comment]

        writer.writerow(row)

    return response


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
    from django.db.models import Count
    from django.urls import reverse

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
