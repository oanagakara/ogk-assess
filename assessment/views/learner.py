"""Learner-facing views: portal, attempt flow, section review, session entry."""
import logging
import random
import uuid
from datetime import timedelta

from opentelemetry import trace

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer(__name__)

from django.conf import settings
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..auto_mark import auto_mark_attempt
from ..forms import LearnerForm, StartForm
from ..models import (
    Attempt, DemoRequest, ExamSession, Learner, Question, Response, Section,
)
from ..nqf import section_kind as _nqf_section_kind
from ..renderers import get_renderer
from ..services import claim_seat, claim_session_seat

from ._common import (
    ASSESSMENT_DURATION, SECTION_DURATION, REVIEW_MAX_SECONDS,
    _extract_inline_choices, _is_layout_only_question, _question_spec,
    _safe_json_loads,
)


# ── Session ownership ─────────────────────────────────────────────────────────

_LEARNER_SESSION_KEY = "learner_attempt_code"
_PASSAGE_CODES = frozenset({"LIT-B-1", "LIT-B-2", "LIT-B-3", "LIT-B-4", "LIT-B-READ"})


def _bind_attempt_to_session(request, code: str) -> None:
    request.session[_LEARNER_SESSION_KEY] = code
    request.session.modified = True


def _owns_attempt(request, code: str) -> bool:
    return request.session.get(_LEARNER_SESSION_KEY) == code


# ── Section timing ────────────────────────────────────────────────────────────

def _section_timedelta(section_pk: int) -> timedelta:
    """Return the allotted duration for a section, parsed from its title.

    Section titles embed the slot in the form '(45 MINUTES)' or '(60 MINUTES)'.
    Falls back to SECTION_DURATION (60 min) when no match is found.
    """
    import re as _re
    title = Section.objects.filter(pk=section_pk).values_list("title", flat=True).first() or ""
    m = _re.search(r'\((\d+)\s+MINUTES?\)', title, _re.IGNORECASE)
    return timedelta(minutes=int(m.group(1))) if m else SECTION_DURATION


def _clock_start_for_section(attempt, section_pk: int):
    """Return the clock-start time for the timer group this section belongs to.

    Sections that share a subject domain (all literacy or all numeracy) within
    the same template share one clock — the start time of the first-entered
    section in that group. This prevents the timer resetting when moving from
    Section 1 to Section 2 of the same subject.

    Sections with domain 'other', or templates where sections belong to
    different domains, each get their own independent clock.
    """
    section = Section.objects.filter(pk=section_pk).select_related("template").first()
    if not section:
        return attempt.section_started_at(section_pk)

    kind = _nqf_section_kind(section.title)
    if kind == "other":
        return attempt.section_started_at(section_pk)

    same_kind_pks = [
        s.pk for s in Section.objects.filter(template=section.template).order_by("order")
        if _nqf_section_kind(s.title) == kind
    ]

    earliest = None
    for pk in same_kind_pks:
        t = attempt.section_started_at(pk)
        if t is not None and (earliest is None or t < earliest):
            earliest = t

    return earliest or attempt.section_started_at(section_pk)


def _attempt_expires_at(attempt):
    if not attempt.started_at:
        return None
    return attempt.started_at + ASSESSMENT_DURATION


def _section_expires_at(attempt, section_pk):
    started = _clock_start_for_section(attempt, section_pk)
    if not started:
        return None
    return started + _section_timedelta(section_pk)


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
    """Return the review window in seconds: min(15min, slot − time_used_since_clock_start).

    For shared-clock groups (e.g. two literacy sections), time_used counts from
    the first section's start so review time cannot be gamed by skipping sections.
    """
    clock_start = _clock_start_for_section(attempt, section_pk)
    review_started = attempt.get_section_review_started_at(section_pk)
    if not (clock_start and review_started):
        return 0
    question_seconds = max(0, int((review_started - clock_start).total_seconds()))
    slot_seconds = int(_section_timedelta(section_pk).total_seconds())
    remaining = slot_seconds - question_seconds
    return max(0, min(REVIEW_MAX_SECONDS, remaining))


def _section_review_expires_at(attempt, section_pk: int):
    review_started = attempt.get_section_review_started_at(section_pk)
    if not review_started:
        return None
    return review_started + timedelta(seconds=_section_review_seconds(attempt, section_pk))


