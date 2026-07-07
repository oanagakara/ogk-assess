"""
Tests for the per-learner extended-time accommodation mechanism
(Attempt.extended_time_multiplier).

Covers:
  - the model field default and persistence
  - every deadline-computation call site applying the multiplier consistently
    (_attempt_expires_at, _section_expires_at, _section_review_seconds,
    _projected_section_review_seconds, _template_total_duration /
    _expire_overdue_attempts)
  - the assessor-facing form (AttemptForm) that sets the multiplier before
    a learner starts
  - end-to-end: an accommodated learner is not force-expired at the
    unaccommodated deadline, and the countdown in question.html reflects
    the extended expiry timestamp.
"""
import json
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from assessment.forms import AttemptForm
from assessment.models import AssessmentTemplate, Attempt, Learner, Question, Section
from assessment.views._common import ASSESSMENT_DURATION, SECTION_DURATION, _template_total_duration, _expire_overdue_attempts
from assessment.views.learner import (
    _attempt_expires_at, _section_expires_at, _section_timedelta,
    _section_review_seconds, _projected_section_review_seconds,
)

User = get_user_model()


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def template(tenant):
    return AssessmentTemplate.objects.create(tenant=tenant, name="Accommodation Test Template", version="v1")


@pytest.fixture
def section(template):
    return Section.objects.create(template=template, title="Section 1 (60 MINUTES)", order=1)


@pytest.fixture
def question(section):
    return Question.objects.create(
        section=section, order=1, code="Q1",
        prompt="What is 2 + 2?", kind=Question.TEXT, max_marks=1,
        answer_key_json=json.dumps({"auto_mark": True, "answers": ["4"]}),
    )


@pytest.fixture
def learner(tenant):
    return Learner.objects.create(
        tenant=tenant, first_names="Palesa", surname="Dube", id_number="9203015001082",
    )


@pytest.fixture
def assessor():
    return User.objects.create_user(username="assessor_acc", password="pass1234", is_staff=True)


def bind_attempt(client, code):
    session = client.session
    session["learner_attempt_code"] = code
    session.save()


def start_attempt(attempt, when=None):
    when = when or timezone.now()
    attempt.honesty_name = f"{attempt.learner.first_names} {attempt.learner.surname}"
    attempt.honesty_accepted_at = when
    attempt.started_at = when
    attempt.last_activity_at = when
    attempt.save(update_fields=["honesty_name", "honesty_accepted_at", "started_at", "last_activity_at"])


# ── Model field ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_default_multiplier_is_one(template, learner):
    attempt = Attempt.objects.create(template=template, learner=learner)
    assert attempt.extended_time_multiplier == 1.0
    assert attempt.accommodation_notes == ""


@pytest.mark.django_db
def test_multiplier_persists(template, learner):
    attempt = Attempt.objects.create(
        template=template, learner=learner,
        extended_time_multiplier=1.5, accommodation_notes="Dyslexia — approved accommodation.",
    )
    attempt.refresh_from_db()
    assert attempt.extended_time_multiplier == 1.5
    assert attempt.accommodation_notes == "Dyslexia — approved accommodation."


# ── _attempt_expires_at ───────────────────────────────────────────────────────

@pytest.mark.django_db
def test_attempt_expires_at_no_accommodation(template, learner):
    attempt = Attempt.objects.create(template=template, learner=learner, extended_time_multiplier=1.0)
    now = timezone.now()
    start_attempt(attempt, when=now)
    expires = _attempt_expires_at(attempt)
    assert expires == now + ASSESSMENT_DURATION


@pytest.mark.django_db
def test_attempt_expires_at_with_1_5x_gives_three_hours(template, learner):
    """A learner with a 1.5x multiplier on a 2-hour assessment gets 3 hours, not 2."""
    attempt = Attempt.objects.create(template=template, learner=learner, extended_time_multiplier=1.5)
    now = timezone.now()
    start_attempt(attempt, when=now)
    expires = _attempt_expires_at(attempt)
    assert expires == now + timedelta(hours=3)


