"""GLASS/WIfI/PLAN computation, angiosome perfusion analysis, and map-derived staging suggestions."""
from .catalog import ANGIOSOME_SOURCE, ANGIOSOME_TERMINAL, ANGIOSOMES
from .schema import _int, clean_segments, upgrade_limb
from .tables import (
    _BENEFIT,
    _DEFAULT_LENGTH_CM,
    BENEFIT_LABEL,
    GLASS_MATRIX,
    GLASS_TEXT,
    INFLOW_AI,
    INFLOW_CFA,
    PEDAL,
    PLAN,
    PLAN_LABEL,
    TAP,
    WIFI_RISK,
    WIFI_STAGE,
    suggest_ischemia,
)


def _fmt_score(val):
    rounded = round(float(val), 1)
    return str(int(rounded)) if rounded.is_integer() else f'{rounded:.1f}'


def _is_sig(seg):
    return seg.get('stenosis') in ('moderate', 'severe', 'occlusion')


def _is_occ(seg):
    return seg.get('stenosis') == 'occlusion'


def angiosome_status(limb):
    """Evaluate direct vs indirect angiosome perfusion for the selected wound angiosome and TAP."""
    if not isinstance(limb, dict):
        return None
    ang = limb.get('wound_angiosome') or ''
    if ang not in ANGIOSOME_SOURCE:
        return None
    label = dict(ANGIOSOMES).get(ang, ang)
    source = ANGIOSOME_SOURCE[ang]
    tap_labels = dict(TAP)
    source_label = tap_labels.get(source, source)
    tap = limb.get('tap') or ''
    segs = clean_segments(limb.get('segments'))

    if tap not in tap_labels:
        return {
            'key': ang, 'label': label, 'source_tap': source, 'source_label': source_label,
            'direct': None, 'status': 'pending', 'tone': 'muted',
            'text': f'პირდაპირი მკვებავი არტერია: {source_label}',
        }

    term = ANGIOSOME_TERMINAL.get(ang)
    if tap == source:
        if term and segs.get(term, {}).get('stenosis') == 'occlusion':
            return {
                'key': ang, 'label': label, 'source_tap': source, 'source_label': source_label,
                'direct': False, 'status': 'collateral', 'tone': 'warn',
                'text': 'სამიზნე წვივის არტერია ემთხვევა, თუმცა ტერფის ტოტი დახშულია (რკალით/კოლატერალით კვება)',
            }
        return {
            'key': ang, 'label': label, 'source_tap': source, 'source_label': source_label,
            'direct': True, 'status': 'direct', 'tone': 'ok',
            'text': 'პირდაპირი (ანგიოსომაზე მიმართული) რევასკულარიზაცია',
        }

    arch_st = segs.get('arch', {}).get('stenosis', 'none')
    arch_intact = arch_st not in ('moderate', 'severe', 'occlusion') and limb.get('pedal') not in ('P1', 'P2')
    if arch_intact:
        return {
            'key': ang, 'label': label, 'source_tap': source, 'source_label': source_label,
            'direct': False, 'status': 'indirect_arch', 'tone': 'warn',
            'text': 'არაპირდაპირი რევასკულარიზაცია ინტაქტური ტერფის რკალის გავლით',
        }
    return {
        'key': ang, 'label': label, 'source_tap': source, 'source_label': source_label,
        'direct': False, 'status': 'indirect_poor', 'tone': 'bad',
        'text': 'არაპირდაპირი რევასკულარიზაცია დაზიანებული ტერფის რკალის პირობებში',
    }


