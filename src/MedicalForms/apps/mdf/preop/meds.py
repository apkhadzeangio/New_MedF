"""Free-text medication/allergy detection and perioperative medication hold/continue scheduler."""
import datetime

from ..models import PreopPatient
from .constants import (
    _FREETEXT_ALLERGY_STEMS,
    _FREETEXT_HIGH_RISK_MED_RULES,
    _HIT_WORD_RE,
)
from .utils import _date, _int, _num, _resolve_pathway, _str_list, cea_live_days_since_event


def _detect_freetext_allergies(text):
    """Detect structured allergy classes from free-text `q.allergy_text` (Georgian & Latin stems)."""
    if not isinstance(text, str) or not text.strip():
        return set()
    low = text.lower()
    matched = set()
    for key, stems in _FREETEXT_ALLERGY_STEMS:
        if any(st in low for st in stems):
            matched.add(key)
    if _HIT_WORD_RE.search(low):
        matched.add('hit_heparin')
    return matched


def _detect_freetext_high_risk_meds(text, q=None):
    """Detect high-risk anticoagulants, P2Y12, SGLT2i, GLP-1 & insulin in `q.other_meds` when not structurally checked."""
    if not isinstance(text, str) or not text.strip():
        return {'matched_keys': set(), 'unstructured_labels': [], 'unstructured_antithrombotic': False, 'ertugliflozin': False}
    low = text.lower()
    q_d = q if isinstance(q, dict) else {}
    med_chips = set(_str_list(q_d.get('med_chips')))
    structured_map = {
        'warfarin': bool(q_d.get('med_warfarin')),
        'doac': bool(q_d.get('med_doac') or 'rivaroxaban_2_5' in med_chips),
        'dabigatran': bool(q_d.get('med_dabigatran')),
        'parenteral_oac': False,
        'clopidogrel': bool(q_d.get('med_clopidogrel')),
        'ticagrelor': bool(q_d.get('med_ticagrelor')),
        'prasugrel': bool(q_d.get('med_prasugrel')),
        'cilostazol': 'cilostazol' in med_chips,
        'sglt2': bool(q_d.get('sglt2')),
        'glp1': bool(q_d.get('glp1')),
        'insulin': bool(q_d.get('dm_insulin')),
    }
    matched_keys = set()
    unstructured_labels = []
    unstructured_at = False
    for key, label, is_at, stems in _FREETEXT_HIGH_RISK_MED_RULES:
        if any(st in low for st in stems):
            matched_keys.add(key)
            if not structured_map.get(key, False):
                unstructured_labels.append(label)
                if is_at:
                    unstructured_at = True
    is_ertug = any(st in low for st in ('ერტუგლიფ', 'ertuglif', 'სტეგლატრ', 'steglatro'))
    return {
        'matched_keys': matched_keys,
        'unstructured_labels': unstructured_labels,
        'unstructured_antithrombotic': unstructured_at,
        'ertugliflozin': is_ertug,
    }


def _effective_allergy_state(q):
    """Combine structured `allergy_chips` / `allergy_iodine` with `_detect_freetext_allergies(q.allergy_text)`."""
    q_d = q if isinstance(q, dict) else {}
    chips = set(_str_list(q_d.get('allergy_chips')))
    ft = _detect_freetext_allergies(q_d.get('allergy_text') or '')
    eff_chips = chips | (ft - {'iodine'})
    eff_iodine = bool(q_d.get('allergy_iodine')) or ('iodine' in ft)
    return eff_chips, eff_iodine, ft


def _apply_stop_lock_to_med_schedule(rows):
    """C1: Suppress dated discontinuation orders when surgery is STOP-blocked (`clearance_seal == 'stop'`)."""
    for r in rows:
        if r.get('hold_or_skip'):
            r['date_plan'] = 'STOP — თვითნებურად არ შეწყვიტოთ!'
            r['tone'] = 'red'
    return rows


def _make_med_row_helpers(op_dt):
    rows = []

    def d_str(delta_days):
        return (op_dt + datetime.timedelta(days=delta_days)).strftime('%d.%m') if op_dt else ''

    def hold_str(hold_days, sfx=''):
        if not op_dt:
            return ''
        return f'ბოლო: {d_str(-hold_days - 1)} · არ მიიღოთ {d_str(-hold_days)}-დან{sfx}'

    def add_row(drug, rule, tone, date_plan='', hold_or_skip=False, force_date_plan=False):
        rows.append({
            'drug': drug,
            'rule': rule,
            'tone': tone,
            'date_plan': date_plan if (op_dt or force_date_plan) else '',
            'hold_or_skip': hold_or_skip,
        })

    return rows, d_str, hold_str, add_row


