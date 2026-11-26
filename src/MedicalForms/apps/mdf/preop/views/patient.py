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
from ..calculators import normalize_fibrinogen_gl, normalize_ldl_mmol
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
    _num,
    _resolve_pathway,
    _update_hold_pause,
    chip_summary,
    consent_url_for_preop,
    cta_live_months_age,
    make_self_fill_token,
    merged_q,
    merged_studies,
)
from .board import admit_preop_to_board, resolve_board_patient

_S1_NUM_FIELDS = (
    ('aaa_mm', 10, 200), ('cta_months', 0, 120),
    ('ven_gsv_mm', 1, 50), ('pad_gsv_mm', 0.5, 30),
    ('abi_r', 0.0, 3.0), ('abi_l', 0.0, 3.0),
    ('tp_r', 0, 400), ('tp_l', 0, 400),
    ('tbi_r', 0.0, 3.0), ('tbi_l', 0.0, 3.0),
    ('bp_sys', 20, 350), ('bp_dia', 15, 220),
    ('bp_sys_l', 20, 350), ('bp_dia_l', 15, 220),
    ('hr', 15, 260), ('spo2', 10, 100),
    ('weight_kg', 25, 260), ('height_cm', 100, 230),
    ('hb', 1, 260), ('inr', 0.4, 20),
    ('creat', 0.1, 3000), ('k', 0.5, 20), ('na', 95, 190),
    ('glucose', 0.5, 1000), ('hba1c', 3, 25),
    ('ferritin', 1, 5000), ('tsat', 1, 100),
    ('nt_probnp', 1, 100000), ('hs_ctn', 0.1, 50000),
    ('albumin', 0.5, 80),
    ('urine_wbc', 0, 2000),
    ('carotid_r', 0, 100), ('carotid_l', 0, 100), ('lvef', 5, 95),
)

_S1_BOOL_FIELDS = (
    'cta_disc', 'irregular_hr', 'aptt_prolonged', 'viral_positive',
    'urine_nitrite', 'carotid_symp', 'echo_valve_issue',
    'aaa_renal_vein_anom', 'abf_renal_stenosis',
    'ven_epifascial', 'ven_tortuous', 'ven_perforator',
)

_S1_SELECT_FIELDS = (
    ('liver_status', ('normal', '2x', '3x')),
    ('cxr', ('pending', 'normal', 'emphysema', 'infiltrate')),
    ('ecg', ('pending', 'normal', 'afib_controlled', 'ischemia_arrhythmia')),
    ('aaa_morphology', ('fusiform', 'saccular', 'rapid_growth', 'inflammatory', 'inflammatory_mycotic')),
    ('aaa_neck_status', ('pending', 'friendly_ifu', 'hostile_neck')),
    ('popliteal_screen_status', ('pending', 'normal', 'aneurysm_found')),
    ('aaa_iliac_access', ('pending', 'ok_7mm', 'narrow_calcified', 'iliac_aneurysm')),
    ('cea_cta_status', ('pending', 'concordant', 'high_calcified')),
    ('brain_imaging', ('pending', 'normal_small', 'large_or_hemorrhage')),
    ('abf_clamp_zone', ('pending', 'infrarenal', 'juxtarenal_calcified')),
    ('abf_outflow', ('pending', 'patent', 'profunda_stenosis', 'poor_outflow')),
    ('abf_mesenteric', ('normal', 'ima_dominant_iia_occ', 'sma_stenosis')),
    ('pad_runoff_status', ('pending', 'popliteal_patent', 'tibial_single', 'desert_foot')),
    ('pad_gsv_status', ('pending', 'adequate', 'inadequate')),
    ('pta_lesion_site', ('iliac', 'femoropopliteal', 'infrapopliteal', 'multilevel')),
    ('pta_access_site', ('pending', 'cfa_clean', 'cfa_calcified_scar')),
    ('ven_duplex_status', ('pending', 'deep_patent_reflux', 'acute_dvt', 'deep_obstruction', 'superficial_thrombus')),
    ('ven_target_vein', ('gsv', 'ssv', 'gsv_ssv', 'aagsv_trib')),
)

