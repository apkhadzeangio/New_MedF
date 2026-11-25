"""Pre-Pilot Large-Scale Synthetic Simulation & Clinical Objectivity Engine (50,000+ Scenarios).

Runs 100% in-memory against unsaved ``PreopPatient`` instances — ZERO writes to ``db.sqlite3``.
Evaluates:
1. Crash-freedom & sanitization across stratified, polymorbid, threshold-boundary, and hostile fuzz inputs;
2. 22 Hard Clinical Safety Invariants ("Never-Event Oracles");
3. Metamorphic Monotonicity across paired clinical-deterioration perturbations;
4. Independent International Guideline Objectivity Benchmark (ESC 2022, ESVS 2022-2024, ASRA 5th Ed,
   NICE NG45, ESAIC 2024, IDSA 2019, SVS VQI) — measuring Sensitivity, Specificity, Over-Testing Rate,
   and Latency.
"""
import argparse
import datetime
import json
import math
import random
import time
from collections import Counter

import django
from django.apps import apps as _dj_apps
if not _dj_apps.ready:
    django.setup()

from django.template.loader import render_to_string

from .duty import MONTHS
from .models import PreopPatient
from .preop import (
    CHIP_CATALOGS,
    STATUS_ALERT,
    STATUS_NO_Q,
    STATUS_PROGRESS,
    STATUS_READY,
    _monday_of,
    _week_title,
    build_med_schedule,
    calc_ariscat_pulm,
    calc_auto_asa,
    calc_bmi,
    calc_caprini,
    calc_crcl,
    calc_egfr,
    calc_rcri,
    calc_vsg_cri,
    evaluate,
    generate_anamnesis,
    normalize_albumin_gl,
    normalize_glucose_mmol,
    normalize_hb_gl,
)

SIM_TODAY = datetime.date(2026, 11, 25)

ALL_SUBTYPES = [
    PreopPatient.OpSubtype.OPEN,
    PreopPatient.OpSubtype.EVAR,
    PreopPatient.OpSubtype.TBD,
    PreopPatient.OpSubtype.CEA_SYMP,
    PreopPatient.OpSubtype.CEA_ASYMP,
    PreopPatient.OpSubtype.ABF_CLAUD,
    PreopPatient.OpSubtype.ABF_CLTI,
    PreopPatient.OpSubtype.BYPASS_CLAUD,
    PreopPatient.OpSubtype.BYPASS_CLTI,
    PreopPatient.OpSubtype.PTA_CLAUD,
    PreopPatient.OpSubtype.PTA_CLTI,
    PreopPatient.OpSubtype.EVLA,
    PreopPatient.OpSubtype.PHLEB,
]

ASA_ORDER = {'I': 1, 'II': 2, 'III': 3, 'IV': 4}
# Severity rank for metamorphic monotonicity (higher = stricter / more cautious clinical state)
STATUS_SEVERITY_ORDER = {
    STATUS_READY: 0,
    STATUS_PROGRESS: 1,
    STATUS_ALERT: 2,
    STATUS_NO_Q: 3,
}


def _make_unsaved_patient(
    idx,
    op_subtype,
    age=68,
    sex='M',
    side='',
    started_days_ago=2,
    someday_list='',
    archived=False,
    approved=False,
    cardio_decision='',
    cardiologist='',
    q=None,
    s=None,
):
    pathway = PreopPatient.SUBTYPE_PATHWAY.get(op_subtype, PreopPatient.Pathway.AAA_INFRA)
    started_on = SIM_TODAY - datetime.timedelta(days=started_days_ago)
    visit_date = None if someday_list else started_on
    p = PreopPatient(
        id=idx + 1,
        patient_name=f'სიმულაცია პაციენტი #{idx}',
        age=age,
        sex=sex,
        side=side,
        pathway=pathway,
        op_subtype=op_subtype,
        visit_date=visit_date,
        started_on=started_on,
        someday_list=someday_list,
        archived=archived,
        cardio_decision=cardio_decision,
        cardiologist=cardiologist,
        questionnaire=q if q is not None else {},
        studies=s if s is not None else {},
    )
    if approved:
        p.approved_at = datetime.datetime(2026, 11, 25, 9, 30, tzinfo=datetime.timezone.utc)
    return p