@pytest.mark.django_db
def test_attempt_expires_at_with_2x_gives_four_hours(template, learner):
    attempt = Attempt.objects.create(template=template, learner=learner, extended_time_multiplier=2.0)
    now = timezone.now()
    start_attempt(attempt, when=now)
    expires = _attempt_expires_at(attempt)
    assert expires == now + timedelta(hours=4)


@pytest.mark.django_db
def test_attempt_expires_at_none_when_not_started(template, learner):
    attempt = Attempt.objects.create(template=template, learner=learner, extended_time_multiplier=1.5)
    assert _attempt_expires_at(attempt) is None


# ── _section_timedelta / _section_expires_at ──────────────────────────────────

@pytest.mark.django_db
def test_section_timedelta_scales_with_multiplier(section):
    assert _section_timedelta(section.pk, 1.0) == timedelta(minutes=60)
    assert _section_timedelta(section.pk, 1.5) == timedelta(minutes=90)
    assert _section_timedelta(section.pk, 2.0) == timedelta(minutes=120)


@pytest.mark.django_db
def test_section_expires_at_uses_attempt_multiplier(template, learner, section):
    attempt = Attempt.objects.create(template=template, learner=learner, extended_time_multiplier=1.5)
    now = timezone.now()
    attempt.record_section_entry(section.pk, when=now)
    expires = _section_expires_at(attempt, section.pk)
    assert expires == now + timedelta(minutes=90)


# ── Review window (_section_review_seconds / _projected_section_review_seconds) ─

@pytest.mark.django_db
def test_section_review_seconds_scales_slot_with_multiplier(template, learner, section):
    """With 1.5x, the 60-min slot becomes 90 min, so 60 min of question time still
    leaves 30 min remaining — capped at the (also-scaled) review cap of 15 min."""
    attempt = Attempt.objects.create(template=template, learner=learner, extended_time_multiplier=1.5)
    base = timezone.now()
    attempt.record_section_entry(section.pk, when=base)
    attempt.start_section_review(section.pk, when=base + timedelta(minutes=60))
    # slot=90min, used=60min, remaining=30min=1800s; review cap = 600*1.5=900s -> capped at 900
    assert _section_review_seconds(attempt, section.pk) == 900


@pytest.mark.django_db
def test_section_review_seconds_unaccommodated_matches_existing_formula(template, learner, section):
    attempt = Attempt.objects.create(template=template, learner=learner, extended_time_multiplier=1.0)
    base = timezone.now()
    attempt.record_section_entry(section.pk, when=base)
    attempt.start_section_review(section.pk, when=base + timedelta(minutes=55))
    assert _section_review_seconds(attempt, section.pk) == 300


@pytest.mark.django_db
def test_projected_section_review_seconds_scales_with_multiplier(template, learner, section):
    attempt = Attempt.objects.create(template=template, learner=learner, extended_time_multiplier=2.0)
    now = timezone.now()
    attempt.record_section_entry(section.pk, when=now)
    # No time has passed yet, so remaining == full (scaled) slot, capped at scaled review cap.
    projected = _projected_section_review_seconds(attempt, section.pk)
    assert projected == 1200  # REVIEW_MAX_SECONDS(600) * 2.0


# ── _template_total_duration / _expire_overdue_attempts ──────────────────────

@pytest.mark.django_db
def test_template_total_duration_scales_with_multiplier(template, section):
    base_total = _template_total_duration(template, 1.0)
    scaled_total = _template_total_duration(template, 1.5)
    assert scaled_total == base_total * 1.5


