"""Track A5 Fail-Safe Unknown-State detection so missing critical data never appears falsely 'normal'."""
from ...models import PreopPatient
from ..constants import STATUS_PROGRESS, STATUS_READY
from ..utils import _date, _int

_LAB_KEYS = ('hb', 'creat', 'inr', 'plt', 'wbc', 'glucose', 'k', 'na', 'albumin')


def _compute_score_completeness(ctx):
    patient, q_filled = ctx.patient, ctx.q_filled
    age_ok = _int(patient.age, lo=12, hi=120) is not None
    creat_missing = (not ctx.on_dialysis) and (ctx.creat_unit_ambiguous or ctx.egfr is None)

    rcri_missing = []
    if not q_filled:
        rcri_missing.append('კითხვარი')
    if creat_missing:
        rcri_missing.append('კრეატინინი')

    vsg_missing = []
    if not ctx.is_venous:
        if not age_ok:
            vsg_missing.append('ასაკი')
        if not q_filled:
            vsg_missing.append('კითხვარი')
        if creat_missing:
            vsg_missing.append('კრეატინინი')

    ariscat_missing = []
    if not age_ok:
        ariscat_missing.append('ასაკი')
    if ctx.spo2 is None:
        ariscat_missing.append('SpO2')
    if ctx.hb_gl is None:
        ariscat_missing.append('Hb')

    return {
        'rcri': {'incomplete': bool(rcri_missing), 'missing': rcri_missing},
        'vsg_cri': {'incomplete': bool(vsg_missing), 'missing': vsg_missing},
        'ariscat': {'incomplete': bool(ariscat_missing), 'missing': ariscat_missing},
    }


def _collect_med_allergy_unknowns(ctx, add):
    patient, q, s = ctx.patient, ctx.q, ctx.s
    renal_oac = bool(q.get('med_doac') or q.get('med_dabigatran'))
    renal_other = bool(q.get('metformin') or 'rivaroxaban_2_5' in ctx.med_chips)
    if not ctx.on_dialysis and (renal_oac or renal_other) and (ctx.crcl is None or ctx.creat_unit_ambiguous):
        miss_bits = []
        if s.get('creat') is None or ctx.creat_unit_ambiguous:
            miss_bits.append('კრეატინინი')
        if ctx.weight_num is None:
            miss_bits.append('წონა')
        if _int(patient.age, lo=12, hi=120) is None or patient.sex not in ('M', 'F'):
            miss_bits.append('ასაკი/სქესი')
        miss_txt = ', '.join(miss_bits) or 'კლირენსი (CrCl)'
        target_fld = 'creat' if (s.get('creat') is None or ctx.creat_unit_ambiguous) else 'weight_kg'
        add(
            'renal_drug_no_crcl', 's1', target_fld,
            f'წამლის დოზირებისთვის აკლია: {miss_txt}',
            'თირკმელ-დამოკიდებულ პრეპარატზე (DOAC / დაბიგატრანი / მეტფორმინი) CrCl-ის გარეშე შეჩერების საათებისა და უკუჩვენებების გადამოწმება შეუძლებელია.',
            blocking=True,
        )

    if ctx.unclarified_allergy_other:
        add(
            'unclarified_allergy', 'q', 'allergy_text',
            'ალერგია დაუზუსტებელია',
            'მონიშნულია „სხვა ალერგია“ ან შეყვანილია დაუზუსტებელი ალერგიის ტექსტი პრეპარატის/რეაქციის გადამოწმების გარეშე.',
            blocking=True,
        )
    elif (q.get('allergy_text') or '').strip() and not q.get('allergy_other') and not (q.get('allergy_chips') or []):
        add(
            'unconfirmed_freetext_allergy', 'q', 'allergy_text',
            'ალერგია მხოლოდ ტექსტშია — დაადასტურეთ ჩიპით',
            'თავისუფალ ტექსტში მითითებულია მედიკამენტური ალერგია, მაგრამ სტრუქტურულად მონიშნული არ არის — დადასტურებამდე GO ბეჭედი არ გაიცემა.',
            blocking=True,
        )
    if ctx.ft_meds.get('unstructured_labels'):
        labels_txt = ', '.join(ctx.ft_meds['unstructured_labels'])
        add(
            'unstructured_med', 'q', 'other_meds',
            f'მაღალი რისკის წამალი ტექსტშია: {labels_txt}',
            'მონიშნეთ შესაბამისი სტრუქტურირებული ჩამრთველი, რომ კალენდარმა და ASRA კარიბჭემ გაითვალისწინოს.',
            blocking=True,
        )


