"""Patients board ("პაციენტების დაფა").

The whiteboard of the department, digitised: patients now in the ICU and in
the ward. Text only:

* the attending doctor writes comments and tasks for the duty doctor;
* the duty doctor writes the patient's state and marks tasks as done;
* every text stays with its patient, with date, time and author;
* at the end of a duty a report is made (every patient's state). Reports are
  kept for a month and then deleted automatically.

A duty uses the same rule as before (Tbilisi time): duty date D runs from
D-1 17:00 to D 16:59.
"""
import datetime
import re

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Max, Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from .duty import DUTY_CHANGE_HOUR, TBILISI, _parse_day, day_label, duty_date_for, strip_astral
from .models import BoardEntry, BoardPatient, BoardSeen, DutyNote, DutyReport

TEXT_LIMIT = 4000
UNIT_ORDER = [BoardPatient.ICU, BoardPatient.WARD]
UNIT_LABELS = dict(BoardPatient.UNITS)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def duty_window(day):
    """[start, end) of duty date ``day``: from the evening before, 17:00, to 17:00."""
    end = datetime.datetime.combine(day, datetime.time(DUTY_CHANGE_HOUR), tzinfo=TBILISI)
    return end - datetime.timedelta(days=1), end


def clean_text(value, limit=TEXT_LIMIT):
    """Plain text: no emoji (old MySQL tables), unified line ends, trimmed."""
    text = strip_astral(value or '').replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()[:limit]


def person_name(user):
    """Short name for the screen (ინა, რამაზი …)."""
    if not user:
        return ''
    profile = getattr(user, 'profile', None)
    try:
        if profile and profile.display_name:
            return profile.display_name
    except Exception:
        pass
    return user.get_full_name() or user.username


def person_full_name(user):
    """Full name for the printed report."""
    if not user:
        return ''
    try:
        profile = user.profile
        return profile.doctor_name
    except Exception:
        return user.get_full_name() or user.username


def bed_key(patient):
    """Natural order of beds: 2 < 10 < 10ა; patients without a bed go last."""
    parts = re.split(r'(\d+)', patient.bed or '')
    key = [(0, int(p)) if p.isdigit() else (1, p.lower()) for p in parts if p]
    return (not key, key, patient.name.lower())


def doctors():
    """People who can be chosen as the attending doctor."""
    users = (get_user_model().objects.filter(is_active=True)
             .select_related('profile').order_by('profile__order', 'username'))
    return [(u.pk, person_name(u)) for u in users if hasattr(u, 'profile')] or \
           [(u.pk, person_name(u)) for u in users]


def add_event(patient, user, text, when=None):
    return BoardEntry.objects.create(patient=patient, kind=BoardEntry.EVENT, text=text,
                                     created_by=user, created_at=when or timezone.now())


def purge_old_reports():
    """Reports older than a month are deleted (the request: keep one month)."""
    limit = duty_date_for() - datetime.timedelta(days=DutyReport.KEEP_DAYS)
    DutyReport.objects.filter(duty_date__lt=limit).delete()


def back_to(request, default):
    """Return to the page the form came from (only our own pages)."""
    nxt = request.POST.get('next') or ''
    return nxt if nxt.startswith('/') and not nxt.startswith('//') else default


USER_FIELDS = ('created_by__profile', 'updated_by__profile', 'done_by__profile')


# --------------------------------------------------------------------------
# board
# --------------------------------------------------------------------------

def seen_baseline():
    """Without a "seen" mark, only texts of the last two duties count as new."""
    return duty_window(duty_date_for())[0] - datetime.timedelta(days=1)


