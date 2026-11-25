"""On-duty doctor's notes ("მორიგე ექიმის ჩანაწერი").

A simple electronic duty journal: free text per patient, grouped by duty
date, editable later, printed on A4 from the browser.

Duty date rule (Asia/Tbilisi time):
    00:00–16:59 -> the same calendar day
    17:00–23:59 -> the next calendar day
"""
import datetime
import re
from html import escape
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from .models import DutyNote

TBILISI = ZoneInfo('Asia/Tbilisi')
DUTY_CHANGE_HOUR = 17

WEEKDAYS = ['ორშაბათი', 'სამშაბათი', 'ოთხშაბათი', 'ხუთშაბათი', 'პარასკევი', 'შაბათი', 'კვირა']
MONTHS = ['იანვარი', 'თებერვალი', 'მარტი', 'აპრილი', 'მაისი', 'ივნისი',
          'ივლისი', 'აგვისტო', 'სექტემბერი', 'ოქტომბერი', 'ნოემბერი', 'დეკემბერი']


def duty_date_for(moment=None):
    """The duty date a moment belongs to (17:00 and later -> next day)."""
    moment = moment or timezone.now()
    if timezone.is_naive(moment):
        moment = moment.replace(tzinfo=TBILISI)
    local = moment.astimezone(TBILISI)
    day = local.date()
    if local.hour >= DUTY_CHANGE_HOUR:
        day += datetime.timedelta(days=1)
    return day


def day_label(day):
    return {
        'date': day,
        'iso': day.isoformat(),
        'short': f'{day:%d.%m.%Y}',
        'weekday': WEEKDAYS[day.weekday()],
        'month': MONTHS[day.month - 1],
    }


# --------------------------------------------------------------------------
# Safe HTML: the editor allows only simple formatting
# --------------------------------------------------------------------------

ALLOWED_TAGS = {'p', 'div', 'br', 'b', 'strong', 'i', 'em', 'u', 'ul', 'ol', 'li'}
DROP_WITH_CONTENT = {'script', 'style', 'iframe', 'object', 'embed', 'template', 'head', 'title'}


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self.skip += 1
        elif not self.skip and tag in ALLOWED_TAGS:
            self.out.append(f'<{tag}>')          # every attribute is dropped

    def handle_startendtag(self, tag, attrs):
        if not self.skip and tag == 'br':
            self.out.append('<br>')

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
        elif not self.skip and tag in ALLOWED_TAGS and tag != 'br':
            self.out.append(f'</{tag}>')

    def handle_data(self, data):
        if not self.skip:
            self.out.append(escape(data, quote=False))


# Emoji and other 4-byte characters: an older MySQL table (utf8mb3) refuses them.
# Also strip C0 control/null bytes and Unicode Bidi overrides/isolates (OWASP ASVS L2).
_ASTRAL = re.compile('[\U00010000-\U0010FFFF]')
_CTRL_AND_BIDI = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069]')


def strip_astral(text):
    return _CTRL_AND_BIDI.sub('', _ASTRAL.sub('', text or ''))


def clean_html(html):
    """Keep only simple formatting tags (no attributes, no scripts)."""
    cleaner = _Cleaner()
    cleaner.feed(strip_astral(html))
    cleaner.close()
    return ''.join(cleaner.out).strip()


def plain_text(html):
    """Text of the note without tags (for previews and search)."""
    text = re.sub(r'<(br|p|/p|div|/div|li|/li)>', '\n', html or '')
    text = re.sub(r'<[^>]+>', '', text)
    from html import unescape
    return re.sub(r'\n{2,}', '\n', unescape(text)).strip()


def is_empty(html):
    return not plain_text(html)


# --------------------------------------------------------------------------
# Views (login required: notes contain patients' personal data)
# --------------------------------------------------------------------------

def _parse_day(value):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.datetime.strptime((value or '').strip(), '%d.%m.%Y').date()
    except ValueError:
        return None


