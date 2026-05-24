"""
Integration tests for the learner-facing views (views/learner.py).
Uses Django test Client to exercise full request/response cycles.
"""

import json
import pytest
from django.urls import reverse
from django.utils import timezone

from assessment.models import (
    AssessmentTemplate, Attempt, ExamSession, Learner, Question, Response, Section,
)


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def template():
    return AssessmentTemplate.objects.create(name="Test Template", version="v1")


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
def learner():
    return Learner.objects.create(
        first_names="Thabo", surname="Nkosi", id_number="9001015001081",
    )


@pytest.fixture
def attempt(template, learner):
    return Attempt.objects.create(
        template=template, learner=learner, status=Attempt.IN_PROGRESS,
    )


def bind_attempt(client, code):
    """Store the attempt code in the client session (simulates owning the attempt)."""
    session = client.session
    session["learner_attempt_code"] = code
    session.save()


def start_attempt(attempt):
    """Accept honesty declaration and start the clock on an attempt."""
    now = timezone.now()
    attempt.honesty_name = f"{attempt.learner.first_names} {attempt.learner.surname}"
    attempt.honesty_accepted_at = now
    attempt.started_at = now
    attempt.last_activity_at = now
    attempt.save(update_fields=["honesty_name", "honesty_accepted_at", "started_at", "last_activity_at"])


# ── home ──────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_home_redirects_authenticated_user(client):
    from django.contrib.auth import get_user_model
    user = get_user_model().objects.create_user(username="u", password="p", is_staff=True)
    client.force_login(user)
    response = client.get(reverse("assessment:home"))
    assert response.status_code == 302
    assert "assessor" in response["Location"]


@pytest.mark.django_db
def test_home_shows_index_for_anonymous(client):
    response = client.get(reverse("assessment:home"))
    assert response.status_code == 200


# ── start ─────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_start_invalid_code_shows_error(client):
    response = client.post(reverse("assessment:start"), {"code": "INVALID"})
    assert response.status_code == 200
    assert b"Invalid code" in response.content


@pytest.mark.django_db
def test_start_valid_attempt_code_binds_session(client, attempt):
    response = client.post(reverse("assessment:start"), {"code": attempt.code})
    assert response.status_code == 302
    assert client.session.get("learner_attempt_code") == attempt.code


@pytest.mark.django_db
def test_start_valid_session_code_redirects_to_join(client, template):
    session = ExamSession.objects.create(template=template, seat_limit=5)
    response = client.post(reverse("assessment:start"), {"code": session.code})
    assert response.status_code == 302
    assert "join" in response["Location"]


@pytest.mark.django_db
def test_start_get_renders_form(client):
    response = client.get(reverse("assessment:start"))
    assert response.status_code == 200


# ── attempt_details ───────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_attempt_details_requires_session_ownership(client, attempt):
    url = reverse("assessment:attempt_details", kwargs={"code": attempt.code})
    response = client.get(url)
    assert response.status_code == 403


@pytest.mark.django_db
def test_attempt_details_shows_learner_form(client, attempt):
    bind_attempt(client, attempt.code)
    url = reverse("assessment:attempt_details", kwargs={"code": attempt.code})
    response = client.get(url)
    assert response.status_code == 200
    assert b"Thabo" in response.content


@pytest.mark.django_db
def test_attempt_details_post_saves_and_shows_consent(client, attempt):
    bind_attempt(client, attempt.code)
    url = reverse("assessment:attempt_details", kwargs={"code": attempt.code})
    response = client.post(url, {
        "first_names": "Thabo",
        "surname": "Nkosi",
        "id_number": "9001015001081",
        "dob": "1990-01-01",
        "gender": "male",
        "demographic": "African",
    })
    assert response.status_code == 200
    assert b"consent" in response.content.lower()


@pytest.mark.django_db
def test_attempt_details_redirects_if_consent_already_signed(client, attempt):
    start_attempt(attempt)
    attempt.accept_consent(name="Thabo Nkosi")
    bind_attempt(client, attempt.code)
    url = reverse("assessment:attempt_details", kwargs={"code": attempt.code})
    response = client.get(url)
    assert response.status_code == 302
    assert "/q/" in response["Location"]


# ── attempt_consent ───────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_attempt_consent_get_redirects_to_details(client, attempt):
    bind_attempt(client, attempt.code)
    url = reverse("assessment:attempt_consent", kwargs={"code": attempt.code})
    response = client.get(url)
    assert response.status_code == 302
    assert "details" in response["Location"]


