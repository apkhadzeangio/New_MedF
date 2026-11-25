"""Conference Case auto-detection and Doppler/WIfI/GLASS import into PreopPatient."""
from ..models import PreopPatient
from .constants import CHIP_CATALOGS
from .utils import merged_q, merged_studies


def detect_case_preop(case):
    """Infer op_subtype and surgical side from a Conference Case (including GVG WIfI / PLAN staging)."""
    blob = ' '.join([
        case.diagnosis or '', case.proposal or '', case.decision or '', case.history or ''
    ]).lower()
    dec_prop = ((case.decision or '') + ' ' + (case.proposal or '')).lower()
    side = case.side if case.side in ('R', 'L', 'B') else ''
    if not side:
        has_r = 'მარჯვ' in blob
        has_l = 'მარცხ' in blob
        if 'ორმხრივ' in blob or (has_r and has_l):
            side = 'B'
        elif has_r:
            side = 'R'
        elif has_l:
            side = 'L'
    is_clti = any(k in blob for k in ('კრიტიკ', 'clti', 'მოსვენებით', 'წყლულ', 'განგრენ', 'ნეკროზ'))
    has_limb_staging = False

    staging_map = case.staging if isinstance(case.staging, dict) else {}
    if staging_map:
        from .. import staging as st_mod
        for s_key in ('R', 'L'):
            raw_limb = staging_map.get(s_key)
            if raw_limb and st_mod.has_staging(raw_limb):
                has_limb_staging = True
            if raw_limb and st_mod.has_wifi(raw_limb):
                cleaned = st_mod.get_limb(staging_map, s_key)
                comp = st_mod.compute(cleaned)
                if (comp.get('wifi') or 0) >= 2 or (comp.get('w') or 0) >= 1 or (comp.get('fi') or 0) >= 1:
                    is_clti = True

    if any(k in blob for k in ('ლაზერ', 'evla', 'რადიოსიხშირ', 'rfa', 'ენდოვენურ')):
        return PreopPatient.OpSubtype.EVLA, side

    if any(k in blob for k in ('კაროტიდ', 'საძილე', 'ენდარტერექტომ', 'cea')):
        is_symp = any(k in blob for k in ('სიმპტომ', 'ინსულტ', 'tia', 'ტ.ი.ა', 'ამავროზ', 'იშემიური შეტევა'))
        return (PreopPatient.OpSubtype.CEA_SYMP if is_symp else PreopPatient.OpSubtype.CEA_ASYMP), side

    if any(k in blob for k in ('ბიფემორ', 'აორტო-ბიფემ', 'აორტობიფემ', 'ლერიშ', 'abf', 'aortobifem')):
        return (PreopPatient.OpSubtype.ABF_CLTI if is_clti else PreopPatient.OpSubtype.ABF_CLAUD), side

    if any(k in blob for k in ('ანევრიზმ', 'aaa', 'evar', 'მუცლის აორტ')):
        op_sub = PreopPatient.OpSubtype.EVAR if ('evar' in dec_prop or 'ენდოვასკ' in dec_prop) else PreopPatient.OpSubtype.OPEN
        return op_sub, side

    if any(k in blob for k in ('შუნტირებ', 'ბარძაყ-მუხლქვეშ', 'ფემორო-პოპლიტ', 'ფემორო-ტიბიალ', 'აუტოვენ')):
        return (PreopPatient.OpSubtype.BYPASS_CLTI if is_clti else PreopPatient.OpSubtype.BYPASS_CLAUD), side

    if any(k in blob for k in ('ანგიოპლასტიკ', 'ბალონირებ', 'სტენტირებ', 'pta', 'რეკანალიზაც', 'plan: ენდოვასკ')):
        return (PreopPatient.OpSubtype.PTA_CLTI if is_clti else PreopPatient.OpSubtype.PTA_CLAUD), side

    if any(k in blob for k in ('ფლებექტომ', 'კროსექტომ', 'ვარიკოზ', 'საჩინო ვენ')) and not is_clti and not has_limb_staging:
        return PreopPatient.OpSubtype.PHLEB, side

    if has_limb_staging or any(k in blob for k in ('ქვემო კიდურ', 'ბარძაყ', 'მუხლქვეშ', 'წვივ', 'ტერფ', 'sfa', 'cfa', 'პერიფერიულ')):
        if 'ენდოვასკ' in blob:
            return (PreopPatient.OpSubtype.PTA_CLTI if is_clti else PreopPatient.OpSubtype.PTA_CLAUD), side
        return (PreopPatient.OpSubtype.BYPASS_CLTI if is_clti else PreopPatient.OpSubtype.BYPASS_CLAUD), side

    op_sub = PreopPatient.OpSubtype.EVAR if ('evar' in dec_prop or 'ენდოვასკ' in dec_prop) else PreopPatient.OpSubtype.OPEN
    return op_sub, side