def _random_stratified_patient(rng, idx):
    op_subtype = ALL_SUBTYPES[idx % len(ALL_SUBTYPES)]
    pathway = PreopPatient.SUBTYPE_PATHWAY[op_subtype]
    sex = rng.choices(['M', 'F', ''], weights=[54, 44, 2])[0]
    age = rng.choices([rng.randint(24, 54), rng.randint(55, 74), rng.randint(75, 92), None], weights=[18, 52, 28, 2])[0]
    if pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF):
        side = ''
    elif pathway == PreopPatient.Pathway.CEA:
        side = rng.choices(['R', 'L', ''], weights=[48, 48, 4])[0]
    else:
        side = rng.choices(['R', 'L', 'B', ''], weights=[44, 44, 8, 4])[0]

    started_days_ago = rng.choices([0, 2, 5, 8, 12, 18], weights=[20, 35, 20, 12, 8, 5])[0]
    someday_choices = [''] + [v for v in PreopPatient.SomedayList.values if v]
    someday_weights = [90] + [max(1, 10 // max(1, len(someday_choices) - 1))] * (len(someday_choices) - 1)
    someday_list = rng.choices(someday_choices, weights=someday_weights)[0]
    filled = rng.random() < 0.94

    q = {'filled': filled}
    if filled:
        by_self = rng.random() < 0.25
        q['filled_by'] = 'პაციენტი (ბმულით)' if by_self else 'ექთანი'
        q['nurse_verified'] = (not by_self) or (rng.random() < 0.65)
        q['allergy_iodine_asked'] = True
        q['mets_ok'] = rng.random() < 0.76
        if not q['mets_ok']:
            q['mets_limitation'] = rng.choice(['dyspnea', 'limb', 'angina'])

        q['htn'] = rng.random() < 0.65
        q['cad'] = rng.random() < 0.28
        if q['cad']:
            pool = [k for k, _ in CHIP_CATALOGS['cad_chips']]
            q['cad_chips'] = rng.sample(pool, k=rng.randint(1, min(3, len(pool))))
        q['recent_stent'] = rng.random() < 0.04
        q['pacemaker'] = rng.random() < 0.04
        q['dm'] = rng.random() < 0.28
        q['dm_insulin'] = bool(q['dm'] and rng.random() < 0.30)
        q['metformin'] = bool(q['dm'] and rng.random() < 0.55)
        q['sglt2'] = bool((q['dm'] or q['cad']) and rng.random() < 0.14)
        q['glp1'] = rng.random() < 0.06
        q['on_dialysis'] = rng.random() < 0.04
        q['pulm'] = rng.random() < 0.16
        if q['pulm']:
            q['pulm_chips'] = [rng.choice(['copd', 'asthma', 'osa'])]
        q['smoker'] = rng.random() < 0.34
        q['thyroid_disease'] = rng.random() < 0.08
        q['neuro'] = rng.random() < 0.12
        q['carotid_bruit_symp'] = rng.random() < 0.06
        q['ulcer'] = rng.random() < 0.10
        q['ulcer_active_symp'] = bool(q['ulcer'] and rng.random() < 0.18)
        q['active_infection'] = rng.random() < 0.05
        if q['active_infection']:
            q['inf_chips'] = [rng.choice(['dysuria', 'fever_resp', 'skin_wound', 'dental_acute'])]
        q['dental_infection'] = rng.random() < 0.05
        q['loose_teeth'] = rng.random() < 0.08

        # Medications
        q['med_aspirin'] = rng.random() < (0.15 if pathway == PreopPatient.Pathway.VENOUS else 0.68)
        q['med_clopidogrel'] = rng.random() < (0.04 if pathway == PreopPatient.Pathway.VENOUS else 0.22)
        q['med_ticagrelor'] = rng.random() < 0.03
        q['med_prasugrel'] = rng.random() < 0.02
        oac_roll = rng.random()
        if oac_roll < 0.07:
            q['med_doac'] = True
        elif oac_roll < 0.10:
            q['med_dabigatran'] = True
        elif oac_roll < 0.13:
            q['med_warfarin'] = True

        med_chips = []
        if rng.random() < (0.15 if pathway == PreopPatient.Pathway.VENOUS else 0.72):
            med_chips.append('statin')
        if rng.random() < 0.35:
            med_chips.append('beta_blocker')
        if rng.random() < 0.42:
            med_chips.append('acei_arb')
        if rng.random() < 0.18:
            med_chips.append('diuretic')
        if rng.random() < 0.05:
            med_chips.append('rivaroxaban_2_5')
        if rng.random() < 0.04:
            med_chips.append('cilostazol')
        if rng.random() < 0.04:
            med_chips.append('amiodarone')
        if rng.random() < 0.12:
            med_chips.append('ppi')
        q['med_chips'] = med_chips

        # Allergies
        allergy_chips = []
        if rng.random() < 0.06:
            allergy_chips.append('beta_lactam')
        if rng.random() < 0.02:
            allergy_chips.append('hit_heparin')
        if rng.random() < 0.03:
            allergy_chips.append('latex_chlorhex')
        if rng.random() < 0.03:
            allergy_chips.append('clopidogrel_intol')
        if rng.random() < 0.02:
            allergy_chips.append('lidocaine')
        if allergy_chips:
            q['allergy_other'] = True
            q['allergy_chips'] = allergy_chips
        q['allergy_iodine'] = rng.random() < 0.05

        # Pathway-specific questionnaire items
        if pathway == PreopPatient.Pathway.AAA_INFRA:
            q['aaa_pain'] = rng.random() < 0.05
            q['cfs_level'] = rng.choices(['1_3', '4', '5_plus'], weights=[70, 20, 10])[0]
            q['aaa_pedal_pulse'] = rng.choices(['present', 'reduced'], weights=[82, 18])[0]
        elif pathway == PreopPatient.Pathway.CEA:
            is_symp = (op_subtype == PreopPatient.OpSubtype.CEA_SYMP) or (rng.random() < 0.12)
            q['cea_symp_event'] = is_symp
            if is_symp:
                ev_ago = rng.choice([2, 6, 11, 16, 40, 200])
                q['cea_event_date'] = (SIM_TODAY - datetime.timedelta(days=ev_ago)).isoformat()
                q['cea_event_chips'] = [rng.choice(['tia', 'amaurosis', 'minor_stroke'])]
            q['cea_crescendo'] = bool(is_symp and rng.random() < 0.08)
            q['cea_thrombolysis'] = bool(is_symp and rng.random() < 0.05)
            q['cea_disabling_stroke'] = bool(is_symp and rng.random() < 0.05)
            q['cea_prior_neck'] = rng.random() < 0.08
            if q['cea_prior_neck']:
                q['cea_neck_chips'] = [rng.choice(['neck_rad', 'ipsi_restenosis', 'thyroid_surg'])]
            q['cea_hoarseness'] = rng.random() < 0.05
            q['cea_neck_limited'] = rng.random() < 0.08
        elif pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA):
            is_clti_sub = op_subtype in (
                PreopPatient.OpSubtype.ABF_CLTI,
                PreopPatient.OpSubtype.BYPASS_CLTI,
                PreopPatient.OpSubtype.PTA_CLTI,
            )
            q['abf_claud_m'] = rng.choice([30, 80, 150, 250])
            q['abf_duration'] = rng.choice(['< 2 კვირა', '1–3 თვე', '> 3 თვე'])
            q['ic_bmt_3m'] = rng.random() < 0.72
            q['abf_rest_pain'] = is_clti_sub and (rng.random() < 0.75)
            q['abf_ulcer'] = is_clti_sub and (rng.random() < 0.55)
            q['abf_wet_infection'] = bool(q['abf_ulcer'] and rng.random() < 0.18)
            q['abf_groin_issue'] = rng.random() < 0.04
            if pathway == PreopPatient.Pathway.PAD_PTA:
                q['pta_cant_lie_flat'] = rng.random() < 0.08
        elif pathway == PreopPatient.Pathway.VENOUS:
            q['ven_ceap'] = rng.choices(['C2', 'C3', 'C4', 'C5', 'C6'], weights=[40, 25, 18, 9, 8])[0]
            q['ven_dvt_history'] = rng.random() < 0.07
            q['ven_thrombophilia'] = rng.random() < 0.04
            q['ven_cancer_immob'] = rng.random() < 0.03
            q['ven_acute_svt'] = rng.random() < 0.05
            q['ven_hormones'] = bool(sex == 'F' and rng.random() < 0.12)
            q['ven_pregnant'] = bool(sex == 'F' and (age or 40) < 46 and rng.random() < 0.06)
            q['ven_skin_infection'] = rng.random() < 0.04
            q['ven_bleeding'] = rng.random() < 0.04
            q['ven_lidocaine_allergy'] = rng.random() < 0.02

    # Studies & Vitals
    wt = round(rng.uniform(42, 138), 1)
    ht = rng.randint(150, 192)
    use_mgdl_creat = rng.random() < 0.22
    if use_mgdl_creat:
        creat_val = round(rng.choice([0.8, 1.1, 1.5, 1.9, 2.4, 3.6]), 2)
        creat_unit = 'mgdl'
    else:
        creat_val = round(rng.choice([68, 86, 108, 138, 185, 265]), 0)
        creat_unit = 'umol' if rng.random() < 0.5 else ''

    hb_val = rng.choice([92, 106, 118, 134, 146, 158, 190, 10.4, 13.8])
    gl_val = rng.choice([3.6, 5.2, 6.1, 7.6, 9.8, 12.2, 15.1, 102, 210])

    s = {
        'weight_kg': wt,
        'height_cm': ht,
        'bp_sys': rng.choice([86, 118, 132, 148, 166, 186]),
        'bp_dia': rng.choice([58, 76, 84, 96, 104, 114]),
        'hr': rng.choice([42, 64, 76, 88, 116]),
        'spo2': rng.choice([88, 92, 95, 98, 99]),
        'irregular_hr': rng.random() < 0.07,
        'hb': hb_val,
        'wbc': rng.choice([5.8, 7.4, 9.2, 13.4]),
        'plt': rng.choice([85, 132, 215, 290]),
        'inr': rng.choice([1.0, 1.12, 1.28, 1.45, 2.3]),
        'fibrinogen': rng.choice([2.8, 3.6, 1.6]),
        'aptt_prolonged': rng.random() < 0.03,
        'creat': creat_val,
        'creat_unit': creat_unit,
        'k': rng.choice([3.1, 3.4, 4.2, 4.7, 5.2, 5.6]),
        'na': rng.choice([128, 138, 141, 152]),
        'glucose': gl_val,
        'albumin': rng.choice([28, 33, 41, 4.2]),
        'ldl': rng.choice([1.1, 1.9, 3.1, None]),
        'liver_status': rng.choices(['normal', '2x', '3x'], weights=[90, 7, 3])[0],
        'urine_wbc': rng.choice([2, 6, 15, 32, None]),
        'urine_nitrite': rng.random() < 0.04,
        'labs_date': (SIM_TODAY - datetime.timedelta(days=rng.choice([1, 4, 10, 19]))).isoformat(),
        'blood_group': rng.choice(['O (I) Rh+', 'A (II) Rh+', 'B (III) Rh-', '']),
        'ecg': rng.choices(['normal', 'afib_controlled', 'ischemia_arrhythmia', 'pending'], weights=[72, 12, 6, 10])[0],
        'cxr': rng.choices(['normal', 'emphysema', 'infiltrate', 'pending'], weights=[60, 10, 3, 27])[0],
        'lvef': rng.choices([58, 52, 44, 34, None], weights=[45, 15, 10, 7, 23])[0],
        'nt_probnp': rng.choices([75, 180, 420, None], weights=[40, 12, 8, 40])[0],
        'hs_ctn': rng.choices([8, 22, 68, None], weights=[45, 8, 3, 44])[0],
        'echo_valve_issue': rng.random() < 0.04,
        'anesthesia_plan': rng.choices(['auto', 'general', 'neuraxial', 'local_tumescent'], weights=[72, 12, 12, 4])[0],
        'anesth_lines_chips': ['epidural'] if rng.random() < 0.06 else (['art_line'] if rng.random() < 0.20 else []),
        'airway_mallampati': rng.choices(['I_II', 'III', 'IV_difficult'], weights=[84, 11, 5])[0],
        'surg_duration_est': rng.choices(['auto', 'lt_2h', '2_3h', 'gt_3h'], weights=[65, 10, 13, 12])[0],
        'planned_surgery_date': (SIM_TODAY + datetime.timedelta(days=rng.choice([3, 7, 16, 62]))).isoformat(),
    }
    if rng.random() < 0.25:
        s['bp_sys_l'] = max(70, s['bp_sys'] - rng.choice([4, 12, 26]))
        s['bp_dia_l'] = s['bp_dia']

    # Pathway imaging & hemodynamics
    if pathway == PreopPatient.Pathway.AAA_INFRA:
        s['aaa_mm'] = rng.choice([46, 52, 58, 66, 74, None])
        s['aaa_morphology'] = rng.choices(
            ['fusiform', 'saccular', 'rapid_growth', 'inflammatory', 'inflammatory_mycotic'],
            weights=[76, 9, 7, 5, 3],
        )[0]
        s['aaa_neck_status'] = rng.choices(['friendly_ifu', 'hostile_neck', 'pending'], weights=[65, 15, 20])[0]
        s['popliteal_screen_status'] = rng.choices(['normal', 'aneurysm_found', 'pending'], weights=[72, 6, 22])[0]
        s['cta_date'] = (SIM_TODAY - datetime.timedelta(days=rng.choice([15, 60, 140, 220]))).isoformat()
        s['cta_disc'] = rng.random() < 0.85
    elif pathway == PreopPatient.Pathway.CEA:
        s['carotid_r'] = rng.choice([30, 55, 65, 78, 92, 100])
        s['carotid_l'] = rng.choice([25, 52, 68, 82, 95, 100])
        s['cea_cta_status'] = rng.choices(['concordant', 'high_calcified', 'pending'], weights=[72, 18, 10])[0]
        s['brain_imaging'] = rng.choices(['normal_small', 'large_or_hemorrhage', 'pending'], weights=[74, 8, 18])[0]
        s['laryngoscopy_res'] = rng.choices(['normal', 'palsy', 'pending'], weights=[45, 5, 50])[0]
        s['cta_disc'] = rng.random() < 0.85
    elif pathway == PreopPatient.Pathway.ABF:
        s['abi_r'] = rng.choice([0.35, 0.55, 0.78, 1.52])
        s['abi_l'] = rng.choice([0.38, 0.58, 0.82, 1.48])
        s['abf_clamp_zone'] = rng.choices(['infrarenal', 'juxtarenal_calcified', 'pending'], weights=[72, 16, 12])[0]
        s['abf_outflow'] = rng.choices(['patent', 'profunda_stenosis', 'poor_outflow', 'pending'], weights=[62, 18, 10, 10])[0]
        s['abf_mesenteric'] = rng.choices(['normal', 'ima_dominant_iia_occ', 'sma_stenosis'], weights=[80, 13, 7])[0]
        s['cta_disc'] = rng.random() < 0.85
    elif pathway == PreopPatient.Pathway.PAD_BYPASS:
        s['abi_r'] = rng.choice([0.32, 0.48, 0.65, 0.96, 1.55])
        s['abi_l'] = rng.choice([0.36, 0.52, 0.68, 0.98, 1.58])
        if rng.random() < 0.45:
            s['tp_r'] = rng.choice([22, 38, 55, 72])
            s['tp_l'] = rng.choice([25, 42, 58, 76])
        s['pad_runoff_status'] = rng.choices(['popliteal_patent', 'tibial_single', 'desert_foot', 'pending'], weights=[55, 25, 8, 12])[0]
        s['pad_gsv_status'] = rng.choices(['adequate', 'inadequate', 'pending'], weights=[64, 24, 12])[0]
        if s['pad_gsv_status'] == 'inadequate':
            s['alt_vein_mapped'] = rng.choice(['contra_gsv_ok', 'arm_vein_ok', 'no_autovein_prosthetic', 'pending'])
        s['post_antithrombotic_plan'] = rng.choices(['auto', 'voyager', 'dapt', 'sapt', 'oac_sapt'], weights=[70, 10, 8, 7, 5])[0]
        s['cta_disc'] = rng.random() < 0.85
    elif pathway == PreopPatient.Pathway.PAD_PTA:
        s['abi_r'] = rng.choice([0.36, 0.58, 0.72, 0.98, 1.54])
        s['abi_l'] = rng.choice([0.40, 0.62, 0.74, 0.99, 1.50])
        if rng.random() < 0.45:
            s['tp_r'] = rng.choice([24, 44, 66])
            s['tp_l'] = rng.choice([26, 46, 68])
        s['pta_lesion_site'] = rng.choices(['iliac', 'femoropopliteal', 'infrapopliteal', 'multilevel'], weights=[28, 42, 16, 14])[0]
        s['pta_access_site'] = rng.choices(['cfa_clean', 'cfa_calcified_scar', 'pending'], weights=[70, 18, 12])[0]
        s['post_antithrombotic_plan'] = rng.choices(['auto', 'voyager', 'dapt', 'sapt', 'oac_sapt'], weights=[72, 10, 10, 4, 4])[0]
    elif pathway == PreopPatient.Pathway.VENOUS:
        s['ven_duplex_status'] = rng.choices(
            ['deep_patent_reflux', 'acute_dvt', 'deep_obstruction', 'superficial_thrombus', 'pending'],
            weights=[76, 4, 4, 6, 10],
        )[0]
        s['ven_gsv_mm'] = rng.choice([6, 9, 13, 17])
        s['ven_epifascial'] = rng.random() < 0.15
        s['ven_tortuous'] = rng.random() < 0.15
        if rng.random() < 0.40:
            s['abi_r'] = rng.choice([0.72, 0.85, 1.05, 1.52])
            s['abi_l'] = rng.choice([0.74, 0.88, 1.08, 1.54])
            if rng.random() < 0.50:
                s['tp_r'] = rng.choice([42, 68])
                s['tp_l'] = rng.choice([44, 70])

    # Partial or full Stage 2 completion
    if rng.random() < 0.45:
        for k_done in (
            'anemia_panel_done', 'pbm_iv_iron_done', 'endo_consult_done', 'nephro_panel_done',
            'hydration_planned', 'dental_sanitation_done', 'contrast_premed_done', 'cied_checked',
            'cea_antiplatelet_started', 'neuro_consult_done', 'vte_prophylaxis_planned',
            'ppi_started', 'urine_culture_done', 'wound_care_done', 'compression_ready',
            'dapt_ready', 'blood_reserved', 'evar_sized', 'decolonization_ready',
            'med_hold_agreed', 'anesth_done', 'stale_labs_refreshed',
        ):
            if rng.random() < 0.78:
                s[k_done] = True
        s['hba1c'] = rng.choice([6.4, 7.8, 9.1])
        s['tsh_status'] = rng.choice(['normal', 'hypothyroid_comp', 'hyperthyroid'])
        s['spiro_fev1'] = rng.choice([78, 62, 44])
        s['gastro_result'] = rng.choice(['normal_remission', 'acute_ulcer'])

    # Morning of surgery (DoS) vitals on subset
    if rng.random() < 0.35:
        s['dos_identity_consent'] = rng.random() < 0.85
        s['dos_site_marked'] = rng.random() < 0.85
        s['dos_fasting_confirmed'] = rng.random() < 0.85
        s['dos_med_hold_verified'] = rng.random() < 0.85
        s['dos_abx_ready'] = rng.random() < 0.85
        s['dos_blood_implant_ready'] = rng.random() < 0.85
        s['icu_bed_booked'] = rng.random() < 0.85
        s['dos_bp_sys'] = rng.choice([85, 128, 152, 188])
        s['dos_bp_dia'] = rng.choice([74, 86, 114])
        s['dos_glucose'] = rng.choice([3.5, 6.2, 11.4, 15.2])
        s['dos_inr'] = rng.choice([1.04, 1.26, 1.62, 3.2])

    cardio_dec = rng.choices(['', 'cleared', 'stress', 'cag', 'pci'], weights=[55, 33, 5, 4, 3])[0]
    cardiologist = 'ლიკა ინჯია' if cardio_dec else ''
    approved = rng.random() < 0.20

    return _make_unsaved_patient(
        idx=idx,
        op_subtype=op_subtype,
        age=age,
        sex=sex,
        side=side,
        started_days_ago=started_days_ago,
        someday_list=someday_list,
        approved=approved,
        cardio_decision=cardio_dec,
        cardiologist=cardiologist,
        q=q,
        s=s,
    )


def _archetype_patient(rng, idx):
    """20 high-acuity clinical archetypes probing multi-system vascular/anesthesia traps."""
    arch = idx % 20
    if arch == 0:
        # Frail 86yo 42kg woman: Creat 1.15 mg/dL -> eGFR ~47 but Cockcroft-Gault CrCl ~22 mL/min on Dabigatran + spinal!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.PHLEB, age=86, sex='F', side='R',
            q={'filled': True, 'med_dabigatran': True, 'htn': True, 'mets_ok': True},
            s={'weight_kg': 42, 'height_cm': 154, 'creat': 1.15, 'creat_unit': 'mgdl', 'hb': 124, 'plt': 210, 'inr': 1.1, 'glucose': 5.3, 'ecg': 'normal', 'ven_duplex_status': 'deep_patent_reflux', 'anesthesia_plan': 'neuraxial'},
        )
    if arch == 1:
        # Morbidly obese 148kg 156cm man: Open AAA, Cefazolin 3g, ABW CrCl, OSA
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.OPEN, age=67, sex='M',
            q={'filled': True, 'htn': True, 'dm': True, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'weight_kg': 148, 'height_cm': 156, 'aaa_mm': 62, 'cta_disc': True, 'hb': 142, 'plt': 240, 'creat': 112, 'creat_unit': 'umol', 'inr': 1.0, 'glucose': 6.4, 'lvef': 55, 'ecg': 'normal'},
        )
    if arch == 2:
        # Lone controlled AFib without IHD/CHF -> must NOT inflate RCRI or VSG-CRI IHD/CHF!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.EVAR, age=71, sex='M', cardio_decision='cleared',
            q={'filled': True, 'cad': True, 'cad_chips': ['afib'], 'med_doac': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'aaa_mm': 58, 'aaa_neck_status': 'friendly_ifu', 'popliteal_screen_status': 'normal', 'cta_disc': True, 'hb': 138, 'plt': 220, 'creat': 88, 'creat_unit': 'umol', 'inr': 1.08, 'lvef': 58, 'nt_probnp': 110, 'ecg': 'afib_controlled'},
        )
    if arch == 3:
        # Clean healthy 32yo F EVLA -> lean Stage 1, ASA I, zero cardio/echo/CXR/spirometry
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.EVLA, age=32, sex='F', side='L',
            q={'filled': True, 'ven_ceap': 'C2', 'mets_ok': True},
            s={'weight_kg': 60, 'height_cm': 168, 'hb': 132, 'plt': 250, 'ven_duplex_status': 'deep_patent_reflux', 'ven_gsv_mm': 7},
        )
    if arch == 4:
        # Symptomatic CEA with IV thrombolysis <=6d and NO antiplatelet -> antiplatelet reflex MUST be suppressed!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.CEA_SYMP, age=69, sex='M', side='R',
            q={'filled': True, 'cea_symp_event': True, 'cea_event_date': (SIM_TODAY - datetime.timedelta(days=4)).isoformat(), 'cea_thrombolysis': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'carotid_r': 85, 'carotid_l': 40, 'cea_cta_status': 'concordant', 'brain_imaging': 'normal_small', 'cta_disc': True, 'hb': 136, 'plt': 210, 'creat': 90, 'inr': 1.0, 'ecg': 'normal'},
        )
    if arch == 5:
        # Symptomatic CEA with intracranial hemorrhage on brain CT -> hard contraindication + no antiplatelet!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.CEA_SYMP, age=72, sex='F', side='L', approved=True,
            q={'filled': True, 'cea_symp_event': True, 'cea_event_date': (SIM_TODAY - datetime.timedelta(days=5)).isoformat(), 'mets_ok': True},
            s={'carotid_r': 30, 'carotid_l': 80, 'cea_cta_status': 'concordant', 'brain_imaging': 'large_or_hemorrhage', 'hb': 128, 'plt': 200, 'creat': 80, 'inr': 1.0, 'ecg': 'normal'},
        )
    if arch == 6:
        # CEA with 100% ipsilateral ICA occlusion -> hard contraindication even if approved!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.CEA_ASYMP, age=66, sex='M', side='R', approved=True,
            q={'filled': True, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'carotid_r': 100, 'carotid_l': 50, 'cea_cta_status': 'concordant', 'cta_disc': True, 'hb': 142, 'plt': 230, 'creat': 85, 'inr': 1.0, 'ecg': 'normal'},
        )
    if arch == 7:
        # Multi-allergy storm: HIT + beta-lactam + latex/chlorhexidine + iodine + clopidogrel intolerance
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.BYPASS_CLTI, age=70, sex='M', side='R',
            q={'filled': True, 'abf_rest_pain': True, 'dental_infection': True, 'allergy_iodine': True, 'allergy_other': True, 'allergy_chips': ['hit_heparin', 'beta_lactam', 'latex_chlorhex', 'clopidogrel_intol'], 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'abi_r': 0.38, 'abi_l': 0.92, 'pad_runoff_status': 'tibial_single', 'pad_gsv_status': 'inadequate', 'alt_vein_mapped': 'no_autovein_prosthetic', 'hb': 128, 'plt': 165, 'creat': 96, 'inr': 1.05, 'ecg': 'normal', 'cta_disc': True},
        )
    if arch == 8:
        # ESRD on hemodialysis + OAC + Hb 94 -> renal anemia does NOT trigger routine EGD; VOYAGER blocked
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.PTA_CLTI, age=65, sex='M', side='L',
            q={'filled': True, 'on_dialysis': True, 'abf_ulcer': True, 'med_doac': True, 'med_aspirin': True, 'metformin': True, 'mets_ok': True},
            s={'abi_r': 0.95, 'abi_l': 1.62, 'tp_r': 78, 'pta_lesion_site': 'infrapopliteal', 'pta_access_site': 'cfa_clean', 'hb': 94, 'plt': 190, 'inr': 1.15, 'glucose': 6.2, 'k': 5.5, 'ecg': 'normal', 'post_antithrombotic_plan': 'voyager'},
        )
    if arch == 9:
        # Unilateral venous EVLA with ipsilateral ABI 0.74 and contralateral ABI 1.12 -> must catch arterial insufficiency!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.EVLA, age=68, sex='M', side='R',
            q={'filled': True, 'ven_ceap': 'C6', 'mets_ok': True},
            s={'abi_r': 0.74, 'abi_l': 1.12, 'hb': 138, 'plt': 225, 'glucose': 5.6, 'ven_duplex_status': 'deep_patent_reflux', 'ecg': 'normal'},
        )
    if arch == 10:
        # Pregnant patient scheduled for EVLA -> hard contraindication!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.EVLA, age=29, sex='F', side='L', approved=True,
            q={'filled': True, 'ven_ceap': 'C3', 'ven_pregnant': True, 'mets_ok': True},
            s={'hb': 122, 'plt': 240, 'ven_duplex_status': 'deep_patent_reflux'},
        )
    if arch == 11:
        # Venous acute DVT on duplex -> hard contraindication!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.PHLEB, age=54, sex='M', side='R', approved=True,
            q={'filled': True, 'ven_ceap': 'C4', 'mets_ok': True},
            s={'hb': 140, 'plt': 210, 'inr': 1.0, 'glucose': 5.4, 'creat': 84, 'ecg': 'normal', 'ven_duplex_status': 'acute_dvt'},
        )
    if arch == 12:
        # Claudication harm guards: BTK PTA & Prosthetic infrainguinal bypass
        sub = PreopPatient.OpSubtype.PTA_CLAUD if (idx // 20) % 2 == 0 else PreopPatient.OpSubtype.BYPASS_CLAUD
        return _make_unsaved_patient(
            idx, sub, age=62, sex='M', side='R',
            q={'filled': True, 'abf_claud_m': 120, 'abf_duration': '< 2 კვირა', 'ic_bmt_3m': False, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'abi_r': 0.64, 'abi_l': 0.98, 'pta_lesion_site': 'infrapopliteal', 'pta_access_site': 'cfa_clean', 'pad_runoff_status': 'popliteal_patent', 'pad_gsv_status': 'inadequate', 'alt_vein_mapped': 'no_autovein_prosthetic', 'hb': 142, 'plt': 230, 'inr': 1.0, 'glucose': 5.4, 'creat': 82, 'ecg': 'normal', 'cta_disc': True},
        )
    if arch == 13:
        # Cardiologist non-compliant CAG order on low-risk asymptomatic EVAR
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.EVAR, age=66, sex='M', cardio_decision='cag', cardiologist='ტესტ კარდიოლოგი',
            q={'filled': True, 'htn': True, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'aaa_mm': 57, 'aaa_neck_status': 'friendly_ifu', 'popliteal_screen_status': 'normal', 'cta_disc': True, 'hb': 144, 'plt': 230, 'creat': 82, 'creat_unit': 'umol', 'inr': 1.0, 'lvef': 60, 'nt_probnp': 65, 'ecg': 'normal'},
        )
    if arch == 14:
        # IDSA 2019 asymptomatic leukocyturia (urine WBC 18, nitrite-, no dysuria) on Open AAA -> NO false UTI red flag!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.OPEN, age=69, sex='M', cardio_decision='cleared',
            q={'filled': True, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'aaa_mm': 60, 'popliteal_screen_status': 'normal', 'cta_disc': True, 'hb': 140, 'plt': 220, 'creat': 86, 'creat_unit': 'umol', 'inr': 1.0, 'urine_wbc': 18, 'urine_nitrite': False, 'lvef': 58, 'ecg': 'normal', 'blood_reserved': True, 'decolonization_ready': True, 'anesth_done': True},
        )
    if arch == 15:
        # Giant 76mm AAA over 7 days with 100% completed workup -> 2D triage unlocks STATUS_READY + priority_tier='urgent'!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.OPEN, age=70, sex='M', started_days_ago=9, cardio_decision='cleared',
            q={'filled': True, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'aaa_mm': 76, 'popliteal_screen_status': 'normal', 'cta_date': (SIM_TODAY - datetime.timedelta(days=10)).isoformat(), 'cta_disc': True, 'hb': 138, 'plt': 210, 'creat': 88, 'creat_unit': 'umol', 'inr': 1.02, 'lvef': 56, 'ecg': 'normal', 'blood_group': 'A (II) Rh+', 'blood_reserved': True, 'decolonization_ready': True, 'anesth_done': True},
        )
    if arch == 16:
        # Bilateral BP asymmetry (R 172/92, L 138/80 -> diff 34 mmHg) + GLP-1 + SGLT2 + Pacemaker
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.CEA_ASYMP, age=71, sex='M', side='L',
            q={'filled': True, 'dm': True, 'glp1': True, 'sglt2': True, 'pacemaker': True, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'bp_sys': 172, 'bp_dia': 92, 'bp_sys_l': 138, 'bp_dia_l': 80, 'carotid_r': 40, 'carotid_l': 82, 'cea_cta_status': 'concordant', 'cta_disc': True, 'hb': 136, 'plt': 215, 'creat': 92, 'inr': 1.0, 'glucose': 6.5, 'hba1c': 7.1, 'ecg': 'normal', 'cied_plan': 'magnet_async', 'planned_surgery_date': (SIM_TODAY + datetime.timedelta(days=10)).isoformat()},
        )
    if arch == 17:
        # Post-MI stent 6-12m (`stent_6_12m` + `mi`) without recent_stent -> ESC 12m DAPT guard!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.ABF_CLAUD, age=64, sex='M',
            q={'filled': True, 'cad': True, 'cad_chips': ['mi', 'stent_6_12m'], 'med_aspirin': True, 'med_clopidogrel': True, 'med_chips': ['statin'], 'ic_bmt_3m': True, 'abf_duration': '> 3 თვე', 'mets_ok': True},
            s={'abi_r': 0.55, 'abi_l': 0.58, 'abf_clamp_zone': 'infrarenal', 'abf_outflow': 'patent', 'cta_disc': True, 'hb': 140, 'plt': 220, 'creat': 88, 'inr': 1.0, 'lvef': 52, 'ecg': 'normal'},
        )
    if arch == 18:
        # Creatinine unit ambiguity (creat=24 without unit -> blocked!)
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.EVAR, age=70, sex='M',
            q={'filled': True, 'med_doac': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'aaa_mm': 58, 'creat': 24, 'creat_unit': '', 'hb': 135, 'plt': 210, 'inr': 1.05, 'ecg': 'normal'},
        )
    # arch == 19: Neuraxial morning INR 1.46 (> ASRA 1.4 limit) -> DoS STOP danger!
    return _make_unsaved_patient(
        idx, PreopPatient.OpSubtype.BYPASS_CLTI, age=68, sex='F', side='R', approved=True,
        q={'filled': True, 'abf_rest_pain': True, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
        s={'abi_r': 0.42, 'abi_l': 0.95, 'pad_runoff_status': 'popliteal_patent', 'pad_gsv_status': 'adequate', 'cta_disc': True, 'hb': 126, 'plt': 205, 'creat': 76, 'inr': 1.08, 'ecg': 'normal', 'anesthesia_plan': 'neuraxial', 'dos_identity_consent': True, 'dos_site_marked': True, 'dos_fasting_confirmed': True, 'dos_med_hold_verified': True, 'dos_abx_ready': True, 'dos_blood_implant_ready': True, 'dos_bp_sys': 130, 'dos_bp_dia': 78, 'dos_glucose': 5.8, 'dos_inr': 1.46},
    )


def _boundary_patient(rng, idx):
    """Exact x - eps / x / x + eps sweeps across clinical thresholds."""
    op_subtype = ALL_SUBTYPES[idx % len(ALL_SUBTYPES)]
    pathway = PreopPatient.SUBTYPE_PATHWAY[op_subtype]
    sex = 'F' if idx % 2 else 'M'
    side = 'R' if pathway not in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF) else ''

    hb_edges = [9.9, 10.0, 11.4, 11.5, 99, 100, 101, 114, 115, 116, 119, 120, 129, 130, 165, 166, 185, 186]
    plt_edges = [99, 100, 101, 149, 150, 151]
    inr_edges = [1.35, 1.36, 1.39, 1.40, 1.41, 1.50, 1.51, 3.00, 3.01]
    creat_edges = [(14.9, ''), (15.0, ''), (25.0, ''), (35.0, ''), (35.1, ''), (1.79, 'mgdl'), (1.80, 'mgdl'), (2.00, 'mgdl'), (2.01, 'mgdl')]
    k_edges = [3.29, 3.30, 3.49, 3.50, 5.00, 5.01, 5.39, 5.40]
    gl_edges = [3.8, 3.9, 6.9, 7.0, 10.0, 10.1, 11.0, 11.1, 13.9, 14.0]
    bp_edges = [(89, 60), (90, 60), (159, 95), (160, 100), (179, 109), (180, 110)]
    abi_edges = [0.39, 0.40, 0.49, 0.50, 0.79, 0.80, 0.89, 0.90, 1.40, 1.41]
    ica_edges = [49, 50, 59, 60, 69, 70, 79, 80, 99, 100]
    aaa_edges = [49, 50, 54, 55, 64, 65, 69, 70]

    cr_val, cr_unit = creat_edges[idx % len(creat_edges)]
    bp_s, bp_d = bp_edges[idx % len(bp_edges)]
    q = {
        'filled': True,
        'mets_ok': True,
        'med_aspirin': True,
        'med_chips': ['statin'],
        'cea_symp_event': (op_subtype == PreopPatient.OpSubtype.CEA_SYMP),
        'cea_event_date': (SIM_TODAY - datetime.timedelta(days=[10, 14, 15, 30, 31, 90, 91, 180, 181][idx % 9])).isoformat(),
    }
    s = {
        'weight_kg': [49.9, 50.0, 119.9, 120.0][idx % 4],
        'height_cm': 168,
        'hb': hb_edges[idx % len(hb_edges)],
        'plt': plt_edges[idx % len(plt_edges)],
        'inr': inr_edges[idx % len(inr_edges)],
        'creat': cr_val,
        'creat_unit': cr_unit,
        'k': k_edges[idx % len(k_edges)],
        'na': [129, 130, 149, 150][idx % 4],
        'glucose': gl_edges[idx % len(gl_edges)],
        'hba1c': [8.4, 8.5, None][idx % 3],
        'albumin': [29, 30, 34, 35, 2.9, 3.5][idx % 6],
        'bp_sys': bp_s,
        'bp_dia': bp_d,
        'bp_sys_l': bp_s - [19, 20][idx % 2],
        'bp_dia_l': bp_d,
        'hr': [44, 45, 54, 55, 110, 111][idx % 6],
        'spo2': [89, 90, 91, 93, 94, 95, 96][idx % 7],
        'lvef': [39, 40, 49, 50, 58][idx % 5],
        'nt_probnp': [124, 125, 299, 300][idx % 4],
        'hs_ctn': [13.9, 14.0, 51.9, 52.0][idx % 4],
        'spiro_fev1': [49, 50, 69, 70][idx % 4],
        'urine_wbc': [10, 11, 24, 25][idx % 4],
        'labs_date': (SIM_TODAY - datetime.timedelta(days=[14, 15][idx % 2])).isoformat(),
        'ecg': 'normal',
        'aaa_mm': aaa_edges[idx % len(aaa_edges)],
        'carotid_r': ica_edges[idx % len(ica_edges)],
        'carotid_l': ica_edges[(idx + 3) % len(ica_edges)],
        'cea_cta_status': 'concordant',
        'brain_imaging': 'normal_small',
        'abf_clamp_zone': 'infrarenal',
        'abf_outflow': 'patent',
        'pad_runoff_status': 'popliteal_patent',
        'pad_gsv_status': 'adequate',
        'pad_gsv_mm': [2.9, 3.0][idx % 2],
        'pta_access_site': 'cfa_clean',
        'ven_duplex_status': 'deep_patent_reflux',
        'ven_gsv_mm': [14, 15][idx % 2],
        'abi_r': abi_edges[idx % len(abi_edges)],
        'abi_l': abi_edges[(idx + 2) % len(abi_edges)],
        'tp_r': [29, 30, 39, 40, 49, 50, 59, 60][idx % 8],
        'tbi_r': [0.29, 0.30, 0.49, 0.50, 0.69, 0.70][idx % 6],
        'dos_inr': [1.39, 1.40, 1.41, 1.50, 1.51, 3.00, 3.01][idx % 7],
    }
    return _make_unsaved_patient(idx, op_subtype, age=[50, 51, 60, 65, 70, 75, 80, 81][idx % 8], sex=sex, side=side, q=q, s=s)


