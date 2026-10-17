"""Django settings for Sympraxi.

Everything that differs between installations comes from environment variables
(or a `.env` file next to `manage.py` during development). `.env.example` lists
them all. Defaults are the safe production choice unless noted.
"""

from pathlib import Path

import environ
from django.utils.csp import CSP

from sympraxi.accounts import sso_config

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
# Variables already set in the environment win over the file.
ENV_FILE = Path(environ.Env().str("SYMPRAXI_ENV_FILE", default=str(BASE_DIR / ".env")))
if ENV_FILE.is_file():
    env.read_env(ENV_FILE)

# --- Core -------------------------------------------------------------------

DEBUG = env.bool("SYMPRAXI_DEBUG", default=False)
SECRET_KEY = env.str("SYMPRAXI_SECRET_KEY")
ALLOWED_HOSTS = env.list(
    "SYMPRAXI_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"] if DEBUG else []
)
CSRF_TRUSTED_ORIGINS = env.list("SYMPRAXI_CSRF_TRUSTED_ORIGINS", default=[])

SYMPRAXI_SITE_NAME = env.str("SYMPRAXI_SITE_NAME", default="Sympraxi")
# The address people use to reach this site, e.g. "https://writing.college.edu".
# Links in emails sent outside a page request (reminders, the worker) start with it.
# The production stack sets SYMPRAXI_DOMAIN, so it is used when this isn't set.
_domain = env.str("SYMPRAXI_DOMAIN", default="")
SYMPRAXI_BASE_URL = env.str(
    "SYMPRAXI_BASE_URL", default=f"https://{_domain}" if _domain else ""
).rstrip("/")
# AGPL section 13: if you run a modified copy, point this at your modified source.
SYMPRAXI_SOURCE_CODE_URL = env.str(
    "SYMPRAXI_SOURCE_CODE_URL", default="https://github.com/sympraxi/sympraxi"
)
SYMPRAXI_REQUIRE_STAFF_MFA = env.bool("SYMPRAXI_REQUIRE_STAFF_MFA", default=True)
SYMPRAXI_SIGNUP_OPEN = env.bool("SYMPRAXI_SIGNUP_OPEN", default=True)
# If set, only these email domains may register, e.g. "college.edu,students.college.edu".
SYMPRAXI_SIGNUP_EMAIL_DOMAINS = [
    d.lower().lstrip("@") for d in env.list("SYMPRAXI_SIGNUP_EMAIL_DOMAINS", default=[])
]
# Only enable behind a reverse proxy that overwrites X-Forwarded-For (Caddy does).
SYMPRAXI_TRUST_X_FORWARDED_FOR = env.bool("SYMPRAXI_TRUST_X_FORWARDED_FOR", default=False)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.openid_connect",
    "django_htmx",
    "django_tasks_db",
    "sympraxi.core",
    "sympraxi.accounts",
    "sympraxi.centers",
    "sympraxi.privacy",
    "sympraxi.scheduling",
    "sympraxi.records",
    "sympraxi.forms",
    "sympraxi.reports",
    "sympraxi.staff",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "sympraxi.accounts.middleware.RequireStaffMFAMiddleware",
]

ROOT_URLCONF = "sympraxi.urls"
WSGI_APPLICATION = "sympraxi.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "sympraxi" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.csp",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "sympraxi.core.context_processors.site",
            ],
        },
    },
]

# --- Data -------------------------------------------------------------------

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("SYMPRAXI_DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

# A shared cache is needed for login rate limiting across worker processes.
# The database cache needs no extra service; set CACHE_URL to a Valkey URL at scale.
CACHES = {"default": env.cache("CACHE_URL", default="dbcache://sympraxi_cache")}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "/static/"
STATIC_ROOT = env.path("SYMPRAXI_STATIC_ROOT", default=BASE_DIR / "staticfiles")
STATICFILES_DIRS = [BASE_DIR / "sympraxi" / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = env.path("SYMPRAXI_MEDIA_ROOT", default=BASE_DIR / "media")
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    # Full-center exports (sympraxi/reports/center_export.py). Never served by the
    # web server: they download through a page that checks who is asking, and the
    # scheduler deletes each one when its link expires. backup.sh leaves them out.
    "exports": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {
            "location": Path(str(MEDIA_ROOT)) / "center-exports",
            "base_url": None,
            "file_permissions_mode": 0o600,
            "directory_permissions_mode": 0o700,
        },
    },
}

TASKS = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}

# --- Accounts and login -----------------------------------------------------

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "core:home"

ACCOUNT_ADAPTER = "sympraxi.accounts.adapter.AccountAdapter"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_SIGNUP_FORM_CLASS = "sympraxi.accounts.forms.SignupForm"
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_EMAIL_NOTIFICATIONS = True
ACCOUNT_PREVENT_ENUMERATION = True
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "http" if DEBUG else "https"
ACCOUNT_EMAIL_SUBJECT_PREFIX = f"[{SYMPRAXI_SITE_NAME}] "

# allauth's rate limits count per client address. Behind Caddy every request comes
# from Caddy's address, so take the client's from the X-Forwarded-For entry Caddy
# writes (the last one), the same address the audit log records.
ALLAUTH_TRUSTED_PROXY_COUNT = 1 if SYMPRAXI_TRUST_X_FORWARDED_FOR else 0
# Many students on a campus network can share one public address, so these are
# higher than allauth's 30 sign-ins, 10 failed sign-ins and 20 sign-ups a minute.
# Each account still gets 5 failed sign-ins in 5 minutes. Every limit that answers
# with the 429 page counts over a minute (tests/test_rate_limits.py checks this).
ACCOUNT_RATE_LIMITS = {
    "login": "300/m/ip",
    "login_failed": "60/m/ip,5/300s/key",
    "signup": "60/m/ip",
}

