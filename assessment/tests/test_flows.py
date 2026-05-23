import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from assessment.models import (
    AssessmentTemplate,
    Attempt,
    Learner,
    Question,
    Response,
    Score,
    Section,
)

User = get_user_model()


@pytest.fixture
def assessment_template():
    return AssessmentTemplate.objects.create(name="Assessment Template", version="v1")


@pytest.fixture
def section(assessment_template):
    return Section.objects.create(template=assessment_template, title="Section 1", order=1)


@pytest.fixture
def learner():
    return Learner.objects.create(
        first_names="Nikki",
        surname="McMahon",
        id_number="7909150081084",
    )


@pytest.fixture
def assessor():
    return User.objects.create_user(
        username="assessor1",
        password="pass1234",
        is_staff=True,
    )


@pytest.mark.django_db
def test_learner_question_page_shows_timer(client, assessment_template, section, learner):
    Question.objects.create(
        section=section,
        order=1,
        code="Q1",
        prompt="What is 2 + 2?",
        kind=Question.TEXT,
        max_marks=1,
    )

    attempt = Attempt.objects.create(
        template=assessment_template,
        learner=learner,
        honesty_accepted_at=timezone.now(),
        started_at=timezone.now(),
        status=Attempt.IN_PROGRESS,
    )

    session = client.session
    session["learner_attempt_code"] = attempt.code
    session.save()

    url = reverse("assessment:attempt_question", kwargs={"code": attempt.code, "n": 1})
    response = client.get(url)

    assert response.status_code == 200
    assert b"countdown" in response.content


@pytest.mark.django_db
def test_final_question_submission_marks_attempt_submitted(
    client,
    assessment_template,
    section,
    learner,
):
    Question.objects.create(
        section=section,
        order=1,
        code="Q1",
        prompt="What is 2 + 2?",
        kind=Question.TEXT,
        max_marks=1,
    )

    attempt = Attempt.objects.create(
        template=assessment_template,
        learner=learner,
        honesty_accepted_at=timezone.now(),
        started_at=timezone.now(),
        status=Attempt.IN_PROGRESS,
    )

    session = client.session
    session["learner_attempt_code"] = attempt.code
    session.save()

    url = reverse("assessment:attempt_question", kwargs={"code": attempt.code, "n": 1})
    response = client.post(url, {"answer": "4", "next": "1"})

    attempt.refresh_from_db()

    # Finishing the last question of a section now redirects to that section's
    # review info screen. Submission happens after the learner skips the review
    # (or after their review timer expires).
    assert response.status_code == 302
    assert response["Location"] == reverse(
        "assessment:attempt_section_review_info",
        kwargs={"code": attempt.code, "section_id": section.pk},
    )
    assert attempt.status == Attempt.IN_PROGRESS

    # Choosing "Skip review and continue" on the section review info screen
    # advances the attempt; with no further sections, the attempt is finalised.
    review_url = reverse(
        "assessment:attempt_section_review_info",
        kwargs={"code": attempt.code, "section_id": section.pk},
    )
    client.post(review_url, {"action": "skip"})
    attempt.refresh_from_db()
    assert attempt.status == Attempt.SUBMITTED
    assert attempt.submitted_at is not None


@pytest.mark.django_db
def test_assessor_can_open_attempts_page(client, assessor):
    client.force_login(assessor)

    url = reverse("assessment:assessor_attempts")
    response = client.get(url)

    assert response.status_code == 200


@pytest.mark.django_db
def test_assessor_can_save_manual_mark(
    client,
    assessor,
    assessment_template,
    section,
    learner,
):
    question = Question.objects.create(
        section=section,
        order=1,
        code="Q1",
        prompt="What is 2 + 2?",
        kind=Question.TEXT,
        max_marks=2,
    )

    attempt = Attempt.objects.create(
        template=assessment_template,
        learner=learner,
        honesty_accepted_at=timezone.now(),
        started_at=timezone.now(),
        status=Attempt.SUBMITTED,
    )

    Response.objects.create(
        attempt=attempt,
        question=question,
        response_json=json.dumps({"answer": "4"}),
    )

    client.force_login(assessor)

    url = reverse("assessment:assessor_mark_attempt", kwargs={"code": attempt.code})
    response = client.post(
        f"{url}?q=1",
        {
            f"manual__{question.pk}": "2",
            f"notes__{question.pk}": "Correct answer",
            "action": "save",
        },
    )

    score = Score.objects.get(response__attempt=attempt, response__question=question)

    assert response.status_code == 302
    assert score.points == 2
    assert score.max_points == 2
    assert score.assessor == assessor


