"""Gentle reminders at the top of every page (free PythonAnywhere plan).

* the free web app must be extended every month (Web tab -> "Run until …");
* a copy of the database should be downloaded now and then (off the server).

The expiry date is read from the PythonAnywhere API when the account has an
API token (PythonAnywhere then gives the web app ``API_TOKEN``). The API does
not document this field, so when it is missing a plain end-of-month reminder
is shown instead. Each reminder can be closed (remembered in that browser).
"""
import datetime
import json
import logging
import os
import urllib.request

from django.core.cache import cache
from django.utils import timezone

from .models import SiteState

log = logging.getLogger(__name__)

EXPIRY_WARN_DAYS = 7
MONTH_END_DAY = 24
BACKUP_EVERY_DAYS = 30
CACHE_SECONDS = 12 * 3600


def web_app_expiry(host):
    """Expiry date of this web app from the PythonAnywhere API, or None."""
    token = os.environ.get('API_TOKEN')
    if not token or not host.endswith('.pythonanywhere.com'):
        return None
    cached = cache.get('pa-expiry')
    if cached is not None:
        return cached or None
    expiry = ''
    username = os.environ.get('PA_USERNAME') or host.split('.')[0]
    api = os.environ.get('PA_API_HOST', 'www.pythonanywhere.com')
    url = f'https://{api}/api/v0/user/{username}/webapps/{host}/'
    try:
        req = urllib.request.Request(url, headers={'Authorization': f'Token {token}'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            value = json.load(resp).get('expiry') or ''
        expiry = datetime.date.fromisoformat(str(value)[:10]) if value else ''
    except Exception as exc:                          # never break a page for a reminder
        log.info('expiry lookup failed: %s', exc)
    cache.set('pa-expiry', expiry, CACHE_SECONDS)
    return expiry or None


def site_reminders(request):
    """Context processor: ``reminders`` for the page header."""
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {}
    today = timezone.localdate()
    month = f'{today:%Y-%m}'
    items = []

    host = request.get_host().split(':')[0]
    on_pythonanywhere = host.endswith('.pythonanywhere.com')   # elsewhere (own server) there is no expiry
    expiry = web_app_expiry(host) if on_pythonanywhere else None
    if expiry:
        days = (expiry - today).days
        if days <= EXPIRY_WARN_DAYS:
            items.append({
                'key': f'expiry-{expiry:%Y-%m-%d}', 'level': 'warn',
                'text': f'საიტის ვადა იწურება {expiry:%d.%m.%Y}-ს'
                        + (f' ({days} დღეში)' if days > 0 else '')
                        + '. PythonAnywhere → Web → „Run until 1 month from today“.',
            })
    elif on_pythonanywhere and today.day >= MONTH_END_DAY:
        items.append({
            'key': f'expiry-{month}', 'level': 'info',
            'text': 'თვის ბოლოა: შეამოწმეთ საიტის ვადა — PythonAnywhere → Web → „Run until 1 month from today“.',
        })

    last = SiteState.objects.filter(key='backup_downloaded').values_list('updated_at', flat=True).first()
    if not last or (timezone.now() - last).days >= BACKUP_EVERY_DAYS:
        when = f'ბოლოს ჩამოიტვირთა {timezone.localtime(last):%d.%m.%Y}' if last else 'ჯერ არ ჩამოგიტვირთავთ'
        items.append({
            'key': f'backup-{month}', 'level': 'info', 'link': 'backup',
            'text': f'შეინახეთ ბაზის ასლი თქვენს კომპიუტერშიც ({when}).',
        })
    return {'reminders': items}