def board_data(user, mine=False):
    """Active patients grouped by unit, with open tasks, last state and "new" counts."""
    day = duty_date_for()
    start, _end = duty_window(day)
    patients = BoardPatient.objects.filter(discharged_at__isnull=True).select_related('doctor__profile')
    if mine:
        patients = patients.filter(doctor=user)
    patients = sorted(patients, key=bed_key)
    ids = [p.pk for p in patients]

    open_tasks = {}
    for task in (BoardEntry.objects.filter(patient_id__in=ids, kind=BoardEntry.TASK, done_at__isnull=True)
                 .select_related('created_by__profile').order_by('created_at', 'id')):
        open_tasks.setdefault(task.patient_id, []).append(task)
    # only the newest state of each patient is read (the board reloads often)
    last_ids = (BoardEntry.objects.filter(patient_id__in=ids, kind=BoardEntry.STATUS)
                .values('patient_id').annotate(last=Max('id')).values_list('last', flat=True))
    last_status = {e.patient_id: e for e in
                   BoardEntry.objects.filter(id__in=list(last_ids)).select_related('created_by__profile')}

    seen = dict(BoardSeen.objects.filter(user=user, patient_id__in=ids).values_list('patient_id', 'seen_at'))
    baseline = seen_baseline()
    new_counts = {}
    for pid, created in (BoardEntry.objects.filter(patient_id__in=ids, created_at__gte=min([baseline, *seen.values()]))
                         .exclude(created_by=user).values_list('patient_id', 'created_at')):
        if created > seen.get(pid, baseline):
            new_counts[pid] = new_counts.get(pid, 0) + 1

    columns = []
    for unit in UNIT_ORDER:
        cards = []
        for p in patients:
            if p.unit != unit:
                continue
            p.open_tasks = open_tasks.get(p.pk, [])
            p.last_status = last_status.get(p.pk)
            p.status_this_duty = bool(p.last_status and p.last_status.created_at >= start)
            p.new_count = new_counts.get(p.pk, 0)
            cards.append(p)
        columns.append({'unit': unit, 'label': UNIT_LABELS[unit], 'patients': cards,
                        'tasks': sum(len(p.open_tasks) for p in cards),
                        'missing': sum(1 for p in cards if not p.status_this_duty)})
    return {'columns': columns, 'day': day_label(day), 'total': len(patients),
            'open_total': sum(c['tasks'] for c in columns), 'new_total': sum(new_counts.values())}


class BoardView(LoginRequiredMixin, generic.TemplateView):
    """The whiteboard: two columns (ICU, ward), one card per patient."""
    template_name = 'mdf/board/board.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        purge_old_reports()
        mine = self.request.GET.get('show') == 'mine'
        ctx.update(board_data(self.request.user, mine))
        ctx.update({'mine': mine, 'old_notes': DutyNote.objects.count(),
                    'reports': DutyReport.objects.count()})
        return ctx


class BoardPrintView(LoginRequiredMixin, generic.TemplateView):
    """The whole board on A4 (landscape) for the morning meeting."""
    template_name = 'mdf/board/board_print.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(board_data(self.request.user, self.request.GET.get('show') == 'mine'))
        ctx['printed_at'] = timezone.localtime()
        return ctx


class SeenAllView(LoginRequiredMixin, generic.View):
    """Mark every patient on the board as seen by me."""

    def post(self, request):
        now = timezone.now()
        for pid in BoardPatient.objects.filter(discharged_at__isnull=True).values_list('id', flat=True):
            mark_seen(request.user, pid, now)
        return HttpResponseRedirect(back_to(request, reverse('board')))


def mark_seen(user, patient_id, when=None):
    """Remember when ``user`` last looked at a patient; return the previous time."""
    when = when or timezone.now()
    row = BoardSeen.objects.filter(user=user, patient_id=patient_id).first()
    if row:
        previous = row.seen_at
        row.seen_at = when
        row.save(update_fields=['seen_at'])
        return previous
    BoardSeen.objects.create(user=user, patient_id=patient_id, seen_at=when)
    return None


