"""View-ready intervention formatting, JS catalog JSON, segment display, and vector map wrappers."""
from .catalog import (
    _INLINE_TYPES,
    AMPUTATION_STATUS,
    AMPUTATION_SUBTYPES,
    ANGIOPLASTY_SUBTYPES,
    ANGIOSOME_SOURCE,
    BYPASS_CONDUIT_GROUPS,
    BYPASS_CONDUITS,
    BYPASS_CUFFS,
    BYPASS_SITES,
    BYPASS_STATUS,
    ENDARTERECTOMY_SUBTYPES,
    EXTRA_ORIGINS,
    INTERVENTION_TYPES,
    SEGMENT_GROUPS,
    SEGMENT_NEXT,
    SEGMENT_SHORT,
    SEGMENTS,
    STENOSIS,
    STENT_STATUS,
    STENT_SUBTYPES,
    THROMBECTOMY_SUBTYPES,
    VESSEL_INTERVENTION_STATUS,
)
from .schema import (
    _raw_stenosis,
    _remap_raw_segments,
    clean_interventions,
    clean_segments,
    format_partial_date,
    segment_path,
)


def interventions_view(interventions):
    """Human-readable Georgian intervention records for the case summary and presentation slide."""
    type_labels = dict(INTERVENTION_TYPES)
    conduit_labels = {code: label for items in BYPASS_CONDUITS.values() for code, label in items}
    group_labels = dict(BYPASS_CONDUIT_GROUPS)
    bypass_status_labels = dict(BYPASS_STATUS)
    bypass_site_labels = dict(BYPASS_SITES)
    stent_sub_labels = dict(STENT_SUBTYPES)
    stent_status_labels = dict(STENT_STATUS)
    pta_sub_labels = dict(ANGIOPLASTY_SUBTYPES)
    ea_sub_labels = dict(ENDARTERECTOMY_SUBTYPES)
    amp_sub_labels = dict(AMPUTATION_SUBTYPES)
    amp_status_labels = dict(AMPUTATION_STATUS)
    thromb_sub_labels = dict(THROMBECTOMY_SUBTYPES)
    vessel_status_labels = dict(VESSEL_INTERVENTION_STATUS)

    rows = []
    for iv in clean_interventions(interventions):
        kind = iv['type']
        detail_parts = []
        if kind == 'bypass':
            prox = SEGMENT_SHORT.get(iv['proximal'], iv['proximal'])
            dist = SEGMENT_SHORT.get(iv['distal'], iv['distal'])
            title = f'შუნტირება: {prox} → {dist}'
            if iv['conduit']:
                detail_parts.append(conduit_labels.get(iv['conduit'], iv['conduit']))
            elif iv['conduit_group'] != 'unspecified':
                detail_parts.append(group_labels.get(iv['conduit_group'], ''))
            else:
                detail_parts.append('კონდუიტი დაუზუსტებელია')
            if iv.get('cuff') == 'miller':
                detail_parts.append('Miller-ის მანჟეტი')
            status_label = bypass_status_labels.get(iv['status'], iv['status'])
            if iv['status'] == 'stenosed' and iv.get('site'):
                status_label += f' ({bypass_site_labels.get(iv["site"], iv["site"])})'
            tone = 'ok' if iv['status'] == 'patent' else ('warn' if iv['status'] == 'stenosed' else 'bad')
        elif kind == 'amputation':
            title = amp_sub_labels.get(iv['subtype'], type_labels[kind])
            status_label = amp_status_labels.get(iv['status'], iv['status'])
            tone = 'ok' if iv['status'] == 'healed' else 'bad'
        else:
            segs_txt = ', '.join(SEGMENT_SHORT.get(s, s) for s in iv['segments'])
            short_kind = {'angioplasty': 'ანგიოპლასტიკა', 'endarterectomy': 'ენდარტერექტომია'}.get(
                kind, type_labels[kind],
            )
            title = f'{short_kind}: {segs_txt}'
            sub_map = {
                'stent': stent_sub_labels, 'angioplasty': pta_sub_labels,
                'endarterectomy': ea_sub_labels, 'thrombectomy': thromb_sub_labels,
            }[kind]
            if iv.get('subtype') and iv['subtype'] != 'unspecified':
                detail_parts.append(sub_map.get(iv['subtype'], iv['subtype']))
            status_map = stent_status_labels if kind == 'stent' else vessel_status_labels
            status_label = status_map.get(iv['status'], iv['status'])
            tone = 'ok' if iv['status'] == 'patent' else ('bad' if iv['status'] == 'occluded' else 'warn')

        rows.append({
            'id': iv['id'],
            'type': kind,
            'title': title,
            'detail': ' · '.join(p for p in detail_parts if p),
            'date_display': format_partial_date(iv.get('performed_on')),
            'status_label': status_label,
            'tone': tone,
            'note': iv.get('note') or '',
        })
    return rows


