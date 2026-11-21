"""Home-screen icon for phones ("add to home screen").

The service worker caches nothing: patient data never stays on the phone.
It exists only so that browsers offer to install the site as an app.
"""
import json

from django.http import HttpResponse
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import cache_control


@cache_control(max_age=86400)
def manifest(request):
    data = {
        'name': 'MedicalForms — პაციენტების დაფა',
        'short_name': 'დაფა',
        'start_url': reverse('board'),
        'scope': '/',
        'display': 'standalone',
        'background_color': '#F3F4F8',
        'theme_color': '#1B2437',
        'lang': 'ka',
        'icons': [
            {'src': static('img/app/icon-192.png'), 'sizes': '192x192', 'type': 'image/png'},
            {'src': static('img/app/icon-512.png'), 'sizes': '512x512', 'type': 'image/png'},
            {'src': static('img/app/icon-maskable-512.png'), 'sizes': '512x512', 'type': 'image/png',
             'purpose': 'maskable'},
        ],
    }
    return HttpResponse(json.dumps(data, ensure_ascii=False), content_type='application/manifest+json; charset=utf-8')


SERVICE_WORKER = """// MedicalForms: no caching, every request goes to the server as usual.
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function () {});
"""


@cache_control(max_age=0, no_cache=True)
def service_worker(request):
    response = HttpResponse(SERVICE_WORKER, content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/'
    return response
