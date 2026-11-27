"""POST field parsers and approval invalidation helpers for PreopPatientView."""
from django.contrib.auth import get_user_model
from django.utils import timezone

from ...board import clean_text
from ...models import PreopPatient, StaffProfile
from ..calculators import normalize_fibrinogen_gl, normalize_ldl_mmol
from ..constants import CHIP_LABELS
from ..registry import rule_value
from ..utils import _date, _int, _num, chip_summary, cta_live_months_age

_S1_NUM_FIELDS = (
    ('aaa_mm', 10, 200), ('cta_months', 0, 240),
    ('ven_gsv_mm', 1, 50), ('pad_gsv_mm', 0.5, 30),
    ('abi_r', 0.0, 3.0), ('abi_l', 0.0, 3.0),
    ('tp_r', 0, 400), ('tp_l', 0, 400),
    ('tbi_r', 0.0, 3.0), ('tbi_l', 0.0, 3.0),
    ('bp_sys', 30, 320), ('bp_dia', 20, 220),
    ('bp_sys_l', 30, 320), ('bp_dia_l', 20, 220),
    ('hr', 15, 260), ('spo2', 30, 100),
    ('weight_kg', 25, 260), ('height_cm', 100, 230),
    ('hb', 3.5, 260), ('inr', 0.4, 20),
    ('creat', 0.1, 3000), ('k', 1.0, 12.0), ('na', 95, 190),
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


def _set_num_field(s, key, raw, lo=None, hi=None, norm_fn=None):
    raw_inv = s.get('_invalid_inputs')
    inv = dict(raw_inv) if isinstance(raw_inv, dict) else {}
    if raw is None or str(raw).strip() == '':
        s[key] = None
        inv.pop(key, None)
    else:
        parsed = norm_fn(raw) if norm_fn else _num(raw, lo=lo, hi=hi)
        s[key] = parsed
        if parsed is None:
            inv[key] = clean_text(raw, 24)
        else:
            inv.pop(key, None)
    if inv:
        s['_invalid_inputs'] = inv
    else:
        s.pop('_invalid_inputs', None)


def _norm_plt_post(raw):
    from ..utils import parse_plt_k
    return parse_plt_k(raw)


def _norm_wbc_post(raw):
    from ..utils import parse_wbc_k
    return parse_wbc_k(raw)


def _parse_s1_numbers_and_dates(post, s, today):
    if 'wbc' in post:
        _set_num_field(s, 'wbc', post.get('wbc'), norm_fn=_norm_wbc_post)
    if 'plt' in post:
        _set_num_field(s, 'plt', post.get('plt'), norm_fn=_norm_plt_post)
    for key, lo, hi in _S1_NUM_FIELDS:
        if key in post:
            _set_num_field(s, key, post.get(key), lo=lo, hi=hi)
    if 'fibrinogen' in post:
        _set_num_field(s, 'fibrinogen', post.get('fibrinogen'), norm_fn=normalize_fibrinogen_gl)
    if 'ldl' in post:
        _set_num_field(s, 'ldl', post.get('ldl'), norm_fn=normalize_ldl_mmol)
    if 'cta_date' in post:
        raw_cd = (post.get('cta_date') or '').strip()
        cd = _date(raw_cd)
        raw_inv = s.get('_invalid_inputs')
        inv = dict(raw_inv) if isinstance(raw_inv, dict) else {}
        if not raw_cd:
            s['cta_date'] = ''
            inv.pop('cta_date', None)
        elif cd and cd <= today:
            s['cta_date'] = cd.isoformat()
            s['cta_months'] = cta_live_months_age({'cta_date': s['cta_date']}, today=today)
            inv.pop('cta_date', None)
        else:
            s['cta_date'] = ''
            inv['cta_date'] = clean_text(raw_cd, 24)
        if inv:
            s['_invalid_inputs'] = inv
        else:
            s.pop('_invalid_inputs', None)
    if 'labs_date' in post:
        raw_ld = (post.get('labs_date') or '').strip()
        ld = _date(raw_ld)
        raw_inv = s.get('_invalid_inputs')
        inv = dict(raw_inv) if isinstance(raw_inv, dict) else {}
        if not raw_ld:
            s['labs_date'] = ''
            inv.pop('labs_date', None)
        elif ld and ld <= today:
            old_ld = s.get('labs_date') or ''
            s['labs_date'] = ld.isoformat()
            inv.pop('labs_date', None)
            if s['labs_date'] != old_ld or (today - ld).days <= rule_value('INF_STALE_LABS_DAYS'):
                s['stale_labs_refreshed'] = False
        else:
            s['labs_date'] = ''
            inv['labs_date'] = clean_text(raw_ld, 24)
        if inv:
            s['_invalid_inputs'] = inv
        else:
            s.pop('_invalid_inputs', None)
    if 'creat_unit' in post and post.get('creat_unit') in ('', 'umol', 'mgdl'):
        s['creat_unit'] = post.get('creat_unit')


def med_hold_agreement_block_reason(ev, s):
    """Return Georgian error string if med_hold_agreed / nurse med-call confirmation is clinically unsafe."""
    if ev.get('archived'):
        return 'დაარქივებულ ბარათზე წამლების შეჩერების სქემის დადასტურება დაბლოკილია — ჯერ აღადგინეთ არქივიდან!'
    out_st = ev.get('case_outcome') or (s.get('case_outcome_status') if isinstance(s, dict) else '') or 'scheduled'
    if out_st != 'scheduled':
        return 'პაციენტი აღარ არის დაგეგმილ სტატუსში (გადადებულია/გაუქმებულია/ნაოპერაციებია) — წამლების შეჩერების სქემის დადასტურება დაბლოკილია!'
    if not ev.get('needs_med_hold'):
        return 'ამ პაციენტს შესაჩერებელი მედიკამენტების გრაფიკი არ აქვს.'
    if ev['clearance_seal']['code'] == 'stop' or ev.get('has_hard_contraindication'):
        return 'STOP-სიგნალის დროს წამლების შეწყვეტა დაბლოკილია — ჯერ ქირურგს დაუკავშირდით!'
    if ev.get('neuraxial_hard_blocked'):
        return 'ნეირაქსიული ბლოკადა დაბლოკილია — წამლების შეჩერების სქემა ჯერ ანესთეზიოლოგთან/ქირურგთან შეათანხმეთ!'
    if not ev['q'].get('filled'):
        return 'კითხვარი ჯერ არ არის შევსებული — წამლების გეგმის დადასტურება შეუძლებელია!'
    if any(
        u.get('key') in ('unverified_self_fill', 'renal_drug_no_crcl', 'ambiguous_creat_unit')
        for u in (ev.get('unknown_state_flags') or ())
    ):
        return 'პაციენტის კითხვარი დაუდასტურებელია ან CrCl/კრეატინინის ერთეული აკლია — წამლების გეგმა ჯერ გადაამოწმეთ!'
    if any(r.get('contraindicated') or r.get('unstructured') for r in (ev.get('med_schedule') or ())):
        return 'წამლების გრაფიკი შეიცავს უკუნაჩვენებ ან დაუზუსტებელ პრეპარატს — ჯერ შეათანხმეთ ექიმთან!'
    if ev.get('has_elapsed_hold_window'):
        return 'წამლის შეჩერების ფანჯარა უკვე ამოწურულია — შეათანხმეთ ქირურგთან ოპერაციის გადადება ან ტაქტიკა!'
    if any(r.get('hold_or_skip') and not r.get('reconcile_only') for r in (ev.get('med_schedule') or ())) and not _date(s.get('planned_surgery_date')):
        return 'შესაჩერებელი წამლებისთვის ჯერ მიუთითეთ დაგეგმილი ოპერაციის თარიღი!'
    return ''


def is_doctor_user(user):
    """Return True if user is authorized as a clinician (superuser, staff, or has StaffProfile)."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser or user.is_staff:
        return True
    return StaffProfile.objects.filter(user_id=user.pk).exists()


def resolve_doctor_user(raw_id):
    """Resolve a posted surgeon PK only if it belongs to a valid clinician account."""
    surg_id = _int(raw_id, lo=1)
    if surg_id is None:
        return None
    cand = get_user_model().objects.filter(pk=surg_id).first()
    return cand if (cand and is_doctor_user(cand)) else None


def preop_admission_block_reason(ev, s):
    """Canonical safety gate for Board handoff, complete_and_board, and case_outcome='operated'."""
    from ..registry import rule_value

    if ev.get('archived'):
        return 'დაარქივებულ ბარათზე ოპერაცია/დაფაზე გადაყვანა დაბლოკილია — ჯერ აღადგინეთ არქივიდან!'
    if not (ev.get('q') or {}).get('filled'):
        return 'პაციენტის კითხვარი ჯერ არ არის შევსებული (ალერგიები/მედიკამენტები დაუზუსტებელია) — ოპერაცია/დაფაზე გადაყვანა დაბლოკილია!'
    out_st = ev.get('case_outcome') or (s.get('case_outcome_status') if isinstance(s, dict) else '') or 'scheduled'
    if out_st.startswith('cancelled_') or out_st == 'deferred':
        return 'გაუქმებული ან გადადებული პაციენტის ოპერაციაზე/დაფაზე გადაყვანა დაბლოკილია!'
    if (
        ev['clearance_seal']['code'] == 'stop'
        or ev.get('has_hard_contraindication')
        or ev.get('dos_hold_danger')
        or ev.get('neuraxial_hard_blocked')
        or ev.get('solo_neuraxial_anatomic_mismatch')
    ):
        return 'STOP-სიგნალის, ნეირაქსიული ბლოკირების ან ოპერაციის დილის კრიტიკული ბლოკირების ფონზე ოპერაციის/დაფის მოქმედება დაბლოკილია!'
    if any(r.get('contraindicated') or r.get('unstructured') for r in (ev.get('med_schedule') or ())):
        return 'წამლების გრაფიკი შეიცავს უკუნაჩვენებ ან დაუზუსტებელ პრეპარატს — ოპერაცია/დაფაზე გადაყვანა დაბლოკილია!'
    if ev.get('has_elapsed_hold_window'):
        return 'წამლის შეჩერების ფანჯარა ამოწურულია — ოპერაცია/დაფაზე გადაყვანა დაბლოკილია!'
    if ev.get('has_blocking_unknown_state'):
        return 'დაუდასტურებელი კითხვარის ან კრიტიკული მონაცემის ნაკლებობის გამო ოპერაცია/დაფაზე გადაყვანა დაბლოკილია!'
    if ev.get('needs_stale_labs') and not s.get('stale_labs_refreshed'):
        stale_d = rule_value('INF_STALE_LABS_DAYS')
        return f'ანალიზები ხანდაზმულია (>{stale_d} დღე) — განახლებამდე ოპერაცია/დაფაზე გადაყვანა დაბლოკილია!'
    return ''


def can_enter_ready_queue(patient, ev):
    """Canonical readiness gate for moving a patient into someday:ready."""
    s = ev.get('s') or {}
    return bool(
        not patient.archived
        and not ev.get('archived')
        and patient.approved_at
        and ev.get('case_outcome', 'scheduled') == 'scheduled'
        and ev.get('workup_ready')
        and ev['clearance_seal']['code'] != 'stop'
        and not ev.get('has_hard_contraindication')
        and not ev.get('has_blocking_unknown_state')
        and not ev.get('dos_hold_danger')
        and not ev.get('neuraxial_hard_blocked')
        and not ev.get('has_unacked_red')
        and not (ev.get('needs_stale_labs') and not s.get('stale_labs_refreshed'))
    )


def _parse_s1_flags_and_selects(post, patient, s):
    partial = post.get('s1_partial') == '1'
    for bf in _S1_BOOL_FIELDS:
        if partial:
            if bf in post:
                s[bf] = post.get(bf) in ('1', 'on', 'true')
        elif bf in post or bf in ('irregular_hr', 'aptt_prolonged', 'viral_positive'):
            s[bf] = post.get(bf) in ('1', 'on', 'true')
        elif bf == 'echo_valve_issue' and (patient.pathway not in (PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA) or 'echo_valve_issue_presented' in post):
            s[bf] = False
        elif bf == 'carotid_symp' and patient.pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS):
            s[bf] = False
        elif bf == 'cta_disc' and patient.pathway not in (PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA):
            s[bf] = False
        elif bf == 'aaa_renal_vein_anom' and patient.pathway == PreopPatient.Pathway.AAA_INFRA:
            s[bf] = False
        elif bf == 'abf_renal_stenosis' and patient.pathway == PreopPatient.Pathway.ABF:
            s[bf] = False
        elif bf in ('ven_epifascial', 'ven_tortuous', 'ven_perforator') and patient.pathway == PreopPatient.Pathway.VENOUS:
            s[bf] = False
        elif bf == 'urine_nitrite' and (patient.pathway not in (PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA) or 'urine_nitrite_presented' in post):
            s[bf] = False

    if 'blood_group' in post:
        s['blood_group'] = clean_text(post.get('blood_group'), 30)
    for sel_key, allowed in _S1_SELECT_FIELDS:
        if post.get(sel_key) in allowed:
            s[sel_key] = post.get(sel_key)
    if post.get('pad_gsv_status') == 'adequate' and s.get('alt_vein_mapped') not in (None, '', 'pending'):
        gsv_mm_v = _num(s.get('pad_gsv_mm'), 0.5, 30)
        if gsv_mm_v is None or gsv_mm_v >= rule_value('PAD_GSV_MIN_MM')['gsv_min_mm']:
            s['alt_vein_mapped'] = 'pending'


def _set_planned_surgery_date_field(s, raw_val, today):
    raw_op = (raw_val or '').strip()
    op_d = _date(raw_op)
    raw_inv = s.get('_invalid_inputs')
    inv = dict(raw_inv) if isinstance(raw_inv, dict) else {}
    is_scheduled = (s.get('case_outcome_status') or 'scheduled') == 'scheduled'
    if not raw_op:
        s['planned_surgery_date'] = ''
        inv.pop('planned_surgery_date', None)
    elif op_d is None or (is_scheduled and op_d < today):
        inv['planned_surgery_date'] = clean_text(raw_op, 24)
    else:
        s['planned_surgery_date'] = op_d.isoformat()
        inv.pop('planned_surgery_date', None)
    if inv:
        s['_invalid_inputs'] = inv
    else:
        s.pop('_invalid_inputs', None)


def _parse_s2_fields_and_booleans(post, patient, ev_before, s):
    for key, fn, lo, hi in (
        ('spiro_fev1', _num, 10, 160),
        ('abi_r', _num, 0.0, 3.0), ('abi_l', _num, 0.0, 3.0),
        ('tp_r', _num, 0, 400), ('tp_l', _num, 0, 400),
        ('tbi_r', _num, 0.0, 3.0), ('tbi_l', _num, 0.0, 3.0),
        ('hba1c', _num, 3, 25), ('ferritin', _num, 1, 5000), ('tsat', _num, 1, 100),
    ):
        if key in post:
            _set_num_field(s, key, post.get(key), lo=lo, hi=hi, norm_fn=(lambda v, f=fn, a=lo, b=hi: f(v, lo=a, hi=b)))
    if 'planned_surgery_date' in post:
        _set_planned_surgery_date_field(s, post.get('planned_surgery_date'), timezone.localdate())
    for sel_key, allowed in _S2_SELECT_FIELDS:
        if post.get(sel_key) in allowed:
            s[sel_key] = post.get(sel_key)

    s2_full = (post.get('s2_full_form') == '1')
    s2_bool_visible = {
        'anesth_done': True,
        'stale_labs_refreshed': bool(ev_before['needs_stale_labs']),
        'anemia_panel_done': bool(ev_before['needs_anemia_panel']),
        'pbm_iv_iron_done': bool(ev_before.get('pbm_iron_required')),
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
            val = post.get(bf) in ('1', 'on', 'true')
            if bf == 'med_hold_agreed' and val and not ev_before.get('needs_med_hold'):
                val = False
            s[bf] = val
        elif s2_full and s2_bool_visible.get(bf, True):
            s[bf] = False
    if s.get('med_hold_agreed'):
        if not s.get('med_hold_agreed_date'):
            s['med_hold_agreed_date'] = timezone.localdate().isoformat()
    else:
        s['med_hold_agreed_date'] = ''
    if (s.get('cied_plan') or 'pending') != 'pending':
        s['cied_checked'] = True

    cab_present = 'anesth_cabinet_present' in post
    if cab_present or 'asa_emergency' in post:
        s['asa_emergency'] = post.get('asa_emergency') in ('1', 'on', 'true')
    if 'icu_bed_field_present' in post or 'icu_bed_booked' in post:
        s['icu_bed_booked'] = post.get('icu_bed_booked') in ('1', 'on', 'true')
    return cab_present


def _parse_s2_notes_and_cardio(post, patient, s, cab_present, old_nurse_chips, old_nurse_chip_txt, old_cardio_chips, old_cardio_chip_txt):
    s2_full = (post.get('s2_full_form') == '1')
    if hasattr(post, 'getlist'):
        if s2_full or 'nurse_note_chips' in post or 'nurse_note_custom' in post:
            s['nurse_note_chips'] = [k for k in post.getlist('nurse_note_chips') if k in CHIP_LABELS['nurse_note_chips']]
        if s2_full or 'cardio_note_chips' in post or 'cardio_note_custom' in post:
            s['cardio_note_chips'] = [k for k in post.getlist('cardio_note_chips') if k in CHIP_LABELS['cardio_note_chips']]
        if cab_present or 'anesth_lines_chips' in post:
            s['anesth_lines_chips'] = [k for k in post.getlist('anesth_lines_chips') if k in CHIP_LABELS['anesth_lines_chips']]

    new_nurse_chip_txt = chip_summary(s.get('nurse_note_chips'), 'nurse_note_chips')
    if 'nurse_note_custom' in post:
        custom_nn = clean_text(post.get('nurse_note_custom'), 1000)
        s['nurse_note_custom'] = custom_nn
        s['nurse_note'] = '; '.join([x for x in (new_nurse_chip_txt, custom_nn) if x])[:1000]
    elif s2_full or 'nurse_note' in post or 'nurse_note_chips' in post:
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
    elif s2_full or 'cardio_note' in post or 'cardio_note_chips' in post:
        raw_cn = clean_text(post.get('cardio_note'), 255).replace('\n', ' ')
        if raw_cn == old_cardio_chip_txt and s['cardio_note_chips'] != old_cardio_chips:
            raw_cn = ''
        s['cardio_note_custom'] = '' if raw_cn == new_cardio_chip_txt else raw_cn
        patient.cardio_note = (raw_cn or new_cardio_chip_txt)[:255]
        uf.append('cardio_note')
    return uf


def _invalidate_approval_if_needed(patient, uf, clear_anamnesis=False):
    if clear_anamnesis and patient.anamnesis:
        patient.anamnesis = ''
        uf.append('anamnesis')
    if patient.approved_at is not None:
        patient.approved_at = None
        patient.approved_by = None
        uf.extend(['approved_at', 'approved_by'])
        if patient.someday_list == PreopPatient.SomedayList.READY:
            patient.someday_list = PreopPatient.SomedayList.CONF
            uf.append('someday_list')
            return 'ready_demoted'
        return 'approval_cleared'
    return ''