def _fuzz_patient(rng, idx):
    """Adversarial / corrupt dictionary & float payloads testing defensive boundaries."""
    weird_vals = [
        None, '', '   ', 'NaN', 'inf', '-inf', float('nan'), float('inf'), float('-inf'),
        -0.0, -50, 0, 99999999, 'abc', '12,5', '<script>alert(1)</script>',
        '2099-12-31', '1810-01-01', 'not-a-date', [], {}, True, False,
    ]
    op_subtype = rng.choice(ALL_SUBTYPES + ['', 'unknown_op'])
    p = PreopPatient(
        id=idx + 1,
        patient_name=rng.choice(['ფაზ პაციენტი', '', 'A' * 200]),
        age=rng.choice([None, -5, 0, 18, 72, 150]),
        sex=rng.choice(['M', 'F', '', 'X']),
        side=rng.choice(['', 'R', 'L', 'B', 'Z']),
        pathway=rng.choice(list(PreopPatient.Pathway.values) + ['', 'bad_pw']),
        op_subtype=op_subtype,
        visit_date=SIM_TODAY,
        started_on=rng.choice([None, SIM_TODAY, SIM_TODAY + datetime.timedelta(days=30)]),
        questionnaire=rng.choice([
            None,
            {},
            {
                'filled': rng.choice([True, False, 1, 'yes']),
                'allergy_chips': rng.choice([None, [], ['beta_lactam', 'unknown_chip'], 'beta_lactam', 42]),
                'cad_chips': rng.choice([None, [], ['afib', 'mi'], 99]),
                'med_chips': rng.choice([None, [], ['statin', 'acei_arb'], 'statin']),
                'pulm_chips': rng.choice([None, [], ['copd'], False]),
                'inf_chips': rng.choice([None, [], ['dysuria'], 0]),
                'vasc_chips': rng.choice([None, [], ['vein_strip'], 'x']),
                'cea_neck_chips': rng.choice([None, [], ['neck_rad'], 7]),
                'cea_event_chips': rng.choice([None, [], ['minor_stroke'], 3]),
                'ulcer_chips': rng.choice([None, [], ['r_foot'], 1]),
                'abdo_chips': rng.choice([None, [], 1]),
                'cea_event_date': rng.choice(weird_vals),
                'other_meds': rng.choice([None, '', 123, 'ატორვასტატინი']),
                'med_doac': rng.choice([True, False, None]),
                'on_dialysis': rng.choice([True, False, None]),
            },
        ]),
        studies=rng.choice([
            None,
            {},
            {
                'hb': rng.choice(weird_vals),
                'plt': rng.choice(weird_vals),
                'inr': rng.choice(weird_vals),
                'creat': rng.choice(weird_vals),
                'creat_unit': rng.choice(['', 'umol', 'mgdl', 'bogus', None]),
                'weight_kg': rng.choice(weird_vals),
                'height_cm': rng.choice(weird_vals),
                'k': rng.choice(weird_vals),
                'na': rng.choice(weird_vals),
                'glucose': rng.choice(weird_vals),
                'albumin': rng.choice(weird_vals),
                'bp_sys': rng.choice(weird_vals),
                'bp_dia': rng.choice(weird_vals),
                'bp_sys_l': rng.choice(weird_vals),
                'hr': rng.choice(weird_vals),
                'spo2': rng.choice(weird_vals),
                'aaa_mm': rng.choice(weird_vals),
                'carotid_r': rng.choice(weird_vals),
                'carotid_l': rng.choice(weird_vals),
                'abi_r': rng.choice(weird_vals),
                'abi_l': rng.choice(weird_vals),
                'tp_r': rng.choice(weird_vals),
                'tbi_r': rng.choice(weird_vals),
                'lvef': rng.choice(weird_vals),
                'nt_probnp': rng.choice(weird_vals),
                'hs_ctn': rng.choice(weird_vals),
                'cta_date': rng.choice(weird_vals),
                'labs_date': rng.choice(weird_vals),
                'birth_date': rng.choice(weird_vals),
                'planned_surgery_date': rng.choice(weird_vals),
                'anesth_lines_chips': rng.choice([None, [], ['epidural'], 'epidural', 5]),
                'cancel_reason_chips': rng.choice([None, [], 12]),
                'vqi_complication_chips': rng.choice([None, [], 'mi']),
                'dos_inr': rng.choice(weird_vals),
                'dos_glucose': rng.choice(weird_vals),
                'dos_bp_sys': rng.choice(weird_vals),
            },
        ]),
    )
    return p


