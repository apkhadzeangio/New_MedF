"""Duty roster ("მორიგეობის განრიგი"): one surgeon on duty per day.

Rules (set by the department):
* some doctors are on a few days a month chosen at random (e.g. Misha: 2 days,
  at least 10 days apart);
* doctors with a fixed rule (weekday + which weeks) are on those days;
* everybody else shares the remaining days evenly, and the weekend load
  (Saturday, Sunday, public holidays) evenly too: nobody wants weekends.
  Who had more weekends in the previous months gets fewer now;
* one doctor never has two days in a row, duties are spread out — also across
  the border with the previous (and next, if saved) month;
* changes made by hand are locked: generating again keeps them.
* a generation is reproducible from its "seed": the preview and the saved
  schedule are the same; "სხვა ვარიანტი" draws a new one.

Ultrasound on-call is a later stage.
"""
import calendar
import datetime
import hashlib
import random

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from .duty import MONTHS, WEEKDAYS
from .models import RosterDay, RosterLog, RosterPerson

# Doctors who must never hand over to each other (one on the day right after the
# other). Kept in the code only: not shown anywhere on the site.
NEVER_ADJACENT = [('ია', 'თორნიკე')]


def _never_adjacent(people):
    """{person pk: pks this doctor may not be next to}, by name (see NEVER_ADJACENT)."""
    by_name = {}
    for p in people:
        if p is not None:
            by_name.setdefault(p.name.strip(), set()).add(p.pk)
    apart = {}
    for a, b in NEVER_ADJACENT:
        for x in by_name.get(a, ()):
            for y in by_name.get(b, ()):
                apart.setdefault(x, set()).add(y)
                apart.setdefault(y, set()).add(x)
    return apart

WEEKDAYS_SHORT = ['ორშ', 'სამ', 'ოთხ', 'ხუთ', 'პარ', 'შაბ', 'კვი']
MONTHS_GEN = ['იანვრის', 'თებერვლის', 'მარტის', 'აპრილის', 'მაისის', 'ივნისის',
              'ივლისის', 'აგვისტოს', 'სექტემბრის', 'ოქტომბრის', 'ნოემბრის', 'დეკემბრის']


# --------------------------------------------------------------------------
# calendar helpers
# --------------------------------------------------------------------------

def month_days(year, month):
    return [datetime.date(year, month, d) for d in range(1, calendar.monthrange(year, month)[1] + 1)]


def orthodox_easter(year):
    a, b, c = year % 4, year % 7, year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month, day = divmod(d + e + 114, 31)
    return datetime.date(year, month, day + 1) + datetime.timedelta(days=13)


def georgian_holidays(year):
    """Official non-working days of Georgia (fixed dates + Orthodox Easter)."""
    fixed = {
        (1, 1): 'ახალი წელი', (1, 2): 'ახალი წელი', (1, 7): 'შობა', (1, 19): 'ნათლისღება',
        (3, 3): 'დედის დღე', (3, 8): 'ქალთა დღე', (4, 9): 'ეროვნული ერთიანობის დღე',
        (5, 9): 'გამარჯვების დღე', (5, 12): 'ანდრიაობა', (5, 17): 'ოჯახის სიწმინდის დღე',
        (5, 26): 'დამოუკიდებლობის დღე', (8, 28): 'მარიამობა', (10, 14): 'სვეტიცხოვლობა',
        (11, 23): 'გიორგობა',
    }
    days = {datetime.date(year, m, d): name for (m, d), name in fixed.items()}
    easter = orthodox_easter(year)
    for offset, name in ((-2, 'წითელი პარასკევი'), (-1, 'დიდი შაბათი'), (0, 'აღდგომა'), (1, 'აღდგომის ორშაბათი')):
        days[easter + datetime.timedelta(days=offset)] = name
    return days


def label(day):
    return f'{day.day} {MONTHS_GEN[day.month - 1]}, {WEEKDAYS[day.weekday()]}'


def parse_month(get):
    today = timezone.localdate()
    try:
        year, month = int(get.get('y', today.year)), int(get.get('m', today.month))
        datetime.date(year, month, 1)
    except (TypeError, ValueError):
        year, month = today.year, today.month
    if not 2000 <= year <= 2100:
        year, month = today.year, today.month
    return year, month


def shift_month(year, month, delta):
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