def _med_schedule_ctx(patient, q, s, egfr, crcl, has_neuraxial_override):
    pw = _resolve_pathway(patient)
    is_evla = (patient.op_subtype == PreopPatient.OpSubtype.EVLA)
    is_phleb = (patient.op_subtype == PreopPatient.OpSubtype.PHLEB)
    is_pta = (pw == PreopPatient.Pathway.PAD_PTA)
    cea_d = cea_live_days_since_event(q, s=s) if pw == PreopPatient.Pathway.CEA else None
    symp_within_6m = (cea_d is None) or (cea_d <= 180)
    is_symp_cea = (pw == PreopPatient.Pathway.CEA) and symp_within_6m and (
        patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP or bool(q.get('cea_symp_event'))
    )
    is_open_major = pw in (
        PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS
    ) and patient.op_subtype != 'evar'
    uses_contrast = (patient.op_subtype in ('evar', 'tbd')) or is_pta

    anesth_plan = s.get('anesthesia_plan') or 'auto'
    has_epidural_line = 'epidural' in _str_list(s.get('anesth_lines_chips'))
    if has_neuraxial_override is not None:
        is_neuraxial = bool(has_neuraxial_override)
    else:
        is_neuraxial = (anesth_plan == 'neuraxial') or has_epidural_line or (anesth_plan == 'auto' and is_phleb)

    on_dialysis = bool(q.get('on_dialysis'))
    renal_vals = [v for v in (egfr, crcl) if v is not None]
    eff_renal = None if on_dialysis else (min(renal_vals) if renal_vals else None)
    if on_dialysis:
        cl_label = 'დიალიზი'
    elif crcl is not None and (egfr is None or crcl <= egfr):
        cl_label = f'CrCl {crcl}'
    elif egfr is not None:
        cl_label = f'eGFR {egfr}'
    else:
        cl_label = ''
    frail_no_wt_note = ' [≥75 წ. — შეიყვანეთ წონა ზუსტი CrCl-ისთვის!]' if (crcl is None and egfr is not None and (patient.age or 0) >= 75) else ''

    op_dt = _date(s.get('planned_surgery_date'))
    liver_3x = (s.get('liver_status') == '3x')
    rows, d_str, hold_str, add_row = _make_med_row_helpers(op_dt)

    med_chips = set(_str_list(q.get('med_chips')))
    cad_chips = set(_str_list(q.get('cad_chips')))
    ven_dvt_chips = set(_str_list(q.get('ven_dvt_chips')))
    eff_allergy_chips, _, _ = _effective_allergy_state(q)
    ft_meds = _detect_freetext_high_risk_meds(q.get('other_meds') or '', q=q)

    has_mech_valve = 'mech_valve' in cad_chips
    has_acs_stent_12m = ('stent_6_12m' in cad_chips) and ('mi' in cad_chips)
    bp_sys_cur = _int(s.get('bp_sys'), lo=30, hi=320)
    hr_cur = _int(s.get('hr'), lo=15, hi=260)
    lvef_cur = _int(s.get('lvef'), lo=5, hi=95)
    nt_probnp_cur = _num(s.get('nt_probnp'), lo=1, hi=100000)
    k_cur = _num(s.get('k'), lo=1.0, hi=12.0)
    chf_any = ('hf' in cad_chips) or (lvef_cur is not None and lvef_cur < 40) or (nt_probnp_cur is not None and nt_probnp_cur >= 300)
    has_true_hfref = (lvef_cur is not None and lvef_cur < 40) or (('hf' in cad_chips) and lvef_cur is None)
    oac_count = sum([
        bool(q.get('med_warfarin')),
        bool(q.get('med_doac')),
        bool(q.get('med_dabigatran')),
        ('rivaroxaban_2_5' in med_chips),
    ])
    dup_oac_warn = ' [ყურადღება: ერთდროულად ≥2 ანტიკოაგულანტია მონიშნული — გადაამოწმეთ დუბლირება!]' if oac_count >= 2 else ''
    mech_doac_warn = ' [მექანიკურ სარქველზე აბსოლუტურად უკუნაჩვენებია (ESC Class III) — გადაამოწმეთ ვარფარინი!]' if has_mech_valve else ''
    liver_doac_warn = ' [ღვიძლის ≥3x დისფუნქცია: კოაგულოპათიის/Child-Pugh B–C დროს DOAC უკუნაჩვენებია (EHRA 2021)!]' if liver_3x else ''

    return {
        'patient': patient, 'q': q, 's': s, 'pw': pw,
        'is_evla': is_evla, 'is_phleb': is_phleb, 'is_pta': is_pta,
        'is_symp_cea': is_symp_cea, 'is_open_major': is_open_major,
        'uses_contrast': uses_contrast, 'anesth_plan': anesth_plan, 'is_neuraxial': is_neuraxial,
        'on_dialysis': on_dialysis, 'eff_renal': eff_renal, 'renal_cl': eff_renal,
        'cl_label': cl_label, 'frail_no_wt_note': frail_no_wt_note,
        'liver_3x': liver_3x, 'rows': rows, 'd_str': d_str, 'hold_str': hold_str, 'add_row': add_row,
        'med_chips': med_chips, 'cad_chips': cad_chips, 'ven_dvt_chips': ven_dvt_chips,
        'eff_allergy_chips': eff_allergy_chips, 'ft_meds': ft_meds,
        'has_mech_valve': has_mech_valve, 'has_acs_stent_12m': has_acs_stent_12m,
        'bp_sys_cur': bp_sys_cur, 'hr_cur': hr_cur, 'lvef_cur': lvef_cur, 'k_cur': k_cur,
        'chf_any': chf_any, 'has_true_hfref': has_true_hfref, 'oac_count': oac_count,
        'dup_oac_warn': dup_oac_warn,
        'doac_extra_warn': f'{mech_doac_warn}{dup_oac_warn}{liver_doac_warn}',
        'force_doac_red': bool(has_mech_valve or oac_count >= 2 or liver_3x),
        'postop_oac_res': (
            ' · აღდგენა: ~24 სთ-ში ჰემოსტაზით'
            if (is_evla or is_pta)
            else ' · აღდგენა: თერაპ. დოზა 48–72 სთ-ში ჰემოსტაზით (D+1 პროფილაქტ. LMWH; ეპიდურული კათეტერიდან ≥6–24 სთ!)'
        ),
    }


