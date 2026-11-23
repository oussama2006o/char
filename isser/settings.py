import os
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "local-development-only" if DEBUG else "")
if not SECRET_KEY:
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY.")
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
CSRF_TRUSTED_ORIGINS = os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split()
ROOT_URLCONF = "isser.urls"
WSGI_APPLICATION = "isser.wsgi.application"

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "booking",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "booking.copy.LanguageMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
if not DEBUG:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "booking.copy.context",
    ]},
}]

database_url = os.getenv("DATABASE_URL")
if database_url:
    database = urlsplit(database_url)
    if database.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured("DATABASE_URL must refer to PostgreSQL.")
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(database.path[1:]),
        "USER": unquote(database.username or ""),
        "PASSWORD": unquote(database.password or ""),
        "HOST": database.hostname,
        "PORT": database.port or 5432,
        "OPTIONS": dict(parse_qsl(database.query)),
    }}
elif DEBUG:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.getenv("BOOKING_SQLITE_PATH", BASE_DIR / "db.sqlite3"),
        "OPTIONS": {"transaction_mode": "IMMEDIATE", "timeout": 20},
    }}
else:
    raise ImproperlyConfigured("Set DATABASE_URL for production.")

LANGUAGE_CODE = "fr"
TIME_ZONE = "Africa/Algiers"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
LOGIN_URL = "staff_login"
LOGIN_REDIRECT_URL = "report"
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31_536_000 if not DEBUG else 0
if os.getenv("DJANGO_TRUST_PROXY") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": f"django.contrib.auth.password_validation.{validator}"}
    for validator in [
        "UserAttributeSimilarityValidator", "MinimumLengthValidator",
        "CommonPasswordValidator", "NumericPasswordValidator",
    ]
]

PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
MESSAGING_BACKEND = os.getenv("MESSAGING_BACKEND", "console" if DEBUG else "live")
if MESSAGING_BACKEND not in {"live", "console"} or (not DEBUG and MESSAGING_BACKEND != "live"):
    raise ImproperlyConfigured("Production requires the live messaging backend.")
DEMO_SMS_CODE = os.getenv("DEMO_SMS_CODE") if DEBUG else None
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_VERIFY_SERVICE = os.getenv("TWILIO_VERIFY_SERVICE", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID", "")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "")
WHATSAPP_TEMPLATES = {
    (kind, lang): os.getenv(f"WHATSAPP_{kind.upper()}_{lang.upper()}", "")
    for kind in ("reminder", "cancellation")
    for lang in ("fr", "ar")
}
