"""Track B Nurse Workspace views: per-user switch, 1-screen mobile registration, and link tracking."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import generic

from ...board import clean_text, doctors, person_name
from ...models import PreopPatient
from ..constants import _PREOP_MUTATION_LOCK, _sqlite_acquire_write_lock
from ..nurse_ui import (
    PREOP_UI_NEW,
    PREOP_UI_OLD,
    REGISTER_PROCEDURE_GROUPS,
    self_fill_status_info,
    set_preop_ui_mode,
)
from ..utils import _date, _int, merged_studies
from .parsers import is_doctor_user, resolve_doctor_user


class PreopUiModeView(LoginRequiredMixin, generic.View):
    """Per-user switch between 'ახალი ხედი' (new nurse UI) and 'ძველი ხედი' (classic UI)."""

    def post(self, request):
        mode = request.POST.get('mode') or PREOP_UI_OLD
        chosen = set_preop_ui_mode(request, mode)
        if chosen == PREOP_UI_NEW:
            messages.success(request, 'ჩართულია ახალი საექთნო ხედი. ძველ ხედზე დაბრუნება ნებისმიერ წამს შეგიძლიათ.')
        else:
            messages.info(request, 'დაბრუნდით ძველ ხედზე.')
        next_url = (request.POST.get('next') or '').strip()
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            return HttpResponseRedirect(next_url)
        return HttpResponseRedirect(reverse('preop'))


class PreopNurseRegisterView(LoginRequiredMixin, generic.View):
    """Fast 1-screen mobile patient registration with plain-Georgian procedure cards and CEA side guard."""
    template_name = 'mdf/preop/register_new.html'

    def _ctx(self, form_data=None, error_msg=''):
        return {
            'procedure_groups': REGISTER_PROCEDURE_GROUPS,
            'doctors': doctors(),
            'today': timezone.localdate(),
            'form_data': form_data or {},
            'error_msg': error_msg,
        }

    def get(self, request):
        return render(request, self.template_name, self._ctx())

    def post(self, request):
        name = clean_text(request.POST.get('patient_name'), 150).replace('\n', ' ')
        phone = clean_text(request.POST.get('phone'), 50).replace('\n', ' ')
        op_subtype = request.POST.get('op_subtype') or PreopPatient.OpSubtype.CEA_SYMP
        if op_subtype not in PreopPatient.OpSubtype.values:
            op_subtype = PreopPatient.OpSubtype.CEA_SYMP
        pathway = PreopPatient.SUBTYPE_PATHWAY.get(op_subtype, PreopPatient.Pathway.AAA_INFRA)
        side = request.POST.get('side') if request.POST.get('side') in ('R', 'L', 'B') else ''
        if pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF):
            side = ''
        sex = request.POST.get('sex') if request.POST.get('sex') in ('M', 'F') else (
            'F' if op_subtype in ('evla', 'phlebectomy') else 'M'
        )
        age = _int(request.POST.get('age'), lo=1, hi=120)
        raw_op_date = (request.POST.get('planned_surgery_date') or '').strip()
        op_date = _date(raw_op_date)
        today = timezone.localdate()
        surgeon_raw = request.POST.get('surgeon') or ''
        surgeon = resolve_doctor_user(surgeon_raw)
        if surgeon is None and is_doctor_user(request.user):
            surgeon = request.user

        form_data = {
            'patient_name': name, 'phone': phone, 'op_subtype': op_subtype,
            'side': side, 'sex': sex, 'age': request.POST.get('age') or '',
            'planned_surgery_date': raw_op_date,
            'surgeon': surgeon_raw,
        }
        if not name:
            return render(request, self.template_name, self._ctx(form_data, 'ჩაწერეთ პაციენტის სახელი და გვარი.'), status=400)
        if pathway == PreopPatient.Pathway.CEA and side == 'B':
            return render(
                request, self.template_name,
                self._ctx(form_data, 'საძილე არტერიის (CEA) ორმხრივი ერთდროული ოპერაცია დაუშვებელია — აირჩიეთ მარჯვენა ან მარცხენა მხარე.'),
                status=400,
            )
        if raw_op_date and (op_date is None or op_date < today):
            return render(
                request, self.template_name,
                self._ctx(form_data, 'ოპერაციის თარიღი არ შეიძლება იყოს წარსული ან არასწორი — აირჩიეთ დღევანდელი ან მომავალი თარიღი.'),
                status=400,
            )

        studies = {}
        if op_date:
            studies['planned_surgery_date'] = op_date.isoformat()
        patient = PreopPatient.objects.create(
            patient_name=name,
            phone=phone,
            age=age,
            sex=sex,
            side=side,
            op_subtype=op_subtype,
            surgeon=surgeon,
            visit_date=today,
            started_on=today,
            studies=studies,
            created_by=request.user,
            updated_by=request.user,
        )
        messages.success(request, f'{patient.patient_name} დარეგისტრირდა — ახლა შეგიძლიათ გაუგზავნოთ კითხვარის ბმული ან შეიყვანოთ ვიტალები.')
        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]))


class PreopNurseSelfFillSentView(LoginRequiredMixin, generic.View):
    """Record when nurse sends or copies the patient self-fill link (WhatsApp / SMS / Copy)."""

    def post(self, request, pk):
        from .. import evaluate

        patient = get_object_or_404(PreopPatient, pk=pk)
        via = request.POST.get('via') or 'copy'
        if via not in ('whatsapp', 'sms', 'copy'):
            via = 'copy'
        with _PREOP_MUTATION_LOCK, transaction.atomic():
            _sqlite_acquire_write_lock(patient.pk)
            patient = PreopPatient.objects.select_for_update().get(pk=patient.pk)
            s = merged_studies(patient)
            if s.get('self_fill_revoked'):
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({'ok': False, 'error': 'revoked'}, status=400)
                messages.error(request, 'ეს ბმული გაუქმებულია — ახალი ბმულის გასაგზავნად ჯერ დააჭირეთ „ახალი ბმული“.')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]))
            s['self_link_sent_at'] = timezone.localtime().strftime('%d.%m.%Y %H:%M')
            s['self_link_sent_via'] = via
            s['self_link_sent_by'] = person_name(request.user)
            patient.studies = s
            patient.updated_by = request.user
            patient.save(update_fields=['studies', 'updated_at', 'updated_by'])

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            ev = evaluate(patient)
            return JsonResponse({'ok': True, 'self_fill': self_fill_status_info(ev), 'sent_at': s['self_link_sent_at']})
        messages.success(request, 'მონიშნულია, რომ კითხვარის ბმული გაგზავნილია პაციენტთან.')
        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]))


class PreopNurseMedCallView(LoginRequiredMixin, generic.View):
    """Stamp or clear 'დავურეკე და წამლების გეგმა ავუხსენი ✓' bound to current surgery date + drug signature."""

    def post(self, request, pk):
        from .. import evaluate
        from ..nurse_ui import med_schedule_signature
        from .parsers import _invalidate_approval_if_needed, med_hold_agreement_block_reason

        patient = get_object_or_404(PreopPatient, pk=pk)
        action = request.POST.get('action') or 'called'

        with _PREOP_MUTATION_LOCK, transaction.atomic():
            _sqlite_acquire_write_lock(patient.pk)
            patient = PreopPatient.objects.select_for_update().get(pk=patient.pk)
            ev = evaluate(patient)
            s = merged_studies(patient)
            uf = ['studies', 'updated_at', 'updated_by']

            block_msg = med_hold_agreement_block_reason(ev, s)
            if action == 'clear':
                s['nurse_med_called_at'] = ''
                s['nurse_med_called_by'] = ''
                s['nurse_med_called_sig'] = ''
                s['med_hold_agreed'] = False
                s['med_hold_agreed_date'] = ''
                _invalidate_approval_if_needed(patient, uf)
                messages.info(request, 'დარეკვის მონიშვნა მოიხსნა.')
            elif not ev.get('needs_med_hold'):
                messages.info(request, 'ამ პაციენტს შესაჩერებელი წამლების გრაფიკი არ აქვს.')
            elif block_msg:
                messages.error(request, block_msg)
            else:
                s['nurse_med_called_at'] = timezone.localtime().strftime('%d.%m.%Y %H:%M')
                s['nurse_med_called_by'] = person_name(request.user)
                s['nurse_med_called_sig'] = med_schedule_signature(ev)
                s['nurse_med_called_op_date'] = s.get('planned_surgery_date') or ''
                s['med_hold_agreed'] = True
                s['med_hold_agreed_date'] = timezone.localdate().isoformat()
                messages.success(request, 'დაფიქსირდა: პაციენტს დაურეკეთ და წამლების შეჩერების გეგმა აუხსენით ✓')

            patient.studies = s
            patient.updated_by = request.user
            patient.save(update_fields=list(dict.fromkeys(uf)))

        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '#nw-med-call-card')


class PreopNurseRescheduleView(LoginRequiredMixin, generic.View):
    """Change or postpone planned surgery date (+3d quick button or exact date) and auto-invalidate old med calls."""

    def post(self, request, pk):
        import datetime

        from .. import evaluate
        from ..nurse_ui import med_schedule_signature

        patient = get_object_or_404(PreopPatient, pk=pk)
        action = request.POST.get('action') or 'set_date'

        with _PREOP_MUTATION_LOCK, transaction.atomic():
            _sqlite_acquire_write_lock(patient.pk)
            patient = PreopPatient.objects.select_for_update().get(pk=patient.pk)
            s = merged_studies(patient)

            if action == 'ack_postpone':
                s['postpone_ack_sig'] = (request.POST.get('sig') or '').strip()[:40]
                patient.studies = s
                patient.updated_by = request.user
                patient.save(update_fields=['studies', 'updated_at', 'updated_by'])
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]))

            out_st = s.get('case_outcome_status') or 'scheduled'
            if out_st in ('operated', 'cancelled_medical', 'cancelled_patient'):
                messages.error(request, 'გაუქმებულ ან უკვე ნაოპერაციებ პაციენტზე ოპერაციის თარიღის შეცვლა დაბლოკილია!')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '#nw-reschedule-card')

            old_dt = _date(s.get('planned_surgery_date'))
            today = timezone.localdate()
            if action == 'shift_3d':
                base = old_dt if (old_dt and old_dt >= today) else today
                try:
                    new_dt = base + datetime.timedelta(days=3)
                except OverflowError:
                    messages.error(request, 'თარიღი სცდება დასაშვებ კალენდარულ დიაპაზონს.')
                    return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '#nw-reschedule-card')
            else:
                new_dt = _date(request.POST.get('planned_surgery_date'))

            if not new_dt:
                messages.error(request, 'აირჩიეთ ოპერაციის ახალი თარიღი.')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '#nw-reschedule-card')
            if new_dt < today:
                messages.error(request, 'წარსული თარიღი დაუშვებელია — აირჩიეთ დღევანდელი ან მომავალი თარიღი.')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '#nw-reschedule-card')

            uf = ['studies', 'updated_at', 'updated_by']
            if old_dt != new_dt:
                if old_dt:
                    s['prev_planned_surgery_date'] = old_dt.isoformat()
                    s['postpone_ack_sig'] = ''
                s['med_hold_agreed'] = False
                s['med_hold_agreed_date'] = ''
                if patient.approved_at is not None:
                    patient.approved_at = None
                    patient.approved_by = None
                    uf.extend(['approved_at', 'approved_by'])
                    if patient.someday_list == PreopPatient.SomedayList.READY:
                        patient.someday_list = PreopPatient.SomedayList.CONF
                        uf.append('someday_list')
            s['planned_surgery_date'] = new_dt.isoformat()
            raw_inv = s.get('_invalid_inputs')
            if isinstance(raw_inv, dict) and 'planned_surgery_date' in raw_inv:
                inv = dict(raw_inv)
                inv.pop('planned_surgery_date', None)
                if inv:
                    s['_invalid_inputs'] = inv
                else:
                    s.pop('_invalid_inputs', None)
            patient.studies = s
            ev_new = evaluate(patient)
            if s.get('nurse_med_called_sig') and s.get('nurse_med_called_sig') != med_schedule_signature(ev_new):
                s['med_hold_agreed'] = False
                s['med_hold_agreed_date'] = ''
            patient.studies = s
            patient.updated_by = request.user
            patient.save(update_fields=uf)

        messages.success(request, f'ოპერაციის თარიღი განახლდა ({new_dt.strftime("%d.%m.%Y")}) — შეამოწმეთ რა უნდა გაკეთდეს თავიდან.')
        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '#nw-postpone-banner')


class PreopNursePassportGuideView(LoginRequiredMixin, generic.View):
    """Plain-Georgian patient explanation card next to the 2-page printable A4 passport."""
    template_name = 'mdf/preop/passport_guide.html'

    def get(self, request, pk):
        from .. import evaluate
        from ..nurse_plan import build_patient_plain_explanation
        from ..nurse_ui import PLAIN_SIDE_KA, _plain_procedure_ka

        patient = get_object_or_404(
            PreopPatient.objects.select_related('surgeon', 'surgeon__profile', 'approved_by'),
            pk=pk,
        )
        today = timezone.localdate()
        ev = evaluate(patient, today=today)
        guide = build_patient_plain_explanation(patient, ev, today)
        return render(request, self.template_name, {
            'p': patient,
            'ev': ev,
            'guide': guide,
            'procedure_ka': _plain_procedure_ka(patient, ev),
            'side_ka': PLAIN_SIDE_KA.get(patient.side or '', ''),
            'surgeon_name': person_name(patient.surgeon),
            'today': today,
        })


