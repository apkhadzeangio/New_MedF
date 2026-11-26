"""Formal Georgian lower-limb angiography protocol generator (unilateral and bilateral)."""
from .schema import clean, get_limb
from .suggestions import _fmt_score, compute
from .views_data import interventions_view

_STENOSIS_PROSE = {
    'mild': '20–49%-იანი სტენოზი',
    'moderate': '50–69%-იანი სტენოზი',
    'severe': '70–99%-იანი კრიტიკული სტენოზი',
    'occlusion': 'ოკლუზია',
}
_PACSS_PROSE = {
    1: 'ცალმხრივი კალციფიკაცია <5 სმ — PACSS 1',
    2: 'ცალმხრივი კალციფიკაცია ≥5 სმ — PACSS 2',
    3: 'ორმხრივი კალციფიკაცია <5 სმ — PACSS 3',
    4: 'მძიმე ორმხრივი კალციფიკაცია ≥5 სმ — PACSS 4',
}
_RECONST_PROSE = {
    'yes': 'დისტალური სეგმენტი აღდგება კოლატერალებით',
    'delayed': 'დისტალური სეგმენტის დაგვიანებული აღდგენით',
    'none': 'დისტალური აღდგენის გარეშე',
}
_MORPH_PROSE = {
    'eccentric': 'ექსცენტრული ფოლაქი',
    'concentric': 'კონცენტრული ფოლაქი',
}
_TAP_PROSE = {
    'at': 'წინა წვივის არტერია (AT)',
    'pt': 'უკანა წვივის არტერია (PT)',
    'per': 'მცირე წვივის არტერია (PER)',
}


def _seg_is_abnormal(item):
    if not item:
        return False
    st = item.get('stenosis') or 'none'
    if st != 'none':
        return True
    return bool(
        (item.get('calc') or 0) > 0
        or item.get('coral')
        or item.get('thrombus')
        or item.get('aneurysm')
        or item.get('ulcerated')
        or item.get('small_caliber')
    )


def _format_lesion_ka(item):
    st = item.get('stenosis') or 'none'
    if st == 'occlusion' and item.get('flush'):
        head = 'სათავის (flush) ოკლუზია'
    elif st in _STENOSIS_PROSE:
        head = _STENOSIS_PROSE[st]
    else:
        head = 'სანათური გამავალია'

    details = []
    if item.get('length_cm') is not None:
        details.append(f'სიგრძე {_fmt_score(float(item["length_cm"]))} სმ')
    if item.get('morph') in _MORPH_PROSE:
        details.append(_MORPH_PROSE[item['morph']])
    calc = item.get('calc') or 0
    if calc in _PACSS_PROSE:
        details.append(_PACSS_PROSE[calc])
    if item.get('coral'):
        details.append('მარჯნისებრი კალციფიკაცია')
    if item.get('thrombus'):
        details.append('სანათურში თრომბული მასებით')
    if item.get('aneurysm'):
        details.append('ანევრიზმული გაგანიერება')
    if item.get('ulcerated'):
        details.append('დაწყლულებული ფოლაქი')
    if item.get('small_caliber'):
        details.append('მცირე კალიბრი (<6 მმ)')
    if item.get('trifurcation'):
        details.append('ტრიფურკაციაზე გავრცელებით')
    if st == 'occlusion' and item.get('reconstitution') in _RECONST_PROSE:
        details.append(_RECONST_PROSE[item['reconstitution']])

    return f'{head} ({", ".join(details)})' if details else head


def _merge_segment_items(items):
    merged = {'stenosis': items[0].get('stenosis') or 'none'}
    lengths = [float(it['length_cm']) for it in items if it.get('length_cm') is not None]
    if lengths:
        merged['length_cm'] = round(sum(lengths), 1)
    calcs = [int(it.get('calc') or 0) for it in items]
    if max(calcs, default=0) > 0:
        merged['calc'] = max(calcs)
    morphs = {it.get('morph') for it in items if it.get('morph')}
    if len(morphs) == 1:
        merged['morph'] = next(iter(morphs))
    if items[0].get('flush'):
        merged['flush'] = True
    for it in reversed(items):
        if it.get('reconstitution'):
            merged['reconstitution'] = it['reconstitution']
            break
    for flag in ('coral', 'thrombus', 'aneurysm', 'ulcerated', 'small_caliber', 'trifurcation'):
        if any(it.get(flag) for it in items):
            merged[flag] = True
    return merged