_S2_SELECT_FIELDS = (
    ('anesthesia_plan', ('auto', 'local', 'plexus', 'neuraxial', 'general')),
    ('asa_class', ('auto', 'I', 'II', 'III', 'IV')),
    ('airway_mallampati', ('I_II', 'III', 'IV_difficult')),
    ('stop_bang_risk', ('auto', 'low', 'intermediate', 'high')),
    ('surg_duration_est', ('auto', 'lt_2h', '2_3h', 'gt_3h')),
    ('cied_plan', ('pending', 'bipolar_only', 'magnet_async', 'ep_reprogram')),
    ('gastro_result', ('pending', 'remission', 'acute_ulcer')),
    ('laryngoscopy_res', ('pending', 'normal', 'palsy')),
    ('tsh_status', ('pending', 'euthyroid', 'hyperthyroid', 'hypothyroid_severe')),
    ('alt_vein_mapped', ('pending', 'contra_gsv_ok', 'arm_ssv_ok', 'no_autovein_prosthetic')),
    ('post_antithrombotic_plan', ('auto', 'voyager', 'dapt', 'sapt', 'oac_sapt')),
)

_S2_BOOL_FIELDS = (
    'urine_culture_done', 'blood_reserved', 'evar_sized', 'med_hold_agreed',
    'anesth_done', 'neuro_consult_done', 'wound_care_done',
    'vte_prophylaxis_planned', 'compression_ready',
    'anemia_panel_done', 'pbm_iv_iron_done', 'cell_saver_ready', 'ppi_started',
    'endo_consult_done', 'nephro_panel_done',
    'hydration_planned', 'dental_sanitation_done', 'decolonization_ready',
    'contrast_premed_done', 'dapt_ready', 'stale_labs_refreshed',
    'cea_antiplatelet_started', 'cied_checked',
)


def _parse_s1_numbers_and_dates(post, s, today):
    if 'wbc' in post:
        raw_wbc = _num(post.get('wbc'), lo=0.1, hi=150000)
        if raw_wbc is not None and raw_wbc >= 200:
            raw_wbc = round(raw_wbc / 1000.0, 2)
        s['wbc'] = _num(raw_wbc, lo=0.1, hi=200) if raw_wbc is not None else None
    if 'plt' in post:
        raw_plt = _num(post.get('plt'), lo=1, hi=2000000)
        if raw_plt is not None and raw_plt >= 1000:
            raw_plt = int(round(raw_plt / 1000.0))
        s['plt'] = _num(raw_plt, lo=1, hi=2500) if raw_plt is not None else None

    for key, lo, hi in _S1_NUM_FIELDS:
        if key in post:
            s[key] = _num(post.get(key), lo=lo, hi=hi)
    if 'fibrinogen' in post:
        s['fibrinogen'] = normalize_fibrinogen_gl(post.get('fibrinogen'))
    if 'ldl' in post:
        s['ldl'] = normalize_ldl_mmol(post.get('ldl'))

    if 'cta_date' in post:
        cd = _date(post.get('cta_date'))
        s['cta_date'] = cd.isoformat() if (cd and cd <= today) else ''
        if cd and cd <= today:
            s['cta_months'] = cta_live_months_age({'cta_date': s['cta_date']}, today=today)
    if 'labs_date' in post:
        ld = _date(post.get('labs_date'))
        s['labs_date'] = ld.isoformat() if (ld and ld <= today) else ''
        if ld and ld <= today and (today - ld).days <= 14:
            s['stale_labs_refreshed'] = False
    if 'creat_unit' in post and post.get('creat_unit') in ('', 'umol', 'mgdl'):
        s['creat_unit'] = post.get('creat_unit')


def _parse_s1_flags_and_selects(post, patient, s):
    for bf in _S1_BOOL_FIELDS:
        if bf in post or bf in ('irregular_hr', 'aptt_prolonged', 'viral_positive', 'carotid_symp', 'echo_valve_issue'):
            s[bf] = post.get(bf) in ('1', 'on', 'true')
        elif bf == 'cta_disc' and patient.pathway not in (PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA):
            s[bf] = False
        elif bf == 'aaa_renal_vein_anom' and patient.pathway == PreopPatient.Pathway.AAA_INFRA:
            s[bf] = False
        elif bf == 'abf_renal_stenosis' and patient.pathway == PreopPatient.Pathway.ABF:
            s[bf] = False
        elif bf in ('ven_epifascial', 'ven_tortuous', 'ven_perforator') and patient.pathway == PreopPatient.Pathway.VENOUS:
            s[bf] = False
        elif bf == 'urine_nitrite' and patient.pathway not in (PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA):
            s[bf] = False

    if 'blood_group' in post:
        s['blood_group'] = clean_text(post.get('blood_group'), 30)
    for sel_key, allowed in _S1_SELECT_FIELDS:
        if post.get(sel_key) in allowed:
            s[sel_key] = post.get(sel_key)