def _suggest_inflow(segs, ivs):
    cfa = 'B' if _is_sig(segs.get('cfa', {})) else 'A'
    ai_keys = ('aorta', 'cia', 'eia')
    ai_diffuse_isr = any(
        iv['type'] == 'stent' and iv['status'] in ('isr_2', 'isr_3', 'occluded')
        and any(k in ai_keys for k in iv.get('segments', ()))
        for iv in ivs
    )
    if (
        _is_occ(segs.get('aorta', {}))
        or (_is_occ(segs.get('cia', {})) and _is_occ(segs.get('eia', {})))
        or any(segs.get(k, {}).get('aneurysm') or segs.get(k, {}).get('small_caliber') for k in ai_keys)
        or ai_diffuse_isr
        or all(segs.get(k, {}).get('stenosis') == 'severe' for k in ('cia', 'eia'))
    ):
        ai = 'II'
    elif any(_is_sig(segs.get(k, {})) for k in ai_keys):
        ai = 'I'
    else:
        ai = ''
    return ai, cfa


def _suggest_fp(segs, seg_len):
    sfa_keys = ('sfa1', 'sfa2', 'sfa3')
    pop_keys = ('pop1', 'pop2', 'pop3')
    sfa_disease_len = sum(seg_len(k, True) for k in sfa_keys if _is_sig(segs.get(k, {})))
    sfa_cto_len = sum(seg_len(k, True) for k in sfa_keys if _is_occ(segs.get(k, {})))
    sfa_separate_ctos = (
        int(_is_occ(segs.get('sfa1', {})))
        + int(_is_occ(segs.get('sfa2', {})) and not _is_occ(segs.get('sfa1', {})))
        + int(_is_occ(segs.get('sfa3', {})) and not _is_occ(segs.get('sfa2', {})))
    )
    sfa_flush_cto = _is_occ(segs.get('sfa1', {})) and bool(segs.get('sfa1', {}).get('flush'))
    pop_disease_len = sum(seg_len(k, True) for k in pop_keys if _is_sig(segs.get(k, {})))
    pop_any_cto = any(_is_occ(segs.get(k, {})) for k in pop_keys)
    pop_trifurcation = any(bool(segs.get(k, {}).get('trifurcation')) for k in pop_keys)

    if sfa_cto_len > 20.0 or pop_any_cto or pop_disease_len > 5.0 or pop_trifurcation:
        fp_grade = 4
    elif (
        sfa_disease_len > 20.0
        or (sfa_flush_cto and sfa_cto_len <= 20.0)
        or (not sfa_flush_cto and 10.0 <= sfa_cto_len <= 20.0)
        or 2.0 <= pop_disease_len <= 5.0
    ):
        fp_grade = 3
    elif (
        10.0 <= sfa_disease_len <= 20.0
        or (not sfa_flush_cto and 5.0 <= sfa_cto_len < 10.0)
        or (not sfa_flush_cto and 0.0 < sfa_cto_len < 5.0 and sfa_separate_ctos > 1)
        or 0.0 < pop_disease_len < 2.0
    ):
        fp_grade = 2
    elif 0.0 < sfa_disease_len < 10.0:
        fp_grade = 1
    else:
        fp_grade = 0

    fp_calc = any(
        (segs.get(k, {}).get('calc') or 0) >= 3 or bool(segs.get(k, {}).get('coral'))
        for k in sfa_keys + pop_keys
    )
    return fp_grade, fp_calc, min(4, fp_grade + (1 if fp_calc else 0))


