from django.conf import settings
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.static import serve


def media(request, path):
    # Staff photos (small, internal) - served by Django so no extra
    # PythonAnywhere configuration is needed.
    return serve(request, path, document_root=settings.MEDIA_ROOT)


urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('apps.mdf.urls')),
    re_path(r'^media/(?P<path>.*)$', media),
]
