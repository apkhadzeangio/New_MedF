"""Individual Preoperative Assessment Workspace view (5 tabs: q, s1, s2, doc, dos)."""
import secrets

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from ...board import clean_text, doctors, person_name
from ...models import BoardPatient, Case, PreopFile, PreopPatient
from ..anamnesis import generate_anamnesis
from ..constants import (
    _PREOP_MUTATION_LOCK,
    AAA_MORPHOLOGY_CHOICES,
    AAA_NECK_CHOICES,
    ANESTH_PLAN_CHOICES,
    ASA_CHOICES,
    CASE_OUTCOME_CHOICES,
    CEAP_CHOICES,
    CFS_CHOICES,
    CHIP_CATALOGS,
    CHIP_LABELS,
    CIED_PLAN_CHOICES,
    DURATION_CHIPS,
    MALLAMPATI_CHOICES,
    METS_LIMIT_CHOICES,
    OP_SUBTYPE_GROUPS,
    POPLITEAL_SCREEN_CHOICES,
    POST_ANTITHROMBOTIC_CHOICES,
    STOP_BANG_CHOICES,
    SURG_DURATION_CHOICES,
    VQI_30D_CHOICES,
    _sqlite_acquire_write_lock,
)
from ..forms import apply_questionnaire_post
from ..registry import annotate_med_rows, annotate_reflex_orders, annotate_texts, rule_meta
from ..utils import (
    _age_from_dob,
    _cardiologist_choices,
    _clean_person_id,
    _date,
    _int,
    _resolve_pathway,
    _update_hold_pause,
    chip_summary,
    consent_url_for_preop,
    is_self_filled_q,
    is_valid_birth_date,
    make_self_fill_token,
    merged_q,
    merged_studies,
)
from .board import admit_preop_to_board, resolve_board_patient
from .parsers import (  # noqa: F401
    _S1_BOOL_FIELDS,
    _S1_NUM_FIELDS,
    _S1_SELECT_FIELDS,
    _S2_BOOL_FIELDS,
    _S2_SELECT_FIELDS,
    _invalidate_approval_if_needed,
    _parse_s1_flags_and_selects,
    _parse_s1_numbers_and_dates,
    _parse_s2_fields_and_booleans,
    _parse_s2_notes_and_cardio,
    _set_num_field,
    _set_planned_surgery_date_field,
    can_enter_ready_queue,
    med_hold_agreement_block_reason,
)

_DOS_CHECK_BOOLS = (
    'dos_identity_consent', 'dos_site_marked', 'dos_fasting_confirmed',
    'dos_med_hold_verified', 'dos_ketones_ok', 'dos_k_verified', 'dos_latex_free_or',
    'dos_bhcg_negative', 'dos_abx_ready', 'dos_blood_implant_ready',
)
_VQI_CHECK_BOOLS = (
    'icu_bed_booked', 'vqi_discharge_antiplatelet', 'vqi_discharge_statin',
    'vqi_smoking_cessation', 'vqi_surveillance_scheduled',
)


def _sync_med_hold_after_mutation(patient, ev_before, ev_after, save=True):
    """Clear med_hold_agreed whenever medication hold signature changes, STOP triggers, or call stamp is stale."""
    from ..nurse_ui import med_schedule_signature

    sig_after = med_schedule_signature(ev_after)
    s_cur = merged_studies(patient)
    if (
        med_schedule_signature(ev_before) != sig_after
        or ev_after['clearance_seal']['code'] == 'stop'
        or ev_after.get('has_hard_contraindication')
        or (
            ev_after['alerts_sig'] != ev_before['alerts_sig']
            and (ev_after.get('has_unacked_red') or ev_after.get('dos_hold_danger'))
        )
        or (s_cur.get('nurse_med_called_sig') and s_cur.get('nurse_med_called_sig') != sig_after)
    ):
        if s_cur.get('med_hold_agreed'):
            s_cur['med_hold_agreed'] = False
            patient.studies = s_cur
            if save:
                patient.save(update_fields=['studies', 'updated_at'])


