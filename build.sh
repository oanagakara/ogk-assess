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

# Placement diagnostic — prints to Render build log for investigation
uv run python manage.py shell -c "
from assessment.models import Attempt, Question
from assessment.nqf import (
    build_question_metadata, _accumulate_scores,
    compute_levels_from_prefix_scores, level_for_percentage,
    NQF_QUESTION_PCT_THRESHOLDS, NQF_PCT_THRESHOLDS,
)

CODE = '2TS77VCR'
try:
    a = Attempt.objects.select_related('learner', 'template').get(code=CODE)
except Attempt.DoesNotExist:
    print(f'[diag] Attempt {CODE} not found on this DB.')
else:
    print(f'[diag] === Attempt {CODE} ===')
    print(f'[diag] Learner: {a.learner}  Template: {a.template}  Status: {a.status}')
    qs = Question.objects.filter(section__template=a.template).select_related('section')
    q_meta = build_question_metadata(qs)
    responses = list(a.response_set.select_related('score', 'question').order_by('question__code'))
    prefix_scores, _, prefix_domain = _accumulate_scores(responses, q_meta)
    total_a = sum(v[0] for v in prefix_scores.values())
    total_m = sum(v[1] for v in prefix_scores.values())
    pct_overall = round(total_a / total_m * 100) if total_m else 0
    print(f'[diag] Overall: {total_a}/{total_m} = {pct_overall}%')
    print('[diag] Per-prefix:')
    for prefix, (awarded, maximum) in sorted(prefix_scores.items()):
        pct = round(awarded / maximum * 100) if maximum else 0
        th = NQF_QUESTION_PCT_THRESHOLDS.get(prefix, NQF_PCT_THRESHOLDS)
        level = level_for_percentage(pct, th)
        print(f'[diag]   {prefix}: {awarded}/{maximum} = {pct}% -> {level}')
    lit, num = compute_levels_from_prefix_scores(prefix_scores, prefix_domain)
    print(f'[diag] Computed: Literacy={lit}  Numeracy={num}')
    print('[diag] Response detail:')
    for r in responses:
        try:
            pts = r.score.points
        except AttributeError:
            pts = 'NO SCORE'
        print(f'[diag]   {r.question.code}: {pts}/{r.question.max_marks}')
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