def _join_ka(parts):
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0]
    return ', '.join(parts[:-1]) + ' და ' + parts[-1]


def _describe_thirds_ka(segs, keys, part_names, unit_word, normal_whole):
    items = [segs.get(k) or {} for k in keys]
    abn = [_seg_is_abnormal(it) for it in items]
    if not any(abn):
        return normal_whole

    stenoses = [it.get('stenosis') or 'none' for it in items]
    if all(abn) and len(set(stenoses)) == 1 and stenoses[0] != 'none':
        return f'მთელ სიგრძეზე — {_format_lesion_ka(_merge_segment_items(items))}.'

    clauses = []
    normal_parts = []
    idx = 0
    while idx < len(keys):
        if not abn[idx]:
            normal_parts.append(part_names[idx])
            idx += 1
            continue
        end = idx + 1
        while (
            end < len(keys) and abn[end]
            and (items[end].get('stenosis') or 'none') == (items[idx].get('stenosis') or 'none')
            and (items[end].get('stenosis') or 'none') != 'none'
            and not items[end].get('flush')
        ):
            end += 1
        span_names = _join_ka(part_names[idx:end])
        merged = _merge_segment_items(items[idx:end])
        clauses.append(f'{span_names} {unit_word} — {_format_lesion_ka(merged)}')
        idx = end

    if normal_parts:
        clauses.append(f'{_join_ka(normal_parts)} {unit_word} გამავალია')
    return '; '.join(clauses) + '.'


def _describe_inflow_lines_ka(segs):
    lines = []
    iliac_defs = (
        ('cia', 'საერთო თეძოს არტერია', 'საერთო'),
        ('eia', 'გარეთა თეძოს არტერია', 'გარეთა'),
        ('iia', 'შიგნითა თეძოს არტერია', 'შიგნითა'),
    )
    iliac_abn = [(full, segs[k]) for k, full, _ in iliac_defs if _seg_is_abnormal(segs.get(k))]
    if not iliac_abn:
        lines.append('• თეძოს არტერიები: საერთო, გარეთა და შიგნითა თეძოს არტერიები გამავალია, მნიშვნელოვანი სტენოზის გარეშე.')
    else:
        iliac_norm = [short for k, _, short in iliac_defs if not _seg_is_abnormal(segs.get(k))]
        parts = [f'{full} — {_format_lesion_ka(it)}' for full, it in iliac_abn]
        if iliac_norm:
            noun = 'თეძოს არტერია გამავალია' if len(iliac_norm) == 1 else 'თეძოს არტერიები გამავალია'
            parts.append(f'{_join_ka(iliac_norm)} {noun}')
        lines.append('• თეძოს არტერიები: ' + '; '.join(parts) + '.')

    fem_defs = (
        ('cfa', 'საერთო ბარძაყის არტერია'),
        ('pfa', 'ბარძაყის ღრმა არტერია'),
    )
    fem_abn = [(full, segs[k]) for k, full in fem_defs if _seg_is_abnormal(segs.get(k))]
    if not fem_abn:
        lines.append('• ბარძაყის საერთო და ღრმა არტერიები: გამავალია, მნიშვნელოვანი სტენოზის გარეშე.')
    else:
        parts = [f'{full} — {_format_lesion_ka(it)}' for full, it in fem_abn]
        for k, full in fem_defs:
            if not _seg_is_abnormal(segs.get(k)):
                parts.append(f'{full} გამავალია')
        lines.append('• ბარძაყის საერთო და ღრმა არტერიები: ' + '; '.join(parts) + '.')
    return lines


