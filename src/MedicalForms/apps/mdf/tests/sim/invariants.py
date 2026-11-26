"""Hard clinical safety invariants (INV-01..INV-25) and metamorphic monotonicity testing."""
import json
import random
from collections import Counter

from ...models import PreopPatient
from ...preop import STATUS_ALERT, STATUS_READY, evaluate
from .cohort import ASA_ORDER, SIM_TODAY, _make_unsaved_patient, _random_stratified_patient


def _safe_list(val):
    return val if isinstance(val, (list, tuple, set)) else []


def _check_passport_and_hard_contra(patient, ev, q, s, pw, q_filled, rendered_passport, violations):
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
    vasc_chips_sim = set(_safe_list(q.get('vasc_chips')))
    is_clti_sim = (
        patient.op_subtype in (PreopPatient.OpSubtype.ABF_CLTI, PreopPatient.OpSubtype.BYPASS_CLTI, PreopPatient.OpSubtype.PTA_CLTI)
        or (q_filled and (bool(q.get('abf_rest_pain')) or bool(q.get('abf_ulcer'))))
    )
    is_ali_sim = bool(q_filled and q.get('abf_duration') == '< 2 კვირა')
    gsv_inadeq_sim = (
        (s.get('pad_gsv_status') == 'inadequate')
        or (ev.get('pad_gsv_mm') is not None and ev['pad_gsv_mm'] < 3.0)
        or ('vein_strip' in vasc_chips_sim)
    )
    preg_bio_ok = (patient.sex != 'M') and not (patient.sex == 'F' and patient.age is not None and patient.age > 55)
    eff_preg_sim = q_filled and preg_bio_ok and (bool(q.get('ven_pregnant')) or bool(q.get('pregnant_possible')))
    ipsi_100 = (pw == PreopPatient.Pathway.CEA and patient.side in ('R', 'L') and ev['ipsi_car'] is not None and ev['ipsi_car'] >= 100)
    brain_bleed = (pw == PreopPatient.Pathway.CEA and s.get('brain_imaging') == 'large_or_hemorrhage')
    bilat_cea = (pw == PreopPatient.Pathway.CEA and patient.side == 'B')
    claud_btk = (pw == PreopPatient.Pathway.PAD_PTA and not is_clti_sim and not is_ali_sim and s.get('pta_lesion_site') == 'infrapopliteal')
    claud_byp_contra = (
        pw == PreopPatient.Pathway.PAD_BYPASS and not is_clti_sim and not is_ali_sim and (
            s.get('pad_runoff_status') == 'desert_foot'
            or (gsv_inadeq_sim and s.get('alt_vein_mapped') == 'no_autovein_prosthetic')
        )
    )
    claud_preg_contra = (
        pw in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA)
        and not is_clti_sim and eff_preg_sim
    )
    ven_contra = (
        pw == PreopPatient.Pathway.VENOUS and (
            s.get('ven_duplex_status') in ('acute_dvt', 'deep_obstruction')
            or eff_preg_sim
            or (ev.get('min_abi') is not None and ev['min_abi'] < 0.60)
            or (ev.get('min_tp') is not None and ev['min_tp'] < 30)
        )
    )
    if ipsi_100 or brain_bleed or bilat_cea or claud_btk or claud_byp_contra or claud_preg_contra or ven_contra:
        if not ev['has_hard_contraindication']:
            violations.append('INV-02_HARD_CONTRA_FLAG_MISSED')
        if ev['status'] == STATUS_READY or ev['workup_ready'] or ev['dos_ready']:
            violations.append('INV-02_HARD_CONTRA_REACHED_READY')
        if ev['clearance_seal']['code'] != 'stop':
            violations.append('INV-02_HARD_CONTRA_SEAL_NOT_STOP')


def _check_allergy_neuraxial_and_renal_meds(patient, ev, q, pw, q_filled, allergy_chips, violations):
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


