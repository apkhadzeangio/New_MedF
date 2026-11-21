"""Nosology analysis: which medicines are prescribed for which diagnosis.

Works on the existing prescriptions (NivForm). Nothing is written to the
database here.
"""
import json
import re
from collections import Counter, defaultdict

from .models import NivForm, ROW_COUNT

ICD_RE = re.compile(r'\b([A-TV-Z]\d{2}(?:\.\d{1,2})?)\b', re.IGNORECASE)

FIELDS = {
    'diagnosis': 'დიაგნოზი',
    'title': 'დასახელება',
}


def normalize(text):
    return ' '.join((text or '').lower().split())


def icd_code(text):
    match = ICD_RE.search(text or '')
    return match.group(1).upper() if match else ''


def nosology_key(text):
    """ICD-10 code if the text has one, otherwise the normalised text."""
    code = icd_code(text)
    return code or normalize(text)[:120]


def overview(field='diagnosis', q='', limit=200):
    """List nosologies (grouped diagnoses or titles) with prescription counts."""
    field = field if field in FIELDS else 'diagnosis'
    q = normalize(q)
    groups = {}
    for value in NivForm.objects.values_list(field, flat=True).iterator():
        if not value or not value.strip():
            continue
        key = nosology_key(value) if field == 'diagnosis' else normalize(value)[:120]
        group = groups.get(key)
        if group is None:
            group = groups[key] = {'key': key, 'count': 0, 'labels': Counter()}
        group['count'] += 1
        group['labels'][value.strip()] += 1

    result = []
    for group in groups.values():
        labels = [label for label, _ in group['labels'].most_common(3)]
        if q and q not in group['key'] and not any(q in normalize(lb) for lb in labels):
            continue
        group['label'] = labels[0]
        group['other_labels'] = labels[1:]
        group['variants'] = len(group['labels'])
        result.append(group)
    result.sort(key=lambda g: (-g['count'], g['label']))
    return result[:limit], len(result)


def matching_prescriptions(q, field='both'):
    """Prescriptions whose diagnosis and/or title contain ``q`` (or its ICD code)."""
    q = (q or '').strip()
    if not q:
        return NivForm.objects.none()
    from django.db.models import Q
    cond = Q()
    if field in ('diagnosis', 'both'):
        cond |= Q(diagnosis__icontains=q)
    if field in ('title', 'both'):
        cond |= Q(title__icontains=q)
    return NivForm.objects.filter(cond)


CLOCK = {
    'd1': dict(zip('abcdefghijklm', [8, 10, 12, 14, 16, 18, 20, 22, 24, 2, 4, 6, 8])),
    'd2': dict(zip('abcdefghijkl', [10, 12, 14, 16, 18, 20, 22, 24, 2, 4, 6, 8])),
    'd3': dict(zip('abcdefghijkl', [10, 12, 14, 16, 18, 20, 22, 24, 2, 4, 6, 8])),
}


def hours_text(hours):
    """{'d1': ['a', 'e']} -> 'დღე 1: 8, 16'"""
    parts = []
    for i, day in enumerate(('d1', 'd2', 'd3'), start=1):
        letters = [h for h in CLOCK[day] if h in (hours.get(day) or [])]
        if letters:
            parts.append(f'დღე {i}: ' + ', '.join(str(CLOCK[day][h]) for h in letters))
    return ' · '.join(parts)


def _hours_key(r):
    r = r if isinstance(r, dict) else {}
    clean = {d: sorted((r.get(d) or {}).keys()) for d in ('d1', 'd2', 'd3')}
    return json.dumps(clean, sort_keys=True)


def medicines_for(q, field='both'):
    """Medicines prescribed for the nosology ``q``, most frequent first.

    Returns (total_prescriptions, [ {name, count, percent, note, notes, hours,
    hours_share, avg_row}, ... ]).
    """
    qs = matching_prescriptions(q, field)
    total = 0
    stats = defaultdict(lambda: {'count': 0, 'names': Counter(), 'notes': Counter(),
                                 'hours': Counter(), 'rows': []})
    columns = []
    for i in range(1, ROW_COUNT + 1):
        columns += [f'm{i}', f'n{i}', f'r{i}']
    for niv in qs.only('id', *columns).iterator():
        total += 1
        seen = set()
        for i in range(1, ROW_COUNT + 1):
            name = (getattr(niv, f'm{i}') or '').strip()
            if not name:
                continue
            key = normalize(name)
            if key in seen:          # same medicine twice in one prescription
                continue
            seen.add(key)
            st = stats[key]
            st['count'] += 1
            st['names'][name] += 1
            note = (getattr(niv, f'n{i}') or '').strip()
            if note:
                st['notes'][note] += 1
            st['hours'][_hours_key(getattr(niv, f'r{i}'))] += 1
            st['rows'].append(i)

    medicines = []
    for key, st in stats.items():
        hours_key, hours_count = st['hours'].most_common(1)[0]
        hours = json.loads(hours_key)
        medicines.append({
            'key': key,
            'name': st['names'].most_common(1)[0][0],
            'count': st['count'],
            'percent': round(100 * st['count'] / total) if total else 0,
            'note': st['notes'].most_common(1)[0][0] if st['notes'] else '',
            'notes': [n for n, _ in st['notes'].most_common(3)],
            'hours': {d: {h: '+' for h in letters} for d, letters in hours.items()},
            'hours_marks': sum(len(v) for v in hours.values()),
            'hours_text': hours_text(hours),
            'hours_share': round(100 * hours_count / st['count']),
            'avg_row': sum(st['rows']) / len(st['rows']),
        })
    medicines.sort(key=lambda m: (-m['count'], m['avg_row'], m['name']))
    return total, medicines


def template_rows(medicines):
    """Template rows (max 16) from chosen medicines, in their usual order on the form."""
    chosen = sorted(medicines, key=lambda m: m['avg_row'])[:ROW_COUNT]
    return [{'m': m['name'], 'n': m['note'], 'r': m['hours']} for m in chosen]
