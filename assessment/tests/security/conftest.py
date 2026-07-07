import pytest 
from django.contrib.auth import get_user_model 
from django.test import Client
from django.utils import timezone

from assessment.models import AssessmentTemplate, Attempt, Learner, Question, Section

User = get_user_model()

@pytest.fixture
def assessment_template(tenant):
    return AssessmentTemplate.objects.create(tenant=tenant, name="Assessment Template", version="v1")


@pytest.fixture
def section(assessment_template):
    return Section.objects.create(template=assessment_template, title="Section 1", order=1)

@pytest.fixture
def learner(tenant):
    return Learner.objects.create(
        tenant=tenant,
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


@pytest.fixture 
def normal_user():
    return User.objects.create_user(
        username="user1",
        password="pass1234",
        is_staff=False,
    )


@pytest.fixture 
def markable_question(section):
    return Question.objects.create(
        section=section,
        order=1,
        code="Q1",
        prompt="What is 2 + 2?",
        kind=Question.TEXT,
        max_marks=2,
    )


@pytest.fixture
def submitted_attempt(assessment_template, learner):
    return Attempt.objects.create(
        template=assessment_template,
        learner=learner,
        status=Attempt.SUBMITTED,
    )

@pytest.fixture
def in_progress_attempt(assessment_template,learner):
    now = timezone.now()
    return Attempt.objects.create(
        template=assessment_template,
        learner=learner,
        status=Attempt.IN_PROGRESS,
        honesty_accepted_at=now,
        started_at=now,
        last_activity_at=now,
    )


@pytest.fixture 
def csrf_client():
    return Client(enforce_csrf_checks=True)
