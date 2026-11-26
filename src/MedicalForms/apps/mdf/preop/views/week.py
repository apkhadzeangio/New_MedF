"""Weekly Tweek-style calendar view, inline patient creation, and drag/move/archive/delete view."""
import datetime

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from ...board import clean_text, doctors, person_name
from ...models import Case, PreopPatient
from ..case_import import detect_case_preop, import_case_to_preop
from ..constants import (
    _PREOP_MUTATION_LOCK,
    MONTHS_SHORT,
    OP_SUBTYPE_GROUPS,
    STATUS_ALERT,
    STATUS_NO_Q,
    STATUS_PROGRESS,
    STATUS_READY,
    WEEKDAYS_KA,
    _sqlite_acquire_write_lock,
)
from ..nurse_ui import PREOP_UI_NEW, build_nurse_handover_queues, get_preop_ui_mode
from ..utils import (
    _date,
    _int,
    _monday_of,
    _update_hold_pause,
    _week_title,
    merged_studies,
)


def _compute_week_counts(all_non_archived, today, pathway_filter, outcome_mode, evaluate_fn):
    pw_counts = {
        'all': 0,
        'aaa_infra': 0, 'cea': 0, 'abf': 0,
        'pad_bypass': 0, 'pad_pta': 0, 'venous': 0,
    }
    counts = {'all': 0, STATUS_NO_Q: 0, STATUS_PROGRESS: 0, STATUS_ALERT: 0, STATUS_READY: 0}
    ev_by_id = {}
    active_scheduled_total = 0
    completed_or_cancelled_total = 0

    for p in all_non_archived:
        ev = evaluate_fn(p, today=today)
        ev_by_id[p.pk] = ev
        is_done_case = ev['case_outcome'] != 'scheduled'
        if is_done_case:
            completed_or_cancelled_total += 1
        else:
            active_scheduled_total += 1
        in_mode = is_done_case if outcome_mode == 'done' else (not is_done_case)
        if not in_mode:
            continue
        pw_counts['all'] += 1
        pw_counts[ev['pathway']] = pw_counts.get(ev['pathway'], 0) + 1
        if pathway_filter == 'all' or ev['pathway'] == pathway_filter:
            counts['all'] += 1
            counts[ev['status']] += 1

    return pw_counts, counts, ev_by_id, active_scheduled_total, completed_or_cancelled_total


def _build_calendar_days(monday, today, visible_patients):
    days = []
    for offset in range(7):
        try:
            d = monday + datetime.timedelta(days=offset)
        except OverflowError:
            d = datetime.date.max
        items = [p for p in visible_patients if p.visit_date == d and not p.someday_list]
        days.append({
            'date': d,
            'iso': d.isoformat(),
            'weekday': WEEKDAYS_KA[offset],
            'short_date': f'{d.day} {MONTHS_SHORT[d.month]}',
            'is_today': d == today,
            'patients': items,
            'empty_lines': range(max(0, (2 if offset >= 5 else 3) - len(items))),
        })
    return days


def _find_overdue_patients(all_non_archived, ev_by_id, pathway_filter, monday, sunday):
    # Surface overdue OR off-week acute vascular emergencies (symptomatic AAA pain / crescendo TIA) so nothing hides in next week!
    overdue_patients = []
    for p in all_non_archived:
        ev = ev_by_id[p.pk]
        if ev['case_outcome'] != 'scheduled':
            continue
        if pathway_filter != 'all' and ev['pathway'] != pathway_filter:
            continue
        has_acute_red = bool(
            ev['q'].get('aaa_pain')
            or ev['q'].get('cea_crescendo')
            or ev['q'].get('abf_wet_infection')
            or (ev['pathway'] in ('abf', 'pad_bypass', 'pad_pta') and ev['q'].get('abf_duration') == '< 2 კვირა')
        )
        off_week_acute = (
            has_acute_red
            and p.someday_list != PreopPatient.SomedayList.HOLD
            and (not p.visit_date or not (monday <= p.visit_date <= sunday))
        )
        if ev['over_target_days'] or off_week_acute:
            p.ev = ev
            overdue_patients.append(p)
    return overdue_patients


def _week_nav_bounds(monday):
    """Return (sunday, prev_week_iso, next_week_iso) safely clamped at Python date bounds."""
    try:
        sunday = monday + datetime.timedelta(days=6)
    except OverflowError:
        sunday = datetime.date.max
    try:
        prev_week = (monday - datetime.timedelta(days=7)).isoformat()
    except OverflowError:
        prev_week = datetime.date.min.isoformat()
    try:
        next_week = (monday + datetime.timedelta(days=7)).isoformat()
    except OverflowError:
        next_week = datetime.date.max.isoformat()
    return sunday, prev_week, next_week


