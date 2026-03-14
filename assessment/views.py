from django.utils import timezone
from django.utils.text import slugify
from django.urls import reverse
from datetime import timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Count

from .forms import (
    AttemptForm,
    StartForm,
    LearnerForm,
    HonestyForm,
    TextResponseForm,
    MatchResponseForm,
)
from .models import Attempt, Question, Response, AssessmentTemplate, Learner, Score
from .services import claim_seat

import json
import random


def home(request):
    return render(request, "index.html")


def start(request):
    form = StartForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        code = form.cleaned_data["code"].strip()
        attempt = get_object_or_404(Attempt, code=code)
        ok, msg = claim_seat(attempt)
        if ok:
            return redirect("assessment:attempt_details", code=code)
        form.add_error("code", msg)

    return render(request, "assessment/start.html", {"form": form})


def is_assessor(user):
    return user.is_authenticated and (
        user.is_staff or user.groups.filter(name="assessor").exists()
    )


def _random_13_digit_id() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(13))


def _safe_json_loads(value, default=None):
    if default is None:
        default = {}
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _question_spec(question):
    return _safe_json_loads(question.spec_json, {})


def _question_answer_key(question):
    return _safe_json_loads(question.answer_key_json, {})


def _extract_rubric(question):
    candidates = []

    for container in (_question_spec(question), _question_answer_key(question)):
        if not isinstance(container, dict):
            continue

        for key in ("rubric", "rubric_criteria", "criteria"):
            value = container.get(key)
            if isinstance(value, list):
                candidates = value
                break

        if candidates:
            break

    rubric = []
    for idx, item in enumerate(candidates, start=1):
        if not isinstance(item, dict):
            continue

        label = (
            item.get("label")
            or item.get("criterion")
            or item.get("name")
            or f"Criterion {idx}"
        ).strip()

        raw_max = item.get("max_points", item.get("points", item.get("max", 0)))

        try:
            max_points = float(raw_max)
        except (TypeError, ValueError):
            max_points = 0.0

        key = slugify(str(item.get("key") or label)) or f"criterion_{idx}"

        rubric.append(
            {
                "key": key,
                "label": label,
                "max_points": max_points,
            }
        )

    return rubric


def _is_layout_only_question(question):
    layout = _question_spec(question).get("layout", "")
    return layout in {"info_only", "info-only", "passage_only"}


def _is_markable_question(question):
    if _is_layout_only_question(question):
        return False
    if _extract_rubric(question):
        return True
    return float(question.max_marks or 0) > 0


def _render_response_for_marking(question, response):
    if not response or not response.response_json:
        return ""

    spec = _question_spec(question)

    if spec.get("layout") == "form_fill":
        data = _safe_json_loads(response.response_json, {})
        if not isinstance(data, dict):
            return str(response.response_json).strip()

        fields = spec.get("fields", [])
        if fields:
            lines = []
            for field in fields:
                name = field.get("name", "")
                label = field.get("label") or name
                value = data.get(name, "")
                lines.append(f"{label}: {value}")
            return "\n".join(lines).strip()

        return "\n".join(f"{k}: {v}" for k, v in data.items()).strip()

    if question.kind == Question.MATCH:
        data = _safe_json_loads(response.response_json, {})
        if not isinstance(data, dict):
            return str(response.response_json).strip()

        targets = {
            str(target.get("id")): target.get("text", "")
            for target in spec.get("targets", [])
            if isinstance(target, dict)
        }

        lines = []
        for target_id, word in data.items():
            target_text = targets.get(str(target_id), str(target_id))
            lines.append(f"{target_text}: {word}")
        return "\n".join(lines).strip()

    data = _safe_json_loads(response.response_json, None)
    if isinstance(data, dict):
        return str(data.get("answer", "")).strip()

    return str(response.response_json).strip()


def _clamped_float(value, upper_bound):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    return max(0.0, min(parsed, float(upper_bound)))


