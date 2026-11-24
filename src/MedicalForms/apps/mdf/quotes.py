"""Quote of the day ("დღის ციტატა") on the prescription page.

One quote a day, in a fixed order: with 365 quotes none repeats within a year.
Quotes are added on /quotes/ — one by one, or many at once in the format
    N | ციტატა | ავტორი | წყაროს ბმული
(the format of the list the clinic prepares).
"""
import datetime
import re

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from .duty import WEEKDAYS, strip_astral
from .models import Quote, RosterDay

MAX_LEN = 400
MONTHS_GEN = ['იანვარი', 'თებერვალი', 'მარტი', 'აპრილი', 'მაისი', 'ივნისი',
              'ივლისი', 'აგვისტო', 'სექტემბერი', 'ოქტომბერი', 'ნოემბერი', 'დეკემბერი']


def quote_of_the_day(day=None):
    day = day or timezone.localdate()
    ids = list(Quote.objects.filter(is_active=True).order_by('id').values_list('id', flat=True))
    if not ids:
        return None
    return Quote.objects.get(pk=ids[day.toordinal() % len(ids)])


# A duty lasts from 09:00 of its day until 09:00 of the next day.
SHIFT_START_HOUR = 9


def today_panel():
    """For the right side: today's date and who is on duty right now (from the roster)."""
    now = timezone.localtime()
    day = now.date()
    shift = day - datetime.timedelta(days=1) if now.hour < SHIFT_START_HOUR else day
    rec = RosterDay.objects.filter(date=shift).select_related('person').first()
    return {'date': day, 'weekday': WEEKDAYS[day.weekday()], 'month': MONTHS_GEN[day.month - 1],
            'duty': rec.person if rec and rec.person else None}


def _clean(value, limit):
    return ' '.join(strip_astral(value or '').replace('„', '').replace('“', '').split()).strip(' "\'')[:limit]


def parse_lines(text):
    """'N | ციტატა | ავტორი | ბმული' lines -> [(text, author, url)], plus the lines that were not understood."""
    found, bad = [], []
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line or set(line) <= set('|-:= '):
            continue
        parts = [p.strip() for p in line.strip('|').split('|')]
        if parts and re.fullmatch(r'\d*\.?', parts[0] or ''):
            parts = parts[1:]                                   # the number (or an empty first cell)
        if len(parts) < 2:
            bad.append(raw)
            continue
        quote, author = _clean(parts[0], MAX_LEN), _clean(parts[1], 150)
        url = parts[2].strip() if len(parts) > 2 and parts[2].strip().startswith('http') else ''
        if not quote or not author or quote.lower() in ('ციტატა',):
            bad.append(raw)
            continue
        found.append((quote, author, url[:500]))
    return found, bad


def _key(text):
    return re.sub(r'[^\w]+', '', text.lower())


class QuotesView(LoginRequiredMixin, generic.TemplateView):
    template_name = 'mdf/quotes.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        quotes = list(Quote.objects.all())
        today = quote_of_the_day()
        long = [q for q in quotes if len(q.text) > 220]
        authors = {}
        for q in quotes:
            authors[q.author] = authors.get(q.author, 0) + 1
        ctx.update({'quotes': quotes, 'today': today, 'active': sum(q.is_active for q in quotes),
                    'long': len(long), 'top_authors': sorted(authors.items(), key=lambda x: -x[1])[:5]})
        return ctx

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')
        if action == 'import':
            found, bad = parse_lines(request.POST.get('lines'))
            existing = {_key(t) for t in Quote.objects.values_list('text', flat=True)}
            added = dupes = 0
            for text, author, url in found:
                if _key(text) in existing:
                    dupes += 1
                    continue
                Quote.objects.create(text=text, author=author, source=url)
                existing.add(_key(text))
                added += 1
            msg = f'დაემატა {added} ციტატა.'
            if dupes:
                msg += f' {dupes} უკვე იყო სიაში (გამოტოვდა).'
            if bad:
                msg += f' {len(bad)} სტრიქონი ვერ გავარჩიე: „{bad[0][:60]}…“'
            (messages.success if added else messages.warning)(request, msg)
        elif action == 'save':
            q = get_object_or_404(Quote, pk=request.POST.get('id'))
            text, author = _clean(request.POST.get('text'), MAX_LEN), _clean(request.POST.get('author'), 150)
            if text and author:
                q.text, q.author = text, author
                q.source = (request.POST.get('source') or '').strip()[:500]
                q.is_active = bool(request.POST.get('is_active'))
                q.save()
                messages.success(request, 'შენახულია.')
        elif action == 'delete':
            Quote.objects.filter(pk=request.POST.get('id')).delete()
            messages.success(request, 'ციტატა წაიშალა.')
        return HttpResponseRedirect(reverse('quotes'))