@pytest.mark.django_db
def test_expire_overdue_attempts_respects_accommodation(template, learner, section):
    """An accommodated attempt started long enough ago to exceed the *unaccommodated*
    deadline, but still within its accommodated deadline, must NOT be force-expired."""
    from django.core.cache import cache
    cache.delete("_expire_ran")

    now = timezone.now()
    # Section is 60 min; unaccommodated total ~60min. Started 80 min ago:
    # - without accommodation this would be overdue (80 > 60)
    # - with a 1.5x multiplier the slot becomes 90 min, so it's still within time.
    started = now - timedelta(minutes=80)
    attempt = Attempt.objects.create(
        template=template, learner=learner,
        extended_time_multiplier=1.5,
        status=Attempt.IN_PROGRESS,
        started_at=started,
        last_activity_at=started,
    )

    _expire_overdue_attempts()

    attempt.refresh_from_db()
    assert attempt.status == Attempt.IN_PROGRESS
    assert attempt.timed_out is False


@pytest.mark.django_db
def test_expire_overdue_attempts_still_expires_unaccommodated(template, learner, section):
    from django.core.cache import cache
    cache.delete("_expire_ran")

    now = timezone.now()
    started = now - timedelta(minutes=80)
    attempt = Attempt.objects.create(
        template=template, learner=learner,
        extended_time_multiplier=1.0,
        status=Attempt.IN_PROGRESS,
        started_at=started,
        last_activity_at=started,
    )

    _expire_overdue_attempts()

    attempt.refresh_from_db()
    assert attempt.status == Attempt.SUBMITTED
    assert attempt.timed_out is True


# ── Assessor-facing form (AttemptForm) ────────────────────────────────────────

@pytest.mark.django_db
def test_attempt_form_defaults_to_no_accommodation(template):
    form = AttemptForm(data={"template": template.pk, "extended_time_multiplier": "1.0", "accommodation_notes": ""})
    assert form.is_valid(), form.errors
    attempt = form.save(commit=False)
    assert attempt.extended_time_multiplier == 1.0


@pytest.mark.django_db
def test_attempt_form_accepts_preset_multiplier(template):
    form = AttemptForm(data={
        "template": template.pk,
        "extended_time_multiplier": "1.5",
        "accommodation_notes": "Approved accommodation — extra time for reading difficulty.",
    })
    assert form.is_valid(), form.errors
    attempt = form.save(commit=False)
    assert attempt.extended_time_multiplier == 1.5
    assert "reading difficulty" in attempt.accommodation_notes


@pytest.mark.django_db
def test_attempt_form_rejects_arbitrary_multiplier_value(template):
    """Only the defined presets are accepted — arbitrary numeric values are rejected
    to keep the assessor UI to a small, auditable set of options."""
    form = AttemptForm(data={
        "template": template.pk,
        "extended_time_multiplier": "3.7",
        "accommodation_notes": "",
    })
    assert not form.is_valid()
    assert "extended_time_multiplier" in form.errors


# ── assessor_new_attempt view (end-to-end creation) ───────────────────────────

@pytest.mark.django_db
def test_assessor_new_attempt_creates_attempt_with_accommodation(client, assessor, template):
    client.force_login(assessor)
    url = reverse("assessment:assessor_new_attempt")
    response = client.post(url, {
        "template": template.pk,
        "extended_time_multiplier": "2.0",
        "accommodation_notes": "Double time — approved by external assessor.",
    })
    assert response.status_code == 200
    attempt = Attempt.objects.filter(template=template).order_by("-pk").first()
    assert attempt is not None
    assert attempt.extended_time_multiplier == 2.0
    assert "Double time" in attempt.accommodation_notes


@pytest.mark.django_db
def test_assessor_new_attempt_default_has_no_accommodation(client, assessor, template):
    client.force_login(assessor)
    url = reverse("assessment:assessor_new_attempt")
    response = client.post(url, {
        "template": template.pk,
        "extended_time_multiplier": "1.0",
        "accommodation_notes": "",
    })
    assert response.status_code == 200
    attempt = Attempt.objects.filter(template=template).order_by("-pk").first()
    assert attempt is not None
    assert attempt.extended_time_multiplier == 1.0


# ── End-to-end: learner-facing countdown reflects accommodation ──────────────