def _suggest_pedal_and_ip(segs, raw_limb, seg_len):
    at_cross = not _is_occ(segs.get('at3', {})) and not _is_occ(segs.get('dp', {}))
    pt_cross = not _is_occ(segs.get('pt3', {})) and (not _is_occ(segs.get('mpa', {})) or not _is_occ(segs.get('lpa', {})))
    per_cross = not _is_occ(segs.get('per3', {})) and (
        not _is_occ(segs.get('dp', {})) or not _is_occ(segs.get('mpa', {})) or not _is_occ(segs.get('lpa', {}))
    )
    crosses_map = {'at': at_cross, 'pt': pt_cross, 'per': per_cross}
    arch_complete = (
        not _is_sig(segs.get('arch', {}))
        and not _is_occ(segs.get('dp', {}))
        and (not _is_occ(segs.get('mpa', {})) or not _is_occ(segs.get('lpa', {})))
    )
    pedal_for = lambda t: ('P0' if arch_complete else 'P1') if crosses_map[t] else 'P2'

    wound_ang = raw_limb.get('wound_angiosome') or ''
    angio_source = ANGIOSOME_SOURCE.get(wound_ang, '')
    tap_labels = dict(TAP)
    tap_short = {'at': 'AT', 'pt': 'PT', 'per': 'PER'}
    ip_by_tap = {}
    for t_key, tib_keys in (('at', ('at1', 'at2', 'at3')),
                            ('pt', ('pt1', 'pt2', 'pt3')),
                            ('per', ('per1', 'per2', 'per3'))):
        uses_tpt = t_key in ('pt', 'per')
        tpt_item = segs.get('tpt', {}) if uses_tpt else {}
        tpt_cto = uses_tpt and _is_occ(tpt_item)
        tpt_sten = uses_tpt and _is_sig(tpt_item) and not tpt_cto
        tpt_len = seg_len('tpt', True) if _is_sig(tpt_item) else 0.0

        total_len = tpt_len + sum(seg_len(k, True) for k in tib_keys if _is_sig(segs.get(k, {})))
        cto_len = sum(seg_len(k, True) for k in tib_keys if _is_occ(segs.get(k, {})))
        sten_cnt = sum(1 for k in tib_keys if _is_sig(segs.get(k, {})) and not _is_occ(segs.get(k, {})))
        cto_runs = (
            int(_is_occ(segs.get(tib_keys[0], {})))
            + int(_is_occ(segs.get(tib_keys[1], {})) and not _is_occ(segs.get(tib_keys[0], {})))
            + int(_is_occ(segs.get(tib_keys[2], {})) and not _is_occ(segs.get(tib_keys[1], {})))
        )
        origin_flush = _is_occ(segs.get(tib_keys[0], {})) and bool(segs.get(tib_keys[0], {}).get('flush'))

        if tpt_cto or total_len > 20.0 or cto_len > 10.0:
            ip_g = 4
        elif (
            10.0 <= total_len <= 20.0
            or 3.0 <= cto_len <= 10.0
            or (0.0 < cto_len < 3.0 and (origin_flush or tpt_sten or cto_runs > 1))
        ):
            ip_g = 3
        elif (
            3.0 <= total_len < 10.0
            or 0.0 < cto_len < 3.0
            or tpt_sten
            or (0.0 < total_len < 3.0 and sten_cnt > 1)
        ):
            ip_g = 2
        elif 0.0 < total_len < 3.0:
            ip_g = 1
        else:
            ip_g = 0

        path_keys = (('tpt',) + tib_keys) if uses_tpt else tib_keys
        ip_c = any(
            (segs.get(k, {}).get('calc') or 0) >= 3 or bool(segs.get(k, {}).get('coral'))
            for k in path_keys
        )
        ip_by_tap[t_key] = {
            'tap': t_key, 'short': tap_short[t_key], 'label': tap_labels[t_key],
            'ip': ip_g, 'ip_calc': ip_c, 'ip_final': min(4, ip_g + (1 if ip_c else 0)),
            'crosses_ankle': crosses_map[t_key], 'angiosome_match': angio_source == t_key,
        }

    direct_pedal_viable = any(
        ip_by_tap[t]['crosses_ankle'] and ip_by_tap[t]['ip_final'] <= 2
        for t in ('at', 'pt')
    )
    sug_tap = min(
        ('at', 'pt', 'per'),
        key=lambda t: (
            0 if ip_by_tap[t]['crosses_ankle'] else 1,
            1 if (t == 'per' and direct_pedal_viable and angio_source != 'per') else 0,
            ip_by_tap[t]['ip_final'],
            0 if ip_by_tap[t]['angiosome_match'] else 1,
            ip_by_tap[t]['ip'],
            0 if t in ('at', 'pt') else 1,
            ('at', 'pt', 'per').index(t),
        ),
    )
    return ip_by_tap, sug_tap, pedal_for, tap_labels, tap_short


