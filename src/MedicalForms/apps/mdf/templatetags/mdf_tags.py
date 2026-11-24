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
def duty_html(value):
    """Note text with only simple formatting kept (safe to show)."""
    from django.utils.safestring import mark_safe
    from apps.mdf.duty import clean_html
    return mark_safe(clean_html(value))


@register.filter
def who(user):
    """Short name of a person (ინა, რამაზი …); '—' when unknown."""
    from apps.mdf.board import person_name
    return person_name(user) or '—'


CASE_SECTIONS = (
    ('comorbidities', 'თანმხლები დიაგნოზები'),
    ('past_history', 'გადატანილი დაავადებები და ოპერაციები'),
    ('history', 'ანამნეზი და ჩივილები'),
    ('findings', 'კვლევები'),
    ('proposal', 'შეთავაზება'),
)


@register.filter
def case_sections(case):
    """The filled-in text parts of a case: [(label, text), ...]."""
    return [(label, getattr(case, field)) for field, label in CASE_SECTIONS if getattr(case, field, '')]