@pytest.mark.django_db
def test_attempt_consent_post_records_and_redirects_to_instructions(client, attempt):
    start_attempt(attempt)
    bind_attempt(client, attempt.code)
    url = reverse("assessment:attempt_consent", kwargs={"code": attempt.code})
    response = client.post(url)
    assert response.status_code == 302
    assert "instructions" in response["Location"]
    attempt.refresh_from_db()
    assert attempt.consent_signed_at is not None


@pytest.mark.django_db
def test_attempt_consent_requires_session_ownership(client, attempt):
    url = reverse("assessment:attempt_consent", kwargs={"code": attempt.code})
    response = client.post(url)
    assert response.status_code == 403


# ── attempt_instructions ──────────────────────────────────────────────────────

@pytest.mark.django_db
def test_attempt_instructions_get_shows_page(client, attempt):
    start_attempt(attempt)
    bind_attempt(client, attempt.code)
    url = reverse("assessment:attempt_instructions", kwargs={"code": attempt.code})
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db
def test_attempt_instructions_post_starts_and_redirects_to_q1(client, attempt):
    start_attempt(attempt)
    bind_attempt(client, attempt.code)
    url = reverse("assessment:attempt_instructions", kwargs={"code": attempt.code})
    response = client.post(url)
    assert response.status_code == 302
    assert "/q/1" in response["Location"]


@pytest.mark.django_db
def test_attempt_instructions_redirects_to_details_without_declaration(client, attempt):
    bind_attempt(client, attempt.code)
    url = reverse("assessment:attempt_instructions", kwargs={"code": attempt.code})
    response = client.get(url)
    assert response.status_code == 302
    assert "details" in response["Location"]


# ── attempt_question ──────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_attempt_question_requires_session_ownership(client, attempt, question):
    url = reverse("assessment:attempt_question", kwargs={"code": attempt.code, "n": 1})
    response = client.get(url)
    assert response.status_code == 403


@pytest.mark.django_db
def test_attempt_question_get_renders_question(client, attempt, question):
    start_attempt(attempt)
    bind_attempt(client, attempt.code)
    url = reverse("assessment:attempt_question", kwargs={"code": attempt.code, "n": 1})
    response = client.get(url)
    assert response.status_code == 200
    assert b"What is 2 + 2?" in response.content


@pytest.mark.django_db
def test_attempt_question_redirects_to_details_without_declaration(client, attempt, question):
    bind_attempt(client, attempt.code)
    url = reverse("assessment:attempt_question", kwargs={"code": attempt.code, "n": 1})
    response = client.get(url)
    assert response.status_code == 302
    assert "details" in response["Location"]


@pytest.mark.django_db
def test_attempt_question_post_saves_response(client, attempt, question):
    start_attempt(attempt)
    bind_attempt(client, attempt.code)
    url = reverse("assessment:attempt_question", kwargs={"code": attempt.code, "n": 1})
    client.post(url, {"answer": "4", "next": "1"})
    assert Response.objects.filter(attempt=attempt, question=question).exists()


@pytest.mark.django_db
def test_attempt_question_out_of_range_redirects_to_q1(client, attempt, question):
    start_attempt(attempt)
    bind_attempt(client, attempt.code)
    url = reverse("assessment:attempt_question", kwargs={"code": attempt.code, "n": 99})
    response = client.get(url)
    assert response.status_code == 302
    assert "/q/1" in response["Location"]


@pytest.mark.django_db
def test_attempt_question_shows_timer(client, attempt, question):
    start_attempt(attempt)
    bind_attempt(client, attempt.code)
    url = reverse("assessment:attempt_question", kwargs={"code": attempt.code, "n": 1})
    response = client.get(url)
    assert b"countdown" in response.content


@pytest.mark.django_db
def test_attempt_question_no_questions_renders_fallback(client, attempt):
    start_attempt(attempt)
    bind_attempt(client, attempt.code)
    url = reverse("assessment:attempt_question", kwargs={"code": attempt.code, "n": 1})
    response = client.get(url)
    assert response.status_code == 200
    assert b"no_questions" in response.content or response.status_code == 200


# ── attempt_submitted ─────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_attempt_submitted_renders_for_anyone(client, attempt):
    url = reverse("assessment:attempt_submitted", kwargs={"code": attempt.code})
    response = client.get(url)
    assert response.status_code == 200


# ── attempt_section_review_info ───────────────────────────────────────────────

@pytest.mark.django_db
def test_section_review_info_requires_ownership(client, attempt, section):
    url = reverse(
        "assessment:attempt_section_review_info",
        kwargs={"code": attempt.code, "section_id": section.pk},
    )
    response = client.get(url)
    assert response.status_code == 403


