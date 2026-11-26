"""Schema v2 normalization, v1->v2 lossless upgrades, lumen sync, and limb persistence helpers."""
import collections
import datetime
import json

from .catalog import (
    _CONDUIT_TO_GROUP,
    _ID_RE,
    _INLINE_TYPES,
    _LEGACY_STENOSIS,
    _SEGMENT_ADJ,
    _SEGMENT_RANK,
    AMPUTATION_STATUS,
    AMPUTATION_SUBTYPES,
    ANGIOPLASTY_SUBTYPES,
    ANGIOSOMES,
    BYPASS_CONDUIT_GROUPS,
    BYPASS_CONDUITS,
    BYPASS_ORIGINS,
    BYPASS_STATUS,
    ENDARTERECTOMY_SUBTYPES,
    INTERVENTION_TYPES,
    LEGACY_SEGMENT_MAP,
    MAX_INTERVENTIONS,
    RECONSTITUTION,
    SCHEMA_VERSION,
    SEGMENT_FLAGS,
    SEGMENT_KEYS,
    SEGMENT_NEXT,
    SEGMENT_ORDER,
    SHARED_SEGMENTS,
    STENOSIS,
    STENT_STATUS,
    STENT_SUBTYPES,
    THROMBECTOMY_SUBTYPES,
    VESSEL_INTERVENTION_STATUS,
)
from .tables import INFLOW_AI, INFLOW_CFA, PEDAL, RISK, TAP, VEIN


def _int(value, low, high):
    if isinstance(value, bool):
        return None
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return v if low <= v <= high else None


def _float(value, low, high):
    if isinstance(value, bool):
        return None
    try:
        v = float(str(value).replace(',', '.'))
    except (TypeError, ValueError):
        return None
    return v if low <= v <= high else None


def _bmp_line(value, limit=200):
    text = ''.join(ch for ch in str(value or '') if ord(ch) <= 0xFFFF)
    return ' '.join(text.split())[:limit]


def _sort_segments(keys):
    seen = set()
    valid = []
    for k in keys:
        k = LEGACY_SEGMENT_MAP.get(k, k)
        if k in SEGMENT_KEYS and k not in seen:
            seen.add(k)
            valid.append(k)
    return sorted(valid, key=lambda k: (_SEGMENT_RANK[k], SEGMENT_ORDER[k]))


def segment_path(proximal, distal):
    """Directed anatomical path from proximal to distal segment (for bypass highlighting)."""
    proximal = LEGACY_SEGMENT_MAP.get(proximal, proximal)
    distal = LEGACY_SEGMENT_MAP.get(distal, distal)
    if distal not in SEGMENT_KEYS:
        return []
    if proximal not in SEGMENT_KEYS:
        return [distal]
    if proximal == distal:
        return [proximal]
    queue = collections.deque([[proximal]])
    visited = {proximal}
    while queue:
        path = queue.popleft()
        for nxt in SEGMENT_NEXT[path[-1]]:
            if nxt == distal:
                return path + [nxt]
            if nxt not in visited:
                visited.add(nxt)
                queue.append(path + [nxt])
    return _sort_segments([proximal, distal])


def clean_partial_date(value, today=None):
    """Accepts YYYY, YYYY-MM, YYYY-MM-DD (or MM.YYYY / DD.MM.YYYY) in 1950..today; else ''."""
    raw = str(value or '').strip().replace('/', '.')
    if not raw:
        return ''
    today = today or datetime.date.today()
    if '.' in raw:
        dots = raw.split('.')
        if len(dots) == 2 and all(p.isdigit() for p in dots) and len(dots[1]) == 4:
            raw = f'{int(dots[1]):04d}-{int(dots[0]):02d}'
        elif len(dots) == 3 and all(p.isdigit() for p in dots) and len(dots[2]) == 4:
            raw = f'{int(dots[2]):04d}-{int(dots[1]):02d}-{int(dots[0]):02d}'
        else:
            return ''
    parts = raw.split('-')
    if len(parts) == 1 and len(parts[0]) == 4 and parts[0].isdigit():
        year = int(parts[0])
        return raw if 1950 <= year <= today.year else ''
    if len(parts) == 2 and len(parts[0]) == 4 and 1 <= len(parts[1]) <= 2 and all(p.isdigit() for p in parts):
        year, month = int(parts[0]), int(parts[1])
        if 1950 <= year and 1 <= month <= 12 and (year, month) <= (today.year, today.month):
            return f'{year:04d}-{month:02d}'
        return ''
    if len(parts) == 3 and len(parts[0]) == 4 and all(p.isdigit() and 1 <= len(p) <= 2 for p in parts[1:]):
        try:
            dt = datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            return ''
        return dt.isoformat() if 1950 <= dt.year and dt <= today else ''
    return ''