def _parse_s2_fields_and_booleans(post, patient, ev_before, s):
    for key, fn, lo, hi in (
        ('spiro_fev1', _int, 10, 150),
        ('abi_r', _num, 0.0, 3.0), ('abi_l', _num, 0.0, 3.0),
        ('tp_r', _int, 0, 400), ('tp_l', _int, 0, 400),
        ('tbi_r', _num, 0.0, 3.0), ('tbi_l', _num, 0.0, 3.0),
        ('hba1c', _num, 3, 25), ('ferritin', _num, 1, 5000), ('tsat', _num, 1, 100),
    ):
        if key in post:
            s[key] = fn(post.get(key), lo=lo, hi=hi)
    if 'planned_surgery_date' in post:
        op_d = _date(post.get('planned_surgery_date'))
        s['planned_surgery_date'] = op_d.isoformat() if op_d else ''
    for sel_key, allowed in _S2_SELECT_FIELDS:
        if post.get(sel_key) in allowed:
            s[sel_key] = post.get(sel_key)

    s2_bool_visible = {
        'anesth_done': True,
        'stale_labs_refreshed': bool(ev_before['needs_stale_labs']),
        'anemia_panel_done': bool(ev_before['needs_anemia_panel']),
        'pbm_iv_iron_done': bool(ev_before['pbm_iv_iron_indicated']),
        'endo_consult_done': bool(ev_before['needs_endo_reflex']),
        'nephro_panel_done': bool(ev_before['needs_nephro_panel']),
        'hydration_planned': bool(ev_before['needs_hydration']),
        'dental_sanitation_done': bool(ev_before['needs_dental']),
        'contrast_premed_done': bool(ev_before['needs_contrast_premed']),
        'cied_checked': bool(ev_before['needs_cied']),
        'cea_antiplatelet_started': bool(ev_before['pathway'] == 'cea' and ev_before['needs_cea_antiplatelet']),
        'vte_prophylaxis_planned': bool(ev_before['is_venous'] and ev_before['needs_vte_prophylaxis']),
        'compression_ready': bool(ev_before['is_venous'] and not ev_before.get('compression_contra')),
        'dapt_ready': bool(ev_before['is_pta'] and ev_before['q'].get('filled')),
        'neuro_consult_done': bool(ev_before['pathway'] == 'cea' and ev_before['needs_neuro']),
        'ppi_started': bool(ev_before['needs_ppi']),
        'urine_culture_done': bool(ev_before['uti_suspected']),
        'wound_care_done': bool(ev_before['needs_wound_care']),
        'blood_reserved': bool(ev_before['needs_blood']),
        'cell_saver_ready': bool(ev_before['needs_blood'] and ev_before['open_aortic']),
        'evar_sized': bool(ev_before['pathway'] == 'aaa_infra' and patient.op_subtype == 'evar'),
        'decolonization_ready': bool(ev_before['has_synthetic_implant']),
        'med_hold_agreed': bool(ev_before['needs_med_hold']),
    }
    for bf in _S2_BOOL_FIELDS:
        if bf in post:
            s[bf] = post.get(bf) in ('1', 'on', 'true')
        elif s2_bool_visible.get(bf, True):
            s[bf] = False
    if (s.get('cied_plan') or 'pending') != 'pending':
        s['cied_checked'] = True

    cab_present = 'anesth_cabinet_present' in post
    if cab_present or 'asa_emergency' in post:
        s['asa_emergency'] = post.get('asa_emergency') in ('1', 'on', 'true')
    if 'icu_bed_field_present' in post or 'icu_bed_booked' in post:
        s['icu_bed_booked'] = post.get('icu_bed_booked') in ('1', 'on', 'true')
    return cab_present