class DutyIndexView(LoginRequiredMixin, generic.TemplateView):
    """All duty dates (cards), newest first; jump to a date; text search."""
    template_name = 'mdf/duty/index.html'
    per_page = 48

    def get(self, request, *args, **kwargs):
        jump = request.GET.get('date')
        if jump:
            day = _parse_day(jump)
            if day:
                return HttpResponseRedirect(reverse('duty-day', args=[day.isoformat()]))
            messages.error(request, 'თარიღი ვერ გაირჩა. ჩაწერეთ ასე: 21.11.2026')
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        order = self.request.GET.get('order', 'new')
        days = (DutyNote.objects.values('duty_date')
                .annotate(count=Count('id'), last=Max('updated_at'))
                .order_by('duty_date' if order == 'old' else '-duty_date'))
        page = Paginator(days, self.per_page).get_page(self.request.GET.get('page'))
        current = duty_date_for()
        cards = []
        for row in page.object_list:
            card = day_label(row['duty_date'])
            card.update(count=row['count'], last=row['last'], is_current=row['duty_date'] == current)
            cards.append(card)

        q = self.request.GET.get('q', '').strip()
        found = []
        if q:
            found = list(DutyNote.objects.filter(Q(patient__icontains=q) | Q(body__icontains=q))
                         .select_related('created_by').order_by('-duty_date', '-created_at')[:100])
            for note in found:
                note.preview = plain_text(note.body)[:220]
        ctx.update({
            'cards': cards, 'page_obj': page, 'order': order, 'q': q, 'found': found,
            'current': day_label(current),
            'current_count': DutyNote.objects.filter(duty_date=current).count(),
        })
        return ctx


class DutyDayView(LoginRequiredMixin, generic.TemplateView):
    """One duty date: all patient notes of that duty."""
    template_name = 'mdf/duty/day.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        day = _parse_day(kwargs['day'])
        if not day:
            raise Http404
        notes = list(DutyNote.objects.filter(duty_date=day).select_related('created_by', 'updated_by'))
        for note in notes:
            note.preview = plain_text(note.body)[:400]
        prev_day = DutyNote.objects.filter(duty_date__lt=day).order_by('-duty_date').values_list('duty_date', flat=True).first()
        next_day = DutyNote.objects.filter(duty_date__gt=day).order_by('duty_date').values_list('duty_date', flat=True).first()
        current = duty_date_for()
        ctx.update({
            'day': day_label(day), 'notes': notes, 'is_current': day == current,
            'current': day_label(current),
            'prev_day': day_label(prev_day) if prev_day else None,
            'next_day': day_label(next_day) if next_day else None,
        })
        return ctx


class DutyNoteEditView(LoginRequiredMixin, generic.TemplateView):
    """The A4-like editor: new note (no pk) or an existing one."""
    template_name = 'mdf/duty/edit.html'

    def get_note(self):
        pk = self.kwargs.get('pk')
        return get_object_or_404(DutyNote, pk=pk) if pk else None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        note = kwargs.get('note', self.get_note())
        ctx.update({
            'note': note,
            'day': day_label(note.duty_date if note else duty_date_for()),
            'now': timezone.localtime(),
            'patient': kwargs.get('patient', note.patient if note else ''),
            'body': kwargs.get('body', note.body if note else ''),
        })
        return ctx

    def post(self, request, *args, **kwargs):
        note = self.get_note()
        patient = strip_astral(request.POST.get('patient') or '').strip()[:255]
        body = clean_html(request.POST.get('body', ''))
        if not patient and is_empty(body):
            messages.error(request, 'ჩანაწერი ცარიელია — დაწერეთ ტექსტი ან მიუთითეთ პაციენტი.')
            return self.render_to_response(self.get_context_data(note=note, patient=patient, body=body))
        if note is None:
            note = DutyNote(created_by=request.user)       # duty_date is set on first save
        note.patient = patient
        note.body = body
        note.updated_by = request.user
        note.save()
        messages.success(request, f'ჩანაწერი შენახულია — მორიგეობა {note.duty_date:%d.%m.%Y}.')
        url = reverse('duty-note', args=[note.pk]) + '?saved=1'
        if request.POST.get('then') == 'print':
            url += '&print=1'
        return HttpResponseRedirect(url)


class DutyPrintView(LoginRequiredMixin, generic.TemplateView):
    """A4 print page: one note (pk) or every note of a duty date (day)."""
    template_name = 'mdf/duty/print.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if 'pk' in kwargs:
            notes = [get_object_or_404(DutyNote.objects.select_related('created_by'), pk=kwargs['pk'])]
            day = notes[0].duty_date
        else:
            day = _parse_day(kwargs['day'])
            if not day:
                raise Http404
            notes = list(DutyNote.objects.filter(duty_date=day).select_related('created_by'))
            if not notes:
                raise Http404('ამ თარიღზე ჩანაწერი არ არის.')
        for note in notes:
            note.author_name = _person_name(note.created_by)
        ctx.update({'notes': notes, 'day': day_label(day)})
        return ctx


def _person_name(user):
    if not user:
        return ''
    profile = getattr(user, 'profile', None)
    try:
        return profile.doctor_name if profile else (user.get_full_name() or user.username)
    except Exception:
        return user.get_full_name() or user.username
