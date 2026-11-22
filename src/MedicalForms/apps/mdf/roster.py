"""Duty roster ("მორიგეობის განრიგი"): one surgeon on duty per day.

Rules (set by the department):
* doctors with a fixed rule are on their fixed days (e.g. Misha: 2nd and 4th
  Friday of the month);
* everybody else takes turns in a fixed order, one day each;
* on a fixed day the rotation pauses: whoever's turn it was goes the next day,
  so nobody loses a duty;
* the rotation continues from month to month (who starts a month follows
  the last one of the previous month), so over time everybody gets the same
  number of duties;
* changes made by hand are locked: generating again keeps them (the rotation
  pauses on them as well).

Ultrasound on-call is a later stage.
"""
import calendar
import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from .duty import MONTHS, WEEKDAYS
from .models import RosterDay, RosterLog, RosterPerson

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


def default_start(year, month, rotation):
    """Who starts the month: the one after the last rotation duty before it."""
    if not rotation:
        return None
    first = datetime.date(year, month, 1)
    last = (RosterDay.objects.filter(date__lt=first, source=RosterDay.AUTO, person__in=rotation)
            .order_by('-date').select_related('person').first())
    if not last:
        return rotation[0]
    ids = [p.pk for p in rotation]
    return rotation[(ids.index(last.person_id) + 1) % len(rotation)]


def generate(year, month, start=None):
    """Proposed duty for every day of the month (nothing is saved here).

    Returns rows: {date, person, source, locked, saved (person saved now), changed}.
    """
    rotation = rotation_people()
    fixed = {}
    for person in RosterPerson.objects.filter(is_active=True, fixed_weekday__isnull=False):
        for day in fixed_dates(person, year, month):
            fixed.setdefault(day, person)
    saved = {d.date: d for d in RosterDay.objects.filter(
        date__year=year, date__month=month).select_related('person')}
    start = start if start in rotation else default_start(year, month, rotation)
    pointer = rotation.index(start) if start else 0

    rows = []
    for day in month_days(year, month):
        current = saved.get(day)
        if current and current.locked:
            person, source, locked = current.person, current.source, True       # kept as it is
        elif day in fixed:
            person, source, locked = fixed[day], RosterDay.FIXED, False          # the rotation pauses
        elif rotation:
            person, source, locked = rotation[pointer % len(rotation)], RosterDay.AUTO, False
            pointer += 1
        else:
            person, source, locked = None, RosterDay.AUTO, False
        saved_person = current.person if current else None
        rows.append({'date': day, 'person': person, 'source': source, 'locked': locked,
                     'saved': saved_person, 'changed': bool(current) and saved_person != person})
    return rows, start


def month_stats(days):
    """Counts per doctor, weekends, holidays, gaps and back-to-back duties."""
    people = {p.pk: {'person': p, 'total': 0, 'weekend': 0, 'holiday': 0}
              for p in RosterPerson.objects.filter(is_active=True)}
    gaps, repeats = [], []
    previous = None
    for d in days:
        person = d['person']
        if not person:
            gaps.append(d['date'])
        else:
            row = people.setdefault(person.pk, {'person': person, 'total': 0, 'weekend': 0, 'holiday': 0})
            row['total'] += 1
            row['weekend'] += d['date'].weekday() >= 5
            row['holiday'] += bool(d.get('holiday'))
            if previous and previous.pk == person.pk:
                repeats.append(d['date'])
        previous = person
    rows = sorted(people.values(), key=lambda r: (r['person'].order, r['person'].pk))
    rotation_totals = [r['total'] for r in rows if r['person'].in_rotation and r['person'].is_active]
    return {'people': rows, 'gaps': gaps, 'repeats': repeats, 'covered': len(days) - len(gaps),
            'spread': (max(rotation_totals) - min(rotation_totals)) if rotation_totals else 0}


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
        ctx.update({
            'year': year, 'month': month, 'month_name': MONTHS[month - 1],
            'days': days, 'weeks': weeks, 'weekday_names': WEEKDAYS_SHORT,
            'prev': {'y': py, 'm': pm, 'name': MONTHS[pm - 1]}, 'next': {'y': ny, 'm': nm, 'name': MONTHS[nm - 1]},
            'people': people, 'colors': colors, 'stats': month_stats(days),
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
        rotation = rotation_people()
        start = next((p for p in rotation if str(p.pk) == self.request.GET.get('start')), None)
        rows, start = generate(year, month, start)
        holidays = georgian_holidays(year)
        for r in rows:
            r['weekday'] = WEEKDAYS[r['date'].weekday()]
            r['weekend'] = r['date'].weekday() >= 5
            r['holiday'] = holidays.get(r['date'])
        ctx.update({
            'year': year, 'month': month, 'month_name': MONTHS[month - 1], 'rows': rows,
            'rotation': rotation, 'start': start, 'stats': month_stats(rows),
            'changed': sum(1 for r in rows if r['changed']),
            'kept': sum(1 for r in rows if r['locked']),
            'has_saved': RosterDay.objects.filter(date__year=year, date__month=month).exists(),
        })
        return ctx

    def post(self, request, *args, **kwargs):
        year, month = parse_month(request.POST)
        rotation = rotation_people()
        start = next((p for p in rotation if str(p.pk) == request.POST.get('start')), None)
        rows, start = generate(year, month, start)
        with transaction.atomic():
            for r in rows:
                if r['locked']:
                    continue
                RosterDay.objects.update_or_create(
                    date=r['date'], defaults={'person': r['person'], 'source': r['source'],
                                              'locked': False, 'updated_by': request.user})
            log(request.user, f'{MONTHS[month - 1]} {year}: განრიგი განაწილდა ავტომატურად'
                              + (f' (იწყებს {start.name})' if start else ''), datetime.date(year, month, 1))
        messages.success(request, f'{MONTHS[month - 1]} {year} — განრიგი შენახულია.')
        return HttpResponseRedirect(reverse('roster') + f'?y={year}&m={month}')


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
                'order': int(request.POST.get(p + 'order') or person.order or 0),
                'in_rotation': bool(request.POST.get(p + 'rotation')),
                'fixed_weekday': int(weekday) if weekday.isdigit() and int(weekday) < 7 else None,
                'fixed_weeks': ''.join(c for c in request.POST.get(p + 'weeks', '') if c.isdigit() or c == ',')[:20],
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
