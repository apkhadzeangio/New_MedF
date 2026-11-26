"""Nosology analysis: which medicines are prescribed for which diagnosis.

Works on the existing prescriptions (NivForm). Nothing is written to the
database here.
"""
import functools
import hashlib
import json
import re
from collections import Counter, defaultdict

from django.core.cache import cache
from django.db.models import Count, Max

from .models import ROW_COUNT, NivForm

ICD_RE = re.compile(r'\b([A-TV-Z]\d{2}(?:\.\d{1,2})?)\b', re.IGNORECASE)

# Georgian case endings, longest first: "ანევრიზმის", "ანევრიზმით" -> "ანევრიზმ"
SUFFIXES = ('ებისთვის', 'ისთვის', 'ებიდან', 'იდან', 'ებით', 'ების', 'ებში', 'ებს', 'ები',
            'ით', 'ის', 'ად', 'ს', 'ა', 'ი', 'ო', 'ე')

# Frequent words that describe *where* or *how*, not the disease itself.
STOPWORDS = {
    'და', 'ან', 'თუ', 'შემდეგ', 'გარეშე', 'მიერ', 'გამო', 'მდგომარეობა', 'სხვა',
    'მარცხენა', 'მარჯვენა', 'ორმხრივი', 'ქვედა', 'ზედა', 'კიდურის', 'კიდურების',
    'მწვავე', 'ქრონიკული', 'დაუზუსტებელი', 'გართულებით', 'გართულების', 'სტადია',
    'ხარისხის', 'ტიპის', 'ფორმა', 'პაციენტი', 'დიაგნოზი', 'ძირითადი', 'თანმხლები',
}

WORD_RE = re.compile(r'[^\W\d_]+(?:-[^\W\d_]+)*', re.UNICODE)


def data_version():
    """Changes whenever a prescription is added, saved or deleted (one small query)."""
    v = NivForm.objects.aggregate(n=Count('id'), last=Max('id'), saved=Max('updated_at'))
    return f"{v['n']}-{v['last']}-{v['saved'].timestamp() if v['saved'] else 0}"


def cached(func):
    """Remember the result until the prescriptions change (reading them all is the slow part)."""
    @functools.wraps(func)
    def wrapper(*args):
        raw = json.dumps([func.__name__, data_version(), *args], ensure_ascii=False)
        key = 'analysis:' + hashlib.sha1(raw.encode()).hexdigest()
        result = cache.get(key)
        if result is None:
            result = func(*args)
            cache.set(key, result, 3600)
        return result
    return wrapper


def normalize(text):
    return ' '.join((text or '').lower().split())


def icd_code(text):
    match = ICD_RE.search(text or '')
    return match.group(1).upper() if match else ''


def disease_text(text):
    """The disease name as written, without ICD codes and extra punctuation."""
    text = ICD_RE.sub(' ', text or '')
    text = re.sub(r'[\s,.;:()\[\]/"“”„-]+', ' ', text)
    return normalize(text)


def stem(word):
    word = word.lower()
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[:-len(suffix)]
    return word


def terms(text):
    """Distinct stems of the meaningful words in a diagnosis."""
    found = {}
    for word in WORD_RE.findall(ICD_RE.sub(' ', text or '')):
        if len(word) < 4 or word.lower() in STOPWORDS:
            continue
        found.setdefault(stem(word), word.lower())
    return found


MODES = {
    'terms': 'სიტყვებით (დაავადება)',
    'full': 'სრული დიაგნოზით',
}


@cached
def overview(mode='terms', q='', limit=300):
    """Nosologies found in the "დიაგნოზი" field, with prescription counts.

    mode="terms": each meaningful word of the diagnosis (ანევრიზმა, ლერიში,
    სტენოზი...) counted once per prescription, word forms merged.
    mode="full": the whole diagnosis text (without ICD code).
    """
    mode = mode if mode in MODES else 'terms'
    q = normalize(q)
    groups = {}
    for value in NivForm.objects.values_list('diagnosis', flat=True).iterator():
        if not value or not value.strip():
            continue
        if mode == 'terms':
            items = terms(value).items()
        else:
            text = disease_text(value)
            items = [(text, text)] if text else []
        for key, form in items:
            group = groups.get(key)
            if group is None:
                group = groups[key] = {'key': key, 'count': 0, 'labels': Counter()}
            group['count'] += 1
            group['labels'][form] += 1

    result = []
    for group in groups.values():
        labels = [label for label, _ in group['labels'].most_common(3)]
        if q and q not in group['key'] and not any(q in lb for lb in labels):
            continue
        group['label'] = labels[0]
        group['other_labels'] = labels[1:]
        group['variants'] = len(group['labels'])
        result.append(group)
    result.sort(key=lambda g: (-g['count'], g['label']))
    return result[:limit], len(result)


def matcher(q):
    """Function telling whether a diagnosis/title text belongs to nosology ``q``.

    Compares the written disease names (ICD codes and punctuation ignored).
    A single word also matches its other forms: ანევრიზმა ~ ანევრიზმის.
    """
    needle = disease_text(q)
    words = needle.split()
    if len(words) == 1:
        needle = stem(words[0])
    if not needle:
        return lambda text: False
    return lambda text: needle in disease_text(text)


def matching_prescriptions(q, field='diagnosis'):
    """Ids of prescriptions whose diagnosis (and/or title) match nosology ``q``."""
    match = matcher(q)
    ids = []
    for pk, diagnosis, title in NivForm.objects.values_list('id', 'diagnosis', 'title').iterator():
        if (field in ('diagnosis', 'both') and match(diagnosis)) or \
                (field in ('title', 'both') and match(title)):
            ids.append(pk)
    return ids


CLOCK = {
    'd1': dict(zip('abcdefghijklm', [8, 10, 12, 14, 16, 18, 20, 22, 24, 2, 4, 6, 8], strict=True)),
    'd2': dict(zip('abcdefghijkl', [10, 12, 14, 16, 18, 20, 22, 24, 2, 4, 6, 8], strict=True)),
    'd3': dict(zip('abcdefghijkl', [10, 12, 14, 16, 18, 20, 22, 24, 2, 4, 6, 8], strict=True)),
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


@cached
def medicines_for(q, field='diagnosis'):
    """Medicines prescribed for the nosology ``q``, most frequent first.

    Returns (total_prescriptions, [ {name, count, percent, note, notes, hours,
    hours_share, avg_row}, ... ]).
    """
    ids = matching_prescriptions(q, field)
    qs = NivForm.objects.filter(id__in=ids) if ids else NivForm.objects.none()
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
