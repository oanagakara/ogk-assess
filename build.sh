#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
python manage.py shell -c "
from assessment.models import Question
if not Question.objects.exists():
    from django.core.management import call_command
    call_command('loaddata', 'questions')
    print('Questions loaded.')
else:
    print('Questions already exist — skipping loaddata.')
"

# Create or update superuser from env vars
python manage.py shell -c "
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
    print('DJANGO_SUPERUSER_USERNAME or DJANGO_SUPERUSER_PASSWORD not set — skipping.')
"