def format_partial_date(value):
    """Canonical YYYY / YYYY-MM / YYYY-MM-DD -> Georgian clinic display (YYYY / MM.YYYY / DD.MM.YYYY)."""
    parts = str(value or '').strip().split('-')
    if len(parts) == 3:
        return f'{parts[2]}.{parts[1]}.{parts[0]}'
    if len(parts) == 2:
        return f'{parts[1]}.{parts[0]}'
    return parts[0] if parts and parts[0] else ''


def _remap_raw_segments(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    if not isinstance(raw, dict):
        return {}
    remapped = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        target = LEGACY_SEGMENT_MAP.get(key, key)
        if target not in SEGMENT_KEYS:
            continue
        if target in remapped and key in LEGACY_SEGMENT_MAP:
            continue                        # explicit 'lpa' takes precedence over legacy 'plantar'
        remapped[target] = dict(value)
    return remapped


def _raw_stenosis(seg):
    st = seg.get('stenosis')
    if st in {k for k, _ in STENOSIS}:
        return st
    return _LEGACY_STENOSIS.get(seg.get('s'), 'none')


def clean_segments(raw):
    """Normalize segment dicts (v1 or v2) -> canonical v2 segment dicts with known keys/values only."""
    out = {}
    reconst_opts = {k for k, _ in RECONSTITUTION}
    morph_opts = {'eccentric', 'concentric'}
    flag_keys = tuple(k for k, _ in SEGMENT_FLAGS)
    for key, value in _remap_raw_segments(raw).items():
        item = {}
        stenosis = _raw_stenosis(value)
        if stenosis != 'none':
            item['stenosis'] = stenosis
        has_aneurysm = value.get('aneurysm') is True
        if stenosis != 'none' or has_aneurysm:
            length = _float(value.get('length_cm'), 0.1, 50.0)
            if length is not None:
                item['length_cm'] = round(length, 1)
        if 'calc' in value and value.get('calc') not in (None, ''):
            calc = _int(value.get('calc'), 0, 4)
        else:
            calc = 3 if value.get('ca') is True else None
        if calc:
            item['calc'] = calc
        if stenosis == 'occlusion':
            if value.get('flush') is True:
                item['flush'] = True
            if value.get('reconstitution') in reconst_opts:
                item['reconstitution'] = value['reconstitution']
        if stenosis != 'none' and value.get('morph') in morph_opts:
            item['morph'] = value['morph']
        for flag in flag_keys:
            if value.get(flag) is True:
                item[flag] = True
        if item:
            out[key] = item
    return out


_VALID_IV_SETS = {
    'types': {k for k, _ in INTERVENTION_TYPES},
    'conduit_groups': {k for k, _ in BYPASS_CONDUIT_GROUPS},
    'bypass_statuses': {k for k, _ in BYPASS_STATUS},
    'bypass_sites': {'proximal', 'body', 'distal'},
    'stent_subtypes': {k for k, _ in STENT_SUBTYPES},
    'stent_statuses': {k for k, _ in STENT_STATUS},
    'pta_subtypes': {k for k, _ in ANGIOPLASTY_SUBTYPES},
    'ea_subtypes': {k for k, _ in ENDARTERECTOMY_SUBTYPES},
    'amp_subtypes': {k for k, _ in AMPUTATION_SUBTYPES},
    'amp_statuses': {k for k, _ in AMPUTATION_STATUS},
    'thromb_subtypes': {k for k, _ in THROMBECTOMY_SUBTYPES},
    'vessel_statuses': {k for k, _ in VESSEL_INTERVENTION_STATUS},
}


def _clean_single_intervention(entry, raw_id, performed_on, note):
    kind = entry.get('type')
    if kind == 'bypass':
        proximal = LEGACY_SEGMENT_MAP.get(entry.get('proximal'), entry.get('proximal'))
        distal = LEGACY_SEGMENT_MAP.get(entry.get('distal'), entry.get('distal'))
        if proximal not in BYPASS_ORIGINS or distal not in SEGMENT_KEYS:
            return None
        conduit = entry.get('conduit') or ''
        group = entry.get('conduit_group')
        if group not in _VALID_IV_SETS['conduit_groups']:
            group = _CONDUIT_TO_GROUP.get(conduit, 'unspecified')
        allowed_conduits = {k for k, _ in BYPASS_CONDUITS[group]}
        if conduit not in allowed_conduits:
            conduit = ''
        status = entry.get('status') if entry.get('status') in _VALID_IV_SETS['bypass_statuses'] else 'patent'
        site = entry.get('site') if status == 'stenosed' and entry.get('site') in _VALID_IV_SETS['bypass_sites'] else ''
        return {
            'id': raw_id, 'type': 'bypass',
            'conduit_group': group, 'conduit': conduit,
            'cuff': 'miller' if entry.get('cuff') == 'miller' else '',
            'proximal': proximal, 'distal': distal,
            'performed_on': performed_on, 'status': status, 'site': site, 'note': note,
        }
    if kind == 'amputation':
        subtype = entry.get('subtype')
        if subtype not in _VALID_IV_SETS['amp_subtypes']:
            return None
        status = entry.get('status') if entry.get('status') in _VALID_IV_SETS['amp_statuses'] else 'healed'
        return {
            'id': raw_id, 'type': 'amputation', 'subtype': subtype,
            'performed_on': performed_on, 'status': status, 'note': note,
        }
    segs = _sort_segments(entry.get('segments') or []) if isinstance(entry.get('segments'), (list, tuple)) else []
    if not segs:
        return None
    if kind == 'stent':
        subtype = entry.get('subtype') if entry.get('subtype') in _VALID_IV_SETS['stent_subtypes'] else 'unspecified'
        status = entry.get('status') if entry.get('status') in _VALID_IV_SETS['stent_statuses'] else 'patent'
    elif kind == 'angioplasty':
        subtype = entry.get('subtype') if entry.get('subtype') in _VALID_IV_SETS['pta_subtypes'] else 'unspecified'
        status = entry.get('status') if entry.get('status') in _VALID_IV_SETS['vessel_statuses'] else 'patent'
    elif kind == 'endarterectomy':
        subtype = entry.get('subtype') if entry.get('subtype') in _VALID_IV_SETS['ea_subtypes'] else 'unspecified'
        status = entry.get('status') if entry.get('status') in _VALID_IV_SETS['vessel_statuses'] else 'patent'
    else:                               # thrombectomy
        subtype = entry.get('subtype') if entry.get('subtype') in _VALID_IV_SETS['thromb_subtypes'] else 'unspecified'
        raw_st = 'occluded' if entry.get('status') == 'reoccluded' else entry.get('status')
        status = raw_st if raw_st in _VALID_IV_SETS['vessel_statuses'] else 'patent'
    return {
        'id': raw_id, 'type': kind, 'subtype': subtype, 'segments': segs,
        'performed_on': performed_on, 'status': status, 'note': note,
    }


def clean_interventions(raw, today=None):
    """Validate structured prior interventions list (max MAX_INTERVENTIONS items)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    used_ids = set()
    for entry in raw:
        if len(out) >= MAX_INTERVENTIONS:
            break
        if not isinstance(entry, dict) or entry.get('type') not in _VALID_IV_SETS['types']:
            continue
        raw_id = str(entry.get('id') or '').strip()
        if not _ID_RE.match(raw_id) or raw_id in used_ids:
            idx = len(out) + 1
            while f'iv{idx}' in used_ids:
                idx += 1
            raw_id = f'iv{idx}'
        performed_on = clean_partial_date(entry.get('performed_on'), today=today)
        note = _bmp_line(entry.get('note'), 200)
        item = _clean_single_intervention(entry, raw_id, performed_on, note)
        if item is None:
            continue
        used_ids.add(raw_id)
        out.append(item)
    return out


def _legacy_rx_components(rx_keys):
    """Group legacy rx=True segments into connected anatomical chains (ordered proximal -> distal)."""
    remaining = set(rx_keys)
    components = []
    for start in _sort_segments(rx_keys):
        if start not in remaining:
            continue
        comp = []
        queue = collections.deque([start])
        remaining.remove(start)
        while queue:
            cur = queue.popleft()
            comp.append(cur)
            for nb in _SEGMENT_ADJ[cur]:
                if nb in remaining:
                    remaining.remove(nb)
                    queue.append(nb)
        components.append(_sort_segments(comp))
    return components


def sync_interventions_lumen(segments, interventions):
    """Keep segment lumen consistent with the latest in-line intervention (stent, PTA, patch, thrombectomy)."""
    out = {k: dict(v) for k, v in (segments or {}).items()}
    inline = [iv for iv in (interventions or []) if iv.get('type') in _INLINE_TYPES]
    if not inline:
        return out

    # Order dated procedures chronologically while keeping undated procedures at their list positions
    dated_slots = [i for i, iv in enumerate(inline) if iv.get('performed_on')]
    dated_sorted = sorted((inline[i] for i in dated_slots), key=lambda iv: iv['performed_on'])
    ordered = list(inline)
    for slot, iv in zip(dated_slots, dated_sorted, strict=True):
        ordered[slot] = iv

    latest_for_seg = {}
    for iv in ordered:
        for seg in iv.get('segments') or ():
            latest_for_seg[seg] = iv

    for iv in ordered:
        active = [s for s in (iv.get('segments') or ()) if latest_for_seg.get(s) is iv]
        if not active:
            continue
        status = iv.get('status')
        if status == 'patent':
            for s in active:
                cur = out.get(s)
                if cur and cur.get('stenosis') in ('moderate', 'severe', 'occlusion'):
                    cur.pop('stenosis', None)
                    cur.pop('flush', None)
                    cur.pop('reconstitution', None)
                    cur.pop('morph', None)
                    if not cur.get('aneurysm'):
                        cur.pop('length_cm', None)
                    if not cur:
                        out.pop(s, None)
        elif status in ('isr_1', 'isr_2', 'restenosed'):
            for s in active:
                if out.get(s, {}).get('stenosis') == 'occlusion':
                    out[s]['stenosis'] = 'severe'
                    out[s].pop('flush', None)
                    out[s].pop('reconstitution', None)
            if not any(out.get(s, {}).get('stenosis') in ('moderate', 'severe') for s in active):
                for s in active:
                    out.setdefault(s, {})['stenosis'] = 'severe'
        elif status == 'occluded':
            if not any(out.get(s, {}).get('stenosis') == 'occlusion' for s in active):
                for s in active:
                    out.setdefault(s, {})['stenosis'] = 'occlusion'
    return out


def upgrade_limb(data, today=None):
    """Losslessly upgrade a v1 (or partial v2) limb dictionary to schema v2 in memory."""
    if not isinstance(data, dict):
        return {}
    out = dict(data)
    raw_segs = _remap_raw_segments(data.get('segments'))
    interventions = list(clean_interventions(data.get('interventions'), today=today))

    rx_keys = [k for k, v in raw_segs.items() if v.get('rx') is True]
    for comp in _legacy_rx_components(rx_keys):
        statuses = {_raw_stenosis(raw_segs[k]) for k in comp}
        if 'occlusion' in statuses:
            st = 'occluded'
        elif statuses & {'moderate', 'severe'}:
            st = 'isr_1'
        else:
            st = 'patent'
        if not any(iv['type'] == 'stent' and iv.get('subtype') == 'unspecified' and iv.get('segments') == comp
                   for iv in interventions):
            interventions.append({
                'type': 'stent', 'subtype': 'unspecified', 'segments': comp,
                'performed_on': '', 'status': st, 'note': '',
            })

    by_keys = _sort_segments([k for k, v in raw_segs.items() if v.get('by') is True])
    if by_keys:
        proximal, distal = by_keys[0], by_keys[-1]
        if not any(iv['type'] == 'bypass' and iv.get('conduit_group') == 'unspecified'
                   and iv.get('proximal') == proximal and iv.get('distal') == distal
                   for iv in interventions):
            interventions.append({
                'type': 'bypass', 'conduit_group': 'unspecified', 'conduit': '', 'cuff': '',
                'proximal': proximal, 'distal': distal,
                'performed_on': '', 'status': 'patent', 'site': '', 'note': '',
            })

    out['v'] = SCHEMA_VERSION
    cleaned_ivs = clean_interventions(interventions, today=today)
    out['segments'] = sync_interventions_lumen(clean_segments(raw_segs), cleaned_ivs)
    out['interventions'] = cleaned_ivs
    return out


def clean(data, today=None):
    """Form / JSON data of one limb (v1 or v2) -> canonical v2 dict ready to store."""
    upgraded = upgrade_limb(data if isinstance(data, dict) else {}, today=today)
    choice = lambda key, options: upgraded.get(key) if upgraded.get(key) in {k for k, _ in options} else ''
    return {
        'v': SCHEMA_VERSION,
        'segments': upgraded['segments'],
        'interventions': upgraded['interventions'],
        'wound_angiosome': choice('wound_angiosome', ANGIOSOMES),
        'tap': choice('tap', TAP),
        'fp': _int(upgraded.get('fp'), 0, 4), 'ip': _int(upgraded.get('ip'), 0, 4),
        'fp_calc': upgraded.get('fp_calc') in (True, 'on', '1'), 'ip_calc': upgraded.get('ip_calc') in (True, 'on', '1'),
        'pedal': choice('pedal', PEDAL),
        'ai': choice('ai', INFLOW_AI), 'cfa': choice('cfa', INFLOW_CFA) or 'A',
        'w': _int(upgraded.get('w'), 0, 3), 'i': _int(upgraded.get('i'), 0, 3), 'fi': _int(upgraded.get('fi'), 0, 3),
        'unsalvageable': upgraded.get('unsalvageable') in (True, 'on', '1'),
        'abi': _float(upgraded.get('abi'), 0, 3), 'tbi': _float(upgraded.get('tbi'), 0, 3),
        'ap': _float(upgraded.get('ap'), 0, 400), 'tp': _float(upgraded.get('tp'), 0, 400),
        'risk': choice('risk', RISK) or 'avg', 'vein': choice('vein', VEIN) or 'yes',
        'note': _bmp_line(upgraded.get('note'), 500),
    }


def get_limb(case_staging, side, today=None):
    """Cleaned v2 limb for `side`; inherits shared segments (aorta) from the other side when unstaged or v1."""
    staging_map = case_staging if isinstance(case_staging, dict) else {}
    other = 'L' if side == 'R' else 'R'
    raw = staging_map.get(side)
    limb = clean(raw if isinstance(raw, dict) else {}, today=today)
    other_raw = staging_map.get(other)
    if isinstance(other_raw, dict):
        is_unstaged_or_v1 = not isinstance(raw, dict) or raw.get('v') != SCHEMA_VERSION
        if is_unstaged_or_v1:
            other_segs = clean_segments(other_raw.get('segments'))
            for shared in SHARED_SEGMENTS:
                if shared not in limb['segments'] and shared in other_segs:
                    limb['segments'][shared] = dict(other_segs[shared])
    return limb


def save_limb(case_staging, side, limb, today=None):
    """Store `limb` under `side` (v2) and synchronize shared segments (infrarenal aorta) across staged limbs."""
    staging_map = dict(case_staging) if isinstance(case_staging, dict) else {}
    cleaned = clean(limb, today=today)
    staging_map[side] = cleaned
    other = 'L' if side == 'R' else 'R'
    if isinstance(staging_map.get(other), dict):
        other_limb = clean(staging_map[other], today=today)
        for shared in SHARED_SEGMENTS:
            if shared in cleaned['segments']:
                other_limb['segments'][shared] = dict(cleaned['segments'][shared])
            else:
                other_limb['segments'].pop(shared, None)
        staging_map[other] = other_limb
    return staging_map


def doppler_measurements(study_data, side):
    """ABI (the higher of PT/DP), TBI, highest ankle pressure and toe pressure from a Doppler study."""
    if not study_data:
        return {}
    idx = (study_data.get('indices') or {}).get(side) or {}
    prs = (study_data.get('pressures') or {}).get(side) or {}
    abis = [v for v in (idx.get('abi_pt'), idx.get('abi_dp')) if v is not None]
    aps = [v for v in (prs.get('pt'), prs.get('dp')) if v is not None]
    out = {}
    if abis:
        out['abi'] = max(abis)
    if idx.get('tbi') is not None:
        out['tbi'] = idx['tbi']
    if aps:
        out['ap'] = max(aps)
    if prs.get('toe') is not None:
        out['tp'] = prs['toe']
    return out


def has_angiography(limb):
    """True when any angiographic map segment, prior intervention, or GLASS field has been entered."""
    if not isinstance(limb, dict):
        return False
    up = clean(limb)
    return bool(
        up['segments'] or up['interventions']
        or up['fp'] is not None or up['ip'] is not None
        or up['fp_calc'] or up['ip_calc']
        or up['tap'] or up['pedal'] or up['ai'] or up['cfa'] == 'B'
    )


def has_wifi(limb):
    """True when any WIfI or clinical PLAN risk/vein/note field has been entered."""
    if not isinstance(limb, dict):
        return False
    up = clean(limb)
    return bool(
        up['w'] is not None or up['i'] is not None or up['fi'] is not None
        or up['unsalvageable'] or up['wound_angiosome']
        or up['risk'] == 'high' or up['vein'] == 'no' or up['note']
    )


def has_manual_doppler(limb):
    """True when manual hemodynamic Doppler numbers (ABI, TBI, AP, or TP) are stored on the limb."""
    if not isinstance(limb, dict):
        return False
    up = clean(limb)
    return any(up.get(k) is not None for k in ('abi', 'tbi', 'ap', 'tp'))


def has_staging(limb):
    """True when the limb has angiographic or WIfI/PLAN staging (excluding standalone Doppler numbers)."""
    return has_angiography(limb) or has_wifi(limb)
