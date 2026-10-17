"""Local synthetic demo settings; secrets come from the environment."""
from pathlib import Path
import os
import secrets

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or secrets.token_urlsafe(50)
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "healthmesh.apps.HealthmeshConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "healthmesh.sources.api.SourceUploadLimitsMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {"default": dj_database_url.parse(
    os.environ.get("DATABASE_URL", f"sqlite:///{(BASE_DIR / 'db.sqlite3').as_posix()}"),
    conn_max_age=0,
)}
USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
REST_FRAMEWORK = {"DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"]}
CSRF_FAILURE_VIEW = 'healthmesh.workflow.auth.csrf_failure'
SOURCE_MAX_BYTES = 1024 * 1024
# Small source files stay in memory until validated and stored in the private DB.
FILE_UPLOAD_MAX_MEMORY_SIZE = SOURCE_MAX_BYTES + 64 * 1024
# No transport is enabled until the authenticated AMD event contract is verified.
AMD_API_KEY = os.environ.get('AMD_API_KEY', '')
AMD_API_BASE_URL = os.environ.get('AMD_API_BASE_URL', '')
AMD_MODEL_ID = os.environ.get('AMD_MODEL_ID', '')