def _append_antiplatelet_rows(c):
    q, s, pw = c['q'], c['s'], c['pw']
    add_row, d_str, hold_str = c['add_row'], c['d_str'], c['hold_str']
    is_phleb, is_evla, is_pta, is_symp_cea, is_neuraxial = c['is_phleb'], c['is_evla'], c['is_pta'], c['is_symp_cea'], c['is_neuraxial']
    has_acs_stent_12m, eff_allergy_chips, med_chips = c['has_acs_stent_12m'], c['eff_allergy_chips'], c['med_chips']

    if q.get('med_aspirin'):
        if is_phleb and not q.get('cad') and not q.get('neuro'):
            add_row('ასპირინი (75–100 მგ)', 'შეთანხმება ქირურგთან (დაბალი კარდიორისკისას შესაძლოა მოიხსნას 5–7 დღით ადრე)', 'amber', f'შეათანხმეთ ({d_str(-5)}-დან?)', hold_or_skip=True)
        else:
            add_row('ასპირინი (75–100 მგ)', 'გრძელდება უწყვეტად ოპერაციის დილის ჩათვლით (ESVS Class I)', 'ok', f'მიიღეთ {d_str(0)}-ის დილის ჩათვლით', hold_or_skip=False)

    if q.get('med_clopidogrel'):
        clop_intol_warn = ' [კლოპიდოგრელის აუტანლობა/ალერგია მონიშნულია — დაუყოვნებლივ გადაამოწმეთ დანიშნულება!]' if 'clopidogrel_intol' in eff_allergy_chips else ''
        if q.get('recent_stent') or has_acs_stent_12m:
            stent_note = 'სტენტი <6 თვ' if q.get('recent_stent') else 'ინფარქტის შემდგომი სტენტი 6–12 თვ (DAPT 12 თვე)'
            add_row('კლოპიდოგრელი / P2Y12', f'{stent_note} — თვითნებური მოხსნა აკრძალულია! კარდიოლოგ-ქირურგის კონსილიუმი{clop_intol_warn}', 'red', 'არ მოხსნათ კონსილიუმის გარეშე!', hold_or_skip=False, force_date_plan=True)
        elif is_symp_cea:
            add_row('კლოპიდოგრელი / P2Y12', f'გრძელდება უწყვეტად CEA-მდე! (ESVS 2023 Class I/IIa — სიმპტომურ CEA-ზე მოხსნა ზრდის ინსულტის რისკს){clop_intol_warn}', 'red' if clop_intol_warn else 'ok', f'მიიღეთ {d_str(0)}-ის ჩათვლით', hold_or_skip=False)
        elif is_pta:
            add_row('კლოპიდოგრელი / P2Y12', f'გრძელდება უწყვეტად (DAPT რეჟიმი სტენტირებისთვის){clop_intol_warn}', 'red' if clop_intol_warn else 'ok', f'მიიღეთ {d_str(0)}-ის ჩათვლით', hold_or_skip=False)
        elif is_evla and not is_neuraxial:
            add_row('კლოპიდოგრელი / P2Y12', f'ESVS 2022: EVLA შესაძლებელია მოუხსნელადაც — დააზუსტეთ ქირურგთან{clop_intol_warn}', 'red' if clop_intol_warn else 'amber', f'დააზუსტეთ ქირურგთან ({d_str(0)})', hold_or_skip=True)
        elif not q.get('med_aspirin') and (pw != PreopPatient.Pathway.VENOUS or q.get('cad') or q.get('neuro')):
            asra_tag = ' (ASRA სპინალურ/ეპიდურულამდე მინ. 5–7 დღე)' if is_neuraxial else ''
            hold_d = 7 if is_neuraxial else 5
            add_row('კლოპიდოგრელი / P2Y12', f'შეჩერება ოპერაციამდე 5–7 დღით ადრე{asra_tag} — სავალდებულოა ჩანაცვლება ასპირინი 75–100 მგ-ით (უანტიაგრეგანტოდ დატოვება საშიშია!){clop_intol_warn}', 'red' if clop_intol_warn else 'amber', hold_str(hold_d, ' (ასპირინით!)'), hold_or_skip=True)
        else:
            asra_tag = ' (ASRA ნეირაქსიალურ ბლოკადამდე მინ. 5–7 დღე)' if is_neuraxial else ''
            hold_d = 7 if is_neuraxial else 5
            asa_sfx = ' (ასპირინის ფონზე)' if q.get('med_aspirin') else ''
            add_row('კლოპიდოგრელი / P2Y12', f'შეჩერება ოპერაციამდე 5–7 დღით ადრე{asra_tag}{asa_sfx}{clop_intol_warn}', 'red' if clop_intol_warn else 'amber', hold_str(hold_d), hold_or_skip=True)

    if q.get('med_ticagrelor'):
        if q.get('recent_stent') or has_acs_stent_12m:
            add_row('ტიკაგრელორი (ბრილინტა)', 'კორონარული სტენტი DAPT რეჟიმში — თვითნებური მოხსნა აკრძალულია! კარდიოლოგ-ქირურგის კონსილიუმი', 'red', 'არ მოხსნათ კონსილიუმის გარეშე!', hold_or_skip=False, force_date_plan=True)
        elif is_symp_cea:
            add_row('ტიკაგრელორი (ბრილინტა)', 'სიმპტომური CEA — თვითნებურად არ მოხსნათ! შეათანხმეთ ქირურგთან (გაგრძელება ან კლოპიდოგრელ/ასპირინზე გადაყვანა)', 'red', 'არ მოხსნათ შეთანხმების გარეშე!', hold_or_skip=False, force_date_plan=True)
        elif is_pta:
            add_row('ტიკაგრელორი (ბრილინტა)', 'გრძელდება ან შეთანხმდება ქირურგთან (ენდოვასკულური DAPT)', 'ok', f'მიიღეთ {d_str(0)}-ის ჩათვლით', hold_or_skip=False)
        elif is_neuraxial:
            sub_asa = 'ასპირინის ფონზე!' if q.get('med_aspirin') else 'ასპირინი 75–100 მგ-ით ჩანაცვლებით!'
            add_row('ტიკაგრელორი (ბრილინტა)', f'შეჩერება სპინალურ/ეპიდურულ ანესთეზიამდე 5–7 დღით ადრე (ASRA: 5–7 დღე; ESC/FDA: 5 დღე) — {sub_asa}', 'amber', hold_str(5, ' (5–7 დღე)'), hold_or_skip=True)
        else:
            sub_asa = 'ასპირინის ფონზე' if q.get('med_aspirin') else 'ასპირინი 75–100 მგ-ით ჩანაცვლებით!'
            add_row('ტიკაგრელორი (ბრილინტა)', f'შეჩერება ოპერაციამდე 3–5 დღით ადრე (ESC 2022 / FDA; {sub_asa})', 'amber', hold_str(4), hold_or_skip=True)

    if q.get('med_prasugrel'):
        stroke_warn = ' [გადატანილ ინსულტ/TIA-ზე პრასუგრელი უკუნაჩვენებია — გადაამოწმეთ!]' if (q.get('neuro') or pw == PreopPatient.Pathway.CEA) else ''
        if q.get('recent_stent') or has_acs_stent_12m:
            add_row('პრასუგრელი (ეფიენტი)', f'კორონარული სტენტი — თვითნებური მოხსნა აკრძალულია! კარდიოლოგ-ქირურგის კონსილიუმი{stroke_warn}', 'red', 'არ მოხსნათ კონსილიუმის გარეშე!', hold_or_skip=False, force_date_plan=True)
        elif is_pta and not is_neuraxial and not stroke_warn:
            add_row('პრასუგრელი (ეფიენტი)', 'ენდოვასკულური PTA — შეათანხმეთ ქირურგთან (გაგრძელება ან კლოპიდოგრელზე დეესკალაცია)', 'amber', f'შეათანხმეთ ქირურგთან ({d_str(0)})', hold_or_skip=True)
        else:
            hold_d = 10 if is_neuraxial else 7
            sub_asa = 'ასპირინის ფონზე!' if q.get('med_aspirin') else 'ასპირინი 75–100 მგ-ით ჩანაცვლებით!'
            add_row('პრასუგრელი (ეფიენტი)', f'შეჩერება ოპერაციამდე და სპინალურ/ეპიდურულ ანესთეზიამდე 7–10 დღით ადრე (ASRA/ESC) — {sub_asa}{stroke_warn}', 'red', hold_str(hold_d), hold_or_skip=True)

    if 'cilostazol' in med_chips:
        cilostazol_contra = (
            c['chf_any']
            or (c['lvef_cur'] is not None and c['lvef_cur'] < 50 and 'hf' in c['cad_chips'])
            or c['on_dialysis']
            or (c['eff_renal'] is not None and c['eff_renal'] <= 25)
            or (s.get('ecg') == 'ischemia_arrhythmia')
            or (c['hr_cur'] is not None and c['hr_cur'] > 110)
        )
        ppi_cyp_warn = ' [CYP2C19: ომეპრაზოლს მოერიდეთ — უპირატესია პანტოპრაზოლი]' if ('ppi' in med_chips or q.get('ulcer') or s.get('ppi_started')) else ''
        if cilostazol_contra:
            add_row(
                'ცილოსტაზოლი (პლეტალი)',
                f'უკუნაჩვენებია გულის უკმარისობის / CrCl ≤25 / ტაქიარითმიის დროს (FDA Black Box / ESVS Class III) — შეათანხმეთ მოხსნა!{ppi_cyp_warn}',
                'red', 'უკუნაჩვენებია — შეათანხმეთ მოხსნა!', hold_or_skip=False, force_date_plan=True,
            )
        else:
            add_row(
                'ცილოსტაზოლი (პლეტალი)',
                f'შეჩერება ოპერაციამდე / სპინალურ ბლოკადამდე 48 საათით (2 დღით) ადრე (ASRA){ppi_cyp_warn}',
                'amber', hold_str(2, ' (48 სთ)'), hold_or_skip=True,
            )