class PatientFormView(LoginRequiredMixin, generic.TemplateView):
    """Add a patient to the board or change bed / diagnosis / doctor / unit."""
    template_name = 'mdf/board/patient_form.html'

    def get_patient(self):
        pk = self.kwargs.get('pk')
        return get_object_or_404(BoardPatient, pk=pk) if pk else None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        patient = kwargs.get('patient', self.get_patient())
        values = kwargs.get('values') or (
            {'name': patient.name, 'bed': patient.bed, 'diagnosis': patient.diagnosis,
             'unit': patient.unit, 'doctor': patient.doctor_id} if patient else
            {'name': '', 'bed': '', 'diagnosis': '',
             'unit': self.request.GET.get('unit') if self.request.GET.get('unit') in UNIT_LABELS else BoardPatient.WARD,
             'doctor': self.request.user.pk})
        ctx.update({'patient': patient, 'values': values, 'units': BoardPatient.UNITS, 'doctors': doctors()})
        return ctx

    def post(self, request, *args, **kwargs):
        patient = self.get_patient()
        values = {
            'name': clean_text(request.POST.get('name'), 150).replace('\n', ' '),
            'bed': clean_text(request.POST.get('bed'), 20).replace('\n', ' '),
            'diagnosis': clean_text(request.POST.get('diagnosis'), 255).replace('\n', ' '),
            'unit': request.POST.get('unit') if request.POST.get('unit') in UNIT_LABELS else BoardPatient.WARD,
        }
        try:
            doctor = get_user_model().objects.filter(pk=int(request.POST.get('doctor') or 0)).first()
        except ValueError:
            doctor = None
        values['doctor'] = doctor.pk if doctor else None
        if not values['name']:
            messages.error(request, 'ჩაწერეთ პაციენტის სახელი და გვარი.')
            return self.render_to_response(self.get_context_data(patient=patient, values=values))

        if patient is None:
            patient = BoardPatient.objects.create(
                name=values['name'], bed=values['bed'], diagnosis=values['diagnosis'],
                unit=values['unit'], doctor=doctor, created_by=request.user,
                admitted_at=timezone.now())
            add_event(patient, request.user, f'დაემატა დაფაზე — {UNIT_LABELS[patient.unit]}')
            messages.success(request, f'{patient.name} დაემატა დაფაზე.')
        else:
            old_unit = patient.unit
            patient.name, patient.bed, patient.diagnosis = values['name'], values['bed'], values['diagnosis']
            patient.unit, patient.doctor = values['unit'], doctor
            patient.save()
            if old_unit != patient.unit:
                add_event(patient, request.user,
                          f'გადაყვანილია: {UNIT_LABELS[old_unit]} → {UNIT_LABELS[patient.unit]}')
            messages.success(request, 'მონაცემები შეინახა.')
        return HttpResponseRedirect(reverse('board-patient', args=[patient.pk]))


class PatientView(LoginRequiredMixin, generic.TemplateView):
    """One patient: write state / task / comment; history of everything written."""
    template_name = 'mdf/board/patient.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        patient = get_object_or_404(BoardPatient.objects.select_related('doctor__profile'), pk=kwargs['pk'])
        entries = list(patient.entries.select_related(*USER_FIELDS))
        previous = mark_seen(self.request.user, patient.pk) or seen_baseline()
        for entry in entries:
            entry.is_new = entry.created_at > previous and entry.created_by_id != self.request.user.pk
        groups = []
        for entry in entries:
            day = duty_date_for(entry.created_at)
            if not groups or groups[-1]['day']['date'] != day:
                groups.append({'day': day_label(day), 'entries': []})
            groups[-1]['entries'].append(entry)
        open_tasks = [e for e in reversed(entries) if e.kind == BoardEntry.TASK and not e.done_at]
        done_tasks = [e for e in entries if e.kind == BoardEntry.TASK and e.done_at][:5]

        kind = self.request.GET.get('kind')
        if kind not in (BoardEntry.STATUS, BoardEntry.TASK, BoardEntry.COMMENT):
            # the attending doctor usually gives tasks, everybody else writes the state
            kind = BoardEntry.TASK if patient.doctor_id == self.request.user.pk else BoardEntry.STATUS
        ctx.update({
            'patient': patient, 'groups': groups, 'open_tasks': open_tasks, 'done_tasks': done_tasks,
            'kind': kind, 'day': day_label(duty_date_for()),
            'is_attending': patient.doctor_id == self.request.user.pk,
        })
        return ctx


class EntryAddView(LoginRequiredMixin, generic.View):

    def post(self, request, pk):
        patient = get_object_or_404(BoardPatient, pk=pk)
        kind = request.POST.get('kind')
        if kind not in (BoardEntry.STATUS, BoardEntry.TASK, BoardEntry.COMMENT):
            kind = BoardEntry.STATUS
        text = clean_text(request.POST.get('text'))
        url = reverse('board-patient', args=[patient.pk]) + f'?kind={kind}'
        if not patient.is_active:
            messages.error(request, 'პაციენტი გაწერილია — ჯერ დააბრუნეთ დაფაზე.')
        elif not text:
            messages.error(request, 'ტექსტი ცარიელია.')
        else:
            BoardEntry.objects.create(patient=patient, kind=kind, text=text, created_by=request.user,
                                      created_at=timezone.now())
            patient.save(update_fields=['updated_at'])
            messages.success(request, {BoardEntry.STATUS: 'მდგომარეობა ჩაიწერა.',
                                       BoardEntry.TASK: 'დავალება მიეცა მორიგეს.',
                                       BoardEntry.COMMENT: 'კომენტარი ჩაიწერა.'}[kind])
        return HttpResponseRedirect(back_to(request, url))


