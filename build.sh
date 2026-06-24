#!/usr/bin/env bash
set -o errexit

pip install uv
uv sync --frozen
uv run pip-audit --strict
uv run python manage.py collectstatic --no-input
uv run python manage.py migrate
uv run python manage.py createcachetable