def _append_doac_dabigatran_rows(c):
    q, add_row, d_str, hold_str = c['q'], c['add_row'], c['d_str'], c['hold_str']
    has_mech_valve, is_evla, is_neuraxial, liver_3x = c['has_mech_valve'], c['is_evla'], c['is_neuraxial'], c['liver_3x']
    on_dialysis, renal_cl, cl_label, frail_no_wt_note = c['on_dialysis'], c['renal_cl'], c['cl_label'], c['frail_no_wt_note']
    dup_oac_warn, doac_extra_warn, force_doac_red, postop_oac_res = c['dup_oac_warn'], c['doac_extra_warn'], c['force_doac_red'], c['postop_oac_res']

    if q.get('med_doac'):
        if has_mech_valve:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'მექანიკურ სარქველზე DOAC აბსოლუტურად უკუნაჩვენებია (ESC Class III — სარქვლის თრომბოზის რისკი)! სასწრაფოდ გადაამოწმეთ ვარფარინი (VKA) კარდიოლოგთან!{dup_oac_warn}', 'red', 'უკუნაჩვენებია — კარდიოლოგი!', hold_or_skip=False, force_date_plan=True)
        elif is_evla and not is_neuraxial and not liver_3x:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'ESVS 2022: EVLA-ზე შესაძლებელია მოუხსნელად ან დილის დოზის გამოტოვებით{doac_extra_warn}{postop_oac_res}', 'red' if force_doac_red else 'amber', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True)
        elif on_dialysis:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'დიალიზი / თირკმლის ტერმინალური უკმარისობა — შეჩერება ≥96–120 სთ + ანტი-Xa კონტროლი და ნეფროლოგ/ქირურგის კონსილიუმი!{doac_extra_warn}', 'red', hold_str(5, ' (კონსილიუმი!)'), hold_or_skip=True)
        elif renal_cl is None:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'შეიყვანეთ კრეატინინი (CrCl / eGFR) — DOAC-ის შეჩერების დრო (48–96+ სთ) დამოკიდებულია თირკმლის ფუნქციაზე!{doac_extra_warn}', 'red', 'შეიყვანეთ კრეატინინი თარიღისთვის', hold_or_skip=True)
        elif renal_cl < 30:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'{cl_label} (<30) — შეჩერება ≥96 სთ-ით ადრე + ნეფროლოგ/ქირურგთან შეთანხმება (ნეირაქსიალური ბლოკადა მოერიდეთ!){frail_no_wt_note}{doac_extra_warn}{postop_oac_res}', 'red', hold_str(4, ' (≥96 სთ)'), hold_or_skip=True)
        elif is_neuraxial:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'სპინალური/ეპიდურული ანესთეზია (ASRA 5th Ed): სავალდებულო შეჩერება მინიმუმ 72 საათით (3 დღით) ადრე! ({cl_label}){frail_no_wt_note}{doac_extra_warn}{postop_oac_res}', 'red' if force_doac_red else 'amber', hold_str(3, ' (72 სთ)'), hold_or_skip=True)
        elif renal_cl < 50:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'{cl_label} (30–49) — შეჩერება ოპერაციამდე 72 საათით (3 დღით) ადრე (EHRA 2021 / PAUSE){frail_no_wt_note}{doac_extra_warn}{postop_oac_res}', 'red' if force_doac_red else 'amber', hold_str(3, ' (72 სთ)'), hold_or_skip=True)
        else:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'შეჩერება ოპერაციამდე 48 საათით (2 დღით) ადრე (EHRA 2021 / PAUSE; {cl_label}){frail_no_wt_note}{doac_extra_warn}{postop_oac_res}', 'red' if force_doac_red else 'amber', hold_str(2, ' (48 სთ)'), hold_or_skip=True)

    if q.get('med_dabigatran'):
        if has_mech_valve:
            add_row('დაბიგატრანი (პრადაქსა)', f'მექანიკურ სარქველზე დაბიგატრანი აბსოლუტურად უკუნაჩვენებია (RE-ALIGN / ESC Class III — თრომბოზის რისკი)! სასწრაფოდ გადაამოწმეთ ვარფარინი (VKA)!{dup_oac_warn}', 'red', 'უკუნაჩვენებია — კარდიოლოგი!', hold_or_skip=False, force_date_plan=True)
        elif is_evla and not is_neuraxial and not liver_3x:
            add_row('დაბიგატრანი (პრადაქსა)', f'ESVS 2022: EVLA-ზე შესაძლებელია მოუხსნელად ან დილის დოზის გამოტოვებით — დააზუსტეთ ქირურგთან{doac_extra_warn}{postop_oac_res}', 'red' if force_doac_red else 'amber', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True)
        elif on_dialysis or renal_cl is None:
            msg = (
                f'დიალიზი — დაბიგატრანი უკუნაჩვენებია, სავალდებულოა თრომბინის დროის (TT) კონტროლი და კონსილიუმი!{doac_extra_warn}'
                if on_dialysis else
                f'შეიყვანეთ კრეატინინი და წონა (CrCl) — პრადაქსას შეჩერების დრო (48–120 სთ) მკაცრად დამოკიდებულია თირკმლის ფუნქციაზე!{doac_extra_warn}'
            )
            add_row('დაბიგატრანი (პრადაქსა)', msg, 'red', 'შეიყვანეთ CrCl თარიღისთვის', hold_or_skip=True)
        elif is_neuraxial:
            if renal_cl < 30:
                add_row('დაბიგატრანი (პრადაქსა)', f'{cl_label} (<30) — სპინალური/ეპიდურული ბლოკადა უკუნაჩვენებია (ASRA 5th Ed)! შეჩერება ≥120 სთ{frail_no_wt_note}{doac_extra_warn}{postop_oac_res}', 'red', hold_str(5, ' (სპინალური არა!)'), hold_or_skip=True)
            elif renal_cl < 50:
                add_row('დაბიგატრანი (პრადაქსა)', f'სპინალური/ეპიდურული (ASRA, {cl_label} 30–49): შეჩერება 120 საათით (5 დღით) ადრე!{frail_no_wt_note}{doac_extra_warn}{postop_oac_res}', 'red', hold_str(5, ' (120 სთ)'), hold_or_skip=True)
            elif renal_cl < 80:
                add_row('დაბიგატრანი (პრადაქსა)', f'სპინალური/ეპიდურული (ASRA, {cl_label} 50–79): შეჩერება 96 საათით (4 დღით) ადრე!{frail_no_wt_note}{doac_extra_warn}{postop_oac_res}', 'red' if force_doac_red else 'amber', hold_str(4, ' (96 სთ)'), hold_or_skip=True)
            else:
                add_row('დაბიგატრანი (პრადაქსა)', f'სპინალური/ეპიდურული (ASRA, {cl_label} ≥80): შეჩერება 72 საათით (3 დღით) ადრე!{frail_no_wt_note}{doac_extra_warn}{postop_oac_res}', 'red' if force_doac_red else 'amber', hold_str(3, ' (72 სთ)'), hold_or_skip=True)
        else:
            if renal_cl < 30:
                add_row('დაბიგატრანი (პრადაქსა)', f'{cl_label} (<30) — შეჩერება ≥120 საათით (5 დღით) ადრე + კოაგულოგრამა/TT კონტროლი!{frail_no_wt_note}{doac_extra_warn}{postop_oac_res}', 'red', hold_str(5, ' (120 სთ)'), hold_or_skip=True)
            elif renal_cl < 50:
                add_row('დაბიგატრანი (პრადაქსა)', f'{cl_label} (30–49) — შეჩერება ოპერაციამდე 96 საათით (4 დღით) ადრე{frail_no_wt_note}{doac_extra_warn}{postop_oac_res}', 'red', hold_str(4, ' (96 სთ)'), hold_or_skip=True)
            elif renal_cl < 80:
                add_row('დაბიგატრანი (პრადაქსა)', f'{cl_label} (50–79) — შეჩერება ოპერაციამდე 72 საათით (3 დღით) ადრე{frail_no_wt_note}{doac_extra_warn}{postop_oac_res}', 'red' if force_doac_red else 'amber', hold_str(3, ' (72 სთ)'), hold_or_skip=True)
            else:
                add_row('დაბიგატრანი (პრადაქსა)', f'{cl_label} (≥80) — შეჩერება ოპერაციამდე 48 საათით (2 დღით) ადრე{frail_no_wt_note}{doac_extra_warn}{postop_oac_res}', 'red' if force_doac_red else 'amber', hold_str(2, ' (48 სთ)'), hold_or_skip=True)