def _suggest_svs_runoff(segs, seg_len):
    def vessel_svs(keys, total_vessel_cm):
        if any(_is_occ(segs.get(k, {})) for k in keys):
            occ_cm = sum(seg_len(k, False) for k in keys if _is_occ(segs.get(k, {})))
            return 3.0 if occ_cm > 0.5 * total_vessel_cm else 2.5
        if any(segs.get(k, {}).get('stenosis') in ('moderate', 'severe') for k in keys):
            return 2.0
        if any(segs.get(k, {}).get('stenosis') == 'mild' for k in keys):
            return 1.0
        return 0.0

    pop_svs = vessel_svs(('pop1', 'pop2', 'pop3'), 12.0) * 3.0 + 1.0
    at_svs = vessel_svs(('at1', 'at2', 'at3'), 27.0)
    pt_svs = vessel_svs(('tpt', 'pt1', 'pt2', 'pt3'), 30.0)
    per_svs = vessel_svs(('tpt', 'per1', 'per2', 'per3'), 30.0)
    svs_runoff = round(pop_svs + at_svs + pt_svs + per_svs, 1)
    foot_runoff = round(
        1.0 + vessel_svs(('dp',), 6.0) + vessel_svs(('mpa',), 6.0) + vessel_svs(('lpa',), 6.0),
        1,
    )
    patent_tibials = [
        lbl for lbl, keys in (
            ('AT', ('at1', 'at2', 'at3')),
            ('PT', ('tpt', 'pt1', 'pt2', 'pt3')),
            ('PER', ('tpt', 'per1', 'per2', 'per3')),
        )
        if not any(_is_occ(segs.get(k, {})) for k in keys)
    ]
    return svs_runoff, foot_runoff, patent_tibials