def _projected_section_review_seconds(attempt, section_pk: int) -> int:
    """Projected review window if review were started right now (for the review_info preview)."""
    clock_start = _clock_start_for_section(attempt, section_pk)
    if not clock_start:
        return 0
    question_seconds = max(0, int((timezone.now() - clock_start).total_seconds()))
    slot_seconds = int(_section_timedelta(section_pk).total_seconds())
    remaining = slot_seconds - question_seconds
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


# ── Attempt lifecycle ─────────────────────────────────────────────────────────

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


def _advance_after_section(attempt, section_pk: int):
    """Move on after a section's review screen is dismissed: next section's first question, or finalise."""
    next_n = _next_section_first_n(attempt.template, section_pk)
    if next_n:
        return redirect("assessment:attempt_question", code=attempt.code, n=next_n)
    _finalize_attempt(attempt)
    return redirect("assessment:attempt_submitted", code=attempt.code)


# ── Question rendering helpers ────────────────────────────────────────────────

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
    # Seed-shuffle MCQ choices (>2 options) so order is stable per attempt but
    # varies across attempts, defeating answer-pattern copying between learners.
    if isinstance(spec.get("choices"), list) and len(spec["choices"]) > 2:
        rng = random.Random(f"{attempt.code}:{question.pk}")
        spec["choices"] = list(spec["choices"])
        rng.shuffle(spec["choices"])
    response, _ = Response.objects.get_or_create(attempt=attempt, question=question)
    renderer = get_renderer(question, spec, response, attempt=attempt)
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


# ── Portal ────────────────────────────────────────────────────────────────────

def home(request):
    if request.user.is_authenticated:
        return redirect("assessment:assessor_dashboard")
    return render(request, "index.html")


@require_POST
def request_demo(request):
    from django.core.mail import send_mail
    name  = request.POST.get("name", "").strip()
    email = request.POST.get("email", "").strip()
    org   = request.POST.get("org", "").strip()
    if not name or not email:
        return JsonResponse({"ok": False, "error": "Name and email are required."}, status=400)
    DemoRequest.objects.create(name=name, email=email, org=org)
    notify = getattr(settings, "NOTIFY_EMAIL", "") or settings.EMAIL_HOST_USER
    if notify:
        try:
            send_mail(
                subject=f"Demo request: {name} — {org or 'no org'}",
                message=f"Name: {name}\nEmail: {email}\nOrganisation: {org or '—'}\n",
                from_email=settings.EMAIL_HOST_USER,
                recipient_list=[notify],
                fail_silently=True,
            )
        except Exception:
            pass
    return JsonResponse({"ok": True})


def start(request):
    form = StartForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        code = form.cleaned_data["code"].strip().upper()

        with _tracer.start_as_current_span("learner.start") as span:
            span.set_attribute("assessment.code", code)
            span.set_attribute("http.client_ip", request.META.get("REMOTE_ADDR", "-"))

            attempt = Attempt.objects.filter(code=code).first()
            if attempt is not None:
                span.set_attribute("assessment.entry_type", "attempt")
                ok, msg = claim_seat(attempt)
                if ok:
                    _bind_attempt_to_session(request, attempt.code)
                    logger.info("Learner claimed seat: code=%s ip=%s", code, request.META.get("REMOTE_ADDR", "-"))
                    return redirect("assessment:attempt_details", code=attempt.code)
                span.set_attribute("assessment.seat_claim_failure", msg)
                logger.warning("Seat claim failed: code=%s reason=%r ip=%s", code, msg, request.META.get("REMOTE_ADDR", "-"))
                form.add_error("code", msg)
                return render(request, "assessment/start.html", {"form": form})

            session = ExamSession.objects.filter(code=code).select_related("template").first()
            if session is not None:
                span.set_attribute("assessment.entry_type", "session")
                logger.info("Learner joining session: code=%s ip=%s", code, request.META.get("REMOTE_ADDR", "-"))
                return redirect("assessment:session_join", code=session.code)

            span.set_attribute("assessment.entry_type", "invalid")
            logger.warning("Invalid code entered: code=%r ip=%s", code, request.META.get("REMOTE_ADDR", "-"))
            form.add_error("code", "Invalid code. Please check and try again.")

    return render(request, "assessment/start.html", {"form": form})