def _append_rivaroxaban_warfarin_rows(c):
    q, add_row, d_str, hold_str = c['q'], c['add_row'], c['d_str'], c['hold_str']
    has_mech_valve, is_evla, is_neuraxial = c['has_mech_valve'], c['is_evla'], c['is_neuraxial']
    on_dialysis, renal_cl, eff_renal = c['on_dialysis'], c['renal_cl'], c['eff_renal']
    dup_oac_warn, doac_extra_warn, force_doac_red = c['dup_oac_warn'], c['doac_extra_warn'], c['force_doac_red']

    if 'rivaroxaban_2_5' in c['med_chips']:
        if has_mech_valve:
            add_row('ქსარელტო 2.5 მგ x2', f'მექანიკურ სარქველზე რივაროქსაბანი აბსოლუტურად უკუნაჩვენებია (ESC Class III)! გადაამოწმეთ ვარფარინი!{dup_oac_warn}', 'red', 'უკუნაჩვენებია — კარდიოლოგი!', hold_or_skip=False, force_date_plan=True)
        elif on_dialysis or (renal_cl is not None and renal_cl < 30):
            add_row('ქსარელტო 2.5 მგ x2', f'თირკმლის მძიმე უკმარისობა / დიალიზი — შეჩერება ≥72–96 სთ-ით ადრე და სქემის გადახედვა ქირურგთან!{doac_extra_warn}', 'red', hold_str(4), hold_or_skip=True)
        elif is_evla and not is_neuraxial and not force_doac_red:
            add_row('ქსარელტო 2.5 მგ x2', 'EVLA-ზე შესაძლებელია დილის დოზის გამოტოვებით (ასპირინი გრძელდება)', 'ok', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True)
        elif is_neuraxial:
            add_row('ქსარელტო 2.5 მგ x2', f'შეჩერება სპინალურ/ეპიდურულ ბლოკადამდე 48–72 საათით ადრე (ASRA) — ასპირინი გრძელდება{doac_extra_warn}', 'red' if force_doac_red else 'amber', hold_str(3), hold_or_skip=True)
        else:
            add_row('ქსარელტო 2.5 მგ x2', f'შეჩერება ოპერაციამდე 24–48 საათით ადრე (ასპირინი 75–100 მგ გრძელდება უწყვეტად){doac_extra_warn}', 'red' if force_doac_red else 'amber', hold_str(2), hold_or_skip=True)

    if q.get('med_warfarin'):
        high_thromb_vka = (
            has_mech_valve
            or (q.get('ven_dvt_history') and q.get('ven_thrombophilia'))
            or ('vte_recent_3m' in c['ven_dvt_chips'])
        )
        if is_evla and not is_neuraxial:
            add_row('ვარფარინი', f'ESVS 2022: EVLA შესაძლებელია მოუხსნელად, თუ INR ≤ 3.0{dup_oac_warn}', 'red' if c['oac_count'] >= 2 else 'amber', f'INR ≤3.0 შემოწმება {d_str(-1)}', hold_or_skip=False)
        else:
            inr_target = 'INR ≤ 1.4 (ASRA 5th Ed: ნეირაქსიულამდე სასურველია ≤1.2 ნორმალიზება!)' if is_neuraxial else 'INR < 1.5 (ქირურგიული ჰემოსტაზისთვის)'
            if has_mech_valve and 'hit_heparin' in c['eff_allergy_chips']:
                bridge_txt = 'მექანიკური სარქველი + HIT — უხიდოდ დატოვება და ჰეპარინი/LMWH აკრძალულია! სავალდებულოა სტაციონარული IV ბივალირუდინი ან არგატრობანი (aPTT კონტროლით)!'
            elif 'hit_heparin' in c['eff_allergy_chips']:
                bridge_txt = 'HIT ანამნეზში — LMWH/ჰეპარინის ხიდი კატეგორიულად აკრძალულია!'
            elif high_thromb_vka:
                if on_dialysis or (eff_renal is not None and eff_renal < 30):
                    bridge_txt = (
                        'მექანიკური სარქველი / მაღალი თრომბოემბოლიური რისკი + CrCl/eGFR <30 (ან დიალიზი) — '
                        'თერაპიული LMWH აკუმულირდება! სავალდებულოა სტაციონარული IV UFH ხიდი (aPTT კონტროლით, შეჩერება ოპერაციამდე 4–6 სთ) '
                        'ან anti-Xa კონტროლირებადი LMWH ნეფროლოგ/კარდიოლოგთან!'
                    )
                else:
                    d3_lbl = d_str(-3) or 'D-3'
                    d1_lbl = d_str(-1) or 'D-1'
                    bridge_txt = (
                        f'მექანიკური სარქველი / მაღალი თრომბოემბოლიური რისკი — LMWH ხიდი (CHEST 2022 / PAUSE): '
                        f'იწყება INR <2.0-ზე (~{d3_lbl}), წინა დილას ({d1_lbl}) კეთდება დღიური დოზის 50% (ნახევარი), '
                        'წინა საღამოს დოზა გამოტოვეთ (ბოლო დოზა ≥24 სთ ოპერაციამდე)!'
                    )
            else:
                bridge_txt = 'რუტინული LMWH ხიდი AFib-ზე არ ტარდება (ESC 2022 Class III); თუ დაინიშნა პროფილაქტიკური LMWH, ჩერდება -12 სთ, თერაპიული -24 სთ'
            add_row(
                'ვარფარინი',
                f'შეჩერება 5 დღით ადრე + წინა დღეს საკონტროლო {inr_target}. {bridge_txt} · აღდგენა: 12–24 სთ-ში (საღამოს/D+1){dup_oac_warn}',
                'red', f'{hold_str(5)} · INR კონტროლი {d_str(-1)}', hold_or_skip=True,
            )


