import pytest
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from assessment.models import Attempt, Response


@pytest.mark.django_db
def test_expired_attempt_post_is_rejected_and_auto_submitted(
    client,
    assessment_template,
    learner,
    markable_question,
):
    started = timezone.now() - timedelta(hours=2, minutes=1)

    attempt = Attempt.objects.create(
        template=assessment_template,
        learner=learner,
        status=Attempt.IN_PROGRESS,
        honesty_accepted_at=started,
        started_at=started,
        last_activity_at=started,
    )

    url = reverse("assessment:attempt_question", kwargs={"code": attempt.code, "n": 1})
    response = client.post(url, {"answer": "4", "next": "1"})

    attempt.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse(
        "assessment:attempt_submitted",
        kwargs={"code": attempt.code},
    )
    assert attempt.status == Attempt.SUBMITTED
    assert attempt.submitted_at is not None
    assert Response.objects.filter(attempt=attempt, question=markable_question).count() == 0


@pytest.mark.django_db
def test_submitted_attempt_question_page_redirects_to_submitted(
    client,
    submitted_attempt,
    markable_question,
):
    url = reverse(
        "assessment:attempt_question",
        kwargs={"code": submitted_attempt.code, "n": 1},
    )
    response = client.get(url)

    assert response.status_code == 302
    assert response.url == reverse(
        "assessment:attempt_submitted",
        kwargs={"code": submitted_attempt.code},
    )
