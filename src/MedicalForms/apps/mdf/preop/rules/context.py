"""Evaluation context object and initial baseline/hemodynamic setup for preop engine."""
from types import SimpleNamespace

from django.utils import timezone

from ...models import PreopPatient
from ..calculators import (
    _parse_scr_mgdl,
    calc_bmi,
    calc_caprini,
    calc_crcl,
    calc_egfr,
    normalize_albumin_gl,
    normalize_glucose_mmol,
    normalize_hb_gl,
    normalize_ldl_mmol,
)
from ..constants import CASE_OUTCOME_CHOICES
from ..meds import _detect_freetext_high_risk_meds, _effective_allergy_state
from ..utils import (
    _int,
    _num,
    _resolve_pathway,
    cta_live_months_age,
    merged_q,
    merged_studies,
    prep_days_elapsed,
    target_prep_days,
)


class EvalContext(SimpleNamespace):
    """Mutable per-patient evaluation state passed sequentially through ordered rule modules."""


def _init_limb_hemodynamics(ctx):
    patient, pathway, s, q = ctx.patient, ctx.pathway, ctx.s, ctx.q
    abi_r = _num(s.get('abi_r'), lo=0.0, hi=3.0)
    abi_l = _num(s.get('abi_l'), lo=0.0, hi=3.0)
    tp_r = _int(s.get('tp_r'), lo=0, hi=400)
    tp_l = _int(s.get('tp_l'), lo=0, hi=400)
    tbi_r = _num(s.get('tbi_r'), lo=0.0, hi=3.0)
    tbi_l = _num(s.get('tbi_l'), lo=0.0, hi=3.0)

    # Side-aware limb hemodynamics: unilateral limbs never let contralateral TP/ABI mask ipsilateral ischemia/calcinosis
    unilateral_limb_pw = pathway in (PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA, PreopPatient.Pathway.VENOUS)
    if unilateral_limb_pw and patient.side == 'R':
        scope_legs = ('R',)
        contra_abi, contra_tp = abi_l, tp_l
    elif unilateral_limb_pw and patient.side == 'L':
        scope_legs = ('L',)
        contra_abi, contra_tp = abi_r, tp_r
    else:
        scope_legs = ('R', 'L')
        contra_abi, contra_tp = None, None

    leg_vals = {
        'R': {'abi': abi_r, 'tp': tp_r, 'tbi': tbi_r},
        'L': {'abi': abi_l, 'tp': tp_l, 'tbi': tbi_l},
    }
    scoped_abis = [leg_vals[lg]['abi'] for lg in scope_legs if leg_vals[lg]['abi'] is not None]
    scoped_tps = [leg_vals[lg]['tp'] for lg in scope_legs if leg_vals[lg]['tp'] is not None]
    scoped_tbis = [leg_vals[lg]['tbi'] for lg in scope_legs if leg_vals[lg]['tbi'] is not None]
    all_abis = [v for v in (abi_r, abi_l) if v is not None]
    all_tps = [v for v in (tp_r, tp_l) if v is not None]
    all_tbis = [v for v in (tbi_r, tbi_l) if v is not None]

    eff_abis = scoped_abis if scoped_abis else all_abis
    eff_tps = scoped_tps if scoped_tps else all_tps
    eff_tbis = scoped_tbis if scoped_tbis else all_tbis

    min_abi = min(eff_abis, default=None)
    max_abi = max(eff_abis, default=None)
    min_comp_abi = min([v for v in eff_abis if v <= 1.40], default=None)
    min_tp = min(eff_tps, default=None)
    min_tbi = min(eff_tbis, default=None)

    nc_legs = [lg for lg in scope_legs if leg_vals[lg]['abi'] is not None and leg_vals[lg]['abi'] > 1.40]
    if not nc_legs and not scoped_abis:
        nc_legs = [lg for lg in ('R', 'L') if leg_vals[lg]['abi'] is not None and leg_vals[lg]['abi'] > 1.40]
    abi_non_compressible = bool(nc_legs)
    if nc_legs:
        tbi_measured = all((leg_vals[lg]['tp'] is not None or leg_vals[lg]['tbi'] is not None) for lg in nc_legs)
        tbi_comp_safe = all(
            ((leg_vals[lg]['tp'] is not None and leg_vals[lg]['tp'] >= 60)
             or (leg_vals[lg]['tbi'] is not None and leg_vals[lg]['tbi'] >= 0.70))
            for lg in nc_legs
        )
    else:
        tbi_measured = bool(scoped_tps or scoped_tbis) if unilateral_limb_pw and patient.side in ('R', 'L') else bool(all_tps or all_tbis)
        tbi_comp_safe = True

    compression_contra = bool(
        (min_comp_abi is not None and min_comp_abi < 0.80)
        or (abi_non_compressible and ((min_tp is not None and min_tp < 50) or (min_tbi is not None and min_tbi < 0.50)))
    )
    tbi_reduced_comp = bool(abi_non_compressible and tbi_measured and not compression_contra and not tbi_comp_safe)
    caprini_score, caprini_tier, caprini_advice = calc_caprini(
        patient, q, ctx.bmi, today=ctx.today, eff_renal=ctx.eff_renal,
        liver_status=s.get('liver_status') or 'normal',
        compression_contra=compression_contra,
    )

    ctx.abi_r, ctx.abi_l = abi_r, abi_l
    ctx.tp_r, ctx.tp_l = tp_r, tp_l
    ctx.tbi_r, ctx.tbi_l = tbi_r, tbi_l
    ctx.contra_abi, ctx.contra_tp = contra_abi, contra_tp
    ctx.scoped_abis, ctx.scoped_tps, ctx.scoped_tbis = scoped_abis, scoped_tps, scoped_tbis
    ctx.min_abi, ctx.max_abi, ctx.min_comp_abi = min_abi, max_abi, min_comp_abi
    ctx.min_tp, ctx.min_tbi = min_tp, min_tbi
    ctx.nc_legs, ctx.abi_non_compressible = nc_legs, abi_non_compressible
    ctx.tbi_measured, ctx.tbi_comp_safe = tbi_measured, tbi_comp_safe
    ctx.compression_contra, ctx.tbi_reduced_comp = compression_contra, tbi_reduced_comp
    ctx.caprini_score, ctx.caprini_tier, ctx.caprini_advice = caprini_score, caprini_tier, caprini_advice


