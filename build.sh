#!/usr/bin/env bash
set -o errexit

pip install uv
uv sync --frozen
uv run python manage.py collectstatic --no-input
uv run python manage.py migrate
uv run python manage.py shell -c "
from assessment.models import Question, AssessmentTemplate
from django.core.management import call_command
if not Question.objects.exists():
    call_command('loaddata', 'questions')
    print('questions fixture loaded.')
else:
    print('Questions already exist, skipping questions fixture.')
if not AssessmentTemplate.objects.filter(pk=5).exists():
    call_command('loaddata', 'lit_nqf_general')
    print('lit_nqf_general fixture loaded.')
else:
    print('Template pk=5 already exists, skipping lit_nqf_general fixture.')
"

# Ensure assessor, moderator, and auditor groups exist
uv run python manage.py shell -c "
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from assessment.models import AssessmentTemplate, Attempt, Learner, Question, Response, Score, Section, ExamSession

group, created = Group.objects.get_or_create(name='assessor')
if created:
    models = [AssessmentTemplate, Attempt, Learner, Question, Response, Score, Section, ExamSession]
    perms = []
    for model in models:
        ct = ContentType.objects.get_for_model(model)
        perms.extend(Permission.objects.filter(content_type=ct))
    group.permissions.set(perms)
    print(f'assessor group created with {len(perms)} permissions.')
else:
    print('assessor group already exists, skipping.')

for name in ['moderator', 'auditor']:
    _, created = Group.objects.get_or_create(name=name)
    print(f'{name} group {\"created\" if created else \"already exists\"}.')
"


# Seed simulation data if no attempts exist yet
uv run python manage.py shell -c "
from assessment.models import Attempt
if not Attempt.objects.exists():
    from django.core.management import call_command
    call_command('simulate_session')
    print('Simulation complete.')
else:
    print('Attempts already exist, skipping simulation.')
"

# Create or update superuser from env vars
uv run python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
import os
username = os.environ.get('DJANGO_SUPERUSER_USERNAME')
password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')
email = os.environ.get('DJANGO_SUPERUSER_EMAIL', '')
if username and password:
    user, created = User.objects.get_or_create(username=username)
    user.email = email
    user.is_staff = True
    user.is_superuser = True
    user.set_password(password)
    user.save()
    print(f'Superuser {username} {\"created\" if created else \"updated\"}.')
else:
    print('DJANGO_SUPERUSER_USERNAME or DJANGO_SUPERUSER_PASSWORD not set, skipping.')
"
