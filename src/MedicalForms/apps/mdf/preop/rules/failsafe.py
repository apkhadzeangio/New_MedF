"""Track A5 Fail-Safe Unknown-State detection so missing critical data never appears falsely 'normal'."""
from ...models import PreopPatient

_LAB_KEYS = ('hb', 'creat', 'inr', 'plt', 'wbc', 'glucose', 'k', 'na', 'albumin')


def _compute_score_completeness(ctx):
    patient, q_filled, s = ctx.patient, ctx.q_filled, ctx.s
    creat_missing = (not ctx.on_dialysis) and (s.get('creat') is None or ctx.creat_unit_ambiguous)

    rcri_missing = []
    if not q_filled:
        rcri_missing.append('კითხვარი')
    if creat_missing:
        rcri_missing.append('კრეატინინი')

    vsg_missing = []
    if not ctx.is_venous:
        if not patient.age:
            vsg_missing.append('ასაკი')
        if not q_filled:
            vsg_missing.append('კითხვარი')
        if creat_missing:
            vsg_missing.append('კრეატინინი')

    ariscat_missing = []
    if not patient.age:
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
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    is_lean_evla = ctx.is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA and not ctx.has_neuraxial_plan
    renal_oac = bool(q_filled and (q.get('med_doac') or q.get('med_dabigatran')))
    renal_other = bool(q_filled and (q.get('metformin') or 'rivaroxaban_2_5' in ctx.med_chips))
    if not ctx.on_dialysis and (renal_oac or renal_other) and (ctx.crcl is None or ctx.creat_unit_ambiguous):
        miss_bits = []
        if s.get('creat') is None or ctx.creat_unit_ambiguous:
            miss_bits.append('კრეატინინი')
        if ctx.weight_num is None:
            miss_bits.append('წონა')
        if not patient.age or patient.sex not in ('M', 'F'):
            miss_bits.append('ასაკი/სქესი')
        miss_txt = ', '.join(miss_bits) or 'კლირენსი (CrCl)'
        target_fld = 'creat' if (s.get('creat') is None or ctx.creat_unit_ambiguous) else 'weight_kg'
        add(
            'renal_drug_no_crcl', 's1', target_fld,
            f'წამლის დოზირებისთვის აკლია: {miss_txt}',
            'თირკმელ-დამოკიდებულ პრეპარატზე (DOAC / დაბიგატრანი / მეტფორმინი) CrCl-ის გარეშე შეჩერების საათების ზუსტი გამოთვლა შეუძლებელია.',
            blocking=bool(renal_oac and not is_lean_evla),
        )

    if ctx.unclarified_allergy_other:
        add(
            'unclarified_allergy', 'q', 'allergy_text',
            'ალერგია დაუზუსტებელია',
            'მონიშნულია „სხვა ალერგია“ პრეპარატის ან რეაქციის დაზუსტების გარეშე.',
            blocking=True,
        )
    if q_filled and ctx.ft_meds.get('unstructured_labels'):
        labels_txt = ', '.join(ctx.ft_meds['unstructured_labels'])
        add(
            'unstructured_med', 'q', 'other_meds',
            f'მაღალი რისკის წამალი ტექსტშია: {labels_txt}',
            'მონიშნეთ შესაბამისი სტრუქტურირებული ჩამრთველი, რომ კალენდარმა და ASRA კარიბჭემ გაითვალისწინოს.',
            blocking=bool(ctx.ft_meds.get('unstructured_antithrombotic')),
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

    if q_filled and q.get('filled_by') == 'პაციენტი (ბმულით)' and not q.get('nurse_verified') and not patient.approved_at:
        add(
            'unverified_self_fill', 'q', 'btn-verify-self',
            'თვითშევსება გადაუმოწმებელია',
            'პაციენტის მიერ ბმულით შევსებული კითხვარი ჯერ არ არის გადამოწმებული ექთნის მიერ.',
            blocking=True,
        )

    _collect_med_allergy_unknowns(ctx, add)

    if any(s.get(k) is not None for k in _LAB_KEYS) and not s.get('labs_date'):
        add(
            'labs_missing_date', 's1', 'labs_date',
            'ანალიზების თარიღი არ არის მითითებული',
            'შეყვანილია ლაბორატორიული შედეგები აღების თარიღის გარეშე — 14-დღიანი ვარგისიანობის გადამოწმება შეუძლებელია.',
            blocking=False,
        )

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
        patient.sex == 'F'
        and patient.age is not None
        and 12 <= patient.age <= 55
        and (patient.op_subtype in (PreopPatient.OpSubtype.EVAR, PreopPatient.OpSubtype.TBD) or ctx.is_pta or ctx.is_venous)
        and not q.get('pregnant_possible')
        and not q.get('ven_pregnant')
        and not s.get('dos_bhcg_negative')
    )
    if repro_female_risk:
        add(
            'reproductive_pregnancy_unverified', 'dos', 'dos_bhcg_negative',
            'ორსულობის გამორიცხვა დაუზუსტებელია (12–55 წ. ქალი)',
            'ფლუოროსკოპიულ (EVAR/PTA) ან ვენურ ჩარევაზე ოპერაციამდე საჭიროა ორსულობის სტატუსის დაზუსტება / β-hCG.',
            blocking=False,
        )

    ctx.score_completeness = score_comp
    ctx.unknown_state_flags = flags
    ctx.has_blocking_unknown_state = any(item['blocking'] for item in flags)