def suggest_from_map(limb):
    """Suggest GLASS (FP/IP/+1 calc, TAP, P0-P2, Inflow AI/CFA) and SVS Runoff from the arterial map.

    Sources:
    - Conte et al. (GVG / GLASS, J Vasc Surg 2019): Table 5.2, Figs 5.1-5.3, Recs 5.1-5.2.
    - Stoner et al. (SVS Reporting Standards, J Vasc Surg 2016): Modified SVS Runoff (1-19) & Foot Score (1-10).
    - Rocha-Singh et al. (PACSS, Catheter Cardiovasc Interv 2014): bilateral calcification PACSS >= 3 -> +1.
    Always advisory: never overwrites doctor-selected form values unless explicitly applied.
    """
    raw_limb = limb if isinstance(limb, dict) else {}
    up = upgrade_limb(raw_limb)
    segs, ivs = up['segments'], up['interventions']
    has_map = bool(segs) or any(iv['type'] != 'amputation' for iv in ivs)
    functioning_bypass = any(iv['type'] == 'bypass' and iv['status'] in ('patent', 'stenosed') for iv in ivs)
    estimated = False

    def seg_len(key, track_est=False):
        nonlocal estimated
        item = segs.get(key, {})
        if item.get('length_cm') is not None:
            return float(item['length_cm'])
        if track_est and _is_sig(item) and not (_is_occ(item) and key in ('pop1', 'pop2', 'pop3', 'tpt')):
            estimated = True
        return _DEFAULT_LENGTH_CM[key]

    ai, cfa = _suggest_inflow(segs, ivs)
    fp_grade, fp_calc, fp_final = _suggest_fp(segs, seg_len)
    ip_by_tap, sug_tap, pedal_for, tap_labels, tap_short = _suggest_pedal_and_ip(segs, raw_limb, seg_len)
    sug_ip = ip_by_tap[sug_tap]['ip']
    sug_ip_calc = ip_by_tap[sug_tap]['ip_calc']
    sug_ip_final = ip_by_tap[sug_tap]['ip_final']
    sug_pedal = pedal_for(sug_tap)
    sug_glass = GLASS_MATRIX[fp_final][sug_ip_final]

    cur_tap = raw_limb.get('tap') if raw_limb.get('tap') in tap_labels else ''
    active_tap = cur_tap or sug_tap
    active_ip_info = ip_by_tap[active_tap]
    svs_runoff, foot_runoff, patent_tibials = _suggest_svs_runoff(segs, seg_len)

    cur_fp = _int(raw_limb.get('fp'), 0, 4)
    cur_ip = _int(raw_limb.get('ip'), 0, 4)
    cur_fp_calc = raw_limb.get('fp_calc') in (True, 'on', '1')
    cur_ip_calc = raw_limb.get('ip_calc') in (True, 'on', '1')
    cur_pedal = raw_limb.get('pedal') if raw_limb.get('pedal') in {k for k, _ in PEDAL} else ''
    cur_ai = raw_limb.get('ai') if raw_limb.get('ai') in {k for k, _ in INFLOW_AI} else ''
    cur_cfa = raw_limb.get('cfa') if raw_limb.get('cfa') in {k for k, _ in INFLOW_CFA} else 'A'
    differs = (
        (cur_tap, cur_fp, cur_fp_calc, cur_ip, cur_ip_calc, cur_pedal, cur_ai, cur_cfa)
        != (sug_tap, fp_grade, fp_calc, sug_ip, sug_ip_calc, sug_pedal, ai, cfa)
    )

    return {
        'has_map': has_map, 'estimated_length': estimated, 'functioning_bypass': functioning_bypass,
        'tap': sug_tap, 'tap_short': tap_short[sug_tap], 'tap_label': tap_labels[sug_tap], 'active_tap': active_tap,
        'fp': fp_grade, 'fp_calc': fp_calc, 'fp_final': fp_final,
        'ip_by_tap': ip_by_tap, 'ip_paths': [ip_by_tap['at'], ip_by_tap['pt'], ip_by_tap['per']],
        'ip': sug_ip, 'ip_calc': sug_ip_calc, 'ip_final': sug_ip_final,
        'active_ip': active_ip_info['ip'], 'active_ip_calc': active_ip_info['ip_calc'],
        'glass': sug_glass, 'pedal': sug_pedal, 'active_pedal': pedal_for(active_tap),
        'ai': ai, 'cfa': cfa,
        'runoff_count': len(patent_tibials), 'patent_tibials': patent_tibials,
        'svs_runoff': svs_runoff, 'svs_runoff_text': _fmt_score(svs_runoff),
        'foot_runoff': foot_runoff, 'foot_runoff_text': _fmt_score(foot_runoff),
        'differs': differs,
    }


