from datetime import timedelta
import json
import random
import re

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator 

from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from .forms import (
    AttemptForm,
    HonestyForm,
    LearnerForm,
    StartForm,
)
from .models import AssessmentTemplate, Attempt, Learner, Question, Response, Score
from .renderers import get_renderer
from .services import claim_seat


ASSESSMENT_DURATION = timedelta(hours=2)

# Question codes that display the Thandi passage.
# TODO: move to spec_json["passage_source"] in a data migration.
_PASSAGE_CODES = frozenset({"LIT-B-1", "LIT-B-2", "LIT-B-3", "LIT-B-4", "LIT-B-READ"})


def _attempt_expires_at(attempt):
    if not attempt.started_at:
        return None
    return attempt.started_at + ASSESSMENT_DURATION


def _finalize_attempt(attempt, when=None):
    attempt.submit(when=when)
    from .auto_mark import auto_mark_attempt
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
        status=Attempt.SUBMITTED,
        submitted_at=now,
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


def home(request):
    return render(request, "index.html")


def start(request):
    form = StartForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        code = form.cleaned_data["code"].strip()
        attempt = Attempt.objects.filter(code=code).first()

        if attempt is None:
            form.add_error("code", "Invalid assessment code.")
        else:
            ok, msg = claim_seat(attempt)
            if ok:
                return redirect("assessment:attempt_details", code=code)
            form.add_error("code", msg)

    return render(request, "assessment/start.html", {"form": form})


def is_assessor(user):
    return user.is_authenticated and (
        user.is_staff or user.groups.filter(name="assessor").exists()
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

    recent_qs  = (
        Attempt.objects.select_related("learner", "template")
        .annotate(response_count=Count("response"))
        .order_by("-last_activity_at", "-started_at")
    )

    paginator = Paginator(recent_qs, 10)
    page_number = request.GET.get("page")
    recent_page = paginator.get_page(page_number)

    return render(
        request,
        "assessment/assessor_dashboard.html",
        {
            "total_attempts": total_attempts,
            "submitted_today": submitted_today,
            "active_now": active_now,
            "recent_page": recent_page,
        },
    )


@login_required
@user_passes_test(is_assessor)
def assessor_attempts(request):
    _expire_overdue_attempts()

    qs = (
        Attempt.objects.select_related("learner", "template")
        .annotate(response_count=Count("response"))
        .order_by("-last_activity_at", "-started_at")
    )
    return render(request, "assessment/assessor_attempts.html", {"attempts": qs})


@login_required
@user_passes_test(is_assessor)
def assessor_mark_attempt(request, code: str):
    _expire_overdue_attempts()

    attempt = get_object_or_404(
        Attempt.objects.select_related("learner", "template"),
        code=code,
    )

    questions = list(
        Question.objects.filter(section__template=attempt.template)
        .select_related("section")
        .order_by("section__order", "order", "code")
    )
    markable_questions = [
        question for question in questions if _is_markable_question(question)
    ]
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

        if action == "done":
            return redirect("assessment:assessor_attempts") 

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


def _load_passage(question, spec) -> str:
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
    """Return a redirect after saving, or None if no navigation key was posted."""
    if "next" in request.POST:
        if n >= total:
            _finalize_attempt(attempt)
            return redirect("assessment:attempt_submitted", code=attempt.code)
        return redirect("assessment:attempt_question", code=attempt.code, n=n + 1)
    if "prev" in request.POST:
        return redirect("assessment:attempt_question", code=attempt.code, n=max(1, n - 1))
    return None


def _base_ctx(attempt, question, spec, n, total, expires_at, passage="", form=None):
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


def _handle_info_only(request, attempt, question, spec, n, total, expires_at):
    if request.method == "POST" and "next" in request.POST:
        attempt.touch()
        if n >= total:
            return redirect("assessment:attempt_submit", code=attempt.code)
        return redirect("assessment:attempt_question", code=attempt.code, n=n + 1)
    return render(request, "assessment/question.html",
                  _base_ctx(attempt, question, spec, n, total, expires_at))


def _handle_passage_only(request, attempt, question, spec, n, total, expires_at):
    passage = _load_passage(question, spec)
    if request.method == "POST" and "next" in request.POST:
        attempt.touch()
        if n >= total:
            return redirect("assessment:attempt_submit", code=attempt.code)
        return redirect("assessment:attempt_question", code=attempt.code, n=n + 1)
    return render(request, "assessment/question.html",
                  _base_ctx(attempt, question, spec, n, total, expires_at, passage=passage))


def _handle_with_response(request, attempt, question, spec, n, total, expires_at):
    passage = _load_passage(question, spec)
    resp, _ = Response.objects.get_or_create(attempt=attempt, question=question)
    renderer = get_renderer(question, spec, resp)
    form = renderer.get_form(request)

    if request.method == "POST":
        renderer.save(request, form)
        attempt.touch()
        nav = _navigate(request, attempt, n, total)
        if nav:
            return nav

    ctx = _base_ctx(attempt, question, spec, n, total, expires_at, passage=passage, form=form)
    ctx.update(renderer.get_context())
    return render(request, "assessment/question.html", ctx)


def attempt_question(request, code: str, n: int):
    attempt = get_object_or_404(Attempt, code=code)

    if _expire_attempt_if_needed(attempt):
        return redirect("assessment:attempt_submitted", code=code)

    if not attempt.has_honesty_declaration:
        return redirect("assessment:attempt_details", code=code)

    if not attempt.started_at:
        attempt.start()

    expires_at = _attempt_expires_at(attempt)

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
    spec = _question_spec(question)
    layout = spec.get("layout", "default")

    if spec.get("kind_hint") == "mcq_or_choice" and not spec.get("choices"):
        spec["choices"] = _extract_inline_choices(question.prompt)

    if layout in {"info_only", "info-only"}:
        return _handle_info_only(request, attempt, question, spec, n, total, expires_at)

    if layout == "passage_only":
        return _handle_passage_only(request, attempt, question, spec, n, total, expires_at)

    return _handle_with_response(request, attempt, question, spec, n, total, expires_at)


def attempt_submit(request, code: str):
    attempt = get_object_or_404(Attempt, code=code)

    if _expire_attempt_if_needed(attempt):
        return redirect("assessment:attempt_submitted", code=code)

    if attempt.status == Attempt.SUBMITTED:
        return redirect("assessment:attempt_submitted", code=code)

    if not attempt.has_honesty_declaration:
        return redirect("assessment:attempt_details", code=code)

    if request.method == "POST":
        attempt.submit()
        from .auto_mark import auto_mark_attempt
        auto_mark_attempt(attempt)
        return redirect("assessment:attempt_submitted", code=code)

    answered = Response.objects.filter(attempt=attempt).exclude(response_json="").count()
    return render(
        request,
        "assessment/submitted.html",
        {"attempt": attempt, "answered": answered},
    )


def attempt_details(request, code: str):
    attempt = get_object_or_404(Attempt, code=code)
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

    if not attempt.has_honesty_declaration:
        return redirect("assessment:attempt_details", code=code)

    if request.method == "POST":
        attempt.start()
        return redirect("assessment:attempt_question", code=code, n=1)

    return render(request, "assessment/instructions.html", {"attempt": attempt})


def attempt_submitted(request, code: str):
    attempt = get_object_or_404(Attempt, code=code)
    return render(request, "assessment/submitted.html", {"attempt": attempt})