# ── Attempt flow ──────────────────────────────────────────────────────────────

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

    qs_section_ids = list(qs.values_list("pk", "section_id"))
    section_pk = qs_section_ids[n - 1][1]

    attempt.record_section_entry(section_pk)

    now = timezone.now()
    sec_expires = _section_expires_at(attempt, section_pk)

    if sec_expires and now >= sec_expires:
        return redirect("assessment:attempt_section_review_info", code=code, section_id=section_pk)

    section_timeout_url = reverse(
        "assessment:attempt_section_review_info",
        kwargs={"code": code, "section_id": section_pk},
    )

    end_redirect = None
    is_last_q_in_section = (n == total) or (qs_section_ids[n][1] != section_pk)
    if is_last_q_in_section:
        end_redirect = redirect(
            "assessment:attempt_section_review_info", code=code, section_id=section_pk
        )

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
        with _tracer.start_as_current_span("learner.attempt_submit") as span:
            span.set_attribute("assessment.attempt_code", code)
            answered = Response.objects.filter(attempt=attempt).exclude(response_json="").count()
            span.set_attribute("assessment.answers_submitted", answered)
            _finalize_attempt(attempt)
            logger.info("Attempt submitted: code=%s answered=%d", code, answered)
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

    if attempt.consent_signed_at:
        return redirect("assessment:attempt_question", code=code, n=1)

    learner = attempt.learner
    details_key = f"attempt_details_done_{code}"

    if request.session.get(details_key) and not attempt.has_honesty_declaration:
        return render(request, "assessment/details.html", {
            "attempt": attempt,
            "show_consent": True,
            "learner_name": f"{learner.first_names} {learner.surname}".strip(),
        })

    learner_form = LearnerForm(request.POST or None, instance=learner)
    if request.method == "POST" and learner_form.is_valid():
        learner_form.save()
        request.session[details_key] = True
        return render(request, "assessment/details.html", {
            "attempt": attempt,
            "show_consent": True,
            "learner_name": f"{learner.first_names} {learner.surname}".strip(),
        })

    return render(request, "assessment/details.html", {
        "attempt": attempt,
        "learner_form": learner_form,
    })


def attempt_consent(request, code: str):
    if request.method != "POST":
        return redirect("assessment:attempt_details", code=code)
    attempt = get_object_or_404(Attempt.objects.select_related("learner"), code=code)
    if not _owns_attempt(request, code):
        return HttpResponseForbidden()
    if attempt.consent_signed_at:
        return redirect("assessment:attempt_question", code=code, n=1)
    full_name = f"{attempt.learner.first_names} {attempt.learner.surname}".strip()
    attempt.accept_consent(name=full_name)
    logger.info("Consent signed: code=%s learner=%r", code, full_name)
    return redirect("assessment:attempt_instructions", code=code)


def attempt_instructions(request, code: str):
    attempt = get_object_or_404(Attempt, code=code)
    if not _owns_attempt(request, code):
        return HttpResponseForbidden()

    if not attempt.has_honesty_declaration:
        return redirect("assessment:attempt_details", code=code)

    if request.method == "POST":
        attempt.start()
        logger.info("Attempt started: code=%s", code)
        return redirect("assessment:attempt_question", code=code, n=1)

    return render(request, "assessment/instructions.html", {"attempt": attempt})


def attempt_submitted(request, code: str):
    attempt = get_object_or_404(Attempt, code=code)
    return render(request, "assessment/submitted.html", {"attempt": attempt})


# ── Session entry ─────────────────────────────────────────────────────────────

def session_join(request, code: str):
    """Step 1 of session entry: learner particulars. On success renders the consent modal."""
    session = get_object_or_404(
        ExamSession.objects.select_related("template"),
        code=code,
    )

    if not session.is_open:
        return render(request, "assessment/session_expired.html", {"session": session})

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
    attempt.accept_consent(name=full_name)
    return redirect("assessment:attempt_instructions", code=attempt.code)


# ── Section review ────────────────────────────────────────────────────────────

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

    if projected_review_seconds == 0 or not section_questions:
        return _advance_after_section(attempt, section.pk)

    all_sections = Section.objects.filter(template=attempt.template)
    total_all_questions = sum(
        len(_section_questions(attempt.template, s.pk)) for s in all_sections
    )

    return render(request, "assessment/review_info.html", {
        "attempt": attempt,
        "section": section,
        "total_questions": total_all_questions,
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