class EntryEditView(LoginRequiredMixin, generic.View):
    """Update a text. Only its author can change it; the change time is kept."""

    def post(self, request, pk):
        entry = get_object_or_404(BoardEntry, pk=pk)
        url = reverse('board-patient', args=[entry.patient_id]) + f'#e{entry.pk}'
        if entry.kind == BoardEntry.EVENT or entry.created_by_id != request.user.pk:
            messages.error(request, 'ჩანაწერის შეცვლა შეუძლია მხოლოდ მის ავტორს.')
            return HttpResponseRedirect(url)
        text = clean_text(request.POST.get('text'))
        if not text:
            messages.error(request, 'ტექსტი ცარიელია — ძველი ტექსტი დარჩა.')
        elif text != entry.text:
            entry.text, entry.updated_at, entry.updated_by = text, timezone.now(), request.user
            entry.save()
            messages.success(request, 'ჩანაწერი განახლდა.')
        return HttpResponseRedirect(url)


class TaskDoneView(LoginRequiredMixin, generic.View):
    """Mark a task as done (or back to open, if clicked by mistake)."""

    def post(self, request, pk):
        task = get_object_or_404(BoardEntry, pk=pk, kind=BoardEntry.TASK)
        if request.POST.get('undo'):
            task.done_at = task.done_by = None
        elif not task.done_at:
            task.done_at, task.done_by = timezone.now(), request.user
        task.save()
        return HttpResponseRedirect(back_to(request, reverse('board-patient', args=[task.patient_id])))


class PatientMoveView(LoginRequiredMixin, generic.View):
    """ICU <-> ward, discharge, back on the board."""

    def post(self, request, pk, action):
        patient = get_object_or_404(BoardPatient, pk=pk)
        if action == 'transfer' and patient.is_active:
            old = patient.unit
            patient.unit = BoardPatient.WARD if old == BoardPatient.ICU else BoardPatient.ICU
            patient.save()
            add_event(patient, request.user, f'გადაყვანილია: {UNIT_LABELS[old]} → {UNIT_LABELS[patient.unit]}')
            messages.success(request, f'{patient.name} — {UNIT_LABELS[patient.unit]}.')
        elif action == 'discharge' and patient.is_active:
            patient.discharged_at = timezone.now()
            patient.save()
            add_event(patient, request.user, 'გაწერილია')
            messages.success(request, f'{patient.name} გაწერილია და მოიხსნა დაფიდან. ისტორია ინახება „გაწერილებში“.')
            return HttpResponseRedirect(reverse('board'))
        elif action == 'restore' and not patient.is_active:
            patient.discharged_at = None
            patient.save()
            add_event(patient, request.user, f'დაბრუნდა დაფაზე — {UNIT_LABELS[patient.unit]}')
            messages.success(request, f'{patient.name} ისევ დაფაზეა.')
        return HttpResponseRedirect(reverse('board-patient', args=[patient.pk]))