@login_required
@user_passes_test(is_assessor)
def assessor_dashboard(request):
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

    recent = (
        Attempt.objects.select_related("learner", "template")
        .annotate(response_count=Count("response"))
        .order_by("-last_activity_at")[:10]
    )

    return render(
        request,
        "assessment/assessor_dashboard.html",
        {
            "total_attempts": total_attempts,
            "submitted_today": submitted_today,
            "active_now": active_now,
            "recent": recent,
        },
    )


@login_required
@user_passes_test(is_assessor)
def assessor_attempts(request):
    qs = (
        Attempt.objects.select_related("learner", "template")
        .annotate(response_count=Count("response"))
        .order_by("-last_activity_at", "-started_at")
    )
    return render(request, "assessment/assessor_attempts.html", {"attempts": qs})



@login_required
@user_passes_test(is_assessor)
def assessor_mark_attempt(request, code: str):
    attempt = get_object_or_404(
        Attempt.objects.select_related("learner", "template"),
        code=code,
    )

    questions = list(
        Question.objects.filter(section__template=attempt.template)
        .select_related("section")
        .order_by("section__order", "order", "code")
    )
    markable_questions = [question for question in questions if _is_markable_question(question)]
    total_questions = len(markable_questions)

    try:
        current_q = int(request.GET.get("q", 1))
    except (TypeError, ValueError):
        current_q = 1

    if total_questions > 0:
        current_q = max(1, min(current_q, total_questions))
    else:
        current_q = 1

    def build_row(question, index):
        response, _ = Response.objects.get_or_create(
            attempt=attempt,
            question=question,
        )

        try:
            score = response.score
        except Score.DoesNotExist:
            score = None

        rubric = _extract_rubric(question)

        if rubric:
            max_points = sum(item["max_points"] for item in rubric)
        else:
            max_points = float(question.max_marks or 0)

        awarded = float(score.points) if score else 0.0

        saved_criteria = {}
        if score and isinstance(score.rubric_json, dict):
            for item in score.rubric_json.get("criteria", []):
                if isinstance(item, dict) and item.get("key"):
                    saved_criteria[str(item["key"])] = item

        rubric_rows = []
        for criterion in rubric:
            saved_item = saved_criteria.get(criterion["key"], {})
            rubric_rows.append(
                {
                    "key": criterion["key"],
                    "label": criterion["label"],
                    "max_points": criterion["max_points"],
                    "value": saved_item.get("points", ""),
                    "feedback": saved_item.get("feedback", ""),
                }
            )

        notes = ""
        if score and isinstance(score.rubric_json, dict):
            notes = score.rubric_json.get("notes", "")

        return {
            "index": index,
            "question": question,
            "response": response,
            "response_text": _render_response_for_marking(question, response),
            "has_rubric": bool(rubric_rows),
            "rubric": rubric_rows,
            "manual_value": awarded if score and not rubric_rows else "",
            "notes": notes,
            "score": score,
            "awarded": awarded,
            "max_points": max_points,
        }

    def save_question(question):
        response, _ = Response.objects.get_or_create(
            attempt=attempt,
            question=question,
        )
        rubric = _extract_rubric(question)

        if rubric:
            criteria_payload = []
            max_points = 0.0
            total_points = 0.0

            for criterion in rubric:
                max_points += criterion["max_points"]

                points = _clamped_float(
                    request.POST.get(
                        f"rubric__{question.pk}__{criterion['key']}",
                        "",
                    ),
                    criterion["max_points"],
                )

                feedback = request.POST.get(
                    f"rubric_feedback__{question.pk}__{criterion['key']}",
                    "",
                ).strip()

                criteria_payload.append(
                    {
                        "key": criterion["key"],
                        "label": criterion["label"],
                        "max_points": criterion["max_points"],
                        "points": points,
                        "feedback": feedback,
                    }
                )
                total_points += points

            rubric_json = {
                "mode": "rubric",
                "criteria": criteria_payload,
                "notes": request.POST.get(f"notes__{question.pk}", "").strip(),
            }
            points = total_points
        else:
            max_points = float(question.max_marks or 0)
            points = _clamped_float(
                request.POST.get(f"manual__{question.pk}", ""),
                max_points,
            )
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

    if request.method == "POST" and total_questions > 0:
        current_question = markable_questions[current_q - 1]
        save_question(current_question)

        action = request.POST.get("action", "save")
        target_q = current_q

        if action == "next":
            target_q = min(total_questions, current_q + 1)
        elif action == "prev":
            target_q = max(1, current_q - 1)

        url = reverse("assessment:assessor_mark_attempt", kwargs={"code": code})
        return redirect(f"{url}?q={target_q}&saved=1")

    total_available = 0.0
    total_awarded = 0.0
    scored_count = 0

    for question in markable_questions:
        response, _ = Response.objects.get_or_create(attempt=attempt, question=question)

        try:
            score = response.score
        except Score.DoesNotExist:
            score = None

        rubric = _extract_rubric(question)
        if rubric:
            max_points = sum(item["max_points"] for item in rubric)
        else:
            max_points = float(question.max_marks or 0)

        awarded = float(score.points) if score else 0.0

        total_available += max_points
        total_awarded += awarded
        if score is not None:
            scored_count += 1

    current_row = None
    if total_questions > 0:
        current_row = build_row(markable_questions[current_q - 1], current_q)

    saved = request.GET.get("saved") == "1"

    return render(
        request,
        "assessment/assessor_mark_attempt.html",
        {
            "attempt": attempt,
            "row": current_row,
            "saved": saved,
            "total_awarded": total_awarded,
            "total_available": total_available,
            "scored_count": scored_count,
            "current_q": current_q,
            "total_questions": total_questions,
            "has_prev": current_q > 1,
            "has_next": current_q < total_questions,
            "prev_q": current_q - 1,
            "next_q": current_q + 1,
        },
    )

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
                id_number=str(int(timezone.now().timestamp()))[:13],
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