def _import_limb_wifi(side, raw_limb, limb, r, q, s, ulcer_chips, badge_parts):
    upgrade_clti = False
    w = r.get('w') if r.get('w') is not None else limb.get('w')
    i = r.get('i') if r.get('i') is not None else limb.get('i')
    fi = r.get('fi') if r.get('fi') is not None else limb.get('fi')
    wifi_st = r.get('wifi')
    if w is not None and w >= 1:
        q['abf_ulcer'] = True
        ulcer_chips.add('r_foot' if side == 'R' else 'l_foot')
        if w >= 2:
            ulcer_chips.add('deep_bone')
        upgrade_clti = True
    if fi is not None and fi >= 2:
        q['abf_wet_infection'] = True
        upgrade_clti = True
    if i is not None and i >= 2 and (w is None or w == 0):
        q['abf_rest_pain'] = True
        upgrade_clti = True
    if limb.get('unsalvageable') or wifi_st == 5:
        s['case_wifi_unsalvageable'] = True
        upgrade_clti = True
    elif wifi_st is not None and wifi_st >= 2:
        upgrade_clti = True
    if wifi_st is not None:
        badge_parts.append(f'{side}: WIfI Stage {wifi_st} (W{w}I{i}fI{fi})')
    if raw_limb.get('vein') == 'no':
        s['pad_gsv_status'] = 'inadequate'
    return upgrade_clti


def _import_limb_angio(side, raw_limb, limb, r, s, badge_parts, runoff_rank):
    sug = r.get('suggested') or {}
    glass_st = r.get('glass') or (sug.get('glass') if sug.get('has_map') else None)
    if glass_st:
        badge_parts.append(f'{side}: GLASS {glass_st}')
    cfa_val = limb.get('cfa') if 'cfa' in raw_limb else (sug.get('cfa') if sug.get('has_map') else '')
    if cfa_val == 'B':
        s['pta_access_site'] = 'cfa_calcified_scar'
    elif cfa_val == 'A' and sug.get('has_map') and s.get('pta_access_site') != 'cfa_calcified_scar':
        s['pta_access_site'] = 'cfa_clean'
    if sug.get('has_map'):
        rc = sug.get('runoff_count', 0)
        pop_occ = any(
            (limb['segments'].get(pk_k) or {}).get('stenosis') == 'occlusion'
            for pk_k in ('pop1', 'pop2', 'pop3')
        )
        cand_run = 'desert_foot' if rc == 0 else ('tibial_single' if (rc == 1 or pop_occ) else 'popliteal_patent')
        if runoff_rank.get(cand_run, 0) > runoff_rank.get(s.get('pad_runoff_status') or 'pending', 0):
            s['pad_runoff_status'] = cand_run


def import_case_to_preop(case, patient):
    """Auto-import ABI/TBI/TP Doppler measurements, WIfI limb threat, GLASS runoff & GSV status from a Case."""
    from .. import staging as st_mod
    from ..cases import case_limb_metrics

    staging_map = case.staging if isinstance(case.staging, dict) else {}
    rows = case_limb_metrics(case)
    has_any = any(
        row['has_any_doppler'] or st_mod.has_staging(staging_map.get(row['side']))
        for row in rows
    )
    if not has_any:
        return False

    q = merged_q(patient)
    s = merged_studies(patient)
    ulcer_chips = set(q.get('ulcer_chips') or [])
    badge_parts = []
    upgrade_clti = False
    runoff_rank = {'pending': 0, 'popliteal_patent': 1, 'tibial_single': 2, 'desert_foot': 3}

    for row in rows:
        side = row['side']
        s_lo = side.lower()
        raw_limb = staging_map.get(side) or {}
        if row['abi'] is not None:
            s[f'abi_{s_lo}'] = row['abi']
        if row['tbi'] is not None:
            s[f'tbi_{s_lo}'] = row['tbi']
        if row['tp'] is not None:
            s[f'tp_{s_lo}'] = row['tp']

        is_target_limb = patient.side in ('', 'B', side)
        if st_mod.has_wifi(raw_limb) and is_target_limb:
            if _import_limb_wifi(side, raw_limb, row['limb'], row['r'], q, s, ulcer_chips, badge_parts):
                upgrade_clti = True
        if st_mod.has_angiography(raw_limb) and is_target_limb:
            _import_limb_angio(side, raw_limb, row['limb'], row['r'], s, badge_parts, runoff_rank)

    if ulcer_chips:
        q['ulcer_chips'] = [k for k, _ in CHIP_CATALOGS['ulcer_chips'] if k in ulcer_chips]

    clti_map = {
        PreopPatient.OpSubtype.ABF_CLAUD: PreopPatient.OpSubtype.ABF_CLTI,
        PreopPatient.OpSubtype.BYPASS_CLAUD: PreopPatient.OpSubtype.BYPASS_CLTI,
        PreopPatient.OpSubtype.PTA_CLAUD: PreopPatient.OpSubtype.PTA_CLTI,
    }
    if upgrade_clti and patient.op_subtype in clti_map:
        patient.op_subtype = clti_map[patient.op_subtype]
        patient.pathway = PreopPatient.SUBTYPE_PATHWAY[patient.op_subtype]

    if badge_parts:
        s['case_summary_badge'] = ' · '.join(badge_parts)
    patient.questionnaire = q
    patient.studies = s
    patient.save()
    return True
