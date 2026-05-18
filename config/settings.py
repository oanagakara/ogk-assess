from pathlib import Path
import os
import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
STATICFILES_DIRS = [BASE_DIR / "static"]

# ── Secret key ────────────────────────────────────────────────────────────────
# H-2: No fallback. If unset, startup fails loudly rather than running with a
# known key that makes all sessions and CSRF tokens forgeable.
_secret_key = os.environ.get("DJANGO_SECRET_KEY")
if not _secret_key:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY is not set. "
        "Generate one: python -c \"import secrets; print(secrets.token_urlsafe(50))\""
    )
SECRET_KEY = _secret_key

ACTIVE_TENANT_SLUG = os.environ.get("TENANT_SLUG", "default")

# H-3: Default False. Debug mode must be explicitly opted in for local dev.
DEBUG = os.environ.get("DEBUG", "False") == "True"

# H-1: Drive from env var — comma-separated hostnames in production.
_allowed_hosts_raw = os.environ.get("ALLOWED_HOSTS", "")
if not _allowed_hosts_raw and not DEBUG:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be set in production.")
ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts_raw.split(",") if h.strip()] or ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'assessment',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'assessment.security.LoginRateLimitMiddleware',
    'assessment.security.SecurityHeadersMiddleware',
    'assessment.middleware.ErrorHandlerMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            BASE_DIR / "templates" / "tenants" / ACTIVE_TENANT_SLUG,
            BASE_DIR / "templates" / "tenants" / "default",
            BASE_DIR / "templates",
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'assessment.context_processors.assessor_nav_counts',
                'assessment.context_processors.tenant_branding',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=600, ssl_require=True)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# ── Password validation ────────────────────────────────────────────────────────
# L-3: Minimum raised to 12 characters.
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 12},
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# ── Internationalisation ───────────────────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# ── Static files ──────────────────────────────────────────────────────────────
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# ── Auth ──────────────────────────────────────────────────────────────────────
LOGIN_REDIRECT_URL = "/assessor/"
LOGIN_URL = "/accounts/login/"

# ── Session config ────────────────────────────────────────────────────────────
# M-3: Exam context — no persistent logins. Expire at browser close; hard cap 4 h.
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 14400

# ── Upload limits ─────────────────────────────────────────────────────────────
# M-4: Prevent memory exhaustion from oversized working sheet uploads.
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024   # 10 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024   # 10 MB

# ── Security headers ──────────────────────────────────────────────────────────
SECURE_CONTENT_TYPE_NOSNIFF = True       # L-1: explicit (SecurityMiddleware default)
SECURE_REFERRER_POLICY = "same-origin"   # M-8: prevent attempt codes leaking via Referer

# ── Email (error notifications — activate once KAIgaba Google Workspace is live)
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get("NOTIFY_EMAIL_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("NOTIFY_EMAIL_PASSWORD", "")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "")
PASSWORD_RESET_TIMEOUT = 3600  # 1 hour

# ── Cookie security (explicit — do not rely on Django defaults) ────────────────
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"

# ── Logging ───────────────────────────────────────────────────────────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.request":  {"handlers": ["console"], "level": "ERROR",   "propagate": False},
        "assessment":      {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

# ── HTTPS / production hardening ──────────────────────────────────────────────
# M-1: Render terminates TLS at the edge — inform Django via the forwarded-proto header.
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