def intervention_catalog():
    """Options and anatomical metadata passed once as JSON to staging.js (single source of truth)."""
    grouped = []
    for grp_key, grp_label in SEGMENT_GROUPS:
        items = [
            [key, SEGMENT_SHORT.get(key, key), name]
            for key, name, _short, _d, g, _w, _lab in SEGMENTS if g == grp_key
        ]
        grouped.append({'key': grp_key, 'label': grp_label, 'items': items})
    return {
        'types': list(INTERVENTION_TYPES),
        'conduit_groups': [g for g in BYPASS_CONDUIT_GROUPS if g[0] != 'unspecified'],
        'conduits': {k: list(v) for k, v in BYPASS_CONDUITS.items() if k != 'unspecified'},
        'cuffs': list(BYPASS_CUFFS),
        'extra_origins': list(EXTRA_ORIGINS),
        'bypass_status': list(BYPASS_STATUS),
        'bypass_sites': list(BYPASS_SITES),
        'subtypes': {
            'stent': [x for x in STENT_SUBTYPES if x[0] != 'unspecified'],
            'angioplasty': [x for x in ANGIOPLASTY_SUBTYPES if x[0] != 'unspecified'],
            'endarterectomy': [x for x in ENDARTERECTOMY_SUBTYPES if x[0] != 'unspecified'],
            'amputation': list(AMPUTATION_SUBTYPES),
            'thrombectomy': [x for x in THROMBECTOMY_SUBTYPES if x[0] != 'unspecified'],
        },
        'statuses': {
            'bypass': list(BYPASS_STATUS),
            'stent': list(STENT_STATUS),
            'angioplasty': list(VESSEL_INTERVENTION_STATUS),
            'endarterectomy': list(VESSEL_INTERVENTION_STATUS),
            'amputation': list(AMPUTATION_STATUS),
            'thrombectomy': list(VESSEL_INTERVENTION_STATUS),
        },
        'segment_groups': grouped,
        'segment_short': SEGMENT_SHORT,
        'segment_names': {s[0]: s[1] for s in SEGMENTS},
        'segment_order': [s[0] for s in SEGMENTS],
        'segment_next': {k: list(v) for k, v in SEGMENT_NEXT.items()},
    }


def _display_state(seg):
    st = _raw_stenosis(seg)
    if st == 'occlusion':
        return 'oc'
    if st in ('moderate', 'severe'):
        return 'st'
    if st == 'mild':
        return 'mild'
    return 'n'


def segment_view(segments, mirror=False, interventions=None):
    """Segment items with their CSS state/flag classes and clinical tooltips."""
    norm_segs = _remap_raw_segments(segments)
    cleaned_segs = clean_segments(norm_segs)
    rx_marked, by_marked = set(), set()
    for iv in clean_interventions(interventions):
        if iv['type'] in _INLINE_TYPES:
            rx_marked.update(iv['segments'])
        elif iv['type'] == 'bypass':
            by_marked.update(segment_path(iv['proximal'], iv['distal']))

    stenosis_labels = dict(STENOSIS)
    view = []
    for key, name, short, d, group, width, _label in SEGMENTS:
        raw_item = norm_segs.get(key, {})
        item = cleaned_segs.get(key, {})
        state = _display_state(raw_item)
        stenosis = _raw_stenosis(raw_item)
        flags = []
        calc = item.get('calc', 0)
        if calc >= 3 or item.get('coral') is True or raw_item.get('ca') is True:
            flags.append('ca')
        if raw_item.get('rx') is True or key in rx_marked:
            flags.append('rx')
        if raw_item.get('by') is True or key in by_marked:
            flags.append('by')
        classes = ['sg', f'sg-{state}'] + [f'sg-{f}' for f in flags]

        tip_parts = [name]
        if stenosis != 'none':
            tip_parts.append(stenosis_labels.get(stenosis, stenosis))
        if item.get('length_cm'):
            tip_parts.append(f'{item["length_cm"]} სმ')
        if calc:
            tip_parts.append(f'PACSS {calc}')
        view.append({
            'key': key, 'name': name, 'short': short, 'd': d, 'group': group, 'w': width,
            'state': state, 'stenosis': stenosis, 'calc': calc,
            'length_cm': item.get('length_cm'),
            'tooltip': ' · '.join(tip_parts),
            'flags': flags, 'css': ' '.join(classes),
        })
    return view


def map_context(segments, side, interventions=None):
    """Pure-vector surgical anatomy & 3-layer vessel map context; left leg is mirrored with upright labels."""
    from .. import artery_map

    cleaned_ivs = clean_interventions(interventions)
    cleaned_segs = clean_segments(segments)
    items = segment_view(segments, interventions=cleaned_ivs)
    return artery_map.build_map_context(SEGMENTS, items, cleaned_segs, cleaned_ivs, side, SEGMENT_NEXT)


def foot_context(segments, side, interventions=None, wound_angiosome=''):
    """Lateral-oblique pedal arch & 6 angiosome territories context for `_foot_map.html`."""
    from .. import artery_map

    cleaned_ivs = clean_interventions(interventions)
    cleaned_segs = clean_segments(segments)
    items = segment_view(segments, interventions=cleaned_ivs)
    angio = wound_angiosome if wound_angiosome in ANGIOSOME_SOURCE else ''
    return artery_map.build_foot_context(items, cleaned_segs, cleaned_ivs, side, angio)


def zoom_zones():
    from .. import artery_map

    return artery_map.ZOOM_ZONES
