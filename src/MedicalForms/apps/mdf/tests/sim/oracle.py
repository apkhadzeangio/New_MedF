"""Independent declarative Guideline Reference Oracle and confusion matrix tracker."""
from ...models import PreopPatient
from ...preop import STATUS_ALERT
from .invariants import _safe_list


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


def _oracle_hard_contra_and_vitals(oracles, patient, ev, q, s, pw, is_venous, is_pta):
    is_clti_or = (
        patient.op_subtype in (PreopPatient.OpSubtype.ABF_CLTI, PreopPatient.OpSubtype.BYPASS_CLTI, PreopPatient.OpSubtype.PTA_CLTI)
        or bool(q.get('abf_rest_pain')) or bool(q.get('abf_ulcer'))
    )
    is_ali_or = bool(q.get('abf_duration') == '< 2 კვირა')
    preg_bio_or = (patient.sex != 'M') and not (patient.sex == 'F' and patient.age is not None and patient.age > 55)
    eff_preg_or = preg_bio_or and (bool(q.get('ven_pregnant')) or bool(q.get('pregnant_possible')))
    uses_contrast_or = (patient.op_subtype in (PreopPatient.OpSubtype.EVAR, PreopPatient.OpSubtype.TBD)) or is_pta
    evla_lido_stop_or = (
        is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA
        and (bool(q.get('ven_lidocaine_allergy')) or 'lidocaine' in (ev.get('eff_allergy_chips') or ()))
        and (s.get('anesthesia_plan') in (None, '', 'auto', 'local'))
    )
    exp_hard_contra = bool(
        (pw == PreopPatient.Pathway.CEA and patient.side in ('R', 'L') and ev['ipsi_car'] is not None and ev['ipsi_car'] >= 100)
        or (pw == PreopPatient.Pathway.CEA and s.get('brain_imaging') == 'large_or_hemorrhage')
        or (pw == PreopPatient.Pathway.CEA and patient.side == 'B')
        or (is_pta and not is_clti_or and not is_ali_or and s.get('pta_lesion_site') == 'infrapopliteal')
        or (pw == PreopPatient.Pathway.PAD_BYPASS and not is_clti_or and not is_ali_or and (
            s.get('pad_runoff_status') == 'desert_foot'
            or (s.get('alt_vein_mapped') == 'no_autovein_prosthetic')
        ))
        or (pw in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA) and not is_clti_or and not is_ali_or and eff_preg_or)
        or (uses_contrast_or and s.get('tsh_status') == 'hyperthyroid')
        or (is_venous and s.get('ven_duplex_status') in ('acute_dvt', 'deep_obstruction'))
        or (is_venous and eff_preg_or)
        or evla_lido_stop_or
        or (is_venous and (
            (ev.get('min_abi') is not None and ev['min_abi'] < 0.60)
            or (ev.get('min_tp') is not None and ev['min_tp'] < 30)
            or (ev.get('min_tbi') is not None and ev['min_tbi'] < 0.30)
        ))
    )
    oracles['hard_contraindication'].record(exp_hard_contra, ev['has_hard_contraindication'])

    bp_s, bp_sl = ev['s'].get('bp_sys'), ev['s'].get('bp_sys_l')
    bp_d, bp_dl = ev['s'].get('bp_dia'), ev['s'].get('bp_dia_l')
    s_r = int(round(bp_s)) if isinstance(bp_s, (int, float)) and not isinstance(bp_s, bool) and 30 <= bp_s <= 320 else None
    d_r = int(round(bp_d)) if isinstance(bp_d, (int, float)) and not isinstance(bp_d, bool) and 20 <= bp_d <= 220 else None
    s_l = int(round(bp_sl)) if isinstance(bp_sl, (int, float)) and not isinstance(bp_sl, bool) and 30 <= bp_sl <= 320 else None
    d_l = int(round(bp_dl)) if isinstance(bp_dl, (int, float)) and not isinstance(bp_dl, bool) and 20 <= bp_dl <= 220 else None
    inv_r = (s_r is not None and d_r is not None and s_r <= d_r)
    inv_l = (s_l is not None and d_l is not None and s_l <= d_l)
    eff_s = max([v for v, inv in ((s_r, inv_r), (s_l, inv_l)) if v is not None and not inv], default=None)
    eff_d = max([v for v, inv in ((d_r, inv_r), (d_l, inv_l)) if v is not None and not inv], default=None)
    k_v = ev['s'].get('k')
    k_num = float(k_v) if isinstance(k_v, (int, float)) and not isinstance(k_v, bool) and 1.0 <= k_v <= 12.0 else None
    exp_vital_lab_crit = bool(
        inv_r or inv_l
        or (eff_s is not None and (eff_s >= 180 or eff_s < 90))
        or (eff_d is not None and eff_d >= 110)
        or (ev['hb_gl'] is not None and ev['hb_gl'] < 100)
        or (k_num is not None and (k_num < 3.3 or k_num >= 5.4))
        or (ev['gl_mmol'] is not None and (ev['gl_mmol'] >= 14.0 or ev['gl_mmol'] < 3.9))
    )
    if exp_vital_lab_crit:
        oracles['critical_instability_recall'].record(True, bool(ev['red_flags']) or (ev['status'] == STATUS_ALERT))
    return eff_s, eff_d


