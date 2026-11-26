"""Track B Nurse Workspace views: per-user switch, 1-screen mobile registration, and link tracking."""
from django.contrib import messages
from django.contrib.auth import get_user_model
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
        op_date = _date(request.POST.get('planned_surgery_date'))
        surgeon_raw = request.POST.get('surgeon') or ''
        surgeon = get_user_model().objects.filter(pk=int(surgeon_raw)).first() if surgeon_raw.isdigit() else None
        if surgeon is None and hasattr(request.user, 'profile'):
            surgeon = request.user

        form_data = {
            'patient_name': name, 'phone': phone, 'op_subtype': op_subtype,
            'side': side, 'sex': sex, 'age': request.POST.get('age') or '',
            'planned_surgery_date': op_date.isoformat() if op_date else '',
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

        today = timezone.localdate()
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
            s['self_link_sent_at'] = timezone.localtime().strftime('%d.%m.%Y %H:%M')
            s['self_link_sent_via'] = via
            s['self_link_sent_by'] = person_name(request.user)
            s['self_fill_revoked'] = False
            patient.studies = s
            patient.updated_by = request.user
            patient.save(update_fields=['studies', 'updated_at', 'updated_by'])

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            ev = evaluate(patient)
            return JsonResponse({'ok': True, 'self_fill': self_fill_status_info(ev), 'sent_at': s['self_link_sent_at']})
        messages.success(request, 'მონიშნულია, რომ კითხვარის ბმული გაგზავნილია პაციენტთან.')
        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]))