def _init_base_flags_and_pre_reflex(ctx):
    q, s, q_filled = ctx.q, ctx.s, ctx.q_filled
    ctx.pad_gsv_mm = _num(s.get('pad_gsv_mm'), lo=0.5, hi=30)
    ctx.ldl_val = normalize_ldl_mmol(s.get('ldl'))
    ctx.weight_num = _num(s.get('weight_kg'), lo=25, hi=260)

    ctx.ipsi_car = None
    ctx.contra_car = None
    ctx.is_symp_cea = False
    ctx.is_clti_any = False
    ctx.days_since_ev = None
    ctx.needs_brain_ct = False
    ctx.needs_neuro = False
    ctx.needs_laryngoscopy = False
    ctx.needs_cea_antiplatelet = False
    ctx.needs_wound_care = False
    ctx.needs_abi_check = False
    ctx.needs_tbi_check = False
    ctx.needs_vte_prophylaxis = False
    ctx.needs_ecg_venous = False
    ctx.needs_alt_vein = False
    ctx.cea_high_surg_risk = False
    ctx.lidocaine_max_mg = None
    ctx.key_badge = ''
    ctx.key_badge_urgent = False

    ctx.med_chips = set(q.get('med_chips') or [])
    ctx.cad_chips = set(q.get('cad_chips') or [])
    ctx.allergy_chips, ctx.eff_allergy_iodine, ctx.ft_allergy_matched = _effective_allergy_state(q)
    ctx.ft_meds = _detect_freetext_high_risk_meds(q.get('other_meds') or '', q=q)
    ctx.unclarified_allergy_other = bool(q_filled and q.get('allergy_other') and not ctx.allergy_chips and not ctx.eff_allergy_iodine)
    ctx.vasc_chips = set(q.get('vasc_chips') or [])
    ctx.anesth_lines_set = set(s.get('anesth_lines_chips') or [])
    ctx.has_statin = ('statin' in ctx.med_chips) or any(
        w in (q.get('other_meds') or '').lower() for w in ('სტატინ', 'ატორვ', 'როზუვ', 'კრესტორ', 'ლიპრიმ')
    )
    ctx.has_antiplatelet = bool(
        q.get('med_aspirin') or q.get('med_clopidogrel') or q.get('med_ticagrelor') or q.get('med_prasugrel')
    )
    ctx.has_any_antithrombotic = bool(
        ctx.has_antiplatelet or q.get('med_doac') or q.get('med_dabigatran')
        or q.get('med_warfarin') or ('rivaroxaban_2_5' in ctx.med_chips) or ('cilostazol' in ctx.med_chips)
    )

    if s.get('case_wifi_unsalvageable'):
        ctx.red_flags.append(
            'კონფერენციის სტადირებით WIfI Stage 5 (კიდური გადარჩენადი არ არის) — '
            'რევასკულარიზაციის ჩვენება გადაამოწმეთ ქირურგთან!'
        )

    if ctx.abi_non_compressible:
        ctx.needs_tbi_check = True
        nc_side_lbl = f" ({'/'.join(ctx.nc_legs)})" if ctx.nc_legs else ''
        ctx.reflex_orders.append({
            'key': 'tbi',
            'trigger': f'არაკომპრესირებადი არტერია{nc_side_lbl} (ABI {ctx.max_abi} > 1.40 — მონკებერგის კალციფიკაცია)',
            'action': 'სამიზნე კიდურზე თითის წნევის (Toe Pressure, mmHg) ან თით-მხრის ინდექსის (TBI) განსაზღვრა რეალური პერფუზიის შესაფასებლად',
            'done': ctx.tbi_measured,
        })
        if not ctx.is_venous and ((ctx.min_tp is not None and ctx.min_tp < 30) or (ctx.min_tbi is not None and ctx.min_tbi < 0.30)):
            ctx.amber_flags.append(
                f'კრიტიკულად დაბალი Toe Pressure / TBI (TP {ctx.min_tp if ctx.min_tp is not None else "—"} mmHg / '
                f'TBI {ctx.min_tbi if ctx.min_tbi is not None else "—"}) — ადასტურებს მძიმე იშემიას (CLTI).'
            )