def attempt_question(request, code: str, n: int):
    attempt = get_object_or_404(Attempt, code=code)

    if not attempt.honesty_accepted_at:
        return redirect("assessment:attempt_details", code=code)

    qs = (
        Question.objects.filter(section__template=attempt.template)
        .select_related("section")
        .order_by("section__order", "order", "code")
    )
    total = qs.count()
    if total == 0:
        return render(request, "assessment/no_questions.html", {"attempt": attempt})

    if n < 1 or n > total:
        return redirect("assessment:attempt_question", code=code, n=1)

    question = qs[n - 1]

    THANDI_CODES = {"LIT-B-1", "LIT-B-2", "LIT-B-3", "LIT-B-4"}

    spec = _question_spec(question)
    layout = spec.get("layout", "default")

    if layout in {"info_only", "info-only"}:
        if request.method == "POST" and "next" in request.POST:
            attempt.last_activity_at = timezone.now()
            attempt.save(update_fields=["last_activity_at"])

            if n >= total:
                return redirect("assessment:attempt_submit", code=code)
            return redirect("assessment:attempt_question", code=code, n=n + 1)

        return render(
            request,
            "assessment/question.html",
            {
                "attempt": attempt,
                "question": question,
                "form": None,
                "n": n,
                "total": total,
                "passage": "",
                "layout": layout,
                "spec": spec,
            },
        )

    passage = ""
    if question.code in THANDI_CODES or question.code == "LIT-B-READ":
        try:
            passage = Question.objects.get(code="LIT-B-READ").prompt
        except Question.DoesNotExist:
            passage = ""

    if layout == "passage_only":
        if request.method == "POST" and "next" in request.POST:
            attempt.last_activity_at = timezone.now()
            attempt.save(update_fields=["last_activity_at"])

            if n >= total:
                return redirect("assessment:attempt_submit", code=code)
            return redirect("assessment:attempt_question", code=code, n=n + 1)

        return render(
            request,
            "assessment/question.html",
            {
                "attempt": attempt,
                "question": question,
                "form": None,
                "n": n,
                "total": total,
                "passage": passage,
                "layout": layout,
                "spec": spec,
            },
        )

    resp, _ = Response.objects.get_or_create(attempt=attempt, question=question)

    form_fill_values = {}

    if spec.get("layout") == "form_fill":
        if resp.response_json:
            loaded = _safe_json_loads(resp.response_json, {})
            if isinstance(loaded, dict):
                form_fill_values = loaded

        for field in spec.get("fields", []):
            field["value"] = form_fill_values.get(field["name"], "")

    if question.kind == Question.MATCH:
        existing_json = resp.response_json or ""
        if existing_json == "":
            existing_json = "{}"
        form = MatchResponseForm(
            request.POST or None,
            initial={"response_json": existing_json},
        )
    else:
        existing = ""
        if resp.response_json:
            loaded = _safe_json_loads(resp.response_json, None)
            if isinstance(loaded, dict):
                existing = loaded.get("answer", "")
            else:
                existing = resp.response_json

        form = TextResponseForm(
            request.POST or None,
            initial={"answer": existing},
        )

    if request.method == "POST":
        if spec.get("layout") == "form_fill":
            out = {}
            for field in spec.get("fields", []):
                out[field["name"]] = (
                    request.POST.get(f"ff_{field['name']}", "") or ""
                ).strip()
            resp.response_json = json.dumps(out, ensure_ascii=False)
        else:
            if question.kind == Question.MATCH:
                if form.is_valid():
                    raw = form.cleaned_data.get("response_json", "") or ""
                else:
                    raw = request.POST.get("response_json", "") or ""
                resp.response_json = raw
            else:
                if form.is_valid():
                    ans = form.cleaned_data.get("answer", "") or ""
                else:
                    ans = request.POST.get("answer", "") or ""
                resp.response_json = json.dumps({"answer": ans}, ensure_ascii=False)

        resp.save(update_fields=["response_json"])
        attempt.last_activity_at = timezone.now()
        attempt.save(update_fields=["last_activity_at"])

        if "next" in request.POST:
            if n >= total:
                return redirect("assessment:attempt_submit", code=code)
            return redirect("assessment:attempt_question", code=code, n=n + 1)

        if "prev" in request.POST:
            prev_n = max(1, n - 1)
            return redirect("assessment:attempt_question", code=code, n=prev_n)

    return render(
        request,
        "assessment/question.html",
        {
            "attempt": attempt,
            "question": question,
            "form": form,
            "n": n,
            "total": total,
            "passage": passage,
            "layout": layout,
            "spec": spec,
            "form_fill_values": form_fill_values,
        },
    )


