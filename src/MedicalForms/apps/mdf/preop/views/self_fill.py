"""Signed, one-time, revocable Patient Self-Fill Questionnaire view."""
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from ...board import clean_text
from ...models import PreopPatient
from ..constants import (
    _PREOP_MUTATION_LOCK,
    CEAP_CHOICES,
    CHIP_CATALOGS,
    DEFAULT_Q,
    DEFAULT_STUDIES,
    DURATION_CHIPS,
    METS_LIMIT_CHOICES,
    SELF_FILL_MAX_AGE,
    SELF_FILL_MED_HINTS,
    SELF_FILL_SALT,
    _sqlite_acquire_write_lock,
)
from ..forms import apply_self_fill_post
from ..utils import (
    _age_from_dob,
    _clean_person_id,
    _date,
    _int,
    mask_person_id,
    mask_phone,
    merged_q,
    merged_studies,
)


class PreopSelfFillView(generic.View):
    """Signed, one-time, revocable Patient Self-Questionnaire link with PII masking & strict privacy headers."""
    template_name = 'mdf/preop/self_fill.html'

    def dispatch(self, request, *args, **kwargs):
        resp = super().dispatch(request, *args, **kwargs)
        resp['Referrer-Policy'] = 'no-referrer'
        resp['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        resp['Cache-Control'] = 'no-store, private'
        return resp

    def _load_patient(self, token):
        signer = TimestampSigner(salt=SELF_FILL_SALT)
        try:
            raw = signer.unsign(token, max_age=SELF_FILL_MAX_AGE)
            pk_str, _, tok_nonce = raw.partition(':')
            pk = int(pk_str)
        except (BadSignature, SignatureExpired, ValueError):
            return None
        patient = PreopPatient.objects.filter(pk=pk, archived=False).first()
        if not patient:
            return None
        s = merged_studies(patient)
        if s.get('self_fill_revoked'):
            return None
        cur_nonce = str(s.get('self_fill_nonce') or '').strip()
        if tok_nonce != cur_nonce:
            return None
        return patient

    def get(self, request, token):
        from .. import evaluate

        patient = self._load_patient(token)
        if not patient:
            return render(request, self.template_name, {'invalid_link': True}, status=404)
        ev = evaluate(patient)
        staff_locked = bool(patient.approved_at or ev['q'].get('nurse_verified') or ev['case_outcome'] != 'scheduled')
        submitted_once = bool(ev['q'].get('self_fill_submitted'))
        is_done_screen = (request.GET.get('done') == '1') and submitted_once and not staff_locked
        locked = staff_locked or (submitted_once and not is_done_screen)
        emergency_symptom = bool(ev['q'].get('aaa_pain') or ev['q'].get('cea_crescendo')) if is_done_screen else False
        # L7-F2: Never pre-populate prior clinical answers/meds/allergies on an unauthenticated bearer GET link!
        q_public = dict(DEFAULT_Q)
        s_public = dict(DEFAULT_STUDIES)
        return render(request, self.template_name, {
            'p': patient,
            'ev': ev,
            'q': q_public,
            's': s_public,
            'chips': CHIP_CATALOGS,
            'self_med_hints': SELF_FILL_MED_HINTS,
            'mets_limit_choices': METS_LIMIT_CHOICES,
            'duration_chips': DURATION_CHIPS,
            'ceap_choices': CEAP_CHOICES,
            'locked': locked,
            'staff_locked': staff_locked,
            'masked_person_id': mask_person_id(ev['person_id']),
            'masked_phone': mask_phone(patient.phone),
            'has_birth_date': bool(ev['s'].get('birth_date')),
            'emergency_symptom': emergency_symptom,
            'submitted': is_done_screen,
        })

    def post(self, request, token):
        patient = self._load_patient(token)
        if not patient:
            return render(request, self.template_name, {'invalid_link': True}, status=404)
        with _PREOP_MUTATION_LOCK, transaction.atomic():
            _sqlite_acquire_write_lock(patient.pk)
            patient = PreopPatient.objects.select_for_update().filter(pk=patient.pk, archived=False).first()
            if not patient:
                return render(request, self.template_name, {'invalid_link': True}, status=404)
            q_cur = merged_q(patient)
            s_cur = merged_studies(patient)
            if (
                patient.approved_at
                or q_cur.get('nurse_verified')
                or q_cur.get('self_fill_submitted')
                or s_cur.get('self_fill_revoked')
                or (s_cur.get('case_outcome_status') or 'scheduled') != 'scheduled'
            ):
                return HttpResponseRedirect(reverse('preop-self-fill', args=[token]) + '?locked=1')
            uf = ['questionnaire', 'studies', 'side', 'started_on', 'updated_at']
            age_posted = _int(request.POST.get('age'), lo=12, hi=120) if request.POST.get('age') else None
            if age_posted and not patient.age:
                patient.age = age_posted
                uf.append('age')
            if not patient.sex and request.POST.get('sex') in ('M', 'F'):
                patient.sex = request.POST.get('sex')
                uf.append('sex')
            if not patient.phone and 'phone' in request.POST:
                ph = clean_text(request.POST.get('phone'), 50).replace('\n', ' ')
                if ph:
                    patient.phone = ph
                    uf.append('phone')
            from .. import evaluate
            from ..nurse_ui import med_schedule_signature

            ev_before = evaluate(patient) if s_cur.get('med_hold_agreed') else None
            apply_self_fill_post(request.POST, patient)
            s_mut = merged_studies(patient)
            pid = _clean_person_id(request.POST.get('person_id'))
            if pid and not s_mut.get('person_id'):
                s_mut['person_id'] = pid
            bd = _date(request.POST.get('birth_date'))
            if bd and bd <= timezone.localdate() and not s_mut.get('birth_date'):
                s_mut['birth_date'] = bd.isoformat()
                if not patient.age:
                    patient.age = _age_from_dob(bd)
                    if 'age' not in uf:
                        uf.append('age')
            patient.studies = s_mut
            if ev_before is not None:
                ev_after = evaluate(patient)
                if (
                    med_schedule_signature(ev_before) != med_schedule_signature(ev_after)
                    or ev_after['clearance_seal']['code'] == 'stop'
                    or ev_after.get('has_hard_contraindication')
                ):
                    s_mut['med_hold_agreed'] = False
                    patient.studies = s_mut
            patient.save(update_fields=uf)
        return HttpResponseRedirect(reverse('preop-self-fill', args=[token]) + '?done=1')
