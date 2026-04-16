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

    url = reverse("assessment:attempt_question", kwargs={"code": attempt.code, "n": 1})
    response = client.post(url, {"answer": "4", "next": "1"})

    attempt.refresh_from_db()

    # Finishing the last question now redirects to the review info screen (section timer
    # still has time on the clock). Submission happens after the learner chooses to
    # submit or finishes the review phase.
    assert response.status_code == 302
    assert response["Location"] == reverse("assessment:attempt_review_info", kwargs={"code": attempt.code})
    assert attempt.status == Attempt.IN_PROGRESS

    # Choosing "submit now" on the review info screen finalises the attempt.
    review_url = reverse("assessment:attempt_review_info", kwargs={"code": attempt.code})
    client.post(review_url, {"action": "submit"})
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

    client.force_login(assessor)

    url = reverse("assessment:assessor_mark_attempt", kwargs={"code": attempt.code})
    response = client.get(f"{url}?q=1")

    html = response.content.decode()

    assert response.status_code == 200
    assert "Save & Next" in html
    assert "Save & Done" not in html


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

    client.force_login(assessor)

    url = reverse("assessment:assessor_mark_attempt", kwargs={"code": attempt.code})
    response = client.get(f"{url}?q=1")

    html = response.content.decode()

    assert response.status_code == 200
    assert "Save & Done" in html
    assert "Save & Next" not in html


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
    assert response.url == reverse("assessment:assessor_attempts")
    assert score.points == 3
    assert score.max_points == 3
    assert score.assessor == assessor