class PreopPatientView(LoginRequiredMixin, generic.View):
    """Individual Preoperative Assessment Workspace for one patient (5 fast tabs)."""
    template_name = 'mdf/preop/patient.html'

    def get(self, request, pk):
        patient = get_object_or_404(
            PreopPatient.objects.select_related('surgeon', 'surgeon__profile', 'approved_by', 'alerts_ack_by', 'case'),
            pk=pk,
        )
        return self._render(request, patient)

    def post(self, request, pk):
        from .. import evaluate

        patient = get_object_or_404(PreopPatient, pk=pk)
        section = request.POST.get('section') or 'header'

        with _PREOP_MUTATION_LOCK, transaction.atomic():
            _sqlite_acquire_write_lock(patient.pk)
            patient = PreopPatient.objects.select_for_update().get(pk=patient.pk)

            if section == 'header':
                ev_before = evaluate(patient)
                self._save_header(request, patient)
                _sync_med_hold_after_mutation(patient, ev_before, evaluate(patient))
                messages.success(request, 'პაციენტის მონაცემები განახლდა.')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + f"?tab={request.POST.get('tab', 'q')}")

            if section in ('questionnaire', 'q'):
                ev_before = evaluate(patient)
                self._save_questionnaire(request, patient)
                _sync_med_hold_after_mutation(patient, ev_before, evaluate(patient))
                messages.success(request, 'კითხვარები შეინახა — განახლდა ანამნეზი და მომზადების გეგმა.')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=s1')

            if section == 'stage1':
                from ..nurse_plan import summarize_stage1_clinical_delta
                from ..nurse_ui import PREOP_UI_NEW, get_preop_ui_mode

                ev_before = evaluate(patient)
                self._save_stage1(request, patient)
                ev_after = evaluate(patient)
                _sync_med_hold_after_mutation(patient, ev_before, ev_after)
                if get_preop_ui_mode(request) == PREOP_UI_NEW:
                    deltas = summarize_stage1_clinical_delta(ev_before, ev_after)
                    if deltas:
                        messages.warning(request, 'ვიტალები/ანალიზები შეინახა — განახლდა გამოთვლა: ' + ' · '.join(deltas))
                    else:
                        messages.success(request, 'ვიტალები და ანალიზები შეინახა.')
                    return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '#nw-vitals-card')
                messages.success(request, 'საფეხური 1-ის ანალიზები და კვლევები შეინახა.')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=s2')

            if section == 'stage2':
                ev_before = evaluate(patient)
                self._save_stage2(request, patient)
                if request.POST.get('med_hold_agreed') not in ('1', 'on', 'true'):
                    _sync_med_hold_after_mutation(patient, ev_before, evaluate(patient))
                messages.success(request, 'მომზადების გეგმა და კონსულტაციები შეინახა.')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=s2')

            if section == 'signoff':
                return self._save_signoff(request, patient)

            if section == 'dos_vqi':
                return self._save_dos_vqi(request, patient)

            if section == 'self_link':
                return self._save_self_link(request, patient)

        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]))

    @staticmethod
    def _parse_header_demographics_and_dates(request, patient, s, today):
        raw_inv = s.get('_invalid_inputs')
        inv = dict(raw_inv) if isinstance(raw_inv, dict) else {}
        age_in = _int(request.POST.get('age'), lo=1, hi=120)
        if 'age' in request.POST:
            raw_age = (request.POST.get('age') or '').strip()
            if raw_age and age_in is None:
                inv['age'] = clean_text(raw_age, 24)
            else:
                inv.pop('age', None)
        if 'person_id' in request.POST:
            s['person_id'] = _clean_person_id(request.POST.get('person_id'))
        if 'birth_date' in request.POST:
            raw_bd = (request.POST.get('birth_date') or '').strip()
            bd = _date(raw_bd)
            if raw_bd and not is_valid_birth_date(bd, today=today):
                s['birth_date'] = ''
                inv['birth_date'] = raw_bd[:60]
            else:
                s['birth_date'] = bd.isoformat() if bd else ''
                inv.pop('birth_date', None)
            if age_in is None and is_valid_birth_date(bd, today=today):
                dob_age = _age_from_dob(bd)
                if dob_age is not None:
                    age_in = dob_age
                    inv.pop('age', None)
        if inv:
            s['_invalid_inputs'] = inv
        else:
            s.pop('_invalid_inputs', None)
        if age_in is not None or 'age' in request.POST:
            patient.age = age_in
        if 'planned_surgery_date' in request.POST:
            _set_planned_surgery_date_field(s, request.POST.get('planned_surgery_date'), today)

    def _save_header(self, request, patient):
        from .. import evaluate

        s = merged_studies(patient)
        today = timezone.localdate()
        old_someday, old_side, old_sub = patient.someday_list, patient.side, patient.op_subtype
        old_age, old_sex, old_op_dt = patient.age, patient.sex, s.get('planned_surgery_date')
        old_pw = _resolve_pathway(patient)
        was_unstarted = (patient.started_on is None)

        name = clean_text(request.POST.get('patient_name'), 150).replace('\n', ' ')
        if name:
            patient.patient_name = name
        self._parse_header_demographics_and_dates(request, patient, s, today)

        if 'sex' in request.POST and (request.POST.get('sex') or '') in ('M', 'F', ''):
            patient.sex = request.POST.get('sex') or ''
        if 'side' in request.POST and request.POST.get('side') in ('', 'R', 'L', 'B'):
            patient.side = request.POST.get('side')
        if 'phone' in request.POST:
            patient.phone = clean_text(request.POST.get('phone'), 50).replace('\n', ' ')
        op_sub = request.POST.get('op_subtype') or ''
        if op_sub in PreopPatient.OpSubtype.values:
            patient.op_subtype = op_sub
        if 'surgeon' in request.POST:
            surg_id = _int(request.POST.get('surgeon'), lo=1)
            patient.surgeon = get_user_model().objects.filter(pk=surg_id).first() if surg_id is not None else None

        if 'slot_kind' in request.POST or 'visit_date' in request.POST:
            slot_kind = request.POST.get('slot_kind') or ''
            if slot_kind.startswith('someday:'):
                key = slot_kind.split(':', 1)[1]
                if key == PreopPatient.SomedayList.READY:
                    ev_hdr = evaluate(patient)
                    if not can_enter_ready_queue(patient, ev_hdr):
                        key = old_someday or PreopPatient.SomedayList.CONF
                patient.someday_list = key if key in PreopPatient.SomedayList.values else PreopPatient.SomedayList.CONF
                patient.visit_date = None
            else:
                vd = _date(request.POST.get('visit_date'))
                if vd:
                    patient.visit_date = vd
                    patient.someday_list = ''
                    if not patient.started_on:
                        patient.started_on = vd

        _update_hold_pause(
            s, old_someday, patient.someday_list, today=today,
            started_on=(None if was_unstarted else patient.started_on),
        )

        new_pw = PreopPatient.SUBTYPE_PATHWAY.get(patient.op_subtype, patient.pathway)
        uf = [
            'patient_name', 'age', 'sex', 'side', 'phone', 'op_subtype', 'pathway',
            'surgeon', 'visit_date', 'someday_list', 'started_on', 'studies',
            'updated_at', 'updated_by',
        ]
        if s.get('planned_surgery_date') != old_op_dt:
            s['med_hold_agreed'] = False
        inv_now = s.get('_invalid_inputs') or {}
        if patient.side != old_side or patient.op_subtype != old_sub:
            s['dos_site_marked'] = False
            s['alerts_ack_sig'] = ''
            patient.alerts_ack_at = None
            patient.alerts_ack_by = None
            uf.extend(['alerts_ack_at', 'alerts_ack_by'])
            _invalidate_approval_if_needed(patient, uf, clear_anamnesis=True)
        elif (
            patient.age != old_age or patient.sex != old_sex
            or s.get('planned_surgery_date') != old_op_dt
            or 'birth_date' in inv_now
            or 'age' in inv_now
            or 'planned_surgery_date' in inv_now
        ):
            _invalidate_approval_if_needed(patient, uf, clear_anamnesis=True)
        if new_pw != old_pw and new_pw in (PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA):
            s['carotid_symp'] = False

        patient.studies = s
        patient.updated_by = request.user
        patient.save(update_fields=list(dict.fromkeys(uf)))

    def _save_questionnaire(self, request, patient):
        med_keys = (
            'med_aspirin', 'med_clopidogrel', 'med_ticagrelor', 'med_prasugrel',
            'med_warfarin', 'med_doac', 'med_dabigatran', 'sglt2', 'glp1', 'dm_insulin',
            'metformin', 'med_chips', 'other_meds', 'recent_stent', 'on_dialysis',
            'cad_chips', 'allergy_chips', 'allergy_text', 'ven_hormones',
        )
        old_meds = {k: merged_q(patient).get(k) for k in med_keys}
        old_side = patient.side
        apply_questionnaire_post(request.POST, patient, person_name(request.user))
        s = merged_studies(patient)
        uf = ['questionnaire', 'studies', 'side', 'started_on', 'anamnesis', 'updated_at', 'updated_by']
        if {k: merged_q(patient).get(k) for k in med_keys} != old_meds:
            s['med_hold_agreed'] = False
        if patient.side != old_side:
            s['dos_site_marked'] = False
            s['alerts_ack_sig'] = ''
            patient.alerts_ack_at = None
            patient.alerts_ack_by = None
            uf.extend(['alerts_ack_at', 'alerts_ack_by'])
        patient.studies = s
        patient.updated_by = request.user
        patient.anamnesis = ''
        _invalidate_approval_if_needed(patient, uf)
        patient.save(update_fields=list(dict.fromkeys(uf)))

    def _save_stage1(self, request, patient):
        s = merged_studies(patient)
        today = timezone.localdate()
        _parse_s1_numbers_and_dates(request.POST, s, today)
        _parse_s1_flags_and_selects(request.POST, patient, s)
        if not patient.started_on:
            patient.started_on = patient.visit_date or today
        patient.studies = s
        patient.updated_by = request.user
        uf = ['studies', 'started_on', 'updated_at', 'updated_by']
        _invalidate_approval_if_needed(patient, uf, clear_anamnesis=True)
        patient.save(update_fields=list(dict.fromkeys(uf)))

    def _save_stage2(self, request, patient):
        from .. import evaluate

        ev_before = evaluate(patient)
        s = merged_studies(patient)
        old_op_dt = s.get('planned_surgery_date')
        old_nurse_chips = list(s.get('nurse_note_chips') or [])
        old_nurse_chip_txt = chip_summary(old_nurse_chips, 'nurse_note_chips')
        old_cardio_chips = list(s.get('cardio_note_chips') or [])
        old_cardio_chip_txt = chip_summary(old_cardio_chips, 'cardio_note_chips')

        cab_present = _parse_s2_fields_and_booleans(request.POST, patient, ev_before, s)
        uf = _parse_s2_notes_and_cardio(
            request.POST, patient, s, cab_present,
            old_nurse_chips, old_nurse_chip_txt, old_cardio_chips, old_cardio_chip_txt,
        )
        from ..nurse_ui import med_schedule_signature

        patient.studies = s
        ev_after = evaluate(patient)
        had_valid_plan_before = bool(old_op_dt) and ev_before['clearance_seal']['code'] != 'stop'
        plan_changed = (
            s.get('planned_surgery_date') != old_op_dt
            or med_schedule_signature(ev_before) != med_schedule_signature(ev_after)
        )
        if (had_valid_plan_before and plan_changed) or (
            s.get('med_hold_agreed') and med_hold_agreement_block_reason(ev_after, s)
        ):
            s['med_hold_agreed'] = False
            patient.studies = s
        patient.updated_by = request.user
        _invalidate_approval_if_needed(patient, uf)
        patient.save(update_fields=list(dict.fromkeys(uf)))

    @staticmethod
    def _apply_dos_vqi_post(post, s, today, dos_check_keys, form_present, dos_partial):
        if 'planned_surgery_date' in post:
            _set_planned_surgery_date_field(s, post.get('planned_surgery_date'), today)
        if 'operated_date' in post:
            done_d = _date(post.get('operated_date'))
            s['operated_date'] = done_d.isoformat() if (done_d and done_d <= today) else ''
        for key, lo, hi in (
            ('dos_bp_sys', 40, 300), ('dos_bp_dia', 20, 200),
            ('dos_glucose', 0.5, 1000), ('dos_inr', 0.5, 15.0),
        ):
            if key in post:
                _set_num_field(s, key, post.get(key), lo=lo, hi=hi)
        present_keys = (
            set(post.getlist('dos_present_keys'))
            if (hasattr(post, 'getlist') and 'dos_present_keys' in post)
            else dos_check_keys
        )
        for bf in _DOS_CHECK_BOOLS:
            if (form_present and bf in present_keys) or (dos_partial and bf in present_keys) or bf in post:
                s[bf] = post.get(bf) in ('1', 'on', 'true')
        for bf in _VQI_CHECK_BOOLS:
            if bf == 'icu_bed_booked':
                if bf in present_keys or bf in post:
                    s[bf] = post.get(bf) in ('1', 'on', 'true')
            elif (form_present and not dos_partial) or (dos_partial and bf in present_keys) or bf in post:
                s[bf] = post.get(bf) in ('1', 'on', 'true')
        if post.get('case_outcome_status') in dict(CASE_OUTCOME_CHOICES):
            s['case_outcome_status'] = post.get('case_outcome_status')
        if post.get('vqi_30d_status') in dict(VQI_30D_CHOICES):
            s['vqi_30d_status'] = post.get('vqi_30d_status')
        if hasattr(post, 'getlist'):
            if (form_present and not dos_partial) or 'cancel_reason_chips' in post:
                s['cancel_reason_chips'] = [
                    k for k in post.getlist('cancel_reason_chips')
                    if k in CHIP_LABELS['cancel_reason_chips']
                ]
            if (form_present and not dos_partial) or 'vqi_complication_chips' in post:
                s['vqi_complication_chips'] = [
                    k for k in post.getlist('vqi_complication_chips')
                    if k in CHIP_LABELS['vqi_complication_chips']
                ]
                if s['vqi_complication_chips'] and s.get('vqi_30d_status') in ('pending', 'uneventful', None, ''):
                    s['vqi_30d_status'] = 'complication'

    def _save_dos_vqi(self, request, patient):
        from .. import evaluate

        ev_before = evaluate(patient)
        dos_check_keys = {c['key'] for c in ev_before['dos_checks']}
        s = merged_studies(patient)
        today = timezone.localdate()
        old_op_dt = s.get('planned_surgery_date')
        action = request.POST.get('action') or 'save_dos'
        form_present = 'dos_form_present' in request.POST
        dos_partial = request.POST.get('dos_partial') == '1'

        self._apply_dos_vqi_post(request.POST, s, today, dos_check_keys, form_present, dos_partial)

        if action == 'complete_and_board':
            s['case_outcome_status'] = 'operated'
            if not s.get('operated_date'):
                s['operated_date'] = today.isoformat()
            s['outcome_recorded_on'] = today.isoformat()
            patient.studies = s
            patient.updated_by = request.user
            patient.save(update_fields=['studies', 'updated_at', 'updated_by'])
            bp, _, added_tasks = admit_preop_to_board(
                patient, request.user,
                unit=request.POST.get('unit') or '',
                bed=request.POST.get('bed') or '',
            )
            messages.success(
                request,
                f'{patient.patient_name} — ოპერაცია დაფიქსირდა და პაციენტი გადაყვანილია დაფაზე ({bp.get_unit_display()}, +{added_tasks} დავალება).',
            )
            return HttpResponseRedirect(reverse('board-patient', args=[bp.pk]))

        if s.get('case_outcome_status') == 'operated' and not s.get('operated_date'):
            plan_dt = _date(s.get('planned_surgery_date'))
            s['operated_date'] = (plan_dt.isoformat() if (plan_dt and plan_dt <= today) else today.isoformat())
        if s.get('case_outcome_status') != 'scheduled' and not s.get('outcome_recorded_on'):
            s['outcome_recorded_on'] = today.isoformat()
        elif s.get('case_outcome_status') == 'scheduled':
            s['outcome_recorded_on'] = ''

        uf = ['studies', 'updated_at', 'updated_by']
        if s.get('planned_surgery_date') != old_op_dt:
            s['med_hold_agreed'] = False
            _invalidate_approval_if_needed(patient, uf)
        patient.studies = s
        patient.updated_by = request.user
        ev_after = evaluate(patient)
        if (
            ev_after.get('dos_hold_danger')
            or ev_after.get('has_hard_contraindication')
            or ev_after['clearance_seal']['code'] == 'stop'
            or ev_after.get('has_unacked_red')
            or ev_after['alerts_sig'] != ev_before['alerts_sig']
        ):
            _invalidate_approval_if_needed(patient, uf)
        _sync_med_hold_after_mutation(patient, ev_before, ev_after, save=False)
        patient.save(update_fields=list(dict.fromkeys(uf)))
        messages.success(request, 'ოპერაციის დილის კარიბჭე და 30-დღიანი VQI მონიტორინგი შეინახა.')
        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=dos')

    def _save_signoff(self, request, patient):
        from .. import evaluate

        action = request.POST.get('action') or 'save_anamnesis'
        can_approve_signoff = (
            request.user.is_superuser
            or request.user.is_staff
            or hasattr(request.user, 'profile')
            or patient.surgeon_id is None
            or patient.surgeon_id == request.user.pk
        )
        if action in ('approve', 'unapprove') and not can_approve_signoff:
            messages.error(request, 'ოპერაციაზე ქირურგის დასტურის/მოხსნის უფლება აქვს მხოლოდ მკურნალ ქირურგს ან ექიმს!')
            return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=doc')

        uf = ['updated_at', 'updated_by']
        if 'anamnesis' in request.POST:
            if action == 'regenerate':
                patient.anamnesis = ''
                uf.append('anamnesis')
            elif action in ('save_anamnesis', 'approve'):
                patient.anamnesis = clean_text(request.POST.get('anamnesis'), 4000)
                uf.append('anamnesis')

        ev = evaluate(patient)
        s = merged_studies(patient)

        if action == 'ack_alerts':
            if ev['clearance_seal']['code'] == 'stop' or ev.get('has_hard_contraindication'):
                messages.error(request, 'STOP-სიგნალის / აბსოლუტური უკუჩვენების გაცნობით დადასტურება დაუშვებელია — უკუჩვენება უნდა აღმოიფხვრას!')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=doc')
            s['alerts_ack_sig'] = ev['alerts_sig']
            patient.studies = s
            patient.alerts_ack_at = timezone.now()
            patient.alerts_ack_by = request.user
            uf.extend(['studies', 'alerts_ack_at', 'alerts_ack_by'])
            messages.success(request, 'გაფრთხილებები მონიშნულია როგორც გაცნობილი.')
        elif action == 'approve':
            if (
                not ev['q'].get('filled')
                or ev.get('has_hard_contraindication')
                or ev.get('dos_hold_danger')
                or ev.get('neuraxial_hard_blocked')
                or ev.get('has_blocking_unknown_state')
                or (ev.get('needs_stale_labs') and not s.get('stale_labs_refreshed'))
            ):
                messages.error(request, 'აბსოლუტური უკუჩვენების, STOP-სიგნალის, ხანდაზმული ანალიზების, ნეირაქსიული ბლოკირების ან შეუვსებელი კრიტიკული მონაცემის გამო დადასტურება დაბლოკილია!')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=doc')
            s['alerts_ack_sig'] = ev['alerts_sig']
            if ev.get('red_flags'):
                patient.alerts_ack_at = timezone.now()
                patient.alerts_ack_by = request.user
                uf.extend(['alerts_ack_at', 'alerts_ack_by'])
            if request.POST.get('move_ready') == '1':
                _update_hold_pause(s, patient.someday_list, PreopPatient.SomedayList.READY, started_on=patient.started_on)
                patient.someday_list = PreopPatient.SomedayList.READY
                patient.visit_date = None
                uf.extend(['someday_list', 'visit_date'])
            patient.studies = s
            patient.approved_at = timezone.now()
            patient.approved_by = request.user
            uf.extend(['studies', 'approved_at', 'approved_by'])
            messages.success(request, f'{patient.patient_name} — დადასტურებულია ოპერაციისთვის.')
        elif action == 'unapprove':
            patient.approved_at = None
            patient.approved_by = None
            uf.extend(['approved_at', 'approved_by'])
            messages.info(request, 'დადასტურება მოიხსნა.')
        else:
            messages.success(request, 'ანამნეზი შეინახა.')

        patient.updated_by = request.user
        patient.save(update_fields=list(dict.fromkeys(uf)))
        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=doc')

    def _save_self_link(self, request, patient):
        act = request.POST.get('action') or 'rotate'
        s = merged_studies(patient)
        uf = ['studies', 'updated_at', 'updated_by']
        if act == 'revoke':
            s['self_fill_revoked'] = True
            s['self_fill_reopened'] = False
            messages.info(request, 'პაციენტის თვითშევსების ბმული გაუქმდა.')
        else:
            s['self_fill_nonce'] = secrets.token_hex(4)
            s['self_fill_revoked'] = False
            s['self_fill_reopened'] = True
            messages.success(request, 'ახალი ერთჯერადი ბმული გენერირდა (წინა ბმული გაუქმდა).')
        patient.studies = s
        patient.updated_by = request.user
        patient.save(update_fields=uf)
        tab_ret = request.POST.get('tab') or 'q'
        if tab_ret not in ('q', 's1', 's2', 'doc', 'dos'):
            tab_ret = 'q'
        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + f'?tab={tab_ret}')

    @staticmethod
    def _build_rule_citations(ev):
        red_items = annotate_texts(ev['red_flags'])
        amber_items = annotate_texts(ev['amber_flags'])
        reflex_items = annotate_reflex_orders(ev['reflex_orders'])
        med_items = annotate_med_rows(ev['med_schedule'])
        dos_items = annotate_texts(ev['dos_warnings'], domain_hint='anaesthesia')
        score_rules = {
            'asa': 'SCORE_ASA_PHYSICAL_STATUS',
            'rcri': 'SCORE_RCRI_LEE_1999',
            'vsg_cri': 'SCORE_VSG_CRI_BERTGES_2010',
            'ariscat': 'SCORE_ARISCAT_CANET_2010',
            'caprini': 'SCORE_CAPRINI_VTE',
            'renal': 'SCORE_RENAL_CKDEPI_CG',
        }
        rules_map = {rid: rule_meta(rid) for rid in score_rules.values()}
        if ev['clearance_seal'].get('code') == 'stop':
            rules_map['MED_STOP_LOCK_SAFETY'] = rule_meta('MED_STOP_LOCK_SAFETY')
        if ev.get('unknown_state_flags'):
            rules_map['WF_UNKNOWN_STATE_FAILSAFE'] = rule_meta('WF_UNKNOWN_STATE_FAILSAFE')
        for group in (red_items, amber_items, reflex_items, dos_items):
            for item in group:
                meta = item.get('rule')
                if meta:
                    rules_map[meta['id']] = meta
        for item in med_items:
            meta = item.get('rule_meta')
            if meta:
                rules_map[meta['id']] = meta
        ordered_map = {k: rules_map[k] for k in sorted(rules_map)}
        return {
            'red_flag_items': red_items,
            'amber_flag_items': amber_items,
            'reflex_order_items': reflex_items,
            'med_schedule_items': med_items,
            'dos_warning_items': dos_items,
            'score_rules': score_rules,
            'page_rules_map': ordered_map,
            'rules_catalog_url': reverse('preop-rules'),
        }

    def _render(self, request, patient):
        from ...blood import BLOOD_GROUPS as BG_LIST
        from .. import evaluate

        ev = evaluate(patient)
        tab = request.GET.get('tab') or ('q' if not ev['q'].get('filled') else 's1')
        if tab not in ('q', 's1', 's2', 'doc', 'dos'):
            tab = 'q'
        auto_anamnesis = generate_anamnesis(patient, ev)
        files = list(patient.files.select_related('created_by', 'created_by__profile'))
        files_by_cat = []
        for key, label in PreopFile.Category.choices:
            cat_items = [f for f in files if f.category == key]
            if cat_items:
                files_by_cat.append({'key': key, 'label': label, 'files': cat_items})

        self_token = make_self_fill_token(patient)
        board_patient = resolve_board_patient(patient)
        default_board_unit = board_patient.unit if board_patient else (BoardPatient.ICU if ev['needs_icu_bed'] else BoardPatient.WARD)
        ctx = {
            'p': patient, 'ev': ev, 'q': ev['q'], 's': ev['s'], 'tab': tab,
            'auto_anamnesis': auto_anamnesis,
            'shown_anamnesis': patient.anamnesis or auto_anamnesis,
            'self_fill_path': reverse('preop-self-fill', args=[self_token]),
            'self_fill_revoked': bool(ev['s'].get('self_fill_revoked')),
            'self_fill_used': is_self_filled_q(ev['q']),
            'consent_url': consent_url_for_preop(patient),
            'board_patient': board_patient,
            'default_board_unit': default_board_unit,
            'chips': CHIP_CATALOGS, 'cfs_choices': CFS_CHOICES,
            'anesth_plan_choices': ANESTH_PLAN_CHOICES, 'asa_choices': ASA_CHOICES,
            'mallampati_choices': MALLAMPATI_CHOICES, 'stop_bang_choices': STOP_BANG_CHOICES,
            'surg_duration_choices': SURG_DURATION_CHOICES, 'cied_plan_choices': CIED_PLAN_CHOICES,
            'case_outcome_choices': CASE_OUTCOME_CHOICES, 'vqi_30d_choices': VQI_30D_CHOICES,
            'aaa_morphology_choices': AAA_MORPHOLOGY_CHOICES, 'aaa_neck_choices': AAA_NECK_CHOICES,
            'popliteal_screen_choices': POPLITEAL_SCREEN_CHOICES,
            'post_antithrombotic_choices': POST_ANTITHROMBOTIC_CHOICES,
            'mets_limit_choices': METS_LIMIT_CHOICES, 'duration_chips': DURATION_CHIPS,
            'ceap_choices': CEAP_CHOICES, 'doctors': doctors(),
            'op_subtype_groups': OP_SUBTYPE_GROUPS, 'side_choices': Case.Side.choices,
            'someday_choices': PreopPatient.SomedayList.choices[1:],
            'cardio_decisions': PreopPatient.CardioDecision.choices,
            'cardiologists': _cardiologist_choices(),
            'file_categories': PreopFile.Category.choices, 'files_by_cat': files_by_cat,
            'total_files': len(files), 'blood_groups': BG_LIST, 'today': timezone.localdate(),
        }
        ctx.update(self._build_rule_citations(ev))
        from ..nurse_ui import PREOP_UI_NEW, build_nurse_patient_context, get_preop_ui_mode
        if get_preop_ui_mode(request) == PREOP_UI_NEW:
            ev_with_path = dict(ev, self_fill_path=ctx['self_fill_path'])
            ctx.update(build_nurse_patient_context(request, patient, ev_with_path))
            return render(request, 'mdf/preop/patient_new.html', ctx)
        return render(request, self.template_name, ctx)