def generate_synthetic_cohort(n=50000, seed=2026):
    """Deterministically generate a 4-tier synthetic preoperative cohort without touching the database."""
    rng = random.Random(seed)
    n_strat = int(n * 0.64)
    n_arch = int(n * 0.12)
    n_bound = int(n * 0.12)
    n_fuzz = n - n_strat - n_arch - n_bound

    cohort = []
    idx = 0
    for _ in range(n_strat):
        cohort.append(('stratified', _random_stratified_patient(rng, idx)))
        idx += 1
    for _ in range(n_arch):
        cohort.append(('archetype', _archetype_patient(rng, idx)))
        idx += 1
    for _ in range(n_bound):
        cohort.append(('boundary', _boundary_patient(rng, idx)))
        idx += 1
    for _ in range(n_fuzz):
        cohort.append(('fuzz', _fuzz_patient(rng, idx)))
        idx += 1
    return cohort


def _safe_list(val):
    return val if isinstance(val, (list, tuple, set)) else []


def check_clinical_invariants(patient, ev, anam, rendered_passport=None):
    """Verify 22 Never-Event Clinical & Engineering Invariants on a single evaluated patient."""
    violations = []
    q = ev['q']
    s = ev['s']
    pw = ev['pathway']
    q_filled = bool(q.get('filled'))
    allergy_chips = set(_safe_list(q.get('allergy_chips')))
    med_chips = set(_safe_list(q.get('med_chips')))
    cad_chips = set(_safe_list(q.get('cad_chips')))

    # INV-01: Rendered Passport Integrity & Physician-Confirmation Gate
    if rendered_passport is not None:
        if 'pp-page-1' not in rendered_passport or 'pp-page-2' not in rendered_passport:
            violations.append('INV-01_PASSPORT_2PAGE_MISSING')
        if 'მოქმედებს მხოლოდ მკურნალი ექიმის დადასტურებით!' not in rendered_passport:
            violations.append('INV-01_PASSPORT_PHYSICIAN_CONFIRMATION_GATE_MISSING')
        if not patient.approved_at and 'სამუშაო (დაუდასტურებელი) ასლი' not in rendered_passport:
            violations.append('INV-01_PASSPORT_UNAPPROVED_BANNER_MISSING')
        for bad_tok in ('>None<', ' None ', 'nan ', '>nan<', 'inf ', '>inf<'):
            if bad_tok in rendered_passport:
                violations.append(f'INV-01_PASSPORT_LEAK_{bad_tok.strip()}')
        if any(ord(ch) > 0xFFFF for ch in rendered_passport) and not any(
            ord(ch) > 0xFFFF for ch in (patient.patient_name or '')
        ):
            violations.append('INV-01_NON_BMP_OUTPUT')

    # INV-02: Hard Contraindication Lock
    ipsi_100 = (pw == PreopPatient.Pathway.CEA and ev['ipsi_car'] is not None and ev['ipsi_car'] >= 100)
    brain_bleed = (pw == PreopPatient.Pathway.CEA and s.get('brain_imaging') == 'large_or_hemorrhage')
    ven_contra = (
        pw == PreopPatient.Pathway.VENOUS and (
            s.get('ven_duplex_status') in ('acute_dvt', 'deep_obstruction')
            or (q_filled and bool(q.get('ven_pregnant')))
        )
    )
    if ipsi_100 or brain_bleed or ven_contra:
        if not ev['has_hard_contraindication']:
            violations.append('INV-02_HARD_CONTRA_FLAG_MISSED')
        if ev['status'] == STATUS_READY or ev['workup_ready'] or ev['dos_ready']:
            violations.append('INV-02_HARD_CONTRA_REACHED_READY')
        if ev['clearance_seal']['code'] != 'stop':
            violations.append('INV-02_HARD_CONTRA_SEAL_NOT_STOP')

    # INV-03: HIT Total Heparin Barrier
    if 'hit_heparin' in allergy_chips:
        hep_plan = ev['surgical_brief']['heparin_plan']
        if 'აკრძალულია' not in hep_plan or ('ბივალირუდინი' not in hep_plan and 'არგატრობანი' not in hep_plan):
            violations.append('INV-03_HIT_SURG_BRIEF_UNSAFE')
        if 'LMWH / DOAC' in ev['caprini_advice'] or 'LMWH პროფილაქტიკური' in ev['caprini_advice']:
            violations.append('INV-03_HIT_CAPRINI_LMWH_LEAK')
        for row in ev['med_schedule']:
            if row['drug'] == 'ვარფარინი' and 'საჭიროა LMWH ხიდი' in row['rule']:
                violations.append('INV-03_HIT_WARFARIN_LMWH_BRIDGE')

    # INV-04: Beta-Lactam Allergy Barrier
    if 'beta_lactam' in allergy_chips:
        abx_reg = ev['surgical_brief']['abx_regimen']
        if 'ცეფაზოლინი 2 გ' in abx_reg or 'ცეფაზოლინი 3 გ' in abx_reg:
            violations.append('INV-04_BETA_LACTAM_CEFAZOLIN_GIVEN')
        for dc in ev['dos_checks']:
            if dc['key'] == 'dos_abx_ready' and 'ცეფაზოლინი' in dc['label']:
                violations.append('INV-04_DOS_CEFAZOLIN_ON_ALLERGY')

    # INV-05: Latex / Chlorhexidine Allergy Barrier
    if 'latex_chlorhex' in allergy_chips and ev['needs_dental']:
        for ro in ev['reflex_orders']:
            if ro['key'] == 'dental' and 'ქლორჰექსიდინის დეკოლონიზაცია' in ro['action']:
                violations.append('INV-05_CHLORHEX_GIVEN_ON_ALLERGY')

    # INV-06: ASRA 5th Ed Neuraxial Anticoagulation & Coagulation Gate (INR <= 1.4)
    if ev['has_neuraxial_plan'] and q_filled:
        for row in ev['med_schedule']:
            if 'DOAC' in row['drug'] and '48 საათით' in row['rule']:
                violations.append('INV-06_NEURAXIAL_DOAC_48H_VIOLATION')
            if 'დაბიგატრანი' in row['drug'] and '48 საათით' in row['rule']:
                violations.append('INV-06_NEURAXIAL_DABIGATRAN_48H_VIOLATION')
        if ev['s'].get('dos_inr') is not None:
            try:
                dinr = float(ev['s']['dos_inr'])
                if 1.4 < dinr <= 15.0 and (not ev['dos_hold_danger'] or ev['dos_ready'] or ev['clearance_seal']['code'] != 'stop'):
                    violations.append('INV-06_NEURAXIAL_DOS_INR_OVER_1_4_NOT_STOPPED')
            except (TypeError, ValueError):
                pass

    # INV-07: Elderly Low-Weight Renal DOAC Trap (Cockcroft-Gault CrCl < 30 overrides normal eGFR)
    if (
        q_filled and (q.get('med_doac') or q.get('med_dabigatran'))
        and not q.get('on_dialysis')
        and not (pw == PreopPatient.Pathway.VENOUS and patient.op_subtype == PreopPatient.OpSubtype.EVLA and not ev['has_neuraxial_plan'])
        and ev['crcl'] is not None and ev['crcl'] < 30
    ):
        for row in ev['med_schedule']:
            if ('DOAC' in row['drug'] or 'დაბიგატრანი' in row['drug']) and ('48 საათით' in row['rule'] or '72 საათით' in row['rule']):
                violations.append('INV-07_LOW_CRCL_UNDER_96H_DOAC_HOLD')

    # INV-08: CEA Antiplatelet Non-Contradiction (Never hold ASA/Clopidogrel on symp CEA; Never start AP on bleed/rtPA)
    if pw == PreopPatient.Pathway.CEA and q_filled:
        prohibited = (s.get('brain_imaging') == 'large_or_hemorrhage') or bool(q.get('cea_thrombolysis'))
        if prohibited and ev['needs_cea_antiplatelet']:
            violations.append('INV-08_CEA_AP_ORDERED_ON_BLEED_OR_RTPA')
        if ev['is_symp_cea']:
            for row in ev['med_schedule']:
                if row['drug'] == 'ასპირინი (75–100 მგ)' and 'გრძელდება' not in row['rule']:
                    violations.append('INV-08_SYMP_CEA_ASPIRIN_HELD')
                if row['drug'] == 'კლოპიდოგრელი / P2Y12' and not (q.get('recent_stent') or ('stent_6_12m' in cad_chips and 'mi' in cad_chips)):
                    if 'შეჩერება' in row['rule']:
                        violations.append('INV-08_SYMP_CEA_CLOPIDOGREL_HELD')

    # INV-09: VOYAGER PAD & Triple-Therapy Safety Gate
    if pw in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA):
        voyager_contra = (
            bool(q.get('on_dialysis'))
            or (ev['egfr'] is not None and ev['egfr'] < 30)
            or (ev['crcl'] is not None and ev['crcl'] < 15)
            or bool(q.get('neuro'))
            or bool(q.get('ulcer_active_symp'))
            or (s.get('liver_status') == '3x')
            or ev['has_full_oac']
        )
        post_raw = s.get('post_antithrombotic_plan') or 'auto'
        if post_raw == 'auto' and voyager_contra:
            lbl = ev['post_antithrombotic_label']
            if 'VOYAGER PAD (რივაროქსაბანი' in lbl:
                violations.append('INV-09_VOYAGER_AUTO_RECOMMENDED_WHEN_CONTRAINDICATED')
        elif post_raw == 'voyager' and voyager_contra:
            if not any('VOYAGER' in rf for rf in ev['red_flags']):
                violations.append('INV-09_MANUAL_VOYAGER_CONTRA_NOT_FLAGGED')

    # INV-10: Claudication Class III Harm Guard
    is_clti = (
        patient.op_subtype in (PreopPatient.OpSubtype.ABF_CLTI, PreopPatient.OpSubtype.BYPASS_CLTI, PreopPatient.OpSubtype.PTA_CLTI)
        or (q_filled and (bool(q.get('abf_rest_pain')) or bool(q.get('abf_ulcer'))))
    )
    if pw == PreopPatient.Pathway.PAD_PTA and not is_clti and s.get('pta_lesion_site') == 'infrapopliteal':
        if not any('Class III' in rf for rf in ev['red_flags']):
            violations.append('INV-10_BTK_PTA_CLAUDICATION_MISSED')
    if pw == PreopPatient.Pathway.PAD_BYPASS and not is_clti and s.get('alt_vein_mapped') == 'no_autovein_prosthetic':
        if s.get('pad_gsv_status') == 'inadequate' and not any('უკუნაჩვენებია' in rf for rf in ev['red_flags']):
            violations.append('INV-10_PROSTHETIC_BYPASS_CLAUDICATION_MISSED')

    # INV-11: Target-Limb Hemodynamic Isolation
    if pw == PreopPatient.Pathway.VENOUS and patient.side in ('R', 'L'):
        t_abi = ev['s'].get('abi_r') if patient.side == 'R' else ev['s'].get('abi_l')
        try:
            t_abi_f = float(t_abi) if t_abi is not None and not isinstance(t_abi, bool) else None
        except (TypeError, ValueError):
            t_abi_f = None
        if t_abi_f is not None and 0.0 <= t_abi_f < 0.80:
            if not any('ABI' in rf and '< 0.80' in rf for rf in ev['red_flags']):
                violations.append('INV-11_UNILATERAL_VENOUS_LOW_ABI_MASKED')

    # INV-12: Lone Controlled AFib Non-Inflation of RCRI & VSG-CRI
    if (
        q_filled and q.get('cad') and cad_chips == {'afib'}
        and not q.get('recent_stent')
        and s.get('ecg') != 'ischemia_arrhythmia'
        and (ev['s'].get('lvef') in (None, 55, 58, 60))
        and (ev['nt_probnp'] is None or ev['nt_probnp'] < 300)
    ):
        rcri_f = ' | '.join(ev['rcri']['factors'])
        if 'IHD' in rcri_f or 'CHF' in rcri_f:
            violations.append('INV-12_LONE_AFIB_INFLATED_RCRI')
        if ev['vsg_cri'] is not None:
            vsg_f = ' | '.join(ev['vsg_cri']['factors'])
            if 'CAD' in vsg_f or 'გულის უკმარისობა' in vsg_f:
                violations.append('INV-12_LONE_AFIB_INFLATED_VSG_CRI')

    # INV-13: Post-Approval Safety Re-Lock
    if patient.approved_at and (ev['has_unacked_red'] or ev['has_hard_contraindication']):
        if ev['status'] != STATUS_ALERT:
            violations.append('INV-13_APPROVED_WITH_UNACKED_RED_NOT_ALERT')

    # INV-14: Weight-Based Cefazolin 3g & EVLA Lidocaine Ceiling
    if ev['lidocaine_max_mg'] is not None and ev['lidocaine_max_mg'] > 500:
        violations.append('INV-14_LIDOCAINE_OVER_500MG')
    wt_v = ev['s'].get('weight_kg')
    try:
        wt_f = float(wt_v) if wt_v is not None and not isinstance(wt_v, bool) else None
    except (TypeError, ValueError):
        wt_f = None
    if (
        wt_f is not None and 120.0 <= wt_f <= 260.0
        and 'beta_lactam' not in allergy_chips
        and not (pw == PreopPatient.Pathway.VENOUS and patient.op_subtype == PreopPatient.OpSubtype.EVLA)
    ):
        if '3 გ' not in ev['surgical_brief']['abx_regimen']:
            violations.append('INV-14_OBESE_120KG_NOT_GIVEN_CEFAZOLIN_3G')

    # INV-15: GLP-1 24h Clear Liquid Fasting Rule
    if q_filled and q.get('glp1') and '24 სთ' not in ev['fasting_instruction']:
        violations.append('INV-15_GLP1_FASTING_RULE_MISSED')

    # INV-16: Bilateral BP Asymmetry >= 20 mmHg Higher-Arm Rule
    if ev['bp_arm_diff'] is not None and ev['bp_arm_diff'] >= 20:
        if 'მკლავზე' not in ev['anesthetic_brief']['bp_corridor']:
            violations.append('INV-16_BP_ASYMMETRY_NOT_IN_ANESTH_CORRIDOR')

    # INV-17: Creatinine Unit Ambiguity Lock (15..35 without unit)
    if ev['creat_unit_ambiguous'] and not q.get('on_dialysis'):
        if ev['egfr'] is not None or ev['crcl'] is not None:
            violations.append('INV-17_AMBIGUOUS_CREAT_COMPUTED_EGFR')
        if not any('ერთეული საეჭვოა' in rf for rf in ev['red_flags']):
            violations.append('INV-17_AMBIGUOUS_CREAT_NO_RED_FLAG')

    # INV-18: Risk Score Bounds
    if not (0 <= ev['rcri']['score'] <= 6):
        violations.append('INV-18_RCRI_OUT_OF_BOUNDS')
    if ev['ariscat']['score'] < 0:
        violations.append('INV-18_ARISCAT_NEGATIVE')
    if ev['vsg_cri'] is not None and ev['vsg_cri']['score'] < 0:
        violations.append('INV-18_VSG_CRI_NEGATIVE')

    # INV-19: Pre-Questionnaire Critical Alert Visibility (never mask critical lab/duplex alerts as STATUS_NO_Q)
    if (ev['has_unacked_red'] or ev['has_hard_contraindication']) and ev['status'] != STATUS_ALERT:
        violations.append('INV-19_CRITICAL_ALERT_MASKED_BY_STATUS')

    # INV-20: Severe CKD / Dialysis / Open-Aortic Gentamicin Nephrotoxicity Guard & CEA Hemorrhage/Thrombolysis Heparin Guard
    eff_renal_sim = min([v for v in (ev.get('crcl'), ev.get('egfr')) if v is not None], default=None)
    if 'beta_lactam' in allergy_chips:
        if pw in (PreopPatient.Pathway.CEA, PreopPatient.Pathway.VENOUS):
            if 'გენტამიცინი 5 მგ/კგ' in ev['surgical_brief']['abx_regimen']:
                violations.append('INV-20_NECK_OR_VENOUS_UNNECESSARY_GENTAMICIN')
        elif q.get('on_dialysis') or (eff_renal_sim is not None and eff_renal_sim < 45) or ev.get('open_aortic'):
            if 'გენტამიცინს მოერიდეთ' not in ev['surgical_brief']['abx_regimen']:
                violations.append('INV-20_SEVERE_CKD_GENTAMICIN_NOT_GUARDED')
    if pw == PreopPatient.Pathway.CEA and 'hit_heparin' not in allergy_chips and (
        s.get('brain_imaging') == 'large_or_hemorrhage' or (q_filled and bool(q.get('cea_thrombolysis')))
    ):
        if 'ჰემორაგიული ტრანსფორმაციის' not in ev['surgical_brief']['heparin_plan']:
            violations.append('INV-20_CEA_BLEED_RTPA_HEPARIN_NOT_GUARDED')

    # INV-21: Canonical Pathway Guarantee
    if pw not in PreopPatient.Pathway.values:
        violations.append('INV-21_INVALID_PATHWAY_LEAK')

    # INV-22: ASRA 5th Ed Uninterrupted-P2Y12 Neuraxial Blockade Barrier
    has_acs_stent_12m = ('stent_6_12m' in cad_chips) and ('mi' in cad_chips)
    uninterrupted_p2y12 = q_filled and (
        bool(q.get('med_clopidogrel') or q.get('med_ticagrelor') or q.get('med_prasugrel'))
        and (bool(q.get('recent_stent')) or has_acs_stent_12m or ev['is_pta'] or (ev['is_symp_cea'] and not q.get('med_prasugrel')))
    )
    if ev['has_neuraxial_plan'] and uninterrupted_p2y12:
        if 'უკუნაჩვენებია' not in ev['anesthetic_brief']['neuraxial_gate']:
            violations.append('INV-22_NEURAXIAL_GATE_MISSED_UNINTERRUPTED_P2Y12')
        if not any('სპინალური ჰემატომის' in rf for rf in ev['red_flags']):
            violations.append('INV-22_NEURAXIAL_RED_FLAG_MISSED_UNINTERRUPTED_P2Y12')

    # INV-23: Lee 1999 RCRI Surgical Factor Strictly Suprainguinal (NEVER on EVAR, CEA, PAD_BYPASS, PTA, or VENOUS)
    is_supra = (pw == PreopPatient.Pathway.ABF) or (pw == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype != 'evar')
    if not is_supra:
        if any('სუპრაინგვინალური' in f for f in ev['rcri']['factors']):
            violations.append('INV-23_RCRI_SURGICAL_INFLATION_ON_NON_SUPRAINGUINAL_OP')

    # INV-24: ASA IV Physiological Calibration on Acute Hypoxemia / Shock / Critical Hyperkalemia
    spo2_v = s.get('spo2')
    if isinstance(spo2_v, (int, float)) and 30 <= spo2_v < 90 and ev['auto_asa'] != 'IV':
        violations.append('INV-24_SEVERE_HYPOXEMIA_NOT_ASA_IV')

    # INV-25: Bertges et al. 2010 (J Vasc Surg) Exact Published VSG-CRI MACE Table
    if ev['vsg_cri'] is not None:
        v_pts = ev['vsg_cri']['score']
        exp_pct = (
            '2.6%' if v_pts <= 3 else
            '3.5%' if v_pts == 4 else
            '6.0%' if v_pts == 5 else
            '6.6%' if v_pts == 6 else
            '8.9%' if v_pts == 7 else
            '14.3%'
        )
        if ev['vsg_cri']['risk_pct'] != exp_pct:
            violations.append('INV-25_VSG_CRI_BERTGES2010_TABLE_MISMATCH')

    return violations