def _oracle_cardio_cxr_uti(oracles, patient, ev, q, s, pw, is_venous, open_aortic, cad_chips, inf_chips, eff_s, eff_d):
    from ...preop.calculators import _s_raw, _sat_num

    if not patient.cardio_decision:
        hr_n = _sat_num(_s_raw(s, 'hr'), 15, 260)
        lvef_n = _sat_num(_s_raw(s, 'lvef'), 5, 95)
        bnp_n = ev['nt_probnp'] if ev['nt_probnp'] is not None else _sat_num(_s_raw(s, 'nt_probnp'), 1, 100000)
        ctn_n = ev['hs_ctn'] if ev['hs_ctn'] is not None else _sat_num(_s_raw(s, 'hs_ctn'), 0.1, 50000)
        sat_s = max([v for v in (_sat_num(_s_raw(s, 'bp_sys'), 30, 320), _sat_num(_s_raw(s, 'bp_sys_l'), 30, 320)) if v is not None], default=None)
        sat_d = max([v for v in (_sat_num(_s_raw(s, 'bp_dia'), 20, 220), _sat_num(_s_raw(s, 'bp_dia_l'), 20, 220)) if v is not None], default=None)
        sev_htn_or = bool(
            (eff_s is not None and eff_s >= 180) or (eff_d is not None and eff_d >= 110)
            or (sat_s is not None and sat_s >= 180) or (sat_d is not None and sat_d >= 110)
        )
        true_low_mets = (not q.get('mets_ok', True)) and (q.get('mets_limitation') != 'limb')
        is_pure_local_pta = (
            pw == PreopPatient.Pathway.PAD_PTA
            and not bool(q.get('pta_cant_lie_flat'))
            and (s.get('anesthesia_plan') in (None, '', 'auto', 'local'))
        )
        ckd_cardio_thresh = 45 if is_pure_local_pta else 60
        exp_cardio = bool(
            open_aortic
            or true_low_mets
            or bool(q.get('cad'))
            or bool(cad_chips)
            or bool(q.get('recent_stent'))
            or bool(q.get('pacemaker'))
            or (lvef_n is not None and lvef_n < 50)
            or (bnp_n is not None and bnp_n >= 125)
            or (ctn_n is not None and ctn_n >= 14)
            or bool(s.get('echo_valve_issue'))
            or s.get('ecg') in ('ischemia_arrhythmia', 'afib_controlled')
            or bool(s.get('irregular_hr'))
            or (hr_n is not None and (hr_n > 110 or hr_n < 45))
            or sev_htn_or
            or (pw not in (PreopPatient.Pathway.CEA, PreopPatient.Pathway.VENOUS) and bool(q.get('neuro')))
            or (not is_venous and (bool(q.get('on_dialysis')) or (ev['egfr'] is not None and ev['egfr'] < ckd_cardio_thresh)))
            or (not is_venous and bool(q.get('dm_insulin')))
        )
        oracles['esc2022_cardiology_gate'].record(exp_cardio, ev['needs_cardio'])

    spo2_v = s.get('spo2')
    spo2_n = (
        max(30, min(100, int(round(spo2_v))))
        if isinstance(spo2_v, (int, float)) and not isinstance(spo2_v, bool) and spo2_v == spo2_v and abs(spo2_v) != float('inf')
        else None
    )
    exp_cxr = bool(
        bool(q.get('pulm'))
        or ('hf' in cad_chips)
        or ('fever_resp' in inf_chips)
        or (spo2_n is not None and spo2_n < 94)
    )
    oracles['nice_ng45_cxr_gate'].record(exp_cxr, ev['needs_cxr'])

    uwbc = s.get('urine_wbc')
    uwbc_n = int(round(uwbc)) if isinstance(uwbc, (int, float)) and not isinstance(uwbc, bool) and 0 <= uwbc <= 2000 else None
    has_uti_symp = ('dysuria' in inf_chips) or (bool(q.get('active_infection')) and not inf_chips)
    exp_uti = bool(
        has_uti_symp
        or bool(s.get('urine_nitrite'))
        or (ev['has_synthetic_implant'] and uwbc_n is not None and uwbc_n >= 25)
    )
    oracles['idsa2019_uti_gate'].record(exp_uti, ev['uti_suspected'])


