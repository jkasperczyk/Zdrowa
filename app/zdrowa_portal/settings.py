from pathlib import Path
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = os.environ.get("ZDROWA_ENV", str(BASE_DIR.parent / "config" / ".env"))
if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)

def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        raise RuntimeError(f"Missing env var: {name}")
    return v

SECRET_KEY = env("DJANGO_SECRET_KEY", "CHANGE_ME")
DEBUG = env("DJANGO_DEBUG", "0") == "1"

ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "zdrowa.pracunia.pl").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "https://zdrowa.pracunia.pl").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "portal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Must be after auth middleware
    "portal.middleware.ForcePasswordChangeMiddleware",
]

ROOT_URLCONF = "zdrowa_portal.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [str(BASE_DIR / "portal" / "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "portal.context_processors.push_context",
            ],
        },
    },
]

WSGI_APPLICATION = "zdrowa_portal.wsgi.application"

DB_PATH = env("ZDROWA_DB_PATH", str(BASE_DIR.parent / "data" / "db.sqlite3"))
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": DB_PATH}}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "portal.validators.HasNumberValidator"},
]

LANGUAGE_CODE = "pl"
TIME_ZONE = env("ZDROWA_TZ", "Europe/Warsaw")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = str(BASE_DIR / "staticfiles")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env("DJANGO_HSTS_INCLUDE_SUBDOMAINS", "0") == "1"
SECURE_HSTS_PRELOAD = env("DJANGO_HSTS_PRELOAD", "0") == "1"

SESSION_COOKIE_SECURE = env("DJANGO_SESSION_COOKIE_SECURE", "1") == "1"
CSRF_COOKIE_SECURE = env("DJANGO_CSRF_COOKIE_SECURE", "1") == "1"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 604800           # 7 days
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_SAVE_EVERY_REQUEST = True     # extend session on each request
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Integrations (read-only)
WEATHERGUARD_DB = env("WG_FEEDBACK_DB", "/opt/weatherguard/data/feedback.db")
WEATHERGUARD_TRENDS_DIR = env("WG_TRENDS_DIR", "/opt/weatherguard/public_media/trends")

FIELD_ENCRYPTION_KEY = env("ZDROWA_FIELD_ENCRYPTION_KEY", "")

# Email — defaults to SMTP; set EMAIL_BACKEND=dummy in .env to disable
_email_backend = env("EMAIL_BACKEND", "smtp").lower().strip()
if _email_backend == "dummy":
    EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

EMAIL_HOST          = os.environ.get("SMTP_HOST")  or env("EMAIL_HOST",          "email-smtp.eu-central-1.amazonaws.com")
EMAIL_PORT          = int(os.environ.get("SMTP_PORT") or env("EMAIL_PORT", "587"))
EMAIL_USE_TLS       = True
EMAIL_HOST_USER     = os.environ.get("SMTP_USER")  or env("EMAIL_HOST_USER",     "AKIA3HV232OII3IOPIDU")
EMAIL_HOST_PASSWORD = os.environ.get("SMTP_PASS")  or env("EMAIL_HOST_PASSWORD", "BIhGLVF0DvSLBOIhvFDqzyLMAT9c/ZN8EBHHCwjRolYz")
DEFAULT_FROM_EMAIL  = os.environ.get("MAIL_FROM")  or env("DEFAULT_FROM_EMAIL",  "Health Guard <no-reply@pracunia.pl>")

WEATHERGUARD_LOG = env("WG_LOG_FILE", "/opt/weatherguard/logs/weatherguard.log")
WEATHERGUARD_BASE_DIR = env("WG_BASE_DIR", "/opt/weatherguard")

# Web Push (VAPID) — generate once with: python manage.py generate_vapid_keys
VAPID_PRIVATE_KEY = env("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY  = env("VAPID_PUBLIC_KEY", "")
VAPID_SUBJECT     = env("VAPID_SUBJECT", "mailto:admin@zdrowa.pracunia.pl")

# OpenAI for weekly health reports
OPENAI_API_KEY = env("OPENAI_API_KEY", "")
OPENAI_MODEL   = env("OPENAI_MODEL", "gpt-4o-mini")

REGISTRATION_OPEN = True

ADMIN_NOTIFY_EMAIL = env("ADMIN_NOTIFY_EMAIL", "jacek.kasperczyk@gmail.com")

# Email verification / password reset token lifetime (24 hours)
PASSWORD_RESET_TIMEOUT = 86400