class PreopWeekView(LoginRequiredMixin, generic.TemplateView):
    """Tweek.so-style weekly calendar + Someday lists (or Track B Nurse Workspace when toggled)."""
    template_name = 'mdf/preop/week.html'

    def get_template_names(self):
        if get_preop_ui_mode(self.request) == PREOP_UI_NEW:
            return ['mdf/preop/week_new.html']
        return [self.template_name]

    def get_context_data(self, **kwargs):
        from .. import evaluate

        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        req_day = _date(self.request.GET.get('week')) or today
        monday = _monday_of(req_day)
        sunday, prev_week, next_week = _week_nav_bounds(monday)

        status_filter = self.request.GET.get('status', 'all')
        if status_filter not in ('all', STATUS_NO_Q, STATUS_PROGRESS, STATUS_ALERT, STATUS_READY):
            status_filter = 'all'
        pathway_filter = self.request.GET.get('pathway', 'all')
        if pathway_filter not in ('all', *PreopPatient.Pathway.values):
            pathway_filter = 'all'
        outcome_mode = 'done' if self.request.GET.get('outcome') == 'done' else 'active'
        q = clean_text(self.request.GET.get('q'), 100)

        qs = (
            PreopPatient.objects.filter(archived=False)
            .filter(Q(visit_date__gte=monday, visit_date__lte=sunday) | ~Q(someday_list=''))
            .select_related('surgeon', 'surgeon__profile', 'case')
            .order_by('id')
        )
        if pathway_filter != 'all':
            qs = qs.filter(pathway=pathway_filter)
        if q:
            qs = qs.filter(patient_name__icontains=q)

        all_non_archived = list(
            PreopPatient.objects.filter(archived=False)
            .select_related('surgeon', 'surgeon__profile')
            .order_by('visit_date', 'id')[:500]
        )
        pw_counts, counts, ev_by_id, active_scheduled_total, completed_or_cancelled_total = _compute_week_counts(
            all_non_archived, today, pathway_filter, outcome_mode, evaluate,
        )

        visible_patients = []
        for p in qs:
            p.ev = ev_by_id.get(p.pk) or evaluate(p, today=today)
            is_done_case = p.ev['case_outcome'] != 'scheduled'
            in_mode = is_done_case if outcome_mode == 'done' else (not is_done_case)
            if not in_mode:
                continue
            p.surgeon_name = person_name(p.surgeon)
            if status_filter == 'all' or p.ev['status'] == status_filter:
                visible_patients.append(p)

        days = _build_calendar_days(monday, today, visible_patients)
        someday_cols = [
            {'key': key, 'label': label, 'patients': [p for p in visible_patients if p.someday_list == key]}
            for key, label in PreopPatient.SomedayList.choices[1:]
        ]
        overdue_patients = _find_overdue_patients(all_non_archived, ev_by_id, pathway_filter, monday, sunday)

        ui_mode = get_preop_ui_mode(self.request)
        if ui_mode == PREOP_UI_NEW:
            ctx['nurse_queues'] = build_nurse_handover_queues(all_non_archived, ev_by_id, today, q_search=q)
        ctx.update({
            'preop_ui_mode': ui_mode,
            'today': today, 'monday': monday,
            'prev_week': prev_week,
            'next_week': next_week,
            'week_title': _week_title(monday),
            'days_mon_fri': days[:5], 'day_sat': days[5], 'day_sun': days[6],
            'someday_cols': someday_cols,
            'status_filter': status_filter, 'pathway_filter': pathway_filter, 'outcome_mode': outcome_mode,
            'active_scheduled_total': active_scheduled_total,
            'completed_or_cancelled_total': completed_or_cancelled_total,
            'q': q, 'doctors': doctors(), 'op_subtype_groups': OP_SUBTYPE_GROUPS,
            'overdue_patients': overdue_patients,
            'pathway_pills': [
                ('all', 'ყველა', pw_counts['all']),
                ('aaa_infra', 'AAA', pw_counts['aaa_infra']),
                ('cea', 'CEA', pw_counts['cea']),
                ('abf', 'ABF', pw_counts['abf']),
                ('pad_bypass', 'პერიფ. შუნტირება', pw_counts['pad_bypass']),
                ('pad_pta', 'PTA / სტენტი', pw_counts['pad_pta']),
                ('venous', 'ვენური (EVLA/ფლებ.)', pw_counts['venous']),
            ],
            'filter_pills': [
                ('all', 'ყველა', counts['all']),
                (STATUS_NO_Q, 'კითხვარი შესავსებია', counts[STATUS_NO_Q]),
                (STATUS_PROGRESS, 'კვლევებშია', counts[STATUS_PROGRESS]),
                (STATUS_ALERT, 'ექიმის ჩართვა', counts[STATUS_ALERT]),
                (STATUS_READY, 'მზადაა ექიმისთვის', counts[STATUS_READY]),
            ],
        })
        return ctx


