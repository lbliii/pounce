"""
Minimal Django settings for ASGI compatibility testing.

No database, no installed apps beyond the minimum required for ASGI.
URL patterns are defined in test_django_asgi_compat.py.
"""

SECRET_KEY = "pounce-test-secret-key-not-for-production"

DEBUG = False

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
]

ROOT_URLCONF = "tests.integration.frameworks.test_django_asgi_compat"

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

# No database — all tests are stateless
DATABASES = {}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
