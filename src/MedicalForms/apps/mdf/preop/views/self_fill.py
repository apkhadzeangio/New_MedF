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
    is_self_filled_q,
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

    @staticmethod
    def _self_fill_lock_state(patient, q, s, outcome):
        cur_nonce = str(s.get('self_fill_nonce') or '').strip()
        reopened = bool(s.get('self_fill_reopened'))
        sub_nonce = str(s.get('self_fill_submitted_nonce') if 'self_fill_submitted_nonce' in s else cur_nonce).strip()
        submitted_once = is_self_filled_q(q) and not reopened and (sub_nonce == cur_nonce)
        staff_locked = bool(patient.approved_at or (q.get('nurse_verified') and not reopened) or outcome != 'scheduled')
        return cur_nonce, staff_locked, submitted_once

    _PLAIN_CHIP_OVERRIDES = {
        'cad_chips': {
            'cabg': 'გულის შუნტირება',
        },
        'abdo_chips': {
            'hernia_r': 'საზარდულის თიაქარი (მარჯვენა)',
            'hernia_l': 'საზარდულის თიაქარი (მარცხენა)',
        },
        'cea_event_chips': {
            'amaurosis': 'ცალ თვალში მხედველობის დაბინდვა',
            'tia_limb': 'კიდურის დროებითი სისუსტე/დაბუჟება (<24 სთ)',
        },
        'cea_neck_chips': {
            'contra_cea': 'მეორე მხარეს საძილე არტერიის ოპერაცია',
        },
        'allergy_chips': {
            'beta_lactam': 'პენიცილინი / ცეფაზოლინი',
            'hit_heparin': 'ჰეპარინი (ალერგია / თრომბოციტების ვარდნა!)',
            'nsaid': 'ტკივილგამაყუჩებლები (იბუპროფენი / ანალგინი / ასპირინი)',
        },
        'ven_dvt_chips': {
            'vte_recent_3m': 'ღრმა ვენების თრომბოზი / ფილტვის ემბოლია ბოლო 3 თვეში!',
            'ipsi_dvt': 'იმავე ფეხის ღრმა ვენის თრომბოზი',
            'contra_dvt': 'მეორე ფეხის ღრმა ვენის თრომბოზი',
            'pe': 'ფილტვის არტერიის ემბოლია',
        },
        'ven_rec_chips': {
            'prev_evla': 'წარსულში ვენების ლაზერული / რადიოსიხშირული ოპერაცია',
        },
    }

    @classmethod
    def _plain_self_fill_chips(cls):
        from ..plain_ka import scrub_plain_ka

        out = {}
        for cat, items in CHIP_CATALOGS.items():
            ov = cls._PLAIN_CHIP_OVERRIDES.get(cat, {})
            out[cat] = tuple(
                (ck, ov.get(ck) or scrub_plain_ka(cl, second_person=False, strip_quoted_ft=True) or cl)
                for ck, cl in items
            )
        return out

    def get(self, request, token):
        from .. import evaluate
        from ..nurse_ui import _plain_procedure_ka
        from ..plain_ka import scrub_plain_ka

        patient = self._load_patient(token)
        if not patient:
            return render(request, self.template_name, {'invalid_link': True}, status=404)
        ev = evaluate(patient)
        _, staff_locked, submitted_once = self._self_fill_lock_state(patient, ev['q'], ev['s'], ev['case_outcome'])
        done_sess = request.session.get(f'sf_done_{patient.pk}')
        is_done_screen = (
            (request.GET.get('done') == '1')
            and submitted_once
            and not staff_locked
            and isinstance(done_sess, dict)
        )
        locked = staff_locked or (submitted_once and not is_done_screen)
        emergency_symptom = bool(done_sess.get('emerg')) if is_done_screen else False
        # L7-F2: Never pre-populate prior clinical answers/meds/allergies on an unauthenticated bearer GET link!
        q_public = dict(DEFAULT_Q)
        s_public = dict(DEFAULT_STUDIES)
        plain_op_lbl = scrub_plain_ka(_plain_procedure_ka(patient, ev), second_person=False, strip_quoted_ft=True)
        plain_med_hints = tuple(
            (ck, scrub_plain_ka(cl, second_person=False, strip_quoted_ft=True) or cl)
            for ck, cl in SELF_FILL_MED_HINTS
        )
        plain_mets_choices = tuple(
            (mk, scrub_plain_ka(ml, second_person=False, strip_quoted_ft=True) or ml)
            for mk, ml in METS_LIMIT_CHOICES
        )
        return render(request, self.template_name, {
            'p': patient,
            'ev': ev,
            'plain_operation_label': plain_op_lbl,
            'q': q_public,
            's': s_public,
            'chips': self._plain_self_fill_chips(),
            'self_med_hints': plain_med_hints,
            'mets_limit_choices': plain_mets_choices,
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
            from ..constants import CASE_OUTCOME_CHOICES

            q_cur = merged_q(patient)
            s_cur = merged_studies(patient)
            raw_out = s_cur.get('case_outcome_status')
            outcome = raw_out if raw_out in dict(CASE_OUTCOME_CHOICES) else 'scheduled'
            cur_nonce, staff_locked, submitted_once = self._self_fill_lock_state(patient, q_cur, s_cur, outcome)
            if staff_locked or submitted_once or s_cur.get('self_fill_revoked'):
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
            apply_self_fill_post(request.POST, patient)
            q_mut = merged_q(patient)
            q_mut['nurse_verified'] = False
            patient.questionnaire = q_mut
            s_mut = merged_studies(patient)
            s_mut['self_fill_submitted_nonce'] = cur_nonce
            s_mut['self_fill_reopened'] = False
            pid = _clean_person_id(request.POST.get('person_id'))
            if pid and not s_mut.get('person_id'):
                s_mut['person_id'] = pid
            raw_bd = (request.POST.get('birth_date') or '').strip()
            bd = _date(raw_bd)
            from ..utils import is_valid_birth_date
            if raw_bd and not s_mut.get('birth_date'):
                if bd and is_valid_birth_date(bd, today=timezone.localdate()):
                    dob_age = _age_from_dob(bd)
                    if dob_age is not None:
                        s_mut['birth_date'] = bd.isoformat()
                        if not patient.age:
                            patient.age = dob_age
                            if 'age' not in uf:
                                uf.append('age')
                else:
                    inv_sf = dict(s_mut.get('_invalid_inputs') or {}) if isinstance(s_mut.get('_invalid_inputs'), dict) else {}
                    inv_sf['birth_date'] = raw_bd[:60]
                    s_mut['_invalid_inputs'] = inv_sf
            if s_mut.get('med_hold_agreed') or s_mut.get('med_hold_agreed_date'):
                s_mut['med_hold_agreed'] = False
                s_mut['med_hold_agreed_date'] = ''
            patient.studies = s_mut
            patient.save(update_fields=uf)
            emerg_flag = bool(q_mut.get('aaa_pain') or q_mut.get('cea_crescendo'))
        request.session[f'sf_done_{patient.pk}'] = {'emerg': emerg_flag}
        return HttpResponseRedirect(reverse('preop-self-fill', args=[token]) + '?done=1')