@pytest.mark.django_db
def test_accommodated_learner_not_expired_at_unaccommodated_deadline(client, template, learner, question):
    """A learner with 1.5x accommodation, 70 minutes into a 60-minute-total (~2hr
    for full assessments, but this template has one 60-min section so total duration
    is 60min) attempt, must still be able to answer — not force-redirected to submitted."""
    attempt = Attempt.objects.create(
        template=template, learner=learner, extended_time_multiplier=1.5,
        status=Attempt.IN_PROGRESS,
    )
    started = timezone.now() - timedelta(minutes=70)
    start_attempt(attempt, when=started)
    bind_attempt(client, attempt.code)

    url = reverse("assessment:attempt_question", kwargs={"code": attempt.code, "n": 1})
    response = client.get(url)

    # Not expired: 70 min < 60min * 1.5 = 90min accommodated deadline.
    assert response.status_code == 200
    assert b"countdown" in response.content


@pytest.mark.django_db
def test_unaccommodated_learner_is_expired_at_same_elapsed_time(client, template, learner, question):
    """Same section-clock start 70 minutes ago, but with the default 1.0 multiplier
    — this section should already be over its 60-minute allotment and redirect to
    the section review screen (section-level expiry, not overall-attempt expiry —
    the latter uses a fixed 2-hour constant unrelated to this test template's
    actual duration, see docs/accessibility_phase_b_blockers.md)."""
    attempt = Attempt.objects.create(
        template=template, learner=learner, extended_time_multiplier=1.0,
        status=Attempt.IN_PROGRESS,
    )
    started = timezone.now() - timedelta(minutes=70)
    start_attempt(attempt, when=started)
    # Simulate the section clock having started 70 minutes ago, as if this were
    # a return visit to a section entered well before the 60-min allotment.
    attempt.record_section_entry(question.section_id, when=started)
    bind_attempt(client, attempt.code)

    url = reverse("assessment:attempt_question", kwargs={"code": attempt.code, "n": 1})
    response = client.get(url)

    assert response.status_code == 302
    assert f"/attempt/{attempt.code}/review/{question.section_id}/" == response["Location"]


@pytest.mark.django_db
def test_question_page_countdown_reflects_accommodated_expiry(client, template, learner, question):
    """The rendered expires-at timestamp on the question page (which drives the
    JS countdown via data-expires-at) reflects the accommodated deadline, not the
    base one."""
    attempt = Attempt.objects.create(
        template=template, learner=learner, extended_time_multiplier=2.0,
        status=Attempt.IN_PROGRESS,
    )
    started = timezone.now()
    start_attempt(attempt, when=started)
    bind_attempt(client, attempt.code)

    url = reverse("assessment:attempt_question", kwargs={"code": attempt.code, "n": 1})
    response = client.get(url)
    assert response.status_code == 200

    # The GET request records the section clock start server-side, in a separate
    # DB round-trip from this test's in-memory `attempt` — refresh to see it.
    attempt.refresh_from_db()
    expected_section_expiry = _section_expires_at(attempt, question.section_id)
    assert expected_section_expiry is not None
    # Section is 60 minutes * 2.0 == 120 minutes from clock start. Compare with a
    # generous tolerance — `started` is captured before the request, and the
    # actual server-side clock start is recorded later during request handling,
    # after any request-time latency (e.g. a cold-starting Redis connection can
    # add several seconds in this environment). The tolerance only needs to be
    # tight enough to distinguish "~120 min" from "~60 min" (unaccommodated) —
    # a minute of slack is generous but still meaningful.
    delta = expected_section_expiry - started
    assert timedelta(minutes=119) <= delta <= timedelta(minutes=121)

    # The rendered page must carry that same timestamp for the countdown. The
    # template renders it via the `date:'c'` filter, which localizes to the
    # project's TIME_ZONE (Africa/Johannesburg) — convert before comparing, or
    # this compares a UTC string against a SAST-rendered one and never matches.
    iso_fragment = timezone.localtime(expected_section_expiry).isoformat()[:16]
    assert iso_fragment.encode() in response.content
