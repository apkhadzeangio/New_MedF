"""Production settings (PythonAnywhere).

Required values come from the environment / the ``.env`` file next to
manage.py. See ``.env.example`` in the project root.
"""
from django.core.exceptions import ImproperlyConfigured

from .base import *  # NOQA
from .base import env, env_bool, env_list

DEBUG = env_bool('DJANGO_DEBUG', False)

ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', ['medf.pythonanywhere.com'])

if not env('DJANGO_SECRET_KEY'):
    raise ImproperlyConfigured(
        'DJANGO_SECRET_KEY is not set. Add it to the .env file next to manage.py.'
    )

if not env('DB_PASSWORD'):
    raise ImproperlyConfigured(
        'DB_PASSWORD is not set. Add it to the .env file next to manage.py.'
    )

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': env('DB_NAME', 'medf$MDF'),
        'USER': env('DB_USER', 'medf'),
        'PASSWORD': env('DB_PASSWORD'),
        'HOST': env('DB_HOST', 'medf.mysql.pythonanywhere-services.com'),
        # Reuse DB connections between requests (faster pages).
        'CONN_MAX_AGE': 60,
        'CONN_HEALTH_CHECKS': True,
    }
}