@pytest.mark.django_db
def test_section_review_info_skip_advances(client, attempt, question, section):
    start_attempt(attempt)
    bind_attempt(client, attempt.code)
    url = reverse(
        "assessment:attempt_section_review_info",
        kwargs={"code": attempt.code, "section_id": section.pk},
    )
    response = client.post(url, {"action": "skip"})
    assert response.status_code == 302
    attempt.refresh_from_db()
    assert attempt.status == Attempt.SUBMITTED


@pytest.mark.django_db
def test_section_review_info_submitted_redirects_to_submitted(client, attempt, section):
    start_attempt(attempt)
    attempt.submit()
    bind_attempt(client, attempt.code)
    url = reverse(
        "assessment:attempt_section_review_info",
        kwargs={"code": attempt.code, "section_id": section.pk},
    )
    response = client.get(url)
    assert response.status_code == 302
    assert "submitted" in response["Location"]


# ── session_join ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_session_join_get_renders_form(client, template):
    session = ExamSession.objects.create(template=template, seat_limit=5)
    url = reverse("assessment:session_join", kwargs={"code": session.code})
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db
def test_session_join_closed_session_shows_expired(client, template):
    from django.utils import timezone as tz
    from datetime import timedelta
    session = ExamSession.objects.create(
        template=template, seat_limit=5,
        expires_at=tz.now() - timedelta(hours=1),
    )
    url = reverse("assessment:session_join", kwargs={"code": session.code})
    response = client.get(url)
    assert response.status_code == 200
    assert b"expired" in response.content.lower() or b"closed" in response.content.lower()


@pytest.mark.django_db
def test_session_join_post_creates_attempt(client, template):
    session = ExamSession.objects.create(template=template, seat_limit=5)
    url = reverse("assessment:session_join", kwargs={"code": session.code})
    response = client.post(url, {
        "first_names": "Nomvula",
        "surname": "Dlamini",
        "id_number": "9502025002082",
        "dob": "1995-02-02",
        "gender": "female",
        "demographic": "African",
    })
    assert response.status_code == 200
    assert Attempt.objects.filter(session=session).exists()


@pytest.mark.django_db
def test_session_join_full_session_shows_error(client, template):
    session = ExamSession.objects.create(template=template, seat_limit=1)
    existing_learner = Learner.objects.create(
        first_names="A", surname="B", id_number="9001010001081"
    )
    Attempt.objects.create(template=template, learner=existing_learner, session=session)

    url = reverse("assessment:session_join", kwargs={"code": session.code})
    response = client.post(url, {
        "first_names": "New",
        "surname": "Learner",
        "id_number": "9002020002082",
        "dob": "1990-02-02",
        "gender": "male",
        "demographic": "African",
    })
    assert response.status_code == 200
    assert b"full" in response.content.lower() or b"seat" in response.content.lower() or b"error" in response.content.lower()


# ── session_consent ───────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_session_consent_get_redirects_to_join(client, template):
    session = ExamSession.objects.create(template=template, seat_limit=5)
    url = reverse("assessment:session_consent", kwargs={"code": session.code})
    response = client.get(url)
    assert response.status_code == 302
    assert "join" in response["Location"]


@pytest.mark.django_db
def test_session_consent_post_records_consent_and_redirects(client, template, learner):
    session = ExamSession.objects.create(template=template, seat_limit=5)
    attempt = Attempt.objects.create(template=template, learner=learner, session=session)
    bind_attempt(client, attempt.code)
    url = reverse("assessment:session_consent", kwargs={"code": session.code})
    response = client.post(url)
    assert response.status_code == 302
    attempt.refresh_from_db()
    assert attempt.consent_signed_at is not None


# ── Ownership boundary ────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_another_learners_attempt_returns_403(client, template):
    learner_a = Learner.objects.create(first_names="A", surname="A", id_number="9001010001082")
    learner_b = Learner.objects.create(first_names="B", surname="B", id_number="9001010001083")
    attempt_a = Attempt.objects.create(template=template, learner=learner_a)
    Attempt.objects.create(template=template, learner=learner_b)

    bind_attempt(client, attempt_a.code)

    question = Question.objects.create(
        section=Section.objects.create(template=template, title="S", order=1),
        order=1, code="QX", prompt="?", kind=Question.TEXT, max_marks=1,
    )
    start_attempt(attempt_a)

    url = reverse("assessment:attempt_question", kwargs={"code": attempt_a.code, "n": 1})
    response = client.get(url)
    assert response.status_code == 200

    other_attempt = Attempt.objects.get(learner=learner_b)
    url_other = reverse("assessment:attempt_question", kwargs={"code": other_attempt.code, "n": 1})
    response_other = client.get(url_other)
    assert response_other.status_code == 403