def run_metamorphic_monotonicity(n_pairs=6000, seed=2026):
    """Apply clinically worsening single-parameter perturbations and verify strict risk monotonicity."""
    rng = random.Random(seed + 77)
    violations = Counter()
    tested = 0

    for i in range(n_pairs):
        base = _random_stratified_patient(rng, i)
        if not base.questionnaire.get('filled'):
            base.questionnaire['filled'] = True
        # Ensure clean numeric baseline on key fields
        base.studies['creat_unit'] = 'umol'
        base.studies['creat'] = 82
        base.studies['hb'] = 138
        base.studies['k'] = 4.2
        base.studies['bp_sys'] = 128
        base.studies['bp_dia'] = 78
        base.studies.pop('bp_sys_l', None)
        base.studies['spo2'] = 98
        base.studies['lvef'] = 58
        base.studies['inr'] = 1.04
        base.approved_at = None

        ev0 = evaluate(base, today=SIM_TODAY)

        # Clone & apply one worsening perturbation
        q1 = json.loads(json.dumps(base.questionnaire))
        s1 = json.loads(json.dumps(base.studies))
        p1 = _make_unsaved_patient(
            idx=i,
            op_subtype=base.op_subtype,
            age=base.age or 68,
            sex=base.sex or 'M',
            side=base.side or 'R',
            someday_list=base.someday_list,
            cardio_decision=base.cardio_decision,
            cardiologist=base.cardiologist,
            q=q1,
            s=s1,
        )
        kind = i % 7
        if kind == 0:
            p1.studies['hb'] = 88  # normal -> significant anemia
        elif kind == 1:
            p1.studies['k'] = 5.8  # normal -> critical hyperkalemia
        elif kind == 2:
            p1.studies['creat'] = 280  # normal -> severe CKD/RCRI >2.0 mg/dL
        elif kind == 3:
            p1.studies['bp_sys'] = 192
            p1.studies['bp_dia'] = 116  # normal -> hypertensive crisis
        elif kind == 4:
            p1.studies['spo2'] = 88  # normal -> severe hypoxemia
        elif kind == 5:
            p1.studies['lvef'] = 32  # normal -> severe HFrEF
        else:
            p1.studies['inr'] = 2.20  # normal -> coagulopathy

        ev1 = evaluate(p1, today=SIM_TODAY)
        tested += 1

        # 1. Never upgrade from STATUS_ALERT to STATUS_READY on clinical deterioration!
        if ev0['status'] == STATUS_ALERT and ev1['status'] == STATUS_READY:
            violations[f'MONO_ALERT_TO_READY_k{kind}'] += 1
        # 2. ASA physical status must never decrease
        if ASA_ORDER[ev1['auto_asa']] < ASA_ORDER[ev0['auto_asa']]:
            violations[f'MONO_ASA_DECREASED_k{kind}'] += 1
        # 3. RCRI, VSG-CRI, ARISCAT, Caprini must never decrease
        if ev1['rcri']['score'] < ev0['rcri']['score']:
            violations[f'MONO_RCRI_DECREASED_k{kind}'] += 1
        if ev0['vsg_cri'] and ev1['vsg_cri'] and ev1['vsg_cri']['score'] < ev0['vsg_cri']['score']:
            violations[f'MONO_VSG_DECREASED_k{kind}'] += 1
        if ev1['ariscat']['score'] < ev0['ariscat']['score']:
            violations[f'MONO_ARISCAT_DECREASED_k{kind}'] += 1
        if ev1['caprini_score'] < ev0['caprini_score']:
            violations[f'MONO_CAPRINI_DECREASED_k{kind}'] += 1

    return {'tested_pairs': tested, 'violations': dict(violations)}