def _oracle_gi_anemia_ppi(oracles, patient, ev, q, s, is_venous, is_pta, med_chips):
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

    exp_anemia = bool(ev['hb_gl'] is not None and ev['hb_gl'] < 115)
    oracles['esaic_pbm_anemia_gate'].record(exp_anemia, ev['needs_anemia_panel'])

    p2y12_sim_cnt = sum([bool(q.get('med_clopidogrel')), bool(q.get('med_ticagrelor')), bool(q.get('med_prasugrel'))])
    has_dapt_sim = (p2y12_sim_cnt >= 2) or (bool(q.get('med_aspirin')) and p2y12_sim_cnt >= 1)
    has_oac_sim = bool(q.get('med_warfarin') or q.get('med_doac') or q.get('med_dabigatran'))
    has_ap_sim = bool(q.get('med_aspirin') or p2y12_sim_cnt >= 1 or 'cilostazol' in med_chips)
    has_any_at_sim = has_oac_sim or has_ap_sim or ('rivaroxaban_2_5' in med_chips)
    has_elder_steroid_sim = ((patient.age or 0) >= 65) and ('steroid' in med_chips) and has_any_at_sim
    exp_ppi = bool(
        (
            bool(q.get('ulcer'))
            or bool(q.get('ulcer_active_symp'))
            or (s.get('gastro_result') == 'acute_ulcer')
            or has_dapt_sim
            or (has_oac_sim and has_ap_sim)
            or has_elder_steroid_sim
        )
        and 'ppi' not in med_chips
    )
    oracles['esc2022_ppi_gate'].record(exp_ppi, ev['needs_ppi'])


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

    eff_s, eff_d = _oracle_hard_contra_and_vitals(oracles, patient, ev, q, s, pw, is_venous, is_pta)
    _oracle_cardio_cxr_uti(oracles, patient, ev, q, s, pw, is_venous, open_aortic, cad_chips, inf_chips, eff_s, eff_d)
    _oracle_gi_anemia_ppi(oracles, patient, ev, q, s, is_venous, is_pta, med_chips)
