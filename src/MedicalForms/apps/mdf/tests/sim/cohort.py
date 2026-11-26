"""Synthetic preoperative patient cohort generators (stratified, boundary, and hostile fuzz)."""
import datetime
import random

from ...models import PreopPatient
from ...preop import (
    CHIP_CATALOGS,
    STATUS_ALERT,
    STATUS_NO_Q,
    STATUS_PROGRESS,
    STATUS_READY,
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
        p.approved_at = datetime.datetime(2026, 11, 25, 9, 30, tzinfo=datetime.UTC)
    return p


def _fill_random_comorbidities(rng, q):
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


def _fill_random_meds_and_allergies(rng, pathway, q):
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


def _fill_random_pathway_q(rng, pathway, op_subtype, sex, age, q):
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


def _build_random_base_studies(rng):
    wt = round(rng.uniform(42, 138), 1)
    ht = rng.randint(150, 192)
    if rng.random() < 0.22:
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
    return s


def _fill_random_pathway_studies(rng, pathway, s):
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


def _fill_random_stage2_and_dos(rng, s):
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
        _fill_random_comorbidities(rng, q)
        _fill_random_meds_and_allergies(rng, pathway, q)
        _fill_random_pathway_q(rng, pathway, op_subtype, sex, age, q)

    s = _build_random_base_studies(rng)
    _fill_random_pathway_studies(rng, pathway, s)
    _fill_random_stage2_and_dos(rng, s)

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
    return PreopPatient(
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


def generate_synthetic_cohort(n=50000, seed=2026):
    """Deterministically generate a 4-tier synthetic preoperative cohort without touching the database."""
    from .archetypes import _archetype_patient

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
