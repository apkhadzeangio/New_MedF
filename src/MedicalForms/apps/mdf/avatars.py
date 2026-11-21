"""Staff avatars: static/img/staff/<username>.png|jpg|webp (a missing file is not shown)."""
from django.contrib.staticfiles import finders
from django.templatetags.static import static

EXTENSIONS = ('png', 'jpg', 'jpeg', 'webp')

_found = {}


def _static_file(folder, name):
    # Only found files are remembered, so pictures added later appear
    # without restarting the site.
    key = (folder, name)
    if key in _found:
        return _found[key]
    for ext in EXTENSIONS:
        path = f'img/{folder}/{name}.{ext}'
        if finders.find(path):
            _found[key] = static(path)
            return _found[key]
    return None


_static_file.cache_clear = _found.clear


def staff_avatar_url(username):
    return _static_file('staff', username)