def _describe_distal_lines_ka(segs, thirds):
    lines = []
    crural_keys = ('tpt', 'at1', 'at2', 'at3', 'pt1', 'pt2', 'pt3', 'per1', 'per2', 'per3')
    if not any(_seg_is_abnormal(segs.get(k)) for k in crural_keys):
        lines.append('• წვივის არტერიები: წვივ-მცირე წვივის ღერო (TPT), წინა წვივის, უკანა წვივის და მცირე წვივის არტერიები მთელ სიგრძეზე გამავალია.')
    else:
        c_parts = []
        if _seg_is_abnormal(segs.get('tpt')):
            c_parts.append(f'წვივ-მცირე წვივის ღერო (TPT) — {_format_lesion_ka(segs["tpt"])}')
        else:
            c_parts.append('წვივ-მცირე წვივის ღერო (TPT) გამავალია')
        for v_title, v_keys in (
            ('წინა წვივის არტერია', ('at1', 'at2', 'at3')),
            ('უკანა წვივის არტერია', ('pt1', 'pt2', 'pt3')),
            ('მცირე წვივის არტერია', ('per1', 'per2', 'per3')),
        ):
            desc = _describe_thirds_ka(segs, v_keys, thirds, 'მესამედში', 'მთელ სიგრძეზე გამავალია.')
            c_parts.append(f'{v_title}: {desc.rstrip(".")}')
        lines.append('• წვივის არტერიები: ' + '; '.join(c_parts) + '.')

    foot_keys = ('dp', 'mpa', 'lpa', 'arch')
    if not any(_seg_is_abnormal(segs.get(k)) for k in foot_keys):
        lines.append('• ტერფის არტერიები: ტერფის ზურგის (DP), მედიალური (MPA) და ლატერალური პლანტარული (LPA) არტერიები გამავალია, ტერფის რკალი ინტაქტურია.')
    else:
        f_parts = []
        foot_vessels = (
            ('dp', 'ტერფის ზურგის არტერია (DP)', 'ტერფის ზურგის (DP)'),
            ('mpa', 'მედიალური პლანტარული არტერია (MPA)', 'მედიალური პლანტარული (MPA)'),
            ('lpa', 'ლატერალური პლანტარული არტერია (LPA)', 'ლატერალური პლანტარული (LPA)'),
        )
        f_abn = [(full, segs[k]) for k, full, _ in foot_vessels if _seg_is_abnormal(segs.get(k))]
        f_norm = [short for k, _, short in foot_vessels if not _seg_is_abnormal(segs.get(k))]
        for full, it in f_abn:
            f_parts.append(f'{full} — {_format_lesion_ka(it)}')
        if f_norm:
            noun = 'არტერია გამავალია' if len(f_norm) == 1 else 'არტერიები გამავალია'
            f_parts.append(f'{_join_ka(f_norm)} {noun}')
        if _seg_is_abnormal(segs.get('arch')):
            arch_st = (segs['arch'].get('stenosis') or 'none')
            if arch_st == 'occlusion':
                f_parts.append('ტერფის რკალი შეკრული არ არის (ოკლუზირებულია)')
            else:
                f_parts.append(f'ტერფის რკალი — {_format_lesion_ka(segs["arch"])}')
        else:
            f_parts.append('ტერფის რკალი ინტაქტურია')
        lines.append('• ტერფის არტერიები: ' + '; '.join(f_parts) + '.')
    return lines


def _limb_body_lines_ka(segs, ivs):
    thirds = ('პროქსიმალურ', 'შუა', 'დისტალურ')
    lines = _describe_inflow_lines_ka(segs)

    sfa_text = _describe_thirds_ka(
        segs, ('sfa1', 'sfa2', 'sfa3'), thirds, 'მესამედში',
        'მთელ სიგრძეზე გამავალია, ჰემოდინამიკურად მნიშვნელოვანი სტენოზის გარეშე.',
    )
    lines.append(f'• ზედაპირული ბარძაყის არტერია: {sfa_text}')

    pop_text = _describe_thirds_ka(
        segs, ('pop1', 'pop2', 'pop3'), ('P1 (მუხლზემოთა)', 'P2 (სახსრის დონის)', 'P3 (მუხლქვემოთა)'),
        'სეგმენტში', 'P1, P2 და P3 სეგმენტები გამავალია, მნიშვნელოვანი სტენოზის გარეშე.',
    )
    lines.append(f'• მუხლქვეშა არტერია: {pop_text}')

    lines.extend(_describe_distal_lines_ka(segs, thirds))

    if ivs:
        iv_texts = []
        for row in interventions_view(ivs):
            bits = [row['title']]
            meta = [x for x in (row['detail'], row['date_display']) if x]
            if meta:
                bits.append(f'({", ".join(meta)})')
            if row['status_label']:
                bits.append(f'— {row["status_label"]}')
            if row['note']:
                bits.append(f'[{row["note"]}]')
            iv_texts.append(' '.join(bits))
        lines.append('• წარსული ჩარევები: ' + '; '.join(iv_texts) + '.')

    return lines


