import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_assessor_mark_post_requires_csrf(
    csrf_client,
    assessor,
    submitted_attempt,
    markable_question,
):
    csrf_client.force_login(assessor)

    url = reverse(
        "assessment:assessor_mark_attempt",
        kwargs={"code": submitted_attempt.code},
    )
    response = csrf_client.post(
        f"{url}?q=1",
        {
            f"manual__{markable_question.pk}": "2",
            f"notes__{markable_question.pk}": "No CSRF token",
            "action": "save",

        },
    )

    assert response.status_code == 403


        