class PreopCreateView(LoginRequiredMixin, generic.View):
    """Inline 1-line Tweek add (POST) or 1-click transfer/re-sync from a Conference Case (GET/POST)."""

    def get(self, request):
        from .parsers import _invalidate_approval_if_needed

        case_id_int = _int(request.GET.get('case'), lo=1)
        if case_id_int is not None:
            case = get_object_or_404(Case, pk=case_id_int)
            existing = PreopPatient.objects.filter(case=case, archived=False).first()
            if existing:
                if request.GET.get('sync') == '1':
                    op_sub, side = detect_case_preop(case)
                    side_changed = False
                    if not existing.side and side:
                        existing.side = side
                        side_changed = True
                    imported = import_case_to_preop(case, existing)
                    if imported or side_changed:
                        s_sy = merged_studies(existing)
                        s_sy['med_hold_agreed'] = False
                        s_sy['alerts_ack_sig'] = ''
                        existing.studies = s_sy
                        existing.alerts_ack_at = None
                        existing.alerts_ack_by = None
                        uf = ['studies', 'alerts_ack_at', 'alerts_ack_by', 'updated_at', 'updated_by']
                        if side_changed:
                            uf.append('side')
                        _invalidate_approval_if_needed(existing, uf, clear_anamnesis=True)
                        existing.updated_by = request.user
                        existing.save(update_fields=list(dict.fromkeys(uf)))
                    messages.success(request, 'კონფერენციის ქეისიდან სტადირება და ანატომიური მონაცემები განახლდა.')
                return HttpResponseRedirect(reverse('preop-patient', args=[existing.pk]))
            op_sub, side = detect_case_preop(case)
            p = PreopPatient.objects.create(
                case=case,
                patient_name=case.patient_name,
                age=case.age,
                sex=case.sex or 'M',
                side=side,
                op_subtype=op_sub,
                surgeon=case.doctor or request.user,
                someday_list=PreopPatient.SomedayList.CONF,
                created_by=request.user,
                updated_by=request.user,
            )
            import_case_to_preop(case, p)
            messages.success(request, f'{p.patient_name} გადმოყვანილია წინასაოპერაციო სიაში.')
            return HttpResponseRedirect(reverse('preop-patient', args=[p.pk]))
        return HttpResponseRedirect(reverse('preop'))

    def post(self, request):
        name = clean_text(request.POST.get('patient_name'), 150).replace('\n', ' ')
        slot = (request.POST.get('slot') or '').strip()
        op_subtype = request.POST.get('op_subtype') or PreopPatient.OpSubtype.OPEN
        if op_subtype not in PreopPatient.OpSubtype.values:
            op_subtype = PreopPatient.OpSubtype.OPEN
        side = request.POST.get('side') if request.POST.get('side') in ('R', 'L', 'B') else ''

        surgeon = None
        surg_id = _int(request.POST.get('surgeon'), lo=1)
        if surg_id is not None:
            surgeon = get_user_model().objects.filter(pk=surg_id).first()
        if surgeon is None and hasattr(request.user, 'profile'):
            surgeon = request.user

        if not name:
            messages.error(request, 'ჩაწერეთ პაციენტის სახელი და გვარი.')
            return HttpResponseRedirect(reverse('preop'))

        visit_date = None
        someday = ''
        started_on = None
        init_studies = {}
        today = timezone.localdate()
        default_sex = 'F' if op_subtype in ('evla', 'phlebectomy') and request.POST.get('sex') not in ('M', 'F') else 'M'
        sex = request.POST.get('sex') if request.POST.get('sex') in ('M', 'F') else default_sex
        if slot.startswith('someday:'):
            key = slot.split(':', 1)[1]
            if key == PreopPatient.SomedayList.READY or key not in PreopPatient.SomedayList.values:
                someday = PreopPatient.SomedayList.CONF
            else:
                someday = key
            if someday == PreopPatient.SomedayList.HOLD:
                init_studies['hold_since'] = today.isoformat()
        else:
            visit_date = _date(slot) or today
            started_on = visit_date

        p = PreopPatient.objects.create(
            patient_name=name,
            age=_int(request.POST.get('age'), lo=1, hi=120),
            sex=sex,
            side=side,
            op_subtype=op_subtype,
            surgeon=surgeon,
            visit_date=visit_date,
            someday_list=someday,
            started_on=started_on,
            studies=init_studies,
            created_by=request.user,
            updated_by=request.user,
        )
        if request.POST.get('open_card') == '1':
            return HttpResponseRedirect(reverse('preop-patient', args=[p.pk]))
        week_q = f'?week={visit_date.isoformat()}' if visit_date else ''
        return HttpResponseRedirect(reverse('preop') + week_q)