# --------------------------------------------------------------------------
# the rotation engine
# --------------------------------------------------------------------------

def fixed_dates(person, year, month):
    """Dates of a fixed rule, e.g. weekday=Friday, weeks="2,4" -> 2nd and 4th Friday."""
    if person.fixed_weekday is None:
        return set()
    same = [d for d in month_days(year, month) if d.weekday() == person.fixed_weekday]
    weeks = {int(w) for w in person.fixed_weeks.replace(' ', '').split(',') if w.isdigit()}
    if not weeks:
        return set(same)
    return {same[w - 1] for w in weeks if 0 < w <= len(same)}


def rotation_people():
    return list(RosterPerson.objects.filter(is_active=True, in_rotation=True).order_by('order', 'id'))


def is_heavy(day, holidays):
    """Saturday, Sunday or a public holiday: the days nobody wants."""
    return day.weekday() >= 5 or day in holidays


def _history(people, first, months=6):
    """Duties and weekend duties of each doctor in the months before ``first``."""
    since = datetime.date(*shift_month(first.year, first.month, -months), 1)
    total = {p.pk: 0 for p in people}
    heavy = {p.pk: 0 for p in people}
    holidays = {}
    for rec in RosterDay.objects.filter(date__gte=since, date__lt=first, person__in=people):
        hol = holidays.setdefault(rec.date.year, georgian_holidays(rec.date.year))
        total[rec.person_id] += 1
        heavy[rec.person_id] += is_heavy(rec.date, hol)
    return total, heavy


