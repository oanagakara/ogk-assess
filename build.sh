#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate

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