def compute(limb, measured=None):
    """Everything derived from one limb's entries (all tables above)."""
    measured = measured or {}
    m = {k: limb.get(k) if limb.get(k) is not None else measured.get(k) for k in ('abi', 'tbi', 'ap', 'tp')}
    sug_i, sug_from = suggest_ischemia(abi=m['abi'], ap=m['ap'], tp=m['tp'])
    out = {'measured': m, 'i_suggested': sug_i, 'i_from': sug_from, 'notes': []}

    # GLASS
    fp, ip = limb.get('fp'), limb.get('ip')
    if fp is not None and ip is not None:
        fp_f = min(4, fp + (1 if limb.get('fp_calc') else 0))
        ip_f = min(4, ip + (1 if limb.get('ip_calc') else 0))
        stage = GLASS_MATRIX[fp_f][ip_f]
        out.update({
            'fp_final': fp_f, 'ip_final': ip_f, 'glass': stage,
            'glass_text': GLASS_TEXT.get(stage, 'ცხრილში არ ეხება (FP 0 და IP 0).'),
        })
    if limb.get('ai') or limb.get('cfa') == 'B':
        out['inflow'] = ('AI ' + limb['ai'] if limb.get('ai') else 'AI —') + ' / CFA ' + (limb.get('cfa') or 'A')
        out['notes'].append('Inflow-ს დაავადება ცალკე ფასდება; GLASS ითვალისწინებს, რომ ის კორექტირებულია (ცხრ. 5.1).')

    # WIfI
    w, i, fi = limb.get('w'), limb.get('i'), limb.get('fi')
    if i is None:
        i = sug_i
        out['i_auto'] = i is not None
    if None not in (w, i, fi):
        stage = 5 if limb.get('unsalvageable') else WIFI_STAGE[(w, i, fi)]
        out.update({'w': w, 'i': i, 'fi': fi, 'wifi': stage, 'wifi_risk': WIFI_RISK[stage]})
        if stage <= 4:
            level = _BENEFIT[i][stage - 1]
            out.update({'benefit': level, 'benefit_label': BENEFIT_LABEL[level]})

    # PLAN
    stage, glass = out.get('wifi'), out.get('glass')
    if stage == 5:
        out['plan'] = 'unsalvageable'
        out['plan_label'] = 'კიდური გადარჩენადი არ არის (WIfI stage 5).'
    elif stage == 1:
        out['plan'] = 'none'
        out['plan_label'] = PLAN_LABEL['none'] + ' (WIfI stage 1).'
    elif stage and limb.get('risk') == 'high':
        out['plan'] = 'endo'
        i = out['i']
        if stage == 4 and i >= 2:
            text = 'მაღალი რისკი: შესთავაზეთ ენდოვასკულური, თუ ტექნიკურად შესაძლებელია (რეკ. 6.33).'
        elif stage in (2, 3) and i >= 2:
            text = 'მაღალი რისკი: განიხილეთ ენდოვასკულური (რეკ. 6.34).'
        elif i == 1:
            text = (
                'მაღალი რისკი, ზომიერი იშემია: განიხილეთ ენდოვასკულური, თუ ჭრილობა პროგრესირებს ან 4 კვირაში ≤50%-ით '
                'არ მცირდება (რეკ. 6.35–6.36).'
            )
        else:
            out['plan'] = 'none'
            text = 'იშემია მნიშვნელოვანი არ არის (ხარისხი 0).'
        if stage in (3, 4) and i >= 2 and glass == 'III':
            text += ' შერჩეულ შემთხვევებში — ღია ქირურგია (რეკ. 6.37).'
        out['plan_label'] = text
    elif stage and glass:
        if limb.get('vein') == 'no':
            out['plan'] = 'endo'
            out['plan_label'] = (
                'ვარგისი ვენის გარეშე: შესაძლებლობისას უპირატესია ენდოვასკულური; პროთეზი / ალოგრაფტი — '
                'მხოლოდ შერჩეულ შემთხვევებში (ნაწ. 6, რეკ. 6.41).'
            )
        else:
            key = PLAN[glass][stage - 1]
            out['plan'] = key
            out['plan_label'] = PLAN_LABEL[key] + ' — საშუალო რისკი, ვარგისი ვენა (სურ. 6.4).'
    if limb.get('pedal') == 'P2':
        out['notes'].append('P2: კოჭს სამიზნე არტერია არ კვეთს — WIfI 3–4-ზე შეიძლება იყოს „no-option“ (სურ. 6.4 P2-ს არ მოიცავს).')
    ivs = upgrade_limb(limb).get('interventions') or () if isinstance(limb, dict) else ()
    if any(iv['type'] == 'bypass' and iv['status'] in ('patent', 'stenosed') for iv in ivs):
        out['notes'].append(
            'კიდურზე ფუნქციონირებს შუნტი — გაითვალისწინეთ, რომ GLASS პირველადი ნატიური არტერიების '
            'შესაფასებლად არის შექმნილი (GVG ნაწ. 5).'
        )
    angio = angiosome_status(limb)
    if angio:
        out['angiosome'] = angio
    out['suggested'] = suggest_from_map(limb)
    return out