def _parse_s2_notes_and_cardio(post, patient, s, cab_present, old_nurse_chips, old_nurse_chip_txt, old_cardio_chips, old_cardio_chip_txt):
    if hasattr(post, 'getlist'):
        s['nurse_note_chips'] = [k for k in post.getlist('nurse_note_chips') if k in CHIP_LABELS['nurse_note_chips']]
        s['cardio_note_chips'] = [k for k in post.getlist('cardio_note_chips') if k in CHIP_LABELS['cardio_note_chips']]
        if cab_present or 'anesth_lines_chips' in post:
            s['anesth_lines_chips'] = [k for k in post.getlist('anesth_lines_chips') if k in CHIP_LABELS['anesth_lines_chips']]

    new_nurse_chip_txt = chip_summary(s.get('nurse_note_chips'), 'nurse_note_chips')
    if 'nurse_note_custom' in post:
        custom_nn = clean_text(post.get('nurse_note_custom'), 1000)
        s['nurse_note_custom'] = custom_nn
        s['nurse_note'] = '; '.join([x for x in (new_nurse_chip_txt, custom_nn) if x])[:1000]
    else:
        raw_nurse = clean_text(post.get('nurse_note'), 1000)
        if raw_nurse == old_nurse_chip_txt and s['nurse_note_chips'] != old_nurse_chips:
            raw_nurse = ''
        s['nurse_note_custom'] = '' if raw_nurse == new_nurse_chip_txt else raw_nurse
        s['nurse_note'] = raw_nurse or new_nurse_chip_txt

    uf = ['studies', 'updated_at', 'updated_by']
    if 'cardiologist' in post or 'cardiologist_custom' in post:
        cardio_name = clean_text(post.get('cardiologist_custom') or post.get('cardiologist'), 100).replace('\n', ' ')
        patient.cardiologist = cardio_name or 'ლიკა ინჯია'
        uf.append('cardiologist')
    if 'cardio_decision' in post:
        dec = post.get('cardio_decision') or ''
        if dec in PreopPatient.CardioDecision.values:
            old_dec = patient.cardio_decision
            patient.cardio_decision = dec
            if dec and not patient.cardio_date:
                patient.cardio_date = timezone.localdate()
            elif not dec:
                patient.cardio_date = None
            uf.extend(['cardio_decision', 'cardio_date'])
            if dec != old_dec and dec in ('stress', 'cag', 'pci'):
                patient.alerts_ack_at = None
                s['alerts_ack_sig'] = ''
                uf.append('alerts_ack_at')

    new_cardio_chip_txt = chip_summary(s.get('cardio_note_chips'), 'cardio_note_chips')
    if 'cardio_note_custom' in post:
        custom_cn = clean_text(post.get('cardio_note_custom'), 255).replace('\n', ' ')
        s['cardio_note_custom'] = custom_cn
        patient.cardio_note = '; '.join([x for x in (new_cardio_chip_txt, custom_cn) if x])[:255]
        uf.append('cardio_note')
    elif 'cardio_note' in post or 'cardio_note_chips' in post:
        raw_cn = clean_text(post.get('cardio_note'), 255).replace('\n', ' ')
        if raw_cn == old_cardio_chip_txt and s['cardio_note_chips'] != old_cardio_chips:
            raw_cn = ''
        s['cardio_note_custom'] = '' if raw_cn == new_cardio_chip_txt else raw_cn
        patient.cardio_note = (raw_cn or new_cardio_chip_txt)[:255]
        uf.append('cardio_note')
    return uf