def _summarize_vessel_lesion_ka(segs, keys, vessel_genitive, part_labels=None):
    items = [segs.get(k) or {} for k in keys]
    abn_idx = [i for i, it in enumerate(items) if _seg_is_abnormal(it)]
    if not abn_idx:
        return None
    rank = {'occlusion': 4, 'severe': 3, 'moderate': 2, 'mild': 1, 'none': 0}
    worst_st = max((items[i].get('stenosis') or 'none' for i in abn_idx), key=lambda s: rank.get(s, 0))
    if worst_st == 'occlusion':
        word = 'ოკლუზია'
    elif worst_st == 'severe':
        word = '70–99%-იანი კრიტიკული სტენოზი'
    elif worst_st == 'moderate':
        word = '50–69%-იანი სტენოზი'
    elif worst_st == 'mild':
        word = '20–49%-იანი სტენოზი'
    elif any(items[i].get('aneurysm') for i in abn_idx):
        word = 'ანევრიზმა'
    else:
        word = 'ათეროსკლეროზული კალციფიკაცია'

    if part_labels and len(keys) > 1 and len(abn_idx) < len(keys):
        loc = f' ({_join_ka([part_labels[i] for i in abn_idx])})'
    else:
        loc = ''
    return f'{vessel_genitive} {word}{loc}'


def _limb_conclusion_ka(cleaned, side_adj, include_aorta=True):
    segs = cleaned['segments']
    ivs = cleaned['interventions']
    r = compute(cleaned)
    sug = r['suggested']

    findings = []
    if include_aorta:
        ao = _summarize_vessel_lesion_ka(segs, ('aorta',), 'ინფრარენული აორტის')
        if ao:
            findings.append(ao)
    for k, gen in (
        ('cia', 'საერთო თეძოს არტერიის'),
        ('eia', 'გარეთა თეძოს არტერიის'),
        ('iia', 'შიგნითა თეძოს არტერიის'),
        ('cfa', 'საერთო ბარძაყის არტერიის'),
        ('pfa', 'ბარძაყის ღრმა არტერიის'),
    ):
        s = _summarize_vessel_lesion_ka(segs, (k,), gen)
        if s:
            findings.append(s)

    thirds_lbl = ('პროქს.', 'შუა', 'დისტ.')
    for keys, gen, lbls in (
        (('sfa1', 'sfa2', 'sfa3'), 'ზედაპირული ბარძაყის არტერიის', thirds_lbl),
        (('pop1', 'pop2', 'pop3'), 'მუხლქვეშა არტერიის', ('P1', 'P2', 'P3')),
        (('tpt',), 'წვივ-მცირე წვივის ღეროს (TPT)', None),
        (('at1', 'at2', 'at3'), 'წინა წვივის არტერიის', thirds_lbl),
        (('pt1', 'pt2', 'pt3'), 'უკანა წვივის არტერიის', thirds_lbl),
        (('per1', 'per2', 'per3'), 'მცირე წვივის არტერიის', thirds_lbl),
        (('dp',), 'ტერფის ზურგის არტერიის', None),
        (('mpa',), 'მედიალური პლანტარული არტერიის', None),
        (('lpa',), 'ლატერალური პლანტარული არტერიის', None),
        (('arch',), 'ტერფის რკალის', None),
    ):
        s = _summarize_vessel_lesion_ka(segs, keys, gen, lbls)
        if s:
            findings.append(s)

    iv_notes = [f'{row["title"]} ({row["status_label"]})' for row in interventions_view(ivs) if row['status_label']]

    if not findings and not iv_notes:
        return f'{side_adj} ქვედა კიდურის მაგისტრალური არტერიები გამავალია ჰემოდინამიკურად მნიშვნელოვანი სტენოზისა და ოკლუზიის გარეშე.'

    parts = []
    if findings:
        has_occ = any((segs.get(k, {}).get('stenosis') == 'occlusion') for k in segs if include_aorta or k != 'aorta')
        has_st = any(
            (segs.get(k, {}).get('stenosis') in ('mild', 'moderate', 'severe'))
            for k in segs if include_aorta or k != 'aorta'
        )
        if has_occ and has_st:
            kind = 'ოკლუზიურ-სტენოზური დაზიანება'
        elif has_occ:
            kind = 'ოკლუზიური დაზიანება'
        elif has_st:
            kind = 'სტენოზური დაზიანება'
        else:
            kind = 'სისხლძარღვოვანი ცვლილებები'
        parts.append(f'{side_adj} ქვედა კიდურის არტერიების {kind}: {", ".join(findings)}.')
    if iv_notes:
        parts.append(f'წარსული ჩარევები: {", ".join(iv_notes)}.')

    if findings:
        pat = ', '.join(sug['patent_tibials'])
        if sug['runoff_count'] == 0:
            runoff = 'წვივის არცერთი მაგისტრალური არტერია მთელ სიგრძეზე გამავალი არ არის'
        else:
            runoff = f'წვივის გამავალი არტერიები — {sug["runoff_count"]}/3 ({pat})'
        arch_occ = (segs.get('arch') or {}).get('stenosis') == 'occlusion'
        arch_txt = 'ტერფის რკალი შეკრული არ არის' if arch_occ else 'ტერფის რკალი ინტაქტურია'
        parts.append(f'{runoff}, {arch_txt}.')

    glass = r.get('glass') or (sug.get('glass') if sug.get('has_map') else None)
    fp_val = r.get('fp_final') if r.get('fp_final') is not None else (sug.get('fp_final') if sug.get('has_map') else None)
    ip_val = r.get('ip_final') if r.get('ip_final') is not None else (sug.get('ip_final') if sug.get('has_map') else None)
    pedal_val = cleaned.get('pedal') or (sug.get('pedal') if sug.get('has_map') else '')
    tap_val = cleaned.get('tap') or (sug.get('tap') if sug.get('has_map') else '')
    if glass:
        g_bits = []
        if fp_val is not None and ip_val is not None:
            g_bits.append(f'FP {fp_val}, IP {ip_val}')
        if pedal_val:
            g_bits.append(pedal_val)
        if tap_val in _TAP_PROSE:
            g_bits.append(f'TAP — {_TAP_PROSE[tap_val]}')
        extra = f' ({"; ".join(g_bits)})' if g_bits else ''
        parts.append(f'GLASS სტადია {glass}{extra}.')

    return ' '.join(parts)