class DischargedView(LoginRequiredMixin, generic.ListView):
    template_name = 'mdf/board/discharged.html'
    context_object_name = 'patients'
    paginate_by = 50

    def get_queryset(self):
        qs = (BoardPatient.objects.filter(discharged_at__isnull=False)
              .select_related('doctor__profile').annotate(n=Count('entries')).order_by('-discharged_at'))
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(diagnosis__icontains=q) | Q(bed__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['q'] = self.request.GET.get('q', '').strip()
        return ctx


# --------------------------------------------------------------------------
# duty report
# --------------------------------------------------------------------------

def _fmt(moment):
    return timezone.localtime(moment, TBILISI).strftime('%d.%m %H:%M') if moment else ''


def build_report(day, user=None):
    """Every patient of duty ``day`` with what was written during that duty.

    Plain data (strings only), so a saved report never changes afterwards.
    """
    start, end = duty_window(day)
    patients = (BoardPatient.objects.filter(admitted_at__lt=end)
                .filter(Q(discharged_at__isnull=True) | Q(discharged_at__gte=start))
                .select_related('doctor__profile'))
    patients = sorted(patients, key=bed_key)
    ids = [p.pk for p in patients]
    entries = (BoardEntry.objects.filter(patient_id__in=ids, created_at__lt=end)
               .filter(Q(created_at__gte=start) | Q(kind=BoardEntry.TASK, done_at__isnull=True)
                       | Q(kind=BoardEntry.TASK, done_at__gte=start))
               .select_related(*USER_FIELDS).order_by('created_at', 'id'))
    by_patient = {}
    for e in entries:
        by_patient.setdefault(e.patient_id, []).append(e)

    units, authors = [], []
    totals = {'patients': len(patients), 'tasks': 0, 'tasks_done': 0, 'without_status': 0}
    for unit in UNIT_ORDER:
        rows = []
        for p in patients:
            if p.unit != unit:
                continue
            own = by_patient.get(p.pk, [])
            row = {
                'name': p.name, 'bed': p.bed, 'diagnosis': p.diagnosis,
                'doctor': person_full_name(p.doctor),
                'discharged': _fmt(p.discharged_at) if p.discharged_at and p.discharged_at < end else '',
                'status': [], 'tasks': [], 'comments': [], 'events': [],
            }
            for e in own:
                item = {'text': e.text, 'at': _fmt(e.created_at), 'by': person_full_name(e.created_by)}
                if e.kind == BoardEntry.STATUS:
                    row['status'].append(item)
                    if item['by'] and item['by'] not in authors:
                        authors.append(item['by'])
                elif e.kind == BoardEntry.TASK:
                    done = e.done_at is not None and e.done_at < end
                    item.update(done=done, done_at=_fmt(e.done_at) if done else '',
                                done_by=person_full_name(e.done_by) if done else '')
                    row['tasks'].append(item)
                    totals['tasks'] += 1
                    totals['tasks_done'] += done
                elif e.kind == BoardEntry.COMMENT:
                    row['comments'].append(item)
                else:
                    row['events'].append(item)
            if not row['status']:
                totals['without_status'] += 1
            rows.append(row)
        units.append({'key': unit, 'label': UNIT_LABELS[unit], 'patients': rows})

    label = day_label(day)
    return {
        'day': label['short'], 'weekday': label['weekday'],
        'from': _fmt(start), 'to': _fmt(end - datetime.timedelta(minutes=1)),
        'units': units, 'totals': totals, 'duty_doctors': authors,
        'made_by': person_full_name(user) if user else '',
        'made_at': _fmt(timezone.now()),
    }


class ReportView(LoginRequiredMixin, generic.TemplateView):
    """Live preview of the current (or a chosen) duty; "save" stores a snapshot."""
    template_name = 'mdf/board/report.html'

    def get_day(self):
        day = _parse_day(self.request.POST.get('date') or self.request.GET.get('date') or '')
        current = duty_date_for()
        return day if day and day <= current else current

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        purge_old_reports()
        day = self.get_day()
        current = duty_date_for()
        ctx.update({
            'r': build_report(day, self.request.user), 'day': day_label(day),
            'is_current': day == current,
            'prev': day_label(day - datetime.timedelta(days=1)),
            'next': day_label(day + datetime.timedelta(days=1)) if day < current else None,
            'saved': DutyReport.objects.filter(duty_date=day).first(),
            'too_old': day < current - datetime.timedelta(days=DutyReport.KEEP_DAYS),
        })
        return ctx

    def post(self, request, *args, **kwargs):
        day = self.get_day()
        if day < duty_date_for() - datetime.timedelta(days=DutyReport.KEEP_DAYS):
            messages.error(request, 'ერთ თვეზე ძველი რეპორტი არ ინახება.')
            return HttpResponseRedirect(reverse('board-report'))
        data = build_report(day, request.user)
        report = DutyReport.objects.filter(duty_date=day).first()
        if report:          # one report per duty: making it again refreshes it
            report.data, report.created_at, report.created_by = data, timezone.now(), request.user
            report.save()
        else:
            report = DutyReport.objects.create(duty_date=day, data=data, created_by=request.user,
                                               created_at=timezone.now())
        messages.success(request, f'რეპორტი შეინახა — მორიგეობა {day:%d.%m.%Y}. ინახება {report.expires_on:%d.%m.%Y}-მდე.')
        return HttpResponseRedirect(reverse('board-report-saved', args=[report.pk]))


class ReportListView(LoginRequiredMixin, generic.ListView):
    template_name = 'mdf/board/reports.html'
    context_object_name = 'reports'

    def get_queryset(self):
        purge_old_reports()
        return DutyReport.objects.select_related('created_by__profile')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        for report in ctx['reports']:
            report.label = day_label(report.duty_date)
            report.totals = (report.data or {}).get('totals', {})
        ctx['keep_days'] = DutyReport.KEEP_DAYS
        return ctx


class SavedReportView(LoginRequiredMixin, generic.TemplateView):
    template_name = 'mdf/board/report_saved.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        purge_old_reports()
        report = get_object_or_404(DutyReport, pk=kwargs['pk'])
        ctx.update({'report': report, 'r': report.data, 'day': day_label(report.duty_date)})
        return ctx


class ReportPrintView(SavedReportView):
    template_name = 'mdf/board/report_print.html'