def _append_metabolic_rows(c):
    q, add_row, d_str, hold_str = c['q'], c['add_row'], c['d_str'], c['hold_str']
    on_dialysis, eff_renal, liver_3x = c['on_dialysis'], c['eff_renal'], c['liver_3x']

    if q.get('dm_insulin'):
        add_row(
            'ინსულინი',
            'ოპერაციის წინა საღამოს/დილას ხანგრძლივი ბაზალური ანალოგი (ლანტუსი/ტუჯეო/ტრესიბა) მცირდება 75–80%-მდე (NPH / შერეული ინსულინი — 50%-მდე!); სწრაფი (საკვებისწინა) ინსულინი უზმოზე გამოტოვეთ + დილის გლუკოზის კონტროლი!',
            'amber', f'{d_str(0)} დილას: სწრაფი არა · ბაზალური 75–80% (NPH 50%)', hold_or_skip=True,
        )

    if q.get('sglt2'):
        hold_sglt2 = 4 if c['ft_meds']['ertugliflozin'] else 3
        hrs_sglt2 = '96 სთ' if c['ft_meds']['ertugliflozin'] else '72 სთ'
        add_row(
            'SGLT2 (ჯარდიანსი / ფორსიგა)',
            'სავალდებულო შეჩერება ოპერაციამდე 72 საათით (3 დღით) ადრე (ერტუგლიფლოზინი/სტეგლატრო — 96 სთ / 4 დღით ადრე) + ოპერაციის დილას სისხლის კეტონების კონტროლი (ეუგლიკემიური DKA-ს პრევენცია)!',
            'amber', hold_str(hold_sglt2, f' ({hrs_sglt2})'), hold_or_skip=True,
        )

    if q.get('glp1'):
        add_row(
            'GLP-1 (ოზემპიკი / მუნჯარო / საქსენდა)',
            'Multi-Society 2024 (ASA/AGA/ESAIC): გულისრევა/ღებინებისა და დოზის მატების ფაზის გარეშე უმეტეს პაციენტთან გრძელდება + სავალდებულოა ოპერაციამდე 24 სთ სუფთა თხევადი დიეტა! შეჩერება (კვირეული 7 დღით / დღიური ოპერაციის დღეს) — მხოლოდ ექიმის/ანესთეზიოლოგის გადაწყვეტილებით მაღალი GI რისკისას.',
            'amber', f'{d_str(-1)} თხევადი დიეტა (24 სთ) · შეჩერება ({d_str(-7)}-დან) მხოლოდ ექიმით', hold_or_skip=True,
        )

    if q.get('metformin'):
        if on_dialysis or (eff_renal is not None and eff_renal < 30):
            add_row(
                'მეტფორმინი',
                f'{c["cl_label"] or "CrCl/eGFR"} < 30 / დიალიზი — მეტფორმინი უკუნაჩვენებია (ლაქტატ-აციდოზის რისკი)! შეწყვიტეთ დაუყოვნებლივ და შეათანხმეთ ენდოკრინოლოგთან!',
                'red', 'შეწყვიტეთ დაუყოვნებლივ!', hold_or_skip=False, force_date_plan=True,
            )
        elif liver_3x:
            add_row(
                'მეტფორმინი',
                'ღვიძლის მძიმე დისფუნქცია (≥3x ULN) — მეტფორმინი უკუნაჩვენებია ლაქტატ-აციდოზის გამო! შეწყვიტეთ ექიმთან შეთანხმებით!',
                'red', 'შეწყვიტეთ დაუყოვნებლივ!', hold_or_skip=False, force_date_plan=True,
            )
        elif (c['uses_contrast'] and (eff_renal is None or eff_renal < 60)) or c['is_open_major'] or (eff_renal is not None and eff_renal < 45):
            dose_cap = ' [CrCl/eGFR 30–44: მაქს. დღიური დოზა ≤1000 მგ/დღ!]' if (eff_renal is not None and eff_renal < 45) else ''
            add_row(
                'მეტფორმინი',
                f'შეჩერება ჩარევის/კონტრასტის დღეს (48 სთ-ით, თირკმლის საკონტროლო კრეატინინამდე — ESUR 2018){dose_cap}',
                'amber', f'არ მიიღოთ {d_str(0)}–{d_str(1)} (48 სთ)', hold_or_skip=True,
            )
        else:
            add_row('მეტფორმინი', 'გამოტოვება ოპერაციის დილას (უზმოზე; ESUR 2018-ით 48 სთ შეჩერება არ ესაჭიროება)', 'ok', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True)


