"""Weekly Tweek-style calendar view, inline patient creation, and drag/move/archive/delete view."""
import datetime

from django.contrib import messages
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
        pw = ev['pathway']
        has_acute_red = bool(
            (pw == 'aaa_infra' and ev['q'].get('aaa_pain'))
            or (pw == 'cea' and ev['q'].get('cea_crescendo'))
            or (pw in ('abf', 'pad_bypass', 'pad_pta') and (
                ev['q'].get('abf_wet_infection') or ev['q'].get('abf_duration') == '< 2 კვირა'
            ))
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
    """Inline 1-line Tweek add (POST) or 1-click transfer/re-sync from a Conference Case (POST)."""

    def get(self, request):
        case_id_int = _int(request.GET.get('case'), lo=1)
        if case_id_int is not None:
            case = get_object_or_404(Case, pk=case_id_int)
            existing = PreopPatient.objects.filter(case=case, archived=False).first()
            if existing:
                return HttpResponseRedirect(reverse('preop-patient', args=[existing.pk]))
            return HttpResponseRedirect(reverse('case', args=[case.pk]))
        return HttpResponseRedirect(reverse('preop'))

    @staticmethod
    def _transfer_or_sync_case(request, case_id_int):
        from .parsers import _invalidate_approval_if_needed, is_doctor_user

        case = get_object_or_404(Case, pk=case_id_int)
        existing = PreopPatient.objects.filter(case=case).order_by('archived', '-id').first()
        if existing:
            if existing.archived:
                messages.error(request, 'ამ ქეისის წინასაოპერაციო ბარათი დაარქივებულია — სინქრონიზაცია და დუბლიკატის შექმნა დაბლოკილია!')
                return HttpResponseRedirect(reverse('preop-patient', args=[existing.pk]))
            if request.POST.get('sync') == '1':
                cur_out = merged_studies(existing).get('case_outcome_status') or 'scheduled'
                if cur_out in ('operated', 'deferred', 'cancelled_logistics', 'cancelled_medical'):
                    messages.error(request, 'არააქტიურ (ნაოპერაციებ/გადადებულ/გაუქმებულ) ბარათზე კონფერენციის ქეისიდან სინქრონიზაცია დაბლოკილია!')
                    return HttpResponseRedirect(reverse('preop-patient', args=[existing.pk]))
                op_sub, side = detect_case_preop(case)
                if existing.pathway == PreopPatient.Pathway.CEA and side == 'B':
                    side = ''
                side_changed = False
                if not existing.side and side:
                    existing.side = side
                    side_changed = True
                imported = import_case_to_preop(case, existing)
                if imported or side_changed:
                    s_sy = merged_studies(existing)
                    if side_changed:
                        s_sy['dos_site_marked'] = False
                    s_sy['med_hold_agreed'] = False
                    s_sy['alerts_ack_sig'] = ''
                    existing.studies = s_sy
                    existing.alerts_ack_at = None
                    existing.alerts_ack_by = None
                    uf = ['studies', 'alerts_ack_at', 'alerts_ack_by', 'updated_at', 'updated_by']
                    if side_changed:
                        uf.append('side')
                    inv_res = _invalidate_approval_if_needed(existing, uf, clear_anamnesis=True)
                    existing.updated_by = request.user
                    existing.save(update_fields=list(dict.fromkeys(uf)))
                    if inv_res == 'ready_demoted':
                        messages.warning(request, 'კონფერენციის მონაცემები განახლდა, თუმცა ცვლილების გამო დადასტურება და „მზა“ სტატუსი მოიხსნა — საჭიროა ქირურგის ხელახალი დადასტურება.')
                        return HttpResponseRedirect(reverse('preop-patient', args=[existing.pk]))
                messages.success(request, 'კონფერენციის ქეისიდან სტადირება და ანატომიური მონაცემები განახლდა.')
            return HttpResponseRedirect(reverse('preop-patient', args=[existing.pk]))

        op_sub, side = detect_case_preop(case)
        if op_sub in ('cea_symp', 'cea_asymp') and side == 'B':
            side = ''
        case_doc = case.doctor if (case.doctor and is_doctor_user(case.doctor)) else (
            request.user if is_doctor_user(request.user) else None
        )
        p = PreopPatient.objects.create(
            case=case,
            patient_name=case.patient_name,
            age=case.age,
            sex=case.sex or 'M',
            side=side,
            op_subtype=op_sub,
            surgeon=case_doc,
            someday_list=PreopPatient.SomedayList.CONF,
            created_by=request.user,
            updated_by=request.user,
        )
        import_case_to_preop(case, p)
        messages.success(request, f'{p.patient_name} გადმოყვანილია წინასაოპერაციო სიაში.')
        return HttpResponseRedirect(reverse('preop-patient', args=[p.pk]))

    def post(self, request):
        from .parsers import is_doctor_user, resolve_doctor_user

        case_id_int = _int(request.POST.get('case'), lo=1)
        if case_id_int is not None:
            return self._transfer_or_sync_case(request, case_id_int)

        name = clean_text(request.POST.get('patient_name'), 150).replace('\n', ' ')
        slot = (request.POST.get('slot') or '').strip()
        op_subtype = request.POST.get('op_subtype') or PreopPatient.OpSubtype.OPEN
        if op_subtype not in PreopPatient.OpSubtype.values:
            op_subtype = PreopPatient.OpSubtype.OPEN
        side = request.POST.get('side') if request.POST.get('side') in ('R', 'L', 'B') else ''

        if op_subtype in ('cea_symp', 'cea_asymp') and side == 'B':
            messages.error(request, 'ერთდროული ორმხრივი CEA აკრძალულია (ESVS) — მიუთითეთ მარჯვენა ან მარცხენა მხარე!')
            return HttpResponseRedirect(reverse('preop'))

        raw_age = (request.POST.get('age') or '').strip()
        age_v = _int(raw_age, lo=12, hi=120)
        if raw_age and age_v is None:
            messages.error(request, 'ასაკი უნდა იყოს 12–120 წლამდე დიაპაზონში.')
            return HttpResponseRedirect(reverse('preop'))

        surgeon = resolve_doctor_user(request.POST.get('surgeon'))
        if surgeon is None and is_doctor_user(request.user):
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
            age=age_v,
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

    @staticmethod
    def _handle_archive(request, patient, ajax):
        from .parsers import is_doctor_user
        if not is_doctor_user(request.user):
            if ajax:
                return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)
            messages.error(request, 'პაციენტის დაარქივების/არქივიდან დაბრუნების უფლება აქვს მხოლოდ ექიმს ან ადმინისტრატორს!')
            return HttpResponseRedirect(reverse('preop'))
        patient.archived = not patient.archived
        patient.updated_by = request.user
        patient.save(update_fields=['archived', 'updated_at', 'updated_by'])
        if ajax:
            return JsonResponse({'ok': True, 'archived': patient.archived})
        messages.info(request, f'{patient.patient_name} {"გადავიდა არქივში" if patient.archived else "დაბრუნდა სიაში"}.')
        return HttpResponseRedirect(reverse('preop'))

    @staticmethod
    def _handle_delete(request, patient, ajax):
        from .parsers import is_doctor_user
        s_del = merged_studies(patient)
        is_audit_protected = (
            patient.archived or bool(patient.approved_at)
            or s_del.get('case_outcome_status') == 'operated'
            or bool(s_del.get('board_patient_id'))
        )
        is_doc = request.user.is_superuser or request.user.is_staff or is_doctor_user(request.user)
        if is_audit_protected and not (request.user.is_superuser or request.user.is_staff):
            if not is_doc:
                if ajax:
                    return JsonResponse({'ok': False, 'archived': patient.archived, 'deleted': False, 'error': 'forbidden'}, status=403)
                messages.error(request, 'დადასტურებული/ნაოპერაციები/დაარქივებული ბარათის წაშლა ან დაარქივება დასაშვებია მხოლოდ ექიმისთვის!')
                return HttpResponseRedirect(reverse('preop'))
            if not patient.archived:
                patient.archived = True
                patient.updated_by = request.user
                patient.save(update_fields=['archived', 'updated_at', 'updated_by'])
            if ajax:
                return JsonResponse({'ok': False, 'archived': True, 'deleted': False, 'error': 'audit_protected'}, status=403)
            messages.info(request, f'{patient.patient_name} დადასტურებული/ნაოპერაციები/დაარქივებულია — სამედიცინო აუდიტის დაცვის მიზნით წაშლა დაბლოკილია (დაცულია არქივში).')
            return HttpResponseRedirect(reverse('preop'))
        has_other_files = patient.files.exclude(created_by=request.user).exists()
        is_owner = (patient.created_by_id is None or patient.created_by_id == request.user.pk) and not has_other_files and patient.surgeon_id is None
        if not (is_doc or is_owner):
            if ajax:
                return JsonResponse({'ok': False, 'deleted': False, 'error': 'forbidden'}, status=403)
            messages.error(request, 'პაციენტის წაშლა სხვა მომხმარებლის ფაილებით ან დანიშნული ქირურგით დასაშვებია მხოლოდ ექიმისთვის/ადმინისტრატორისთვის!')
            return HttpResponseRedirect(reverse('preop'))
        name = patient.patient_name
        patient.delete()
        if ajax:
            return JsonResponse({'ok': True, 'deleted': True})
        messages.success(request, f'{name} წაიშალა წინასაოპერაციო სიიდან.')
        return HttpResponseRedirect(reverse('preop'))

    def post(self, request, pk):
        from .. import evaluate

        patient = get_object_or_404(PreopPatient, pk=pk)
        action = request.POST.get('action') or 'move'
        ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

        with _PREOP_MUTATION_LOCK, transaction.atomic():
            _sqlite_acquire_write_lock(patient.pk)
            patient = PreopPatient.objects.select_for_update().get(pk=patient.pk)

            if action == 'archive':
                return self._handle_archive(request, patient, ajax)
            if action == 'delete':
                return self._handle_delete(request, patient, ajax)

            s_mut = merged_studies(patient)
            out_st = s_mut.get('case_outcome_status') or 'scheduled'
            if patient.archived or out_st != 'scheduled':
                if ajax:
                    return JsonResponse({'ok': False, 'error': 'inactive_card'}, status=400)
                messages.error(request, 'დაარქივებულ, გაუქმებულ, გადადებულ ან ნაოპერაციებ ბარათზე გადაადგილება დაბლოკილია!')
                return HttpResponseRedirect(reverse('preop'))

            old_someday = patient.someday_list
            was_unstarted = (patient.started_on is None)
            slot = (request.POST.get('slot') or '').strip()
            if slot.startswith('someday:'):
                key = slot.split(':', 1)[1]
                if key not in PreopPatient.SomedayList.values or not key:
                    return JsonResponse({'error': 'bad_slot'}, status=400) if ajax else HttpResponseRedirect(reverse('preop'))
                if key == PreopPatient.SomedayList.READY:
                    from .parsers import can_enter_ready_queue
                    ev_mv = evaluate(patient)
                    if not can_enter_ready_queue(patient, ev_mv):
                        if ajax:
                            return JsonResponse({'error': 'not_approved'}, status=400)
                        messages.error(request, 'მზა სიაში გადატანა დასაშვებია მხოლოდ სრული მზაობისა და ქირურგის დადასტურების შემდეგ (STOP-ისა და ხანდაზმული ანალიზების გარეშე)!')
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