def angiography_protocol(limb, side, today=None):
    """Formal Georgian angiography protocol (anatomical description + conclusion) for one limb."""
    cleaned = clean(limb if isinstance(limb, dict) else {}, today=today)
    side_adj = 'მარჯვენა' if side == 'R' else 'მარცხენა'
    segs = cleaned['segments']

    if _seg_is_abnormal(segs.get('aorta')):
        aorta_line = f'• ინფრარენული აორტა: {_format_lesion_ka(segs["aorta"])}.'
    else:
        aorta_line = '• ინფრარენული აორტა: გამავალია, კონტურები სწორი, მნიშვნელოვანი სტენოზის გარეშე.'

    body = [aorta_line, *_limb_body_lines_ka(segs, cleaned['interventions'])]
    conclusion = _limb_conclusion_ka(cleaned, side_adj, include_aorta=True)
    return (
        f'ქვედა კიდურის არტერიოგრაფია — {side_adj} კიდური\n\n'
        + '\n'.join(body)
        + f'\n\nდასკვნა: {conclusion}'
    )


def angiography_protocol_bilateral(case_staging, today=None):
    """Combined formal Georgian angiography protocol when both lower limbs have angiographic data."""
    r_limb = get_limb(case_staging, 'R', today=today)
    l_limb = get_limb(case_staging, 'L', today=today)
    aorta_item = r_limb['segments'].get('aorta') or l_limb['segments'].get('aorta')
    if _seg_is_abnormal(aorta_item):
        aorta_line = f'• ინფრარენული აორტა: {_format_lesion_ka(aorta_item)}.'
    else:
        aorta_line = '• ინფრარენული აორტა: გამავალია, კონტურები სწორი, მნიშვნელოვანი სტენოზის გარეშე.'

    r_body = '\n'.join(_limb_body_lines_ka(r_limb['segments'], r_limb['interventions']))
    l_body = '\n'.join(_limb_body_lines_ka(l_limb['segments'], l_limb['interventions']))

    conc = []
    if _seg_is_abnormal(aorta_item):
        ao_sum = _summarize_vessel_lesion_ka({'aorta': aorta_item}, ('aorta',), 'ინფრარენული აორტის')
        if ao_sum:
            conc.append(f'— აორტა: {ao_sum}.')
    conc.append(f'— მარჯვენა კიდური: {_limb_conclusion_ka(r_limb, "მარჯვენა", include_aorta=False)}')
    conc.append(f'— მარცხენა კიდური: {_limb_conclusion_ka(l_limb, "მარცხენა", include_aorta=False)}')

    return (
        'ქვედა კიდურების არტერიოგრაფია\n\n'
        f'{aorta_line}\n\n'
        f'მარჯვენა ქვედა კიდური:\n{r_body}\n\n'
        f'მარცხენა ქვედა კიდური:\n{l_body}\n\n'
        'დასკვნა:\n' + '\n'.join(conc)
    )