def _append_cardio_other_rows(c):
    patient, q, s, pw = c['patient'], c['q'], c['s'], c['pw']
    add_row, d_str, med_chips = c['add_row'], c['d_str'], c['med_chips']
    bp_sys_cur, hr_cur, k_cur, eff_renal = c['bp_sys_cur'], c['hr_cur'], c['k_cur'], c['eff_renal']

    if 'statin' in med_chips:
        if c['liver_3x']:
            add_row('სტატინი', 'ღვიძლის ფერმენტები ≥3x ULN — გადაამოწმეთ მწვავე ტრანსამინიტი ექიმთან სტატინის გაგრძელებამდე!', 'amber', f'შეათანხმეთ ექიმთან ({d_str(0)})', hold_or_skip=False)
        else:
            add_row('სტატინი', 'გრძელდება უწყვეტად ოპერაციის დილის ჩათვლით (ESVS/ESC Class I)', 'ok', f'მიიღეთ {d_str(0)}-ის დილის ჩათვლით', hold_or_skip=False)
    if 'beta_blocker' in med_chips:
        bb_hold_vitals = (bp_sys_cur is not None and bp_sys_cur < 100) or (hr_cur is not None and hr_cur < 55)
        bb_plan = f'გამოტოვეთ დილას! (SBP<100 ან HR<55)' if bb_hold_vitals else f'მიიღეთ {d_str(0)}-ის დილას (თუ SBP≥100, HR≥55)'
        add_row(
            'ბეტა-ბლოკატორი',
            'გრძელდება უწყვეტად ოპერაციის დილას ყლუპი წყლით, თუ SBP ≥ 100 mmHg და HR ≥ 55/წთ (დაბალ წნევა/პულსზე დილის დოზა გამოტოვეთ! — ESC Class I / POISE-3)',
            'amber' if bb_hold_vitals else 'ok', bb_plan, hold_or_skip=False, force_date_plan=bb_hold_vitals,
        )
    if 'acei_arb' in med_chips:
        ace_hold_vitals = (bp_sys_cur is not None and bp_sys_cur < 100) or (k_cur is not None and k_cur >= 5.2)
        if ace_hold_vitals:
            add_row('ACEi / ARB (პრილი/სარტანი)', 'საბაზისო SBP < 100 mmHg ან K+ ≥ 5.2 მმოლ/ლ — ოპერაციის დილის დოზა გამოტოვეთ (ჰიპოტენზიის/ჰიპერკალიემიის რისკი)!', 'amber', f'გამოტოვეთ დილას! (SBP<100 / K+≥5.2)', hold_or_skip=True, force_date_plan=True)
        elif c['has_true_hfref']:
            add_row('ACEi / ARB (პრილი/სარტანი)', 'ჭეშმარიტი HFrEF (LVEF <40%) — გრძელდება ანესთეზიოლოგთან შეთანხმებით (თუ SBP ≥ 100 mmHg და K+ < 5.2 — ESC 2022)', 'amber', f'შეათანხმეთ HFrEF-ზე ({d_str(0)})', hold_or_skip=False)
        elif c['is_open_major'] or c['is_neuraxial'] or pw == PreopPatient.Pathway.CEA or c['anesth_plan'] == 'general' or patient.op_subtype in ('evar', 'tbd'):
            add_row('ACEi / ARB (პრილი/სარტანი)', 'გამოტოვება ოპერაციის დილას (ინტრაოპერაციული ჰიპოტენზიის პრევენცია — POISE-3; HFpEF-ზეც დილას ჩერდება)', 'amber', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True)
        else:
            add_row('ACEi / ARB (პრილი/სარტანი)', 'გრძელდება ჩვეულ რეჟიმში (თუ წნევა სტაბილურია ≥100 mmHg)', 'ok', f'მიიღეთ ჩვეულ რეჟიმში ({d_str(0)})', hold_or_skip=False)
    if 'ccb' in med_chips:
        ccb_hold = bp_sys_cur is not None and bp_sys_cur < 110
        add_row(
            'ამლოდიპინი / CCB',
            'გამოტოვეთ დილის დოზა დაბალი წნევის (SBP < 110 mmHg) გამო!' if ccb_hold else 'გრძელდება ჩვეულ რეჟიმში ოპერაციის დილას, თუ SBP ≥ 110 mmHg',
            'amber' if ccb_hold else 'ok',
            'გამოტოვეთ დილას! (SBP<110)' if ccb_hold else f'მიიღეთ {d_str(0)}-ის დილას (SBP≥110)',
            hold_or_skip=ccb_hold, force_date_plan=ccb_hold,
        )
    if 'amiodarone' in med_chips:
        amio_pgp_warn = ' [P-gp ურთიერთქმედება: DOAC/დაბიგატრანთან და CrCl <60-ზე ზრდის სისხლდენის რისკს!]' if ((q.get('med_doac') or q.get('med_dabigatran')) and eff_renal is not None and eff_renal < 60) else ''
        add_row('ამიოდარონი', f'გრძელდება უწყვეტად ანტიარითმიული სქემით ოპერაციის დილის ჩათვლით{amio_pgp_warn}', 'ok', f'მიიღეთ {d_str(0)}-ის დილას', hold_or_skip=False)
    if 'diuretic' in med_chips:
        add_row('დიურეტიკი', 'გამოტოვება ოპერაციის დილას', 'amber', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True)
    if q.get('ven_hormones'):
        horm_rule = (
            'EVLA-ზე შესაძლებელია გაგრძელება VTE რისკის შეფასებით (Caprini); ფლებექტომიაზე/მაღალ რისკზე შეათანხმეთ თრომბოპროფილაქტიკა (ESVS 2022)'
            if c['is_evla'] else
            'ესტროგენული/ჰორმონული პრეპარატი ზრდის VTE რისკს — შეათანხმეთ ქირურგთან (4 კვ-ით ადრე მოხსნა ან LMWH პროფილაქტიკა)'
        )
        add_row('ჰორმონული პრეპარატი / კონტრაცეპტივი', horm_rule, 'amber', f'VTE შეფასება ({d_str(0)})', hold_or_skip=True)
    p2y12_med_cnt = sum([bool(q.get('med_clopidogrel')), bool(q.get('med_ticagrelor')), bool(q.get('med_prasugrel'))])
    has_dapt_med = (bool(q.get('med_aspirin')) and p2y12_med_cnt >= 1) or (p2y12_med_cnt >= 2)
    has_oac_med = bool(q.get('med_warfarin') or q.get('med_doac') or q.get('med_dabigatran'))
    has_ap_any_med = bool(q.get('med_aspirin') or p2y12_med_cnt >= 1 or 'cilostazol' in med_chips)
    has_high_gi_ppi_ind = (
        bool(q.get('ulcer'))
        or has_dapt_med
        or (has_oac_med and has_ap_any_med)
        or (((patient.age or 0) >= 65) and 'steroid' in med_chips and (has_oac_med or has_ap_any_med or 'rivaroxaban_2_5' in med_chips))
    )
    if 'ppi' in med_chips or has_high_gi_ppi_ind or s.get('ppi_started'):
        cyp_ppi_sensitive = bool(q.get('med_clopidogrel') or 'cilostazol' in med_chips)
        ppi_drug_lbl = 'პანტოპრაზოლი 40 მგ (PPI)' if cyp_ppi_sensitive else 'ომეპრაზოლი / პანტოპრაზოლი (PPI)'
        ppi_cyp_note = ' — კლოპიდოგრელ/ცილოსტაზოლზე უპირატესია პანტოპრაზოლი (CYP2C19)!' if cyp_ppi_sensitive else ''
        add_row(ppi_drug_lbl, f'გრძელდება / ინიშნება უწყვეტად პერიოპერაციულად კუჭის დაცვისთვის (ESC Class I){ppi_cyp_note}', 'ok', f'მიიღეთ {d_str(0)}-ის დილას', hold_or_skip=False)
    if 'steroid' in med_chips:
        add_row(
            'სტეროიდი / იმუნოსუპრესანტი',
            'არ მოხსნათ! სტრეს-დოზა (Assoc. of Anaesthetists 2020): არტერიულ/დიდ ოპერაციაზე — ჰიდროკორტიზონი 100 მგ IV ინდუქციაზე + 200 მგ/24 სთ ინფუზია (ან 50 მგ IV ყოველ 6 სთ-ში; მცირე ვენურზე — ჩვეული დოზა + 25–50 მგ IV); მოერიდეთ ეტომიდატს!',
            'amber', f'არ შეწყვიტოთ ({d_str(0)} IV სტრეს-დოზა)', hold_or_skip=False,
        )


def build_med_schedule(patient, q, s, egfr, crcl=None, stop_blocked=False, has_neuraxial_override=None):
    """Deterministic perioperative medication hold/continue table (ESVS 2023/2024 + ESC 2022 + ASRA 5th Ed)."""
    c = _med_schedule_ctx(patient, q, s, egfr, crcl, has_neuraxial_override)
    _append_antiplatelet_rows(c)
    _append_doac_dabigatran_rows(c)
    _append_rivaroxaban_warfarin_rows(c)
    _append_metabolic_rows(c)
    _append_cardio_other_rows(c)
    if stop_blocked:
        _apply_stop_lock_to_med_schedule(c['rows'])
    return c['rows']
