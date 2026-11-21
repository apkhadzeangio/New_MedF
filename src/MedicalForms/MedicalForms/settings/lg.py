"""Local development settings (PostgreSQL). Values can be overridden via .env."""
from .base import *  # NOQA
from .base import env

DEBUG = True

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': env('DB_NAME', 'forms'),
        'USER': env('DB_USER', 'dev'),
        'PASSWORD': env('DB_PASSWORD', 'toor'),
        'HOST': env('DB_HOST', 'localhost'),
        'PORT': env('DB_PORT', '5432'),
    },
}