def _collect_demographics_and_vitals_unknowns(ctx, add):
    patient, pathway, s, q_filled = ctx.patient, ctx.pathway, ctx.s, ctx.q_filled
    age_ok = _int(patient.age, lo=12, hi=120) is not None
    if getattr(ctx, 'implausible_fields', None):
        add(
            'implausible_values', 's1', 'creat',
            f'დაუშვებელი მნიშვნელობები: {", ".join(ctx.implausible_fields[:3])}',
            'შეყვანილია ფიზიოლოგიურ დიაპაზონს გარეთა ან არასწორი ფორმატის მონაცემი — შეფასება ვერ დადასტურდება.',
            blocking=True,
        )
    if getattr(ctx, 'creat_unit_ambiguous', False) and not ctx.on_dialysis:
        add(
            'ambiguous_creat_unit', 's1', 'creat',
            'კრეატინინის ერთეული დაუზუსტებელია (15–35 ზონა)',
            'მიუთითეთ კრეატინინის ერთეული (µmol/L ან mg/dL) — ერთეულის გარეშე eGFR/CrCl ვერ ფასდება და GO ბეჭედი დაბლოკილია.',
            blocking=True,
        )
    has_any_lab = any(s.get(k) is not None for k in _LAB_KEYS)
    if has_any_lab:
        labs_dt = _date(s.get('labs_date'))
        if labs_dt is None or labs_dt > ctx.today:
            add(
                'labs_missing_date', 's1', 'labs_date',
                'ანალიზების თარიღი აკლია ან არასწორია',
                'შეყვანილია ლაბორატორიული შედეგები ვალიდური აღების თარიღის გარეშე — 14-დღიანი ვარგისიანობის გადამოწმება შეუძლებელია.',
                blocking=True,
            )
    if (not age_ok or patient.sex not in ('M', 'F')) and has_any_lab:
        add(
            'missing_demographics', 'header', 'age',
            'აკლია ან არასწორია ასაკი (12–120 წ.) / სქესი',
            'ასაკისა და სქესის გარეშე eGFR/CrCl, ASA და რისკ-ინდექსების უსაფრთხო გამოთვლა შეუძლებელია.',
            blocking=True,
        )
    if q_filled and pathway in (PreopPatient.Pathway.CEA, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA, PreopPatient.Pathway.VENOUS):
        valid_sides = ('R', 'L') if pathway == PreopPatient.Pathway.CEA else ('R', 'L', 'B')
        if patient.side not in valid_sides:
            add(
                'missing_surgical_side', 'header', 'side',
                'საოპერაციო მხარე არ არის მითითებული',
                'ლატერალიზებულ ოპერაციაზე მხარის (R / L) გარეშე შეუძლებელია იფსილატერალური სტენოზისა და ABI-ის გადამოწმება.',
                blocking=True,
            )
    if q_filled and has_any_lab:
        if not ctx.is_venous:
            miss_v = []
            if ctx.spo2 is None:
                miss_v.append('SpO2')
            if ctx.eff_sys is None or ctx.eff_dia is None:
                miss_v.append('არტერიული წნევა (BP)')
            if ctx.hr is None:
                miss_v.append('პულსი (HR)')
            if miss_v:
                add(
                    'missing_core_vitals', 's1', 'spo2' if ctx.spo2 is None else 'bp_sys',
                    f'აკლია საბაზისო ვიტალური პარამეტრები ({", ".join(miss_v)})',
                    'საბაზისო წნევის, პულსისა და SpO2-ის გარეშე ჰემოდინამიკური რისკი და ARISCAT სრულად ვერ ფასდება.',
                    blocking=True,
                )
        elif ctx.eff_sys is None and ctx.eff_dia is None and ctx.hr is None and ctx.spo2 is None:
            add(
                'missing_core_vitals', 's1', 'bp_sys',
                'აკლია საბაზისო ვიტალური პარამეტრები (BP / HR / SpO2)',
                'მიღების საბაზისო ვიტალური პარამეტრების გარეშე ოპერაციაზე GO დაშვება არ გაიცემა.',
                blocking=True,
            )