def init_eval_context(patient, today=None):
    today = today or timezone.localdate()
    pathway = _resolve_pathway(patient)
    q = merged_q(patient)
    s = merged_studies(patient)
    case_outcome = s.get('case_outcome_status') if s.get('case_outcome_status') in dict(CASE_OUTCOME_CHOICES) else 'scheduled'
    on_dialysis = bool(q.get('on_dialysis'))
    creat_unit = s.get('creat_unit') or ''
    _, creat_unit_ambiguous = _parse_scr_mgdl(s.get('creat'), creat_unit)
    egfr = calc_egfr(s.get('creat'), patient.age, patient.sex or '', creat_unit, on_dialysis=on_dialysis)
    crcl = calc_crcl(
        s.get('creat'), patient.age, patient.sex or '', s.get('weight_kg'),
        creat_unit, on_dialysis=on_dialysis, height_cm=s.get('height_cm'),
    )
    car_r, car_l = _int(s.get('carotid_r'), lo=0, hi=100), _int(s.get('carotid_l'), lo=0, hi=100)

    ctx = EvalContext(
        patient=patient,
        today=today,
        pathway=pathway,
        is_venous=(pathway == PreopPatient.Pathway.VENOUS),
        is_phleb=(patient.op_subtype == PreopPatient.OpSubtype.PHLEB),
        is_pta=(pathway == PreopPatient.Pathway.PAD_PTA),
        is_pad_bypass=(pathway == PreopPatient.Pathway.PAD_BYPASS),
        is_on_hold=(patient.someday_list == PreopPatient.SomedayList.HOLD),
        q=q,
        s=s,
        q_filled=bool(q.get('filled')),
        case_outcome=case_outcome,
        is_active_case=(case_outcome == 'scheduled') and not patient.archived,
        days=prep_days_elapsed(patient, today=today),
        t_days=target_prep_days(patient, today=today),
        red_flags=[],
        amber_flags=[],
        reflex_orders=[],
        priority_flags=[],
        hard_contraindications=[],
        hb_gl=normalize_hb_gl(s.get('hb')),
        gl_mmol=normalize_glucose_mmol(s.get('glucose')),
        alb_gl=normalize_albumin_gl(s.get('albumin')),
        on_dialysis=on_dialysis,
        creat_unit=creat_unit,
        creat_unit_ambiguous=creat_unit_ambiguous,
        egfr=egfr,
        crcl=crcl,
        eff_renal=None if on_dialysis else min([v for v in (egfr, crcl) if v is not None], default=None),
        bmi=calc_bmi(s.get('weight_kg'), s.get('height_cm')),
        liver_3x=(s.get('liver_status') == '3x'),
        aaa_mm=_num(s.get('aaa_mm'), lo=10, hi=200),
        cta_months=cta_live_months_age(s, today=today),
        car_r=car_r,
        car_l=car_l,
        max_car=max([v for v in (car_r, car_l) if v is not None], default=None),
    )
    _init_limb_hemodynamics(ctx)
    _init_base_flags_and_pre_reflex(ctx)
    return ctx