MFA_SUPPORTED_TYPES = ["totp", "recovery_codes", "webauthn"]
MFA_PASSKEY_LOGIN_ENABLED = True
MFA_TOTP_ISSUER = SYMPRAXI_SITE_NAME
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = DEBUG

SESSION_COOKIE_AGE = env.int("SYMPRAXI_SESSION_SECONDS", default=8 * 60 * 60)

# --- Single sign-on ---------------------------------------------------------
# Microsoft Entra ID and Google Workspace through OpenID Connect. Each provider is
# set up from its SYMPRAXI_ENTRA_* or SYMPRAXI_GOOGLE_* variables (see
# sympraxi/accounts/sso_config.py and docs/install.md); one whose variables
# aren't set doesn't appear. The checks every sign-in must pass are in
# sympraxi/accounts/sso.py.

# Accept the identity provider's multi-factor login in place of Sympraxi's 2FA,
# but only when its ID token proves it (`amr` contains "mfa"). Off by default.
SYMPRAXI_TRUST_IDP_MFA = env.bool("SYMPRAXI_TRUST_IDP_MFA", default=False)
# Hide password sign-in and sign-up once SSO is set up. Site administrators keep
# a break-glass password sign-in at /accounts/login/local/.
SYMPRAXI_SSO_ONLY = env.bool("SYMPRAXI_SSO_ONLY", default=False)

SYMPRAXI_SSO_PROVIDERS = sso_config.providers_from_env(env, trust_idp_mfa=SYMPRAXI_TRUST_IDP_MFA)
sso_config.check_sso_only(SYMPRAXI_SSO_ONLY, SYMPRAXI_SSO_PROVIDERS)

SOCIALACCOUNT_ADAPTER = "sympraxi.accounts.adapter.SocialAccountAdapter"
SOCIALACCOUNT_PROVIDERS = {"openid_connect": {"APPS": SYMPRAXI_SSO_PROVIDERS}}
# A sign-in that passes the checks in sso.py may link to an existing account with
# the same address; allauth then records the link, so later sign-ins match the
# provider's stable ID rather than the address.
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
SOCIALACCOUNT_STORE_TOKENS = False
SOCIALACCOUNT_LOGIN_ON_GET = False
SOCIALACCOUNT_REQUESTS_TIMEOUT = 10

# --- Email ------------------------------------------------------------------

DEFAULT_FROM_EMAIL = env.str("SYMPRAXI_FROM_EMAIL", default="sympraxi@localhost")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

_email = env.email_url("EMAIL_URL", default="consolemail://")
_delivery: dict[str, object] = {"BACKEND": _email["EMAIL_BACKEND"]}
if _email["EMAIL_BACKEND"].endswith("smtp.EmailBackend"):
    _delivery["OPTIONS"] = {
        "host": _email.get("EMAIL_HOST", "localhost"),
        "port": _email.get("EMAIL_PORT", 25),
        "username": _email.get("EMAIL_HOST_USER", ""),
        "password": _email.get("EMAIL_HOST_PASSWORD", ""),
        "use_tls": bool(_email.get("EMAIL_USE_TLS", False)),
        "use_ssl": bool(_email.get("EMAIL_USE_SSL", False)),
        "timeout": 30,
    }
# "delivery" talks to the real relay. "default" hands mail to the task worker,
# so a slow SMTP relay never blocks a page.
MAILERS = {
    "default": (
        {"BACKEND": "sympraxi.core.mail.QueuedEmailBackend"}
        if env.bool("SYMPRAXI_QUEUE_EMAIL", default=True)
        else _delivery
    ),
    "delivery": _delivery,
}

# --- Security headers -------------------------------------------------------

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SYMPRAXI_SSL_REDIRECT", default=not DEBUG)
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
SECURE_HSTS_SECONDS = env.int("SYMPRAXI_HSTS_SECONDS", default=0 if DEBUG else 31_536_000)
# HSTS covers this host only. Extending it to every subdomain, or preloading it,
# would bind the whole college domain; that is campus IT's call, not this app's.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF, CSP.NONCE],
    "style-src": [CSP.SELF],
    "img-src": [CSP.SELF, "data:"],
    "font-src": [CSP.SELF],
    "connect-src": [CSP.SELF],
    # Browsers apply form-action to redirects too, and an SSO button is a form that
    # redirects to the identity provider's sign-in page.
    "form-action": [CSP.SELF, *sso_config.login_origins(SYMPRAXI_SSO_PROVIDERS)],
    "frame-ancestors": [CSP.NONE],
    "base-uri": [CSP.NONE],
    "object-src": [CSP.NONE],
}

# --- Localization -----------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = env.str("SYMPRAXI_TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True

# --- Logging ----------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    # Survey link tokens never reach the log (sympraxi/core/logs.py).
    "filters": {"scrub_links": {"()": "sympraxi.core.logs.ScrubLinks"}},
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "plain",
            "filters": ["scrub_links"],
        }
    },
    "root": {"handlers": ["console"], "level": env.str("SYMPRAXI_LOG_LEVEL", default="INFO")},
}