class _Confusion:
    def __init__(self):
        self.tp = 0
        self.tn = 0
        self.fp = 0
        self.fn = 0

    def record(self, expected, predicted):
        if expected and predicted:
            self.tp += 1
        elif not expected and not predicted:
            self.tn += 1
        elif not expected and predicted:
            self.fp += 1
        else:
            self.fn += 1

    def as_dict(self):
        pos = self.tp + self.fn
        neg = self.tn + self.fp
        total = pos + neg
        return {
            'tp': self.tp,
            'tn': self.tn,
            'fp': self.fp,
            'fn': self.fn,
            'sensitivity_pct': round(self.tp * 100.0 / pos, 2) if pos else 100.0,
            'specificity_pct': round(self.tn * 100.0 / neg, 2) if neg else 100.0,
            'accuracy_pct': round((self.tp + self.tn) * 100.0 / total, 2) if total else 100.0,
        }


def _record_guideline_oracle(oracles, patient, ev):
    """Compare engine outputs against an independent declarative Guideline Reference Oracle."""
    q = ev['q']
    s = ev['s']
    if not isinstance(q, dict) or not isinstance(s, dict) or q.get('filled') is not True:
        return
    pw = ev['pathway']
    is_venous = (pw == PreopPatient.Pathway.VENOUS)
    is_pta = (pw == PreopPatient.Pathway.PAD_PTA)
    open_aortic = (
        pw == PreopPatient.Pathway.ABF
        or (pw == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype != 'evar')
    )
    cad_chips = set(_safe_list(q.get('cad_chips')))
    inf_chips = set(_safe_list(q.get('inf_chips')))
    med_chips = set(_safe_list(q.get('med_chips')))

    # 1. Hard Surgical Contraindication Oracle (ESVS 2022/2023)
    exp_hard_contra = bool(
        (pw == PreopPatient.Pathway.CEA and ev['ipsi_car'] is not None and ev['ipsi_car'] >= 100)
        or (pw == PreopPatient.Pathway.CEA and s.get('brain_imaging') == 'large_or_hemorrhage')
        or (is_venous and s.get('ven_duplex_status') in ('acute_dvt', 'deep_obstruction'))
        or (is_venous and bool(q.get('ven_pregnant')))
    )
    oracles['hard_contraindication'].record(exp_hard_contra, ev['has_hard_contraindication'])

    # 2. Critical Vital / Electrolyte / Coagulation Instability Oracle
    bp_s = ev['s'].get('bp_sys')
    bp_sl = ev['s'].get('bp_sys_l')
    bp_d = ev['s'].get('bp_dia')
    bp_dl = ev['s'].get('bp_dia_l')
    eff_s = max([int(round(v)) for v in (bp_s, bp_sl) if isinstance(v, (int, float)) and not isinstance(v, bool) and 30 <= v <= 320], default=None)
    eff_d = max([int(round(v)) for v in (bp_d, bp_dl) if isinstance(v, (int, float)) and not isinstance(v, bool) and 20 <= v <= 220], default=None)
    k_v = ev['s'].get('k')
    k_num = float(k_v) if isinstance(k_v, (int, float)) and not isinstance(k_v, bool) and 1.0 <= k_v <= 12.0 else None
    exp_vital_lab_crit = bool(
        (eff_s is not None and (eff_s >= 180 or eff_s < 90))
        or (eff_d is not None and eff_d >= 110)
        or (ev['hb_gl'] is not None and ev['hb_gl'] < 100)
        or (k_num is not None and (k_num < 3.3 or k_num >= 5.4))
        or (ev['gl_mmol'] is not None and (ev['gl_mmol'] >= 14.0 or ev['gl_mmol'] < 3.9))
    )
    pred_vital_lab_crit = bool(ev['red_flags']) or (ev['status'] == STATUS_ALERT)
    if exp_vital_lab_crit:
        oracles['critical_instability_recall'].record(True, pred_vital_lab_crit)

    # 3. ESC 2022 Preoperative Cardiology Consultation Gate (when cardiologist hasn't already intervened)
    if not patient.cardio_decision:
        hr_v = s.get('hr')
        hr_n = int(round(hr_v)) if isinstance(hr_v, (int, float)) and not isinstance(hr_v, bool) and 15 <= hr_v <= 260 else None
        lvef_v = s.get('lvef')
        lvef_n = int(round(lvef_v)) if isinstance(lvef_v, (int, float)) and not isinstance(lvef_v, bool) and 5 <= lvef_v <= 95 else None
        true_low_mets = (not q.get('mets_ok', True)) and (q.get('mets_limitation') != 'limb')
        exp_cardio = bool(
            open_aortic
            or true_low_mets
            or bool(q.get('cad'))
            or bool(q.get('pacemaker'))
            or (lvef_n is not None and lvef_n < 50)
            or (ev['nt_probnp'] is not None and ev['nt_probnp'] >= 125)
            or (ev['hs_ctn'] is not None and ev['hs_ctn'] >= 14)
            or bool(s.get('echo_valve_issue'))
            or s.get('ecg') in ('ischemia_arrhythmia', 'afib_controlled')
            or bool(s.get('irregular_hr'))
            or (hr_n is not None and (hr_n > 110 or hr_n < 45))
            or ((eff_s is not None and eff_s >= 180) or (eff_d is not None and eff_d >= 110))
            or (pw not in (PreopPatient.Pathway.CEA, PreopPatient.Pathway.VENOUS) and bool(q.get('neuro')))
            or (not is_venous and (bool(q.get('on_dialysis')) or (ev['egfr'] is not None and ev['egfr'] < 60)))
            or (not is_venous and bool(q.get('dm_insulin')))
        )
        oracles['esc2022_cardiology_gate'].record(exp_cardio, ev['needs_cardio'])

    # 4. NICE NG45 / ESAIC 2024 Indication-Driven Chest X-Ray Stewardship
    spo2_v = s.get('spo2')
    spo2_n = int(round(spo2_v)) if isinstance(spo2_v, (int, float)) and not isinstance(spo2_v, bool) and 30 <= spo2_v <= 100 else None
    exp_cxr = bool(
        bool(q.get('pulm'))
        or ('hf' in cad_chips)
        or ('fever_resp' in inf_chips)
        or (spo2_n is not None and spo2_n < 94)
    )
    oracles['nice_ng45_cxr_gate'].record(exp_cxr, ev['needs_cxr'])

    # 5. IDSA 2019 Asymptomatic Bacteriuria / Leukocyturia Stewardship
    uwbc = s.get('urine_wbc')
    uwbc_n = int(round(uwbc)) if isinstance(uwbc, (int, float)) and not isinstance(uwbc, bool) and 0 <= uwbc <= 2000 else None
    has_uti_symp = ('dysuria' in inf_chips) or (bool(q.get('active_infection')) and not inf_chips)
    exp_uti = bool(
        has_uti_symp
        or bool(s.get('urine_nitrite'))
        or (ev['has_synthetic_implant'] and uwbc_n is not None and uwbc_n >= 25)
    )
    oracles['idsa2019_uti_gate'].record(exp_uti, ev['uti_suspected'])

    # 6. ESC 2022 Smart Upper GI (EGD vs PPI) Stewardship
    if (s.get('gastro_result') or 'pending') == 'pending':
        if is_venous or is_pta:
            exp_egd = bool(
                q.get('ulcer_active_symp')
                or (q.get('ulcer') and ev['hb_gl'] is not None and ev['hb_gl'] < 100 and not ev['is_urgent_case'] and not q.get('on_dialysis'))
            )
        else:
            exp_egd = bool(
                q.get('ulcer_active_symp')
                or (ev['hb_gl'] is not None and ev['hb_gl'] < 100 and not ev['is_urgent_case'] and not q.get('on_dialysis'))
            )
        oracles['esc2022_gastro_egd_gate'].record(exp_egd, ev['needs_gastro'])

    # 7. ESAIC Patient Blood Management (Anemia Reflex Gate)
    exp_anemia = bool(ev['hb_gl'] is not None and ev['hb_gl'] < 115)
    oracles['esaic_pbm_anemia_gate'].record(exp_anemia, ev['needs_anemia_panel'])

    # 8. Remitted Ulcer PPI Gastroprotection Gate
    exp_ppi = bool(q.get('ulcer') and not ev['needs_gastro'] and 'ppi' not in med_chips)
    oracles['esc2022_ppi_gate'].record(exp_ppi, ev['needs_ppi'])


