import pytest 
from django.conf import settings
from django.urls import reverse 

from assessment.models import Score


@pytest.mark.django_db
def test_anonymous_cannot_access_assessor_attempts(client):
    url = reverse("assessment:assessor_attempts")
    response = client.get(url)

    assert response.status_code == 302
    assert settings.LOGIN_URL in response.url


@pytest.mark.django_db
def test_non_assessor_cannot_access_assessor_attempts(client, normal_user):
    client.force_login(normal_user)

    url = reverse("assessment:assessor_attempts")
    response = client.get(url)

    assert response.status_code == 302
    assert settings.LOGIN_URL in response.url


@pytest.mark.django_db
def test_anonymous_cannot_access_assessor_mark_attempt(
    client,
    submitted_attempt,
    markable_question,
):
    url = reverse("assessment:assessor_mark_attempt",
        kwargs={"code": submitted_attempt.code},
    )
    response = client.get(f"{url}?q=1")

    assert response.status_code == 302
    assert settings.LOGIN_URL in response.url 


@pytest.mark.django_db
def test_non_assessor_cannot_post_marks(
    client,
    normal_user,
    submitted_attempt,
    markable_question,
):
    client.force_login(normal_user)

    url = reverse(
        "assessment:assessor_mark_attempt",
        kwargs={"code": submitted_attempt.code},
    )
    response = client.post(
        f"{url}?q=1",
        {
            f"manual__{markable_question.pk}": "2",
            f"notes__{markable_question.pk}": "Trying to mark",
            "action": "save",
        },
    )

    assert response.status_code == 302
    assert settings.LOGIN_URL in response.url
    assert Score.objects.count() == 0