def _random_days(person, free, taken, rnd, near=()):
    """``person.per_month`` days among ``free``, at least ``min_gap`` apart.

    The gap is kept from ``taken`` (this month) and from ``near`` (the end of the
    previous month / start of the next one), which do not count to the month.
    """
    need = max(0, (person.per_month or 0) - len(taken))
    gap = max(1, person.min_gap or 1)
    for _ in range(400):
        chosen = list(taken)
        for day in rnd.sample(free, len(free)):
            if len(chosen) - len(taken) == need:
                break
            if all(abs((day - other).days) >= gap for other in list(chosen) + list(near)):
                chosen.append(day)
        if len(chosen) - len(taken) == need:
            return [d for d in chosen if d not in taken], True
    # cannot keep the gap (e.g. many locked days): as far apart as possible
    free = sorted(free)
    step = max(1, len(free) // max(1, need))
    return free[::step][:need], False


def _split(n, people, weight, rnd):
    """``n`` days among ``people`` as evenly as possible; the extra ones go to the lowest ``weight``."""
    if not people:
        return {}
    base, extra = divmod(n, len(people))
    order = sorted(people, key=lambda p: (weight(p), rnd.random()))
    return {p.pk: base + (1 if i < extra else 0) for i, p in enumerate(order)}


def _place(days, heavy_days, people, need_heavy, need_light, fixed, rnd, outside=None):
    """One randomised pass: a doctor for every free day, keeping the quotas and spreading duties.

    ``outside``: duties just before / after the month, so the month border is spaced too.
    """
    left = {'h': dict(need_heavy), 'l': dict(need_light)}
    last = {}
    on_duty = {}
    for day, person in list((outside or {}).items()) + list(fixed.items()):
        if person:
            last.setdefault(person.pk, []).append(day)
            on_duty[day] = person.pk
    apart = _never_adjacent(list(people) + [p for p in _on_duty_people(outside, fixed)])
    one = datetime.timedelta(days=1)
    result = {}
    by_pk = {p.pk: p for p in people}
    for day in days:
        if day in fixed:
            continue
        kind = 'h' if day in heavy_days else 'l'
        best, best_score = None, None
        for p in people:
            if left[kind].get(p.pk, 0) <= 0:
                continue
            mine = last.get(p.pk, []) + [d for d, x in result.items() if x == p.pk]
            gaps = [abs((day - d).days) for d in mine]
            nearest = min(gaps) if gaps else 99
            if nearest <= 1:
                continue                                  # never two days in a row
            score = min(nearest, 8) + rnd.random()        # prefer whoever has waited longest
            if apart.get(p.pk) and any(
                    (result.get(n) or on_duty.get(n)) in apart[p.pk] for n in (day - one, day + one)):
                score -= 1000                             # only if nobody else can take the day
            if best_score is None or score > best_score:
                best, best_score = p.pk, score
        if best is None:
            return None
        result[day] = best
        left[kind][best] -= 1
    return {d: by_pk[pk] for d, pk in result.items()}


def _solve(days, heavy_days, people, need_heavy, need_light, fixed, rnd, outside=None,
           keep_apart=True, by_kind=True, max_steps=20000):
    """Backtracking search for when the one-pass ``_place`` gets stuck.

    Hard rules: everybody's number of (weekend / weekday) duties, never two days in a
    row (also across the month border), NEVER_ADJACENT unless ``keep_apart`` is off.
    ``by_kind=False`` only keeps the total number of duties. None if impossible.
    """
    one = datetime.timedelta(days=1)
    by_pk = {p.pk: p for p in people}
    around = {}
    for day, person in list((outside or {}).items()) + list(fixed.items()):
        if person:
            around[day] = person.pk
    apart = _never_adjacent(list(people) + list(_on_duty_people(outside, fixed))) if keep_apart else {}
    todo = [d for d in days if d not in fixed]

    def kind(day):
        return ('h' if day in heavy_days else 'l') if by_kind else 'a'

    if by_kind:
        left = {'h': dict(need_heavy), 'l': dict(need_light)}
    else:
        left = {'a': {pk: need_heavy.get(pk, 0) + need_light.get(pk, 0) for pk in by_pk}}
    result, steps = {}, [0]

    def who(day):
        return result.get(day) or around.get(day)

    def go(i):
        if i == len(todo):
            return True
        steps[0] += 1
        if steps[0] > max_steps:
            return False
        day = todo[i]
        k = kind(day)
        near = (who(day - one), who(day + one))
        options = []
        for pk, n in left[k].items():
            if n <= 0 or pk in near or any(x in apart.get(pk, ()) for x in near if x):
                continue
            mine = [d for d, x in list(result.items()) + list(around.items()) if x == pk]
            wait = min((abs((day - d).days) for d in mine), default=99)
            options.append((min(wait, 8) + rnd.random(), pk))
        for _, pk in sorted(options, reverse=True):
            result[day] = pk
            left[k][pk] -= 1
            if go(i + 1):
                return True
            left[k][pk] += 1
            del result[day]
        return False

    if not go(0):
        return None
    return {d: by_pk[pk] for d, pk in result.items()}


def _on_duty_people(*assignments):
    return {p for a in assignments for p in (a or {}).values() if p is not None}


def _separate(placed, fixed, outside, heavy_days, rnd):
    """Swap duties so that NEVER_ADJACENT doctors are not on neighbouring days.

    Only days of the same kind (weekend / weekday) are swapped, so everybody keeps
    the same number of duties and weekends; a swap never makes two days in a row.
    """
    placed = dict(placed)
    one = datetime.timedelta(days=1)
    apart = _never_adjacent(_on_duty_people(placed, fixed, outside))
    if not apart:
        return placed

    def who(day):
        p = placed.get(day) or fixed.get(day) or (outside or {}).get(day)
        return p.pk if p else None

    def fine(day):
        pk = who(day)
        return all(who(n) != pk and who(n) not in apart.get(pk, ()) for n in (day - one, day + one))

    for _ in range(3):
        bad = [d for d in sorted(placed) if not fine(d)
               and any(who(n) in apart.get(who(d), ()) for n in (d - one, d + one))]
        if not bad:
            break
        for day in bad:
            if fine(day):
                continue
            others = [d for d in placed if d != day and (d in heavy_days) == (day in heavy_days)
                      and placed[d].pk != placed[day].pk]
            for other in rnd.sample(others, len(others)):
                touched = {day, other, day - one, day + one, other - one, other + one} & set(placed)
                before = sum(not fine(d) for d in touched)
                placed[day], placed[other] = placed[other], placed[day]
                if fine(day) and fine(other) and sum(not fine(d) for d in touched) < before:
                    break
                placed[day], placed[other] = placed[other], placed[day]
    return placed


def _spread_penalty(assign):
    per = {}
    for day, person in assign.items():
        if person:
            per.setdefault(person.pk, []).append(day)
    penalty = 0
    apart = _never_adjacent(_on_duty_people(assign))
    one = datetime.timedelta(days=1)
    for day, person in assign.items():
        nxt = assign.get(day + one)
        if person and nxt and nxt.pk in apart.get(person.pk, ()):
            penalty += 1000
    for days in per.values():
        days.sort()
        for a, b in zip(days, days[1:], strict=False):
            gap = (b - a).days
            penalty += {1: 100, 2: 6, 3: 2}.get(gap, 0)
    return penalty


def generate(year, month, seed=None):
    """Proposed duty for every day of the month (nothing is saved here).

    Returns (rows, seed); rows: {date, person, source, locked, saved, changed}.
    """
    if seed is None:
        seed = random.randrange(1, 10 ** 9)
    rnd = random.Random(seed)
    days = month_days(year, month)
    holidays = georgian_holidays(year)
    heavy_days = {d for d in days if is_heavy(d, holidays)}
    saved = {d.date: d for d in RosterDay.objects.filter(
        date__year=year, date__month=month).select_related('person')}

    # the neighbouring months (already saved): no two days in a row across the border,
    # and Misha's gap counts from his last duty of the previous month
    border = datetime.timedelta(days=14)
    outside = {d.date: d.person for d in RosterDay.objects.filter(
        date__gte=days[0] - border, date__lte=days[-1] + border, person__isnull=False).exclude(
        date__year=year, date__month=month).select_related('person')}

    assign, source = {}, {}
    for day, rec in saved.items():
        if rec.locked:
            assign[day], source[day] = rec.person, rec.source
    # doctors on fixed weekdays
    for person in RosterPerson.objects.filter(is_active=True, fixed_weekday__isnull=False):
        for day in fixed_dates(person, year, month):
            if day not in assign:
                assign[day], source[day] = person, RosterDay.FIXED
    # doctors on a few random days (Misha)
    for person in RosterPerson.objects.filter(is_active=True, in_rotation=False, per_month__gt=0):
        taken = [d for d, p in assign.items() if p and p.pk == person.pk]
        free = [d for d in days if d not in assign]
        near = [d for d, p in outside.items() if p.pk == person.pk]
        chosen, _ok = _random_days(person, free, taken, rnd, near)
        for day in chosen:
            assign[day], source[day] = person, RosterDay.FIXED

    # everybody else: equal number of duties and equal weekends
    people = rotation_people()
    free = [d for d in days if d not in assign]
    if people and free:
        hist_total, hist_heavy = _history(people, days[0])
        locked_h = {p.pk: sum(1 for d, x in assign.items() if x and x.pk == p.pk and d in heavy_days) for p in people}
        locked_l = {p.pk: sum(1 for d, x in assign.items() if x and x.pk == p.pk and d not in heavy_days) for p in people}
        free_h = sum(1 for d in free if d in heavy_days)
        free_l = len(free) - free_h
        target_h = _split(free_h + sum(locked_h.values()), people, lambda p: hist_heavy[p.pk] + locked_h[p.pk] * 0, rnd)
        target_l = _split(free_l + sum(locked_l.values()), people,
                          lambda p: hist_total[p.pk] + target_h[p.pk] * 10, rnd)
        need_h = _fit({p.pk: target_h[p.pk] - locked_h[p.pk] for p in people}, free_h)
        need_l = _fit({p.pk: target_l[p.pk] - locked_l[p.pk] for p in people}, free_l)
        best, best_pen = None, None
        for _ in range(120):
            placed = _place(days, heavy_days, people, need_h, need_l, assign, rnd, outside)
            if placed is None:
                continue
            placed = _separate(placed, assign, outside, heavy_days, rnd)
            pen = _spread_penalty({**outside, **assign, **placed})
            if best_pen is None or pen < best_pen:
                best, best_pen = placed, pen
                if pen == 0:
                    break
        if best is None or best_pen >= 1000:
            # the one-pass placement got stuck (or could not keep NEVER_ADJACENT apart):
            # search properly, a few solutions, the best spread; if impossible
            # (e.g. many locked days) relax step by step
            for keep_apart, by_kind in ((True, True), (False, True), (False, False)):
                found = False
                for _ in range(8):
                    placed = _solve(days, heavy_days, people, need_h, need_l, assign, rnd, outside,
                                    keep_apart=keep_apart, by_kind=by_kind)
                    if placed is None:
                        break
                    found = True
                    pen = _spread_penalty({**outside, **assign, **placed})
                    if best_pen is None or pen < best_pen:
                        best, best_pen = placed, pen
                if found or best is not None:
                    break
        if best is None:
            # nothing possible at all: fill in turn, two days in a row allowed
            best = {d: people[i % len(people)] for i, d in enumerate(free)}
        for day, person in best.items():
            assign[day], source[day] = person, RosterDay.AUTO

    rows = []
    for day in days:
        current = saved.get(day)
        person = assign.get(day)
        saved_person = current.person if current else None
        rows.append({'date': day, 'person': person, 'source': source.get(day, RosterDay.AUTO),
                     'locked': bool(current and current.locked), 'saved': saved_person,
                     'changed': bool(current) and saved_person != person})
    return rows, seed


def _fit(need, total):
    """Make the wanted numbers non-negative and add up to ``total``."""
    need = {k: max(0, v) for k, v in need.items()}
    keys = list(need)
    while sum(need.values()) > total and keys:
        k = max(keys, key=lambda x: need[x])
        need[k] -= 1
    while sum(need.values()) < total and keys:
        k = min(keys, key=lambda x: need[x])
        need[k] += 1
    return need


def month_stats(days):
    """Counts per doctor, weekends, holidays, gaps and back-to-back duties."""
    people = {p.pk: {'person': p, 'total': 0, 'weekend': 0, 'holiday': 0, 'heavy': 0}
              for p in RosterPerson.objects.filter(is_active=True)}
    gaps, repeats = [], []
    previous = None
    for d in days:
        person = d['person']
        if not person:
            gaps.append(d['date'])
        else:
            row = people.setdefault(person.pk, {'person': person, 'total': 0, 'weekend': 0, 'holiday': 0, 'heavy': 0})
            row['total'] += 1
            row['weekend'] += d['date'].weekday() >= 5
            row['holiday'] += bool(d.get('holiday'))
            row['heavy'] += d['date'].weekday() >= 5 or bool(d.get('holiday'))
            if previous and previous.pk == person.pk:
                repeats.append(d['date'])
        previous = person
    rows = sorted(people.values(), key=lambda r: (r['person'].order, r['person'].pk))
    rot = [r for r in rows if r['person'].in_rotation and r['person'].is_active]
    spread = lambda key: (max(r[key] for r in rot) - min(r[key] for r in rot)) if rot else 0
    return {'people': rows, 'gaps': gaps, 'repeats': repeats, 'covered': len(days) - len(gaps),
            'spread': spread('total'), 'heavy_spread': spread('heavy')}


def log(user, text, day=None):
    RosterLog.objects.create(user=user, text=text[:255], date=day)


# --------------------------------------------------------------------------
# views (every signed-in doctor can edit; every change is logged)
# --------------------------------------------------------------------------

def _month_context(year, month):
    holidays = georgian_holidays(year)
    saved = {d.date: d for d in RosterDay.objects.filter(
        date__year=year, date__month=month).select_related('person', 'updated_by__profile')}
    fixed_people = list(RosterPerson.objects.filter(is_active=True, fixed_weekday__isnull=False))
    today = timezone.localdate()
    days = []
    for day in month_days(year, month):
        rec = saved.get(day)
        expected = next((p for p in fixed_people if day in fixed_dates(p, year, month)), None)
        days.append({
            'date': day, 'iso': day.isoformat(), 'weekday': WEEKDAYS[day.weekday()],
            'weekend': day.weekday() >= 5, 'holiday': holidays.get(day), 'today': day == today,
            'person': rec.person if rec else None, 'locked': bool(rec and rec.locked),
            'source': rec.source if rec else '', 'saved': bool(rec), 'fixed_for': expected,
            'label': label(day),
        })
    weeks, week = [], [None] * days[0]['date'].weekday()
    for d in days:
        week.append(d)
        if len(week) == 7:
            weeks.append(week)
            week = []
    if week:
        weeks.append(week + [None] * (7 - len(week)))
    return days, weeks


class RosterMonthView(LoginRequiredMixin, generic.TemplateView):
    template_name = 'mdf/roster/month.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        year, month = parse_month(self.request.GET)
        days, weeks = _month_context(year, month)
        py, pm = shift_month(year, month, -1)
        ny, nm = shift_month(year, month, 1)
        people = list(RosterPerson.objects.filter(is_active=True))
        colors = {p.pk: i % 8 for i, p in enumerate(RosterPerson.objects.all())}
        for d in days:
            d['color'] = colors.get(d['person'].pk) if d['person'] else None
        view = self.request.GET.get('view', 'calendar')
        stats = month_stats(days)
        _total, hist_heavy = _history(rotation_people(), datetime.date(year, month, 1))
        for row in stats['people']:
            row['hist_heavy'] = hist_heavy.get(row['person'].pk)
        ctx.update({
            'year': year, 'month': month, 'month_name': MONTHS[month - 1],
            'days': days, 'weeks': weeks, 'weekday_names': WEEKDAYS_SHORT,
            'prev': {'y': py, 'm': pm, 'name': MONTHS[pm - 1]}, 'next': {'y': ny, 'm': nm, 'name': MONTHS[nm - 1]},
            'people': people, 'colors': colors, 'stats': stats,
            'has_saved': any(d['saved'] for d in days), 'view': 'table' if view == 'table' else 'calendar',
            'years': range(year - 1, year + 3), 'months': list(enumerate(MONTHS, start=1)),
            'log': RosterLog.objects.filter(date__year=year, date__month=month).select_related('user__profile')[:15],
        })
        return ctx


class RosterGenerateView(LoginRequiredMixin, generic.TemplateView):
    """Preview of the automatic distribution; saving needs a confirmation."""
    template_name = 'mdf/roster/generate.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        year, month = parse_month(self.request.GET)
        rows, seed = generate(year, month, _seed(self.request.GET))
        holidays = georgian_holidays(year)
        for r in rows:
            r['weekday'] = WEEKDAYS[r['date'].weekday()]
            r['weekend'] = r['date'].weekday() >= 5
            r['holiday'] = holidays.get(r['date'])
        people = rotation_people()
        hist_total, hist_heavy = _history(people, datetime.date(year, month, 1))
        stats = month_stats(rows)
        for row in stats['people']:
            row['hist_heavy'] = hist_heavy.get(row['person'].pk)
        ctx.update({
            'year': year, 'month': month, 'month_name': MONTHS[month - 1], 'rows': rows, 'seed': seed,
            'fingerprint': fingerprint(rows),
            'rotation': people, 'specials': RosterPerson.objects.filter(is_active=True, in_rotation=False),
            'stats': stats,
            'changed': sum(1 for r in rows if r['changed']),
            'kept': sum(1 for r in rows if r['locked']),
            'has_saved': RosterDay.objects.filter(date__year=year, date__month=month).exists(),
        })
        return ctx

    def post(self, request, *args, **kwargs):
        year, month = parse_month(request.POST)
        rows, seed = generate(year, month, _seed(request.POST))      # the same as the preview
        if request.POST.get('fp') and request.POST['fp'] != fingerprint(rows):
            # someone changed a day or the doctors list after this preview was opened
            messages.warning(request, 'სანამ წინასწარ ნახვა ღია იყო, განრიგში ან მორიგეების სიაში რაღაც შეიცვალა. '
                                      'შეამოწმეთ განახლებული განაწილება და დაადასტურეთ ხელახლა.')
            return HttpResponseRedirect(reverse('roster-generate') + f'?y={year}&m={month}&seed={seed}')
        with transaction.atomic():
            for r in rows:
                if r['locked']:
                    continue
                RosterDay.objects.update_or_create(
                    date=r['date'], defaults={'person': r['person'], 'source': r['source'],
                                              'locked': False, 'updated_by': request.user})
            log(request.user, f'{MONTHS[month - 1]} {year}: განრიგი განაწილდა ავტომატურად', datetime.date(year, month, 1))
        messages.success(request, f'{MONTHS[month - 1]} {year} — განრიგი შენახულია.')
        return HttpResponseRedirect(reverse('roster') + f'?y={year}&m={month}')


def fingerprint(rows):
    """Short hash of a distribution: the confirm button saves only what was shown."""
    text = ';'.join(f"{r['date']:%d}:{r['person'].pk if r['person'] else '-'}:{int(bool(r['locked']))}" for r in rows)
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _or(value, default):
    return default if value is None else value


def _small_int(value, low, high):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if low <= n <= high else None


def _seed(data):
    try:
        return int(data.get('seed') or 0) or None
    except ValueError:
        return None


class RosterDayView(LoginRequiredMixin, generic.View):
    """Change one day: another doctor, nobody, lock/unlock, or swap with another day."""

    def post(self, request, day):
        try:
            date = datetime.date.fromisoformat(day)
        except ValueError:
            raise Http404
        rec = RosterDay.objects.filter(date=date).select_related('person').first()
        old = rec.person if rec else None
        action = request.POST.get('action', 'set')

        if action == 'swap':
            try:
                other_date = datetime.date.fromisoformat(request.POST.get('other', ''))
            except ValueError:
                return JsonResponse({'ok': False, 'error': 'აირჩიეთ დღე გასაცვლელად.'}, status=400)
            other = RosterDay.objects.filter(date=other_date).select_related('person').first()
            other_person = other.person if other else None
            with transaction.atomic():
                for d, p in ((date, other_person), (other_date, old)):
                    RosterDay.objects.update_or_create(date=d, defaults={
                        'person': p, 'locked': True, 'source': RosterDay.MANUAL, 'updated_by': request.user})
                log(request.user, f'გაცვლა: {date:%d.%m} {old or "—"} ⇄ {other_date:%d.%m} {other_person or "—"}', date)
            return JsonResponse({'ok': True})

        if action == 'unlock':
            if rec:
                rec.locked = False
                rec.updated_by = request.user
                rec.save()
                log(request.user, f'{date:%d.%m}: გაიხსნა (ავტომატური განაწილება ისევ შეცვლის)', date)
            return JsonResponse({'ok': True})

        person = None
        if request.POST.get('person'):
            person = RosterPerson.objects.filter(pk=request.POST['person']).first()
        RosterDay.objects.update_or_create(date=date, defaults={
            'person': person, 'locked': True, 'source': RosterDay.MANUAL, 'updated_by': request.user})
        log(request.user, f'{date:%d.%m}: {old or "—"} → {person or "არავინ"}', date)
        return JsonResponse({'ok': True})


class RosterPeopleView(LoginRequiredMixin, generic.TemplateView):
    """The doctors: names, rotation order, fixed days."""
    template_name = 'mdf/roster/people.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({'people': RosterPerson.objects.all(), 'weekdays': RosterPerson.WEEKDAYS})
        return ctx

    def post(self, request, *args, **kwargs):
        changed = 0
        for person in RosterPerson.objects.all():
            p = f'p{person.pk}-'
            if p + 'name' not in request.POST:
                continue
            name = ' '.join(request.POST.get(p + 'name', '').split())[:100]
            if not name:
                continue
            weekday = request.POST.get(p + 'weekday', '')
            new = {
                'name': name,
                'order': _or(_small_int(request.POST.get(p + 'order'), 0, 999), person.order),
                'in_rotation': bool(request.POST.get(p + 'rotation')),
                'fixed_weekday': int(weekday) if weekday.isdigit() and int(weekday) < 7 else None,
                'fixed_weeks': ''.join(c for c in request.POST.get(p + 'weeks', '') if c.isdigit() or c == ',')[:20],
                'per_month': _small_int(request.POST.get(p + 'per_month'), 0, 15),
                'min_gap': _small_int(request.POST.get(p + 'min_gap'), 1, 20) or 10,
                'is_active': bool(request.POST.get(p + 'active')),
            }
            if any(getattr(person, k) != v for k, v in new.items()):
                for k, v in new.items():
                    setattr(person, k, v)
                person.save()
                changed += 1
        new_name = ' '.join(request.POST.get('new-name', '').split())[:100]
        if new_name:
            last = RosterPerson.objects.order_by('-order').first()
            RosterPerson.objects.create(name=new_name, order=(last.order + 1) if last else 1, in_rotation=True)
            changed += 1
        if changed:
            log(request.user, f'მორიგეების სია შეიცვალა ({changed})')
            messages.success(request, 'შენახულია. ცვლილება ახალ განაწილებაზე აისახება; შენახული თვეები არ იცვლება.')
        return HttpResponseRedirect(reverse('roster-people'))


class RosterPrintView(LoginRequiredMixin, generic.TemplateView):
    """The clean A4 document: title, month and the duty table only."""
    template_name = 'mdf/roster/print.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        year, month = parse_month(self.request.GET)
        days, _ = _month_context(year, month)
        ctx.update({'year': year, 'month_name': MONTHS[month - 1], 'days': days,
                    'landscape': self.request.GET.get('orient') == 'landscape',
                    'preview': self.request.GET.get('preview') == '1'})
        return ctx
