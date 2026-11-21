from django import template

from apps.mdf.models import ROW_COUNT

register = template.Library()


@register.filter
def get_item(mapping, key):
    """{{ some_dict|get_item:key }} -> some_dict[key] or None."""
    if isinstance(mapping, dict):
        return mapping.get(key)
    return None


@register.filter
def niv_rows(niv, bounds=None):
    """Medicine rows of a NivForm as a list of dicts.

    ``{% for row in niv|niv_rows:"1-12" %}`` -> rows 1..12 with keys
    ``i`` (row number), ``m`` (medicine), ``n`` (note), ``r`` (hours).
    """
    start, end = 1, ROW_COUNT
    if bounds:
        start, end = (int(x) for x in str(bounds).split('-'))
    rows = []
    for i in range(start, end + 1):
        rows.append({
            'i': i,
            'm': getattr(niv, f'm{i}', None) if niv else None,
            'n': getattr(niv, f'n{i}', None) if niv else None,
            'r': getattr(niv, f'r{i}', None) if niv else None,
        })
    return rows


@register.filter
def disease_avatar(text):
    """{{ niv.diagnosis|disease_avatar }} -> static URL of the patient avatar or ''."""
    from apps.mdf.avatars import disease_avatar as find
    return find(text)[0] or ''