def _check_pathway_and_risk_guards(patient, ev, q, s, pw, q_filled, cad_chips, violations):
    # INV-08: CEA Antiplatelet Non-Contradiction
    if pw == PreopPatient.Pathway.CEA and q_filled:
        brain_bleed = s.get('brain_imaging') == 'large_or_hemorrhage'
        days_ev = ev.get('days_since_ev')
        eff_alg = set(ev.get('eff_allergy_chips') or ())
        rtpa_prohibited = bool(q.get('cea_thrombolysis')) and (
            days_ev is None or days_ev < 1 or (days_ev <= 6 and s.get('brain_imaging') != 'normal_small')
        )
        prohibited = brain_bleed or (bool(q.get('cea_thrombolysis')) and (days_ev is None or days_ev < 1))
        if prohibited and ev['needs_cea_antiplatelet']:
            violations.append('INV-08_CEA_AP_ORDERED_ON_BLEED_OR_RTPA')
        if ev['is_symp_cea'] and not brain_bleed and not rtpa_prohibited and 'nsaid' not in eff_alg:
            for row in ev['med_schedule']:
                if row['drug'] == 'ასპირინი (75–100 მგ)' and 'გრძელდება' not in row['rule']:
                    violations.append('INV-08_SYMP_CEA_ASPIRIN_HELD')
                if row['drug'] == 'კლოპიდოგრელი / P2Y12' and not (q.get('recent_stent') or ('stent_6_12m' in cad_chips and 'mi' in cad_chips)) and 'clopidogrel_intol' not in eff_alg:
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
            if 'VOYAGER PAD (რივაროქსაბანი' in ev['post_antithrombotic_label']:
                violations.append('INV-09_VOYAGER_AUTO_RECOMMENDED_WHEN_CONTRAINDICATED')
        elif post_raw == 'voyager' and voyager_contra:
            if not any('VOYAGER' in rf for rf in ev['red_flags']):
                violations.append('INV-09_MANUAL_VOYAGER_CONTRA_NOT_FLAGGED')

    # INV-10: Claudication Class III Harm Guard
    is_clti = (
        patient.op_subtype in (PreopPatient.OpSubtype.ABF_CLTI, PreopPatient.OpSubtype.BYPASS_CLTI, PreopPatient.OpSubtype.PTA_CLTI)
        or (q_filled and (bool(q.get('abf_rest_pain')) or bool(q.get('abf_ulcer'))))
    )
    is_ali = bool(q_filled and q.get('abf_duration') == '< 2 კვირა')
    if pw == PreopPatient.Pathway.PAD_PTA and not is_clti and not is_ali and s.get('pta_lesion_site') == 'infrapopliteal':
        if not any('Class III' in rf for rf in ev['red_flags']):
            violations.append('INV-10_BTK_PTA_CLAUDICATION_MISSED')
    if pw == PreopPatient.Pathway.PAD_BYPASS and not is_clti and not is_ali and s.get('alt_vein_mapped') == 'no_autovein_prosthetic':
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
            if not any('ABI' in rf and ('< 0.80' in rf or '< 0.60' in rf) for rf in ev['red_flags']):
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


def _check_dosing_vitals_and_scores(patient, ev, q, s, pw, q_filled, allergy_chips, cad_chips, violations):
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

    # INV-15..19: GLP-1, BP asymmetry, ambiguous Cr, score bounds, pre-Q critical visibility
    if q_filled and q.get('glp1'):
        if ev['clearance_seal']['code'] == 'stop':
            if 'STOP' not in ev['fasting_instruction']:
                violations.append('INV-15_GLP1_FASTING_RULE_MISSED')
        elif '24 სთ' not in ev['fasting_instruction']:
            violations.append('INV-15_GLP1_FASTING_RULE_MISSED')
    if ev['bp_arm_diff'] is not None and ev['bp_arm_diff'] >= 20:
        if 'მკლავზე' not in ev['anesthetic_brief']['bp_corridor']:
            violations.append('INV-16_BP_ASYMMETRY_NOT_IN_ANESTH_CORRIDOR')
    if ev['creat_unit_ambiguous'] and not q.get('on_dialysis'):
        if ev['egfr'] is not None or ev['crcl'] is not None:
            violations.append('INV-17_AMBIGUOUS_CREAT_COMPUTED_EGFR')
        if not any(('ერთეული საეჭვოა' in rf or 'ერთეული არ ემთხვევა' in rf) for rf in ev['red_flags']):
            violations.append('INV-17_AMBIGUOUS_CREAT_NO_RED_FLAG')
    if not (0 <= ev['rcri']['score'] <= 6):
        violations.append('INV-18_RCRI_OUT_OF_BOUNDS')
    if ev['ariscat']['score'] < 0:
        violations.append('INV-18_ARISCAT_NEGATIVE')
    if ev['vsg_cri'] is not None and ev['vsg_cri']['score'] < 0:
        violations.append('INV-18_VSG_CRI_NEGATIVE')
    if (ev['has_unacked_red'] or ev['has_hard_contraindication']) and ev['status'] != STATUS_ALERT:
        violations.append('INV-19_CRITICAL_ALERT_MASKED_BY_STATUS')

    # INV-20..25: Gentamicin CKD guard, CEA heparin guard, canonical pathway, P2Y12 neuraxial, RCRI suprainguinal, ASA IV hypoxemia, VSG-CRI table
    eff_renal_sim = min([v for v in (ev.get('crcl'), ev.get('egfr')) if v is not None], default=None)
    if 'beta_lactam' in allergy_chips:
        if pw in (PreopPatient.Pathway.CEA, PreopPatient.Pathway.VENOUS):
            if 'გენტამიცინი 5 მგ/კგ' in ev['surgical_brief']['abx_regimen']:
                violations.append('INV-20_NECK_OR_VENOUS_UNNECESSARY_GENTAMICIN')
        elif q.get('on_dialysis') or (eff_renal_sim is not None and eff_renal_sim < 45) or ev.get('open_aortic'):
            if 'გენტამიცინს მოერიდეთ' not in ev['surgical_brief']['abx_regimen']:
                violations.append('INV-20_SEVERE_CKD_GENTAMICIN_NOT_GUARDED')
    active_rtpa = q_filled and bool(q.get('cea_thrombolysis')) and (ev.get('days_since_ev') is None or ev['days_since_ev'] <= 6)
    if pw == PreopPatient.Pathway.CEA and 'hit_heparin' not in allergy_chips and (
        s.get('brain_imaging') == 'large_or_hemorrhage' or active_rtpa
    ):
        if 'ჰემორაგიული ტრანსფორმაციის' not in ev['surgical_brief']['heparin_plan']:
            violations.append('INV-20_CEA_BLEED_RTPA_HEPARIN_NOT_GUARDED')
    if pw not in PreopPatient.Pathway.values:
        violations.append('INV-21_INVALID_PATHWAY_LEAK')

    has_acs_stent_12m = ('stent_6_12m' in cad_chips) and ('mi' in cad_chips)
    uninterrupted_p2y12 = q_filled and (
        bool(q.get('med_clopidogrel') or q.get('med_ticagrelor') or q.get('med_prasugrel'))
        and (
            bool(q.get('recent_stent'))
            or has_acs_stent_12m
            or (ev['is_pta'] and not q.get('med_prasugrel'))
            or (ev['is_symp_cea'] and not q.get('med_prasugrel'))
        )
    )
    if ev['has_neuraxial_plan'] and uninterrupted_p2y12:
        if 'უკუნაჩვენებია' not in ev['anesthetic_brief']['neuraxial_gate']:
            violations.append('INV-22_NEURAXIAL_GATE_MISSED_UNINTERRUPTED_P2Y12')
        if not any('სპინალური ჰემატომის' in rf for rf in ev['red_flags']):
            violations.append('INV-22_NEURAXIAL_RED_FLAG_MISSED_UNINTERRUPTED_P2Y12')

    is_supra = (pw == PreopPatient.Pathway.ABF) or (pw == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype != 'evar')
    if not is_supra and any('სუპრაინგვინალური' in f for f in ev['rcri']['factors']):
        violations.append('INV-23_RCRI_SURGICAL_INFLATION_ON_NON_SUPRAINGUINAL_OP')

    spo2_v = s.get('spo2')
    if isinstance(spo2_v, (int, float)) and 30 <= spo2_v < 90 and ev['auto_asa'] != 'IV':
        violations.append('INV-24_SEVERE_HYPOXEMIA_NOT_ASA_IV')

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