def eval_unknown_state_failsafe(ctx):
    """Detect clinically critical unknown/incomplete inputs and compute risk-score completeness."""
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    flags = []

    def add(key, tab, field, label, detail, blocking=False):
        flags.append({
            'key': key,
            'tab': tab,
            'field': field,
            'label': label,
            'detail': detail,
            'blocking': blocking,
            'rule_id': 'WF_UNKNOWN_STATE_FAILSAFE',
        })

    from ..utils import is_pregnancy_eligible, is_self_filled_q
    is_unverified_sf = is_self_filled_q(q) and not q.get('nurse_verified')
    if is_unverified_sf:
        add(
            'unverified_self_fill', 'q', 'btn-verify-self',
            'თვითშევსება გადაუმოწმებელია',
            'პაციენტის მიერ ბმულით შევსებული კითხვარი ჯერ არ არის გადამოწმებული ექთნის მიერ.',
            blocking=True,
        )

    _collect_med_allergy_unknowns(ctx, add)
    _collect_demographics_and_vitals_unknowns(ctx, add)

    score_comp = _compute_score_completeness(ctx)
    if q_filled:
        score_missing_names = [
            lbl for key, lbl in (('rcri', 'RCRI'), ('vsg_cri', 'VSG-CRI'), ('ariscat', 'ARISCAT'))
            if score_comp[key]['incomplete']
        ]
        if score_missing_names:
            all_miss = list(dict.fromkeys(
                score_comp['rcri']['missing'] + score_comp['vsg_cri']['missing'] + score_comp['ariscat']['missing']
            ))
            add(
                'incomplete_risk_scores', 's1', 'spo2' if 'SpO2' in all_miss else 'creat',
                f'რისკ-ინდექსები არასრულია ({", ".join(score_missing_names)})',
                f'აკლია: {", ".join(all_miss)} — რეალური პერიოპერაციული რისკი შეიძლება მაღალი იყოს.',
                blocking=False,
            )

    repro_female_risk = (
        is_pregnancy_eligible(patient)
        and (
            (patient.sex == 'F' and (
                patient.op_subtype in (PreopPatient.OpSubtype.EVAR, PreopPatient.OpSubtype.TBD)
                or ctx.is_pta or ctx.is_venous
            ))
            or bool(q.get('pregnant_possible') or q.get('ven_pregnant'))
        )
        and not s.get('dos_bhcg_negative')
    )
    if repro_female_risk:
        add(
            'reproductive_pregnancy_unverified', 'dos', 'dos_bhcg_negative',
            'ორსულობის გამორიცხვა დაუზუსტებელია (12–55 წ. ქალი)',
            'ორსულობის სტატუსის დაზუსტების / უარყოფითი β-hCG-ის გარეშე ოპერაციაზე GO დაშვება არ გაიცემა.',
            blocking=True,
        )

    ctx.score_completeness = score_comp
    ctx.unknown_state_flags = flags
    ctx.has_blocking_unknown_state = any(item['blocking'] for item in flags)
    if ctx.has_blocking_unknown_state or (ctx.needs_stale_labs and not s.get('stale_labs_refreshed')):
        if ctx.status == STATUS_READY:
            ctx.status = STATUS_PROGRESS
        ctx.dos_ready = False