def _invalidate_approval_if_needed(patient, uf):
    if patient.approved_at is not None:
        patient.approved_at = None
        patient.approved_by = None
        uf.extend(['approved_at', 'approved_by'])
        if patient.someday_list == PreopPatient.SomedayList.READY:
            patient.someday_list = PreopPatient.SomedayList.CONF
            uf.append('someday_list')


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
        patient = get_object_or_404(PreopPatient, pk=pk)
        section = request.POST.get('section') or 'header'

        with _PREOP_MUTATION_LOCK, transaction.atomic():
            _sqlite_acquire_write_lock(patient.pk)
            patient = PreopPatient.objects.select_for_update().get(pk=patient.pk)

            if section == 'header':
                self._save_header(request, patient)
                messages.success(request, 'პაციენტის მონაცემები განახლდა.')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + f"?tab={request.POST.get('tab', 'q')}")

            if section in ('questionnaire', 'q'):
                self._save_questionnaire(request, patient)
                messages.success(request, 'კითხვარები შეინახა — განახლდა ანამნეზი და მომზადების გეგმა.')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=s1')

            if section == 'stage1':
                from .. import evaluate
                from ..nurse_plan import summarize_stage1_clinical_delta
                from ..nurse_ui import PREOP_UI_NEW, get_preop_ui_mode, med_schedule_signature

                ev_before = evaluate(patient)
                self._save_stage1(request, patient)
                ev_after = evaluate(patient)
                if patient.studies.get('nurse_med_called_sig') and patient.studies.get('nurse_med_called_sig') != med_schedule_signature(ev_after):
                    s_sync = merged_studies(patient)
                    s_sync['med_hold_agreed'] = False
                    patient.studies = s_sync
                    patient.save(update_fields=['studies', 'updated_at'])
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
                self._save_stage2(request, patient)
                messages.success(request, 'მომზადების გეგმა და კონსულტაციები შეინახა.')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=s2')

            if section == 'signoff':
                return self._save_signoff(request, patient)

            if section == 'dos_vqi':
                return self._save_dos_vqi(request, patient)

            if section == 'self_link':
                return self._save_self_link(request, patient)

        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]))

    def _save_header(self, request, patient):
        s = merged_studies(patient)
        today = timezone.localdate()
        old_someday = patient.someday_list
        old_side = patient.side
        old_sub = patient.op_subtype
        old_pw = _resolve_pathway(patient)
        was_unstarted = (patient.started_on is None)

        name = clean_text(request.POST.get('patient_name'), 150).replace('\n', ' ')
        if name:
            patient.patient_name = name
        age_in = _int(request.POST.get('age'), lo=1, hi=120)
        if 'person_id' in request.POST:
            s['person_id'] = _clean_person_id(request.POST.get('person_id'))
        if 'birth_date' in request.POST:
            bd = _date(request.POST.get('birth_date'))
            s['birth_date'] = bd.isoformat() if (bd and bd <= today) else ''
            if age_in is None and bd:
                age_in = _age_from_dob(bd)
        if age_in is not None or 'age' in request.POST:
            patient.age = age_in
        if 'planned_surgery_date' in request.POST:
            op_d = _date(request.POST.get('planned_surgery_date'))
            s['planned_surgery_date'] = op_d.isoformat() if op_d else ''

        sex = request.POST.get('sex') or ''
        if sex in ('M', 'F', ''):
            patient.sex = sex
        if 'side' in request.POST and request.POST.get('side') in ('', 'R', 'L', 'B'):
            patient.side = request.POST.get('side')
        patient.phone = clean_text(request.POST.get('phone'), 50).replace('\n', ' ')
        op_sub = request.POST.get('op_subtype') or ''
        if op_sub in PreopPatient.OpSubtype.values:
            patient.op_subtype = op_sub
        surg_raw = request.POST.get('surgeon') or ''
        patient.surgeon = get_user_model().objects.filter(pk=int(surg_raw)).first() if surg_raw.isdigit() else None

        slot_kind = request.POST.get('slot_kind') or ''
        if slot_kind.startswith('someday:'):
            key = slot_kind.split(':', 1)[1]
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
        if patient.side != old_side or patient.op_subtype != old_sub:
            s['dos_site_marked'] = False
            s['alerts_ack_sig'] = ''
            patient.approved_at = None
            patient.approved_by = None
            patient.alerts_ack_at = None
            patient.alerts_ack_by = None
            patient.anamnesis = ''
            uf.extend(['approved_at', 'approved_by', 'alerts_ack_at', 'alerts_ack_by', 'anamnesis'])
        if new_pw != old_pw and new_pw in (PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA):
            s['carotid_symp'] = False

        patient.studies = s
        patient.updated_by = request.user
        patient.save(update_fields=uf)

    def _save_questionnaire(self, request, patient):
        apply_questionnaire_post(request.POST, patient, person_name(request.user))
        patient.updated_by = request.user
        patient.anamnesis = ''
        uf = ['questionnaire', 'studies', 'side', 'started_on', 'anamnesis', 'updated_at', 'updated_by']
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
        _invalidate_approval_if_needed(patient, uf)
        patient.save(update_fields=list(dict.fromkeys(uf)))

    def _save_stage2(self, request, patient):
        from .. import evaluate

        ev_before = evaluate(patient)
        s = merged_studies(patient)
        old_nurse_chips = list(s.get('nurse_note_chips') or [])
        old_nurse_chip_txt = chip_summary(old_nurse_chips, 'nurse_note_chips')
        old_cardio_chips = list(s.get('cardio_note_chips') or [])
        old_cardio_chip_txt = chip_summary(old_cardio_chips, 'cardio_note_chips')

        cab_present = _parse_s2_fields_and_booleans(request.POST, patient, ev_before, s)
        uf = _parse_s2_notes_and_cardio(
            request.POST, patient, s, cab_present,
            old_nurse_chips, old_nurse_chip_txt, old_cardio_chips, old_cardio_chip_txt,
        )
        patient.studies = s
        patient.updated_by = request.user
        _invalidate_approval_if_needed(patient, uf)
        patient.save(update_fields=list(dict.fromkeys(uf)))

    def _save_dos_vqi(self, request, patient):
        s = merged_studies(patient)
        today = timezone.localdate()
        action = request.POST.get('action') or 'save_dos'
        form_present = 'dos_form_present' in request.POST

        if 'planned_surgery_date' in request.POST:
            op_d = _date(request.POST.get('planned_surgery_date'))
            s['planned_surgery_date'] = op_d.isoformat() if op_d else ''
        if 'operated_date' in request.POST:
            done_d = _date(request.POST.get('operated_date'))
            s['operated_date'] = done_d.isoformat() if (done_d and done_d <= today) else ''

        for key, lo, hi in (
            ('dos_bp_sys', 20, 350),
            ('dos_bp_dia', 15, 220),
            ('dos_glucose', 0.5, 1000),
            ('dos_inr', 0.4, 20),
        ):
            if key in request.POST:
                s[key] = _num(request.POST.get(key), lo=lo, hi=hi)

        for bf in (
            'dos_identity_consent', 'dos_site_marked', 'dos_fasting_confirmed',
            'dos_med_hold_verified', 'dos_ketones_ok', 'dos_k_verified', 'dos_latex_free_or',
            'dos_bhcg_negative', 'dos_abx_ready', 'dos_blood_implant_ready',
            'icu_bed_booked',
            'vqi_discharge_antiplatelet', 'vqi_discharge_statin',
            'vqi_smoking_cessation', 'vqi_surveillance_scheduled',
        ):
            if form_present or bf in request.POST:
                s[bf] = request.POST.get(bf) in ('1', 'on', 'true')

        if request.POST.get('case_outcome_status') in dict(CASE_OUTCOME_CHOICES):
            s['case_outcome_status'] = request.POST.get('case_outcome_status')
        if request.POST.get('vqi_30d_status') in dict(VQI_30D_CHOICES):
            s['vqi_30d_status'] = request.POST.get('vqi_30d_status')

        if hasattr(request.POST, 'getlist'):
            if form_present or 'cancel_reason_chips' in request.POST:
                s['cancel_reason_chips'] = [
                    k for k in request.POST.getlist('cancel_reason_chips')
                    if k in CHIP_LABELS['cancel_reason_chips']
                ]
            if form_present or 'vqi_complication_chips' in request.POST:
                s['vqi_complication_chips'] = [
                    k for k in request.POST.getlist('vqi_complication_chips')
                    if k in CHIP_LABELS['vqi_complication_chips']
                ]
                if s['vqi_complication_chips'] and s.get('vqi_30d_status') in ('pending', 'uneventful', None, ''):
                    s['vqi_30d_status'] = 'complication'

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

        patient.studies = s
        patient.updated_by = request.user
        patient.save(update_fields=['studies', 'updated_at', 'updated_by'])
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
            s['alerts_ack_sig'] = ev['alerts_sig']
            patient.studies = s
            patient.alerts_ack_at = timezone.now()
            patient.alerts_ack_by = request.user
            uf.extend(['studies', 'alerts_ack_at', 'alerts_ack_by'])
            messages.success(request, 'გაფრთხილებები მონიშნულია როგორც გაცნობილი.')
        elif action == 'approve':
            if ev.get('has_hard_contraindication') or ev.get('dos_hold_danger') or ev.get('neuraxial_hard_blocked'):
                messages.error(request, 'აბსოლუტური უკუჩვენების, STOP-სიგნალის ან ნეირაქსიული ბლოკირების გამო ოპერაციაზე დადასტურება დაბლოკილია!')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=doc')
            s['alerts_ack_sig'] = ev['alerts_sig']
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
        q = merged_q(patient)
        s = merged_studies(patient)
        uf = ['studies', 'updated_at', 'updated_by']
        if act == 'revoke':
            s['self_fill_revoked'] = True
            messages.info(request, 'პაციენტის თვითშევსების ბმული გაუქმდა.')
        else:
            s['self_fill_nonce'] = secrets.token_hex(4)
            s['self_fill_revoked'] = False
            q['self_fill_submitted'] = False
            patient.questionnaire = q
            uf.append('questionnaire')
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
            'self_fill_used': bool(ev['q'].get('self_fill_submitted')),
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
