"""Settings for the test suite. Tests need only DATABASE_URL pointing at PostgreSQL."""

import os

os.environ.setdefault("SYMPRAXI_SECRET_KEY", "test-only-not-secret")
os.environ.setdefault("SYMPRAXI_DEBUG", "false")
os.environ.setdefault("SYMPRAXI_SSL_REDIRECT", "false")

from sympraxi.settings import *  # noqa: F403

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    "exports": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "uploads": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
}
TASKS = {"default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}}
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
