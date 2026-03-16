import json

import pytest
from django.urls import reverse

from assessment.models import Response


@pytest.mark.django_db
def test_learner_response_is_escaped_on_assessor_mark_page(
    client,
    assessor,
    submitted_attempt,
    markable_question,
):
    malicious = "<script>alert(1)</script>"

    Response.objects.create(
        attempt=submitted_attempt,
        question=markable_question,
        response_json=json.dumps({"answer": malicious}),
    )

    client.force_login(assessor)

    url = reverse(
        "assessment:assessor_mark_attempt",
        kwargs={"code": submitted_attempt.code},
    )
    response = client.get(f"{url}?q=1")

    html = response.content.decode()

    assert response.status_code == 200
    assert malicious not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
