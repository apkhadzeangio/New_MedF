"""Pictures: staff avatars and the (funny) patient avatar of each disease.

Files live in static/img/staff/<username>.png|jpg|webp
and static/img/patients/<file> (see DISEASE_AVATARS below).
A missing file is simply not shown.
"""
from django.contrib.staticfiles import finders
from django.templatetags.static import static

from .analysis import disease_text

EXTENSIONS = ('png', 'jpg', 'jpeg', 'webp')

# (words to look for in the diagnosis, file in static/img/patients/)
# The first matching line wins, so put the more specific diseases first.
# Words are matched as the beginning of a word form: "ანევრიზმ" matches
# ანევრიზმა / ანევრიზმის / ანევრიზმით.
DISEASE_AVATARS = [
    (('ლერიშ',), 'leriche'),
    (('ანევრიზმ',), 'aneurysm'),
    (('ბარძაყ მუხლქვეშ', 'ბარძაყმუხლქვეშ', 'მუხლქვეშ', 'ფემორო პოპლიტ'), 'fem-pop'),
    (('საძილე', 'კაროტიდ'), 'carotid'),
    (('სტენტირებ', 'სტენტ'), 'stenting'),
    (('ვარიკოზ',), 'varicose'),
    (('დიაბეტურ ტერფ', 'დიაბეტური ტერფ'), 'diabetic-foot'),
    (('თრომბოზ', 'თრომბ'), 'thrombosis'),
    (('ათეროსკლეროზ', 'იშემი', 'ობლიტერაც'), 'atherosclerosis'),
    (('ფისტულ',), 'av-fistula'),
]


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


def disease_avatar(text):
    """(url, file key) of the patient avatar for a diagnosis text, or (None, None)."""
    text = disease_text(text)
    if not text:
        return None, None
    for words, name in DISEASE_AVATARS:
        if any(w in text for w in words):
            url = _static_file('patients', name)
            if url:
                return url, name
    return None, None
