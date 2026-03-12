from django.utils import timezone
from datetime import timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import IntegrityError, transaction
from django.db.models import Count

from .forms import StartForm, LearnerForm, HonestyForm, TextResponseForm, MatchResponseForm
from .models import Attempt, Question, Response, AssessmentTemplate, Learner
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
    return user.is_authenticated and (user.is_staff or user.groups.filter(name="assessor").exists())

def _random_13_digit_id() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(13))

@login_required
@user_passes_test(is_assessor)
def assessor_dashboard(request):
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    active_cutoff = now - timedelta(minutes=30)

    total_attempts = Attempt.objects.count()
    submitted_today = Attempt.objects.filter(status=Attempt.SUBMITTED, submitted_at__gte=today_start).count()
    active_now = Attempt.objects.filter(status=Attempt.IN_PROGRESS, last_activity_at__gte=active_cutoff).count()

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
        Attempt.objects
        .select_related("learner", "template")
        .annotate(response_count=Count("response"))
        .order_by("-last_activity_at", "-started_at")
    )
    return render(request, "assessment/assessor_attempts.html", {"attempts": qs})

@login_required
@user_passes_test(is_assessor)
def assessor_mark_attempt(request):
    attempt = get_object_or_404(attempt, code=code)
    
    resp, _ = Response.objects.get_or_create(attempt=attempt, code=code)
    
    for r in resp:
        spec = {
            Question.objects.get(Question.spec_json, attempt=attempt, code=code)
        },
    
    
    

@login_required
@user_passes_test(is_assessor)
def assessor_new_attempt(request):
    # pick the latest template (simple default)
    template = AssessmentTemplate.objects.order_by("-created_at").first()
    if template is None:
        return render(request, "assessment/assessor_new_attempt.html", {"error": "No assessment template exists yet."})

    if request.method == "POST":
        # create placeholder learner; learner fills real details later
        learner = Learner.objects.create(first_names="Temp", surname="Learner", id_number=str(int(timezone.now().timestamp()))[:13])
        attempt = Attempt.objects.create(template=template, learner=learner)
        return render(request, "assessment/assessor_new_attempt.html", {"attempt": attempt, "template": template})

    return render(request, "assessment/assessor_new_attempt.html", {"template": template})

def attempt_question(request, code: str, n: int):
    attempt = get_object_or_404(Attempt, code=code)

    # must have accepted honesty first
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

    # -------------------------
    # Passage / layout logic
    # -------------------------
    THANDI_CODES = {"LIT-B-1", "LIT-B-2", "LIT-B-3", "LIT-B-4"}

    spec = {}
    if question.spec_json:
        try:
            spec = json.loads(question.spec_json)
        except Exception:
            spec = {}

    layout = spec.get("layout", "default")

    if layout == "info-only":
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
    passage = ""
    if question.code in THANDI_CODES or question.code == "LIT-B-READ":
        try:
            passage = Question.objects.get(code="LIT-B-READ").prompt
        except Question.DoesNotExist:
            passage = ""

    # -------------------------
    # Passage-only screen (no response)
    # -------------------------
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

    # -------------------------
    # Response + forms
    # -------------------------
    resp, _ = Response.objects.get_or_create(attempt=attempt, question=question)
    
    form_fill_values = {}

    if spec.get("layout") == "form_fill":
        if resp.response_json:
            try:
                form_fill_values = json_loads(resp.response_json) or {}
            except Exception:
                form_fill_values = {}
        for f in spec.get("fields", []):
            f["value"] = form_fill_values.get(f["name"], "")

    if question.kind == Question.MATCH:
        existing_json = resp.response_json or ""
        # make sure it isn't blank for JS parsing; blank is fine but "{}" is nicer
        if existing_json == "":
            existing_json = "{}"
        form = MatchResponseForm(
            request.POST or None,
            initial={"response_json": existing_json},
        )
    else:
        existing = ""
        if resp.response_json:
            try:
                existing = json.loads(resp.response_json).get("answer", "")
            except Exception:
                existing = resp.response_json
        form = TextResponseForm(
            request.POST or None,
            initial={"answer": existing},
        )
        
    if request.method == "POST":
        if spec.get("layout") == "form_fill":
            out = {}
            for f in spec.get("fields", []):
                out[f["name"]] = (request.POST.get(f"ff_{f['name']}", "") or "").strip()
            resp.response_json = json.dumps(out, ensure_ascii=False)
        else:
            # Save even if user skips (your instructions allow skipping)
            if question.kind == Question.MATCH:
                # Prefer cleaned_data if valid; otherwise store raw POST
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

            next_n = n + 1

            # After the passage-only screen, switch to split view for Thandi questions
            # If you want the split view for all Thandi questions, set spec.layout="passage_split"
            # in seed data for LIT-B-1..LIT-B-4.
            return redirect("assessment:attempt_question", code=code, n=next_n)

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

    # check for signed honesty
    if not attempt.honesty_accepted_at:
        return redirect("assessment:attempt_details", code=code)

    if request.method == "POST":
        attempt.status = Attempt.SUBMITTED
        attempt.submitted_at = timezone.now()
        attempt.last_activity_at = timezone.now()
        attempt.save(update_fields=["status", "submitted_at", "last_activity_at"])
        return redirect("assessment:attempt_submitted", code=code)

    #summary
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
            attempt.save(update_fields=["honesty_name", "honesty_accepted_at", "last_activity_at"])

            return redirect("assessment:attempt_instructions", code=code)
    else:
        learner_form = LearnerForm(instance=learner)
        honesty_form = HonestyForm(initial={"honesty_name": attempt.honesty_name})

    return render(
        request,
        "assessment/details.html",
        {"attempt": attempt, "learner_form": learner_form, "honesty_form": honesty_form},
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

