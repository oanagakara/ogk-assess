from pathlib import Path
from datetime import timedelta
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

# Path-based multi-tenant mode for the free-tier demo/prospect deployment.
# iCan's paid deployment leaves this unset — every code path it gates is a
# no-op there, falling back to the single-tenant ACTIVE_TENANT_SLUG behavior
# above. TENANT_SLUG itself is unused once this is on (tenant is resolved
# per-request from the URL path instead).
MULTI_TENANT_MODE = os.environ.get("MULTI_TENANT_MODE", "False") == "True"
DEFAULT_TENANT_SLUG = os.environ.get("DEFAULT_TENANT_SLUG", "demo")

# H-3: Default False. Debug mode must be explicitly opted in for local dev.
DEBUG = os.environ.get("DEBUG", "False") == "True"

# H-1: Drive from env var — comma-separated hostnames in production.
_allowed_hosts_raw = os.environ.get("ALLOWED_HOSTS", "")
if not _allowed_hosts_raw and not DEBUG:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be set in production.")
ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts_raw.split(",") if h.strip()] or ["localhost", "127.0.0.1"]

# Admin URL prefix — security-through-obscurity per deployment. Read here (not
# just inline in config/urls.py) since PathTenantMiddleware also needs to know
# this reserved prefix, to avoid the two drifting if the default ever changes.
ADMIN_URL_PREFIX = os.environ.get("ADMIN_URL_PREFIX", "_platform-admin")

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'axes',
    'assessment',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'axes.middleware.AxesMiddleware',
    'assessment.security.SecurityHeadersMiddleware',
    'assessment.tenant_middleware.PathTenantMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'assessment.security.LoginRateLimitMiddleware',
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
                'assessment.context_processors.csp_nonce',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    _local_hosts = {"localhost", "127.0.0.1", "::1"}
    _parsed_host = DATABASE_URL.split("@")[-1].split(":")[0].split("/")[0]
    _ssl_required = _parsed_host not in _local_hosts and not _parsed_host.startswith("192.168.")
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=600, ssl_require=_ssl_required)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# ── Cache (shared across Gunicorn workers — required for rate limiting) ────────
# Use Redis when REDIS_URL is set (production + any dev with a local Redis).
# Falls back to DatabaseCache so the app starts without Redis in bare local dev,
# but the fallback is slow — set REDIS_URL=redis://localhost:6379/0 in .env.
_REDIS_URL = os.environ.get("REDIS_URL", "")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": _REDIS_URL,
        # Free-tier Render Key Value instances sleep and take several seconds to
        # wake on first connection — the pool absorbs this once, not per-request.
        "OPTIONS": {
            "socket_connect_timeout": 15,
            "socket_timeout": 15,
            "retry_on_timeout": True,
        },
    } if _REDIS_URL else {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "cache_table",
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

# ── Authentication backends ───────────────────────────────────────────────────
AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# ── django-axes (brute-force protection) ──────────────────────────────────────
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]
AXES_RESET_ON_SUCCESS = True
AXES_IPWARE_META_PRECEDENCE_ORDER = [
    "HTTP_X_FORWARDED_FOR",
    "REMOTE_ADDR",
]

# ── Internationalisation ───────────────────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Johannesburg'
USE_I18N = True
USE_TZ = True

# ── Static files ──────────────────────────────────────────────────────────────
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
WHITENOISE_ALLOW_ALL_ORIGINS = False


def _whitenoise_add_corp_header(headers, path, url):
    headers["Cross-Origin-Resource-Policy"] = "same-origin"


WHITENOISE_ADD_HEADERS_FUNCTION = _whitenoise_add_corp_header

# ── Auth ──────────────────────────────────────────────────────────────────────
LOGIN_REDIRECT_URL = "/assessor/"
LOGIN_URL = "/accounts/login/"

# ── Session config ────────────────────────────────────────────────────────────
# M-3: Exam context — no persistent logins. Expire at browser close; hard cap 4 h.
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 14400

# ── Upload limits ─────────────────────────────────────────────────────────────
# M-4: Prevent memory exhaustion from oversized working sheet uploads.
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024   # 10 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024   # 10 MB

# ── Security headers ──────────────────────────────────────────────────────────
SECURE_CONTENT_TYPE_NOSNIFF = True       # L-1: explicit (SecurityMiddleware default)
SECURE_REFERRER_POLICY = "same-origin"   # M-8: prevent attempt codes leaking via Referer

# ── Email (error notifications)
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.zoho.com"
EMAIL_PORT = 465
EMAIL_USE_SSL = True
EMAIL_USE_TLS = False
EMAIL_HOST_USER = os.environ.get("NOTIFY_EMAIL_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("NOTIFY_EMAIL_PASSWORD", "")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "")
DEMO_VIDEO_URL = os.environ.get("DEMO_VIDEO_URL", "")
PASSWORD_RESET_TIMEOUT = 3600  # 1 hour

# ── Cookie security (explicit — do not rely on Django defaults) ────────────────
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_FAILURE_VIEW = "assessment.views.csrf_failure"

# ── Logging ───────────────────────────────────────────────────────────────────
_LOG_LEVEL = "DEBUG" if DEBUG else "INFO"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {funcName}: {message}",
            "style": "{",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "simple": {
            "format": "{levelname} {name}: {message}",
            "style": "{",
        },
        # Structured JSON — readable by any OTLP/Loki-based log pipeline
        "json": {
            "()": "config.log_formatters.JSONFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            # JSON in production; the OTel logging bridge (see otel.py) also
            # forwards these records to Grafana Cloud (Loki) via OTLP.
            # Readable "verbose" format in dev.
            "formatter": "verbose" if DEBUG else "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.db.backends": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "assessment": {
            "handlers": ["console"],
            "level": _LOG_LEVEL,
            "propagate": False,
        },
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
    # Build CSRF_TRUSTED_ORIGINS from ALLOWED_HOSTS so POST requests over HTTPS
    # are accepted without relying solely on the Referer header check.
    CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1")]