@pytest.mark.django_db
def test_non_final_mark_page_shows_save_and_next(
    client,
    assessor,
    assessment_template,
    section,
    learner,
):
    Question.objects.create(
        section=section,
        order=1,
        code="Q1",
        prompt="Question 1",
        kind=Question.TEXT,
        max_marks=1,
    )
    Question.objects.create(
        section=section,
        order=2,
        code="Q2",
        prompt="Question 2",
        kind=Question.TEXT,
        max_marks=1,
    )

    attempt = Attempt.objects.create(
        template=assessment_template,
        learner=learner,
        status=Attempt.SUBMITTED,
    )

    q1 = Question.objects.get(section=section, order=1)
    q2 = Question.objects.get(section=section, order=2)
    Response.objects.create(attempt=attempt, question=q1, response_json=json.dumps({"answer": "A1"}))
    Response.objects.create(attempt=attempt, question=q2, response_json=json.dumps({"answer": "A2"}))

    client.force_login(assessor)

    url = reverse("assessment:assessor_mark_attempt", kwargs={"code": attempt.code})
    response = client.get(f"{url}?q=1")

    html = response.content.decode()

    assert response.status_code == 200
    assert "Save & Next" in html
    assert "Save & Review Summary" not in html


@pytest.mark.django_db
def test_final_mark_page_shows_save_and_done(
    client,
    assessor,
    assessment_template,
    section,
    learner,
):
    Question.objects.create(
        section=section,
        order=1,
        code="Q1",
        prompt="Question 1",
        kind=Question.TEXT,
        max_marks=1,
    )

    attempt = Attempt.objects.create(
        template=assessment_template,
        learner=learner,
        status=Attempt.SUBMITTED,
    )

    q1 = Question.objects.get(section=section, order=1)
    Response.objects.create(attempt=attempt, question=q1, response_json=json.dumps({"answer": "A1"}))

    client.force_login(assessor)

    url = reverse("assessment:assessor_mark_attempt", kwargs={"code": attempt.code})
    response = client.get(f"{url}?q=1")

    html = response.content.decode()

    assert response.status_code == 200
    assert "Save & Review Summary" in html
    assert "Save & Next" not in html


@pytest.mark.django_db
def test_section_review_seconds_formula(assessment_template, section, learner):
    """min(10min, 60min − question_time) for both sections, computed from real timestamps."""
    from datetime import timedelta
    from assessment.views import _section_review_seconds

    attempt = Attempt.objects.create(
        template=assessment_template,
        learner=learner,
        honesty_accepted_at=timezone.now(),
        started_at=timezone.now(),
        status=Attempt.IN_PROGRESS,
    )

    base = timezone.now()

    # 30 minutes on questions → 10 min review (capped)
    attempt.record_section_entry(section.pk, when=base)
    attempt.start_section_review(section.pk, when=base + timedelta(minutes=30))
    assert _section_review_seconds(attempt, section.pk) == 600

    # Reset and try 55 min on questions → 5 min review
    attempt.section_timings_json = {}
    attempt.section_review_started_at = {}
    attempt.save(update_fields=["section_timings_json", "section_review_started_at"])
    attempt.record_section_entry(section.pk, when=base)
    attempt.start_section_review(section.pk, when=base + timedelta(minutes=55))
    assert _section_review_seconds(attempt, section.pk) == 300

    # Reset and try full 60 min on questions → 0 review
    attempt.section_timings_json = {}
    attempt.section_review_started_at = {}
    attempt.save(update_fields=["section_timings_json", "section_review_started_at"])
    attempt.record_section_entry(section.pk, when=base)
    attempt.start_section_review(section.pk, when=base + timedelta(minutes=60))
    assert _section_review_seconds(attempt, section.pk) == 0


@pytest.mark.django_db
def test_save_done_on_final_question_persists_score_and_redirects_to_attempts(
    client,
    assessor,
    assessment_template,
    section,
    learner,
):
    question = Question.objects.create(
        section=section,
        order=1,
        code="Q1",
        prompt="Final question",
        kind=Question.TEXT,
        max_marks=3,
    )

    attempt = Attempt.objects.create(
        template=assessment_template,
        learner=learner,
        status=Attempt.SUBMITTED,
    )

    Response.objects.create(
        attempt=attempt,
        question=question,
        response_json=json.dumps({"answer": "Final learner answer"}),
    )

    client.force_login(assessor)

    url = reverse("assessment:assessor_mark_attempt", kwargs={"code": attempt.code})
    response = client.post(
        f"{url}?q=1",
        {
            f"manual__{question.pk}": "3",
            f"notes__{question.pk}": "Done marking",
            "action": "done",
        },
    )

    score = Score.objects.get(response__attempt=attempt, response__question=question)

    assert response.status_code == 302
    assert response.url == reverse("assessment:assessor_review_queue")
    assert score.points == 3
    assert score.max_points == 3
    assert score.assessor == assessor