def run_simulation(n=50000, n_mono=6000, passport_sample=2000, seed=2026):
    """Execute the full Pre-Pilot Synthetic Simulation & Clinical Objectivity Suite."""
    t0 = time.perf_counter()
    cohort = generate_synthetic_cohort(n=n, seed=seed)
    gen_sec = time.perf_counter() - t0

    latencies_ms = []
    exceptions = []
    invariant_counts = Counter()
    invariant_examples = {}
    pathway_counts = Counter()
    tier_counts = Counter()
    status_counts = Counter()
    seal_counts = Counter()
    passport_rendered_count = 0

    oracles = {
        'hard_contraindication': _Confusion(),
        'critical_instability_recall': _Confusion(),
        'esc2022_cardiology_gate': _Confusion(),
        'nice_ng45_cxr_gate': _Confusion(),
        'idsa2019_uti_gate': _Confusion(),
        'esc2022_gastro_egd_gate': _Confusion(),
        'esaic_pbm_anemia_gate': _Confusion(),
        'esc2022_ppi_gate': _Confusion(),
    }

    passport_stride = max(1, n // passport_sample) if passport_sample > 0 else (n + 1)

    t_eval_start = time.perf_counter()
    for idx, (cohort_tier, patient) in enumerate(cohort):
        tier_counts[cohort_tier] += 1
        t1 = time.perf_counter()
        try:
            ev = evaluate(patient, today=SIM_TODAY)
            anam = generate_anamnesis(patient, ev)
            rendered_html = None
            if passport_sample > 0 and (idx % passport_stride == 0 or cohort_tier == 'fuzz' and idx % 8 == 0):
                rendered_html = render_to_string('mdf/preop/passport.html', {
                    'p': patient,
                    'ev': ev,
                    'q': ev['q'],
                    's': ev['s'],
                    'shown_anamnesis': anam,
                    'surgeon_name': 'ნ. აფხაძე',
                    'approved_by_name': 'ნ. აფხაძე' if patient.approved_at else '',
                    'printed_on': '25.11.2026 12:00',
                })
                passport_rendered_count += 1
        except Exception as exc:  # pragma: no cover - surfaces bugs during simulation
            exceptions.append({
                'idx': idx,
                'tier': cohort_tier,
                'op_subtype': patient.op_subtype,
                'error': f'{type(exc).__name__}: {exc}',
            })
            continue
        dt_ms = (time.perf_counter() - t1) * 1000.0
        latencies_ms.append(dt_ms)

        pathway_counts[ev['pathway']] += 1
        status_counts[ev['status']] += 1
        seal_counts[ev['clearance_seal']['code']] += 1

        viols = check_clinical_invariants(patient, ev, anam, rendered_passport=rendered_html)
        for v in viols:
            invariant_counts[v] += 1
            if v not in invariant_examples:
                invariant_examples[v] = {
                    'idx': idx,
                    'tier': cohort_tier,
                    'op_subtype': patient.op_subtype,
                    'side': patient.side,
                    'status': ev['status'],
                }

        _record_guideline_oracle(oracles, patient, ev)

    eval_sec = time.perf_counter() - t_eval_start
    mono_res = run_metamorphic_monotonicity(n_pairs=n_mono, seed=seed)

    # Full-year calendar header sweep across leap year (2024) + standard year (2026) including all December weeks
    for yr in (2024, 2026):
        d_cur = datetime.date(yr, 1, 1)
        while d_cur.year == yr:
            mon = _monday_of(d_cur)
            sun = mon + datetime.timedelta(days=6)
            try:
                w_txt = _week_title(mon)
                if mon.month == sun.month and not w_txt.startswith(f'{MONTHS[mon.month - 1]} {mon.year}'):
                    invariant_counts['INV-26_CALENDAR_WEEK_TITLE_WRONG_MONTH'] += 1
            except Exception as exc:
                exceptions.append({'idx': -1, 'tier': 'calendar', 'op_subtype': str(mon), 'error': f'{type(exc).__name__}: {exc}'})
            d_cur += datetime.timedelta(days=1)

    lat_sorted = sorted(latencies_ms)
    n_lat = len(lat_sorted)
    latency_summary = {
        'mean_ms': round(sum(lat_sorted) / n_lat, 4) if n_lat else 0.0,
        'p50_ms': round(lat_sorted[int(0.50 * (n_lat - 1))], 4) if n_lat else 0.0,
        'p95_ms': round(lat_sorted[int(0.95 * (n_lat - 1))], 4) if n_lat else 0.0,
        'p99_ms': round(lat_sorted[int(0.99 * (n_lat - 1))], 4) if n_lat else 0.0,
        'max_ms': round(lat_sorted[-1], 4) if n_lat else 0.0,
    }

    return {
        'total_scenarios': n,
        'metamorphic_pairs': mono_res['tested_pairs'],
        'total_evaluations': n + mono_res['tested_pairs'] * 2,
        'passports_rendered': passport_rendered_count,
        'generation_sec': round(gen_sec, 2),
        'evaluation_sec': round(eval_sec, 2),
        'cohort_tiers': dict(tier_counts),
        'pathway_distribution': dict(pathway_counts),
        'status_distribution': dict(status_counts),
        'seal_distribution': dict(seal_counts),
        'exceptions_count': len(exceptions),
        'exceptions_sample': exceptions[:10],
        'invariant_violations_total': sum(invariant_counts.values()),
        'invariant_violations': dict(invariant_counts),
        'invariant_examples': invariant_examples,
        'metamorphic_violations': mono_res['violations'],
        'guideline_oracles': {k: v.as_dict() for k, v in oracles.items()},
        'latency_ms': latency_summary,
    }


def main():
    parser = argparse.ArgumentParser(description='Run Pre-Pilot 50,000+ Synthetic Clinical Safety Simulation.')
    parser.add_argument('--n', type=int, default=50000)
    parser.add_argument('--mono', type=int, default=6000)
    parser.add_argument('--passports', type=int, default=2000)
    parser.add_argument('--seed', type=int, default=2026)
    args = parser.parse_args()

    import django
    django.setup()
    report = run_simulation(n=args.n, n_mono=args.mono, passport_sample=args.passports, seed=args.seed)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
