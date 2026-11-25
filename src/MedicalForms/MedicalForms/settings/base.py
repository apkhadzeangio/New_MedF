"""
Django settings for MedicalForms project.

Secrets (SECRET_KEY, database password) are NOT stored in this file.
They are read from environment variables or from the ``.env`` file that
lives next to ``manage.py`` (see ``.env.example``). The ``.env`` file is
listed in ``.gitignore`` and must never be committed.
"""

import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _load_env_file(path):
    """Load KEY=VALUE lines from ``path`` into os.environ (no override)."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_env_file(BASE_DIR / '.env')


def env(key, default=None):
    return os.environ.get(key, default)


def env_bool(key, default=False):
    value = os.environ.get(key)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def env_list(key, default=()):
    value = os.environ.get(key)
    if not value:
        return list(default)
    return [item.strip() for item in value.split(',') if item.strip()]


# SECURITY WARNING: keep the secret key used in production secret!
# The fallback key is only good for local development.
SECRET_KEY = env('DJANGO_SECRET_KEY', 'dev-only-insecure-key-change-me')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env_bool('DJANGO_DEBUG', False)

ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', ['localhost', '127.0.0.1'])

# Friendly page (in Georgian) when a form's security token is out of date.
CSRF_FAILURE_VIEW = 'apps.mdf.views.csrf_failure'


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

OUR_APPS = [
    'apps.mdf.apps.MdfConfig',
    'django_docxtpl',
]

INSTALLED_APPS += OUR_APPS

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Serves /static/ files efficiently even when DEBUG is off.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'MedicalForms.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.mdf.reminders.site_reminders',
            ],
        },
    },
]

WSGI_APPLICATION = 'MedicalForms.wsgi.application'


# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            'timeout': 20,
        },
    }
}


# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

# Internal clinic program: simple passwords are allowed on purpose.
AUTH_PASSWORD_VALIDATORS = []


# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'Asia/Tbilisi'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = '/static/'

STATICFILES_DIRS = (
    BASE_DIR / 'static',
)

STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'

# Staff photos. Kept outside the code folder so updates don't delete them.
MEDIA_ROOT = Path(env('MEDIA_ROOT', str(Path.home() / 'medf-media')))

# Patient photos (cases). NOT under MEDIA_ROOT: no web server publishes this folder,
# the site shows the photos only to logged-in users.
PRIVATE_MEDIA_ROOT = Path(env('PRIVATE_MEDIA_ROOT', str(Path.home() / 'medf-private')))

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        # gzip-compressed copies of css/js -> faster page loads.
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}

# Serve files straight from STATICFILES_DIRS too, so the site keeps its
# styles even if "collectstatic" was not run after an update.
WHITENOISE_USE_FINDERS = True


# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/'


# Stay logged in on the clinic computers: the session lasts a year and is
# renewed on every visit, so the prescription page is always open.
SESSION_COOKIE_AGE = 60 * 60 * 24 * 365
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_HTTPONLY = True

# HTTPS hardening. Turn on (DJANGO_HTTPS=1) once the site is always opened
# with https:// (PythonAnywhere: Web tab -> "Force HTTPS").
if env_bool('DJANGO_HTTPS', False):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # browsers remember "always https" (own server with a certificate); 0 = off
    SECURE_HSTS_SECONDS = int(env('DJANGO_HSTS_SECONDS', '0') or 0)

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'SAMEORIGIN'


# Errors are written to the server error log (PythonAnywhere: Web tab ->
# "Error log") so problems can be diagnosed while DEBUG is off.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {'class': 'logging.StreamHandler'},
    },
    'loggers': {
        'django': {'handlers': ['console'], 'level': 'WARNING'},
        'apps': {'handlers': ['console'], 'level': 'INFO'},
    },
}


DOCXTPL_TEMPLATE_DIR = BASE_DIR / 'templates'

# In-memory caches of each server process (nothing to install or run).
# 'pdf' keeps the last printed prescriptions, so printing again is instant;
# a saved change gives a new key, a reload/deploy empties it.
CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
    'pdf': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'pdf',
            'TIMEOUT': 12 * 3600, 'OPTIONS': {'MAX_ENTRIES': 40}},
}