class PreopMoveView(LoginRequiredMixin, generic.View):
    """Move a patient between dates / Someday lists (drag & drop AJAX or form POST), archive, or delete."""

    def post(self, request, pk):
        from .. import evaluate

        patient = get_object_or_404(PreopPatient, pk=pk)
        action = request.POST.get('action') or 'move'
        ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

        with _PREOP_MUTATION_LOCK, transaction.atomic():
            _sqlite_acquire_write_lock(patient.pk)
            patient = PreopPatient.objects.select_for_update().get(pk=patient.pk)

            if action == 'archive':
                patient.archived = not patient.archived
                patient.updated_by = request.user
                patient.save(update_fields=['archived', 'updated_at', 'updated_by'])
                if ajax:
                    return JsonResponse({'ok': True, 'archived': patient.archived})
                messages.info(request, f'{patient.patient_name} {"გადავიდა არქივში" if patient.archived else "დაბრუნდა სიაში"}.')
                return HttpResponseRedirect(reverse('preop'))

            if action == 'delete':
                s_del = merged_studies(patient)
                is_audit_protected = (
                    s_del.get('case_outcome_status') == 'operated'
                    or bool(s_del.get('board_patient_id'))
                )
                if is_audit_protected and not (request.user.is_superuser or request.user.is_staff):
                    patient.archived = True
                    patient.updated_by = request.user
                    patient.save(update_fields=['archived', 'updated_at', 'updated_by'])
                    if ajax:
                        return JsonResponse({'ok': True, 'archived': True, 'deleted': False})
                    messages.info(request, f'{patient.patient_name} ნაოპერაციები/ჰოსპიტალიზებულია — სამედიცინო აუდიტის დაცვის მიზნით გადატანილია არქივში.')
                    return HttpResponseRedirect(reverse('preop'))
                name = patient.patient_name
                patient.delete()
                if ajax:
                    return JsonResponse({'ok': True, 'deleted': True})
                messages.success(request, f'{name} წაიშალა წინასაოპერაციო სიიდან.')
                return HttpResponseRedirect(reverse('preop'))

            old_someday = patient.someday_list
            was_unstarted = (patient.started_on is None)
            s_mut = merged_studies(patient)
            slot = (request.POST.get('slot') or '').strip()
            if slot.startswith('someday:'):
                key = slot.split(':', 1)[1]
                if key not in PreopPatient.SomedayList.values or not key:
                    return JsonResponse({'error': 'bad_slot'}, status=400) if ajax else HttpResponseRedirect(reverse('preop'))
                if key == PreopPatient.SomedayList.READY:
                    ev_mv = evaluate(patient)
                    if (
                        not patient.approved_at
                        or ev_mv['clearance_seal']['code'] == 'stop'
                        or ev_mv.get('has_hard_contraindication')
                        or ev_mv.get('has_blocking_unknown_state')
                    ):
                        if ajax:
                            return JsonResponse({'error': 'not_approved'}, status=400)
                        messages.error(request, 'მზა სიაში გადატანა დასაშვებია მხოლოდ ქირურგის დადასტურების შემდეგ (STOP-ის გარეშე)!')
                        return HttpResponseRedirect(reverse('preop'))
                patient.someday_list = key
                patient.visit_date = None
            else:
                vd = _date(slot)
                if not vd:
                    return JsonResponse({'error': 'bad_date'}, status=400) if ajax else HttpResponseRedirect(reverse('preop'))
                patient.visit_date = vd
                patient.someday_list = ''
                if not patient.started_on:
                    patient.started_on = vd

            _update_hold_pause(
                s_mut, old_someday, patient.someday_list,
                started_on=(None if was_unstarted else patient.started_on),
            )
            patient.studies = s_mut
            patient.updated_by = request.user
            patient.save(update_fields=['visit_date', 'someday_list', 'started_on', 'studies', 'updated_at', 'updated_by'])
            if ajax:
                ev = evaluate(patient)
                return JsonResponse({'ok': True, 'days_elapsed': ev['days_elapsed'], 'status': ev['status']})
            return HttpResponseRedirect(reverse('preop'))
