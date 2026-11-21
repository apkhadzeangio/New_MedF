"""Zero-setup local settings: SQLite database, DEBUG on.

    python manage.py migrate --settings=MedicalForms.settings.dev
    python manage.py runserver --settings=MedicalForms.settings.dev
"""
from .base import *  # NOQA

DEBUG = True
ALLOWED_HOSTS = ['*']