def check_clinical_invariants(patient, ev, anam, rendered_passport=None):
    """Verify 25 Never-Event Clinical & Engineering Invariants on a single evaluated patient."""
    violations = []
    q = ev['q']
    s = ev['s']
    pw = ev['pathway']
    q_filled = bool(q.get('filled'))
    allergy_chips = set(_safe_list(q.get('allergy_chips')))
    cad_chips = set(_safe_list(q.get('cad_chips')))

    _check_passport_and_hard_contra(patient, ev, q, s, pw, q_filled, rendered_passport, violations)
    _check_allergy_neuraxial_and_renal_meds(patient, ev, q, pw, q_filled, allergy_chips, violations)
    _check_pathway_and_risk_guards(patient, ev, q, s, pw, q_filled, cad_chips, violations)
    _check_dosing_vitals_and_scores(patient, ev, q, s, pw, q_filled, allergy_chips, cad_chips, violations)
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
            p1.studies['hb'] = 88
        elif kind == 1:
            p1.studies['k'] = 5.8
        elif kind == 2:
            p1.studies['creat'] = 280
        elif kind == 3:
            p1.studies['bp_sys'] = 192
            p1.studies['bp_dia'] = 116
        elif kind == 4:
            p1.studies['spo2'] = 88
        elif kind == 5:
            p1.studies['lvef'] = 32
        else:
            p1.studies['inr'] = 2.20

        ev1 = evaluate(p1, today=SIM_TODAY)
        tested += 1

        if ev0['status'] == STATUS_ALERT and ev1['status'] == STATUS_READY:
            violations[f'MONO_ALERT_TO_READY_k{kind}'] += 1
        if ASA_ORDER[ev1['auto_asa']] < ASA_ORDER[ev0['auto_asa']]:
            violations[f'MONO_ASA_DECREASED_k{kind}'] += 1
        if ev1['rcri']['score'] < ev0['rcri']['score']:
            violations[f'MONO_RCRI_DECREASED_k{kind}'] += 1
        if ev0['vsg_cri'] and ev1['vsg_cri'] and ev1['vsg_cri']['score'] < ev0['vsg_cri']['score']:
            violations[f'MONO_VSG_DECREASED_k{kind}'] += 1
        if ev1['ariscat']['score'] < ev0['ariscat']['score']:
            violations[f'MONO_ARISCAT_DECREASED_k{kind}'] += 1
        if ev1['caprini_score'] < ev0['caprini_score']:
            violations[f'MONO_CAPRINI_DECREASED_k{kind}'] += 1

    return {'tested_pairs': tested, 'violations': dict(violations)}
