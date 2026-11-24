"""Automatic cache-busting query string (?v=<hash>) for static files."""
import hashlib
from pathlib import Path

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()

# (resolved_path, mtime_ns, size) -> 8-char SHA-256 hex digest
_hashes = {}


def _find_file(path):
    found = finders.find(path)
    if found:
        return Path(found[0] if isinstance(found, (list, tuple)) else found)
    static_root = getattr(settings, 'STATIC_ROOT', None)
    if static_root:
        candidate = Path(static_root) / path
        if candidate.is_file():
            return candidate
    return None


def file_version(path):
    """Short content hash of a static file, refreshed automatically when edited."""
    filepath = _find_file(path)
    if not filepath:
        return ''
    try:
        st = filepath.stat()
    except OSError:
        return ''
    key = (str(filepath), st.st_mtime_ns, st.st_size)
    digest = _hashes.get(key)
    if digest is None:
        try:
            digest = hashlib.sha256(filepath.read_bytes()).hexdigest()[:8]
        except OSError:
            return ''
        if len(_hashes) > 256:
            _hashes.clear()
        _hashes[key] = digest
    return digest


@register.simple_tag
def static_v(path):
    """Static URL with an automatic content-hash query string (?v=<hash>)."""
    url = static(path)
    version = file_version(path)
    if not version:
        return url
    sep = '&' if '?' in url else '?'
    return f'{url}{sep}v={version}'
