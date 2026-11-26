import re

from apps.mdf.enums import Symbols

TM_KEY_REGEX = re.compile(r'^r(1[0-6]|[1-9])-d[123]$')


def validate_take_medicine(data: dict) -> tuple[dict, list[str]]:
    result, keys = dict(), list()
    if not data:
        return result, keys

    for key, _ in data.items():
        if not TM_KEY_REGEX.match(key):
            continue

        keys.append(key)
        value = data.getlist(key)
        medicine, day = key.split('-')
        if medicine not in result:
            result[medicine] = dict()

        result[medicine][day] = {h: Symbols.PLUS.value for h in value if 'del' not in h}

    return result, keys


def jsonfield_default_value():
    return dict(d1=dict(), d2=dict(), d3=dict())


DAY_LETTERS = {'d1': 'abcdefghijklm', 'd2': 'abcdefghijkl', 'd3': 'abcdefghijkl'}


def clean_template_rows(rows, row_count=16):
    """Normalise template rows coming from the browser or a prescription.

    Always returns ``row_count`` rows of {'m': str, 'n': str, 'r': {d1, d2, d3}}
    where each day only contains valid hour letters marked with "+".
    """
    cleaned = []
    rows = rows if isinstance(rows, list) else []
    for i in range(row_count):
        row = rows[i] if i < len(rows) and isinstance(rows[i], dict) else {}
        hours = row.get('r') if isinstance(row.get('r'), dict) else {}
        r = {}
        for day, letters in DAY_LETTERS.items():
            marked = hours.get(day) or {}
            if isinstance(marked, list):
                marked = {h: '+' for h in marked}
            r[day] = {h: Symbols.PLUS.value for h in letters if h in marked}
        cleaned.append({
            'm': str(row.get('m') or '').strip()[:512],
            'n': str(row.get('n') or '').strip()[:512],
            'r': r,
        })
    return cleaned


def resize_photo(uploaded, size=400):
    """Return a small square JPEG (ContentFile) made from an uploaded image."""
    from io import BytesIO

    from django.core.files.base import ContentFile
    from PIL import Image, ImageOps

    image = Image.open(uploaded)
    image = ImageOps.exif_transpose(image).convert('RGB')
    image = ImageOps.fit(image, (size, size), Image.LANCZOS, centering=(0.5, 0.35))
    out = BytesIO()
    image.save(out, 'JPEG', quality=85)
    return ContentFile(out.getvalue())