def attempt_submit(request, code: str):
    attempt = get_object_or_404(Attempt, code=code)
    if attempt.status == Attempt.SUBMITTED:
        return redirect("assessment:attempt_submitted", code=code)

    if not attempt.honesty_accepted_at:
        return redirect("assessment:attempt_details", code=code)

    if request.method == "POST":
        attempt.status = Attempt.SUBMITTED
        attempt.submitted_at = timezone.now()
        attempt.last_activity_at = timezone.now()
        attempt.save(update_fields=["status", "submitted_at", "last_activity_at"])
        return redirect("assessment:attempt_submitted", code=code)

    answered = Response.objects.filter(attempt=attempt).exclude(response_json="").count()
    return render(request, "assessment/submitted.html", {"attempt": attempt, "answered": answered})


def attempt_details(request, code: str):
    attempt = get_object_or_404(Attempt, code=code)
    learner = attempt.learner

    if request.method == "POST":
        learner_form = LearnerForm(request.POST, instance=learner)
        honesty_form = HonestyForm(request.POST)

        if learner_form.is_valid() and honesty_form.is_valid():
            learner_form.save()

            attempt.honesty_name = honesty_form.cleaned_data["honesty_name"].strip()
            attempt.honesty_accepted_at = timezone.now()
            attempt.last_activity_at = timezone.now()
            attempt.save(
                update_fields=[
                    "honesty_name",
                    "honesty_accepted_at",
                    "last_activity_at",
                ]
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

    if not attempt.honesty_accepted_at:
        return redirect("assessment:attempt_details", code=code)

    if request.method == "POST":
        attempt.last_activity_at = timezone.now()
        attempt.save(update_fields=["last_activity_at"])
        return redirect("assessment:attempt_question", code=code, n=1)

    return render(request, "assessment/instructions.html", {"attempt": attempt})


def attempt_submitted(request, code: str):
    attempt = get_object_or_404(Attempt, code=code)
    return render(request, "assessment/submitted.html", {"attempt": attempt})