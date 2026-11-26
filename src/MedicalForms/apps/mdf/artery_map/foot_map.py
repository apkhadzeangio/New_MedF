"""Lateral-oblique foot, pedal arch, and 6 angiosome territories SVG context builder."""
import math

from .geometry import (
    _offset_polyline,
    _stenosis_factor,
    _tube_polygon,
    _vessel_radius_fn,
    sample_curve,
    svg_bezier_path,
)

FOOT_VIEW = (0, 0, 360, 294)
FOOT_SILHOUETTE = (
    'M42,14 L145,14 L152,88 C196,118 258,148 312,178 C336,192 340,222 316,240 '
    'C282,262 192,268 108,262 C52,258 26,232 28,188 C30,152 44,118 44,82 Z'
)
FOOT_BONES = (
    # Distal tibia/fibula above ankle plafond
    'M56,14 L126,14 L130,82 L58,82 Z',
    # Talus, calcaneus & tarsal-metatarsal arch hint
    'M40,148 C42,114 78,96 126,104 C178,128 242,162 296,192 C302,206 286,222 254,224 '
    'C192,226 126,234 66,238 C38,236 34,196 40,148 Z',
)
FOOT_GEOMETRY = (
    ('at3',  'AT',   3.8, 3.4, False, ((118, 18), (122, 56), (130, 96)), (138, 52, 'start')),
    ('pt3',  'PT',   3.8, 3.5, True,  ((64, 18), (68, 66), (76, 118)), (50, 62, 'end')),
    ('per3', 'PER',  3.2, 2.5, True,  ((92, 18), (94, 64), (98, 112)), (94, 14, 'middle')),
    ('dp',   'DP',   3.4, 2.8, False, ((130, 96), (178, 128), (232, 160), (266, 184)), (212, 132, 'start')),
    ('mpa',  'MPA',  2.8, 2.3, True,  ((76, 118), (132, 158), (204, 192), (274, 206)), (168, 174, 'start')),
    ('lpa',  'LPA',  3.1, 2.6, True,  ((76, 118), (116, 186), (184, 234), (248, 240)), (146, 250, 'middle')),
    ('arch', 'ARCH', 2.6, 2.6, True,  ((248, 240), (272, 226), (274, 202), (266, 184)), (290, 214, 'start')),
)
FOOT_GEOM_BY_KEY = {row[0]: row for row in FOOT_GEOMETRY}
_FOOT_SAMPLES = {row[0]: sample_curve(row[5], 22) for row in FOOT_GEOMETRY}
_FOOT_CENTERS = {row[0]: svg_bezier_path(row[5]) for row in FOOT_GEOMETRY}

FOOT_JUNCTIONS = (('at3', 'dp'), ('pt3', 'mpa'), ('pt3', 'lpa'), ('dp', 'arch'), ('lpa', 'arch'))
FOOT_PERONEAL_COMM = (
    'M98,112 C86,144 66,178 52,212',    # lateral calcaneal branch
    'M98,104 C112,106 122,104 130,98',  # anterior perforating branch -> DP
)
FOOT_AMPUTATION_LINES = {
    'toes': ('M304,166 C318,200 314,232 296,252', 'თითების ამპ.', 270, 266),
    'ray': ('M282,162 L274,248', 'სხივური რეზ.', 250, 266),
    'tma': ('M246,146 C252,196 246,234 232,258', 'TMA', 214, 268),
    'chopart_lisfranc': ('M178,108 C182,172 174,226 162,262', 'შოპარი/ლისფრანკი', 150, 268),
}

# 6 foot & ankle angiosome territories (Taylor & Palmer 1987; Attinger et al. 2006)
ANGIOSOME_ZONES = {
    'dorsal': {
        'd': 'M142,96 C195,122 258,152 314,182 C318,196 296,204 252,194 C198,176 154,142 132,114 Z',
        'label': 'ტერფის ზურგი / დორსალური თითები (AT → DP)',
    },
    'medial_plantar': {
        'd': 'M128,158 C186,182 254,196 318,206 C322,226 294,238 242,232 C184,222 140,196 118,174 Z',
        'label': 'მედიალური ძირი / I თითი (PT → MPA)',
    },
    'lateral_plantar': {
        'd': 'M108,196 C168,224 238,236 304,238 C286,258 205,264 128,256 C104,244 98,218 108,196 Z',
        'label': 'ლატერალური ძირი / თითები (PT → LPA)',
    },
    'medial_heel': {
        'd': 'M32,168 C58,162 92,178 106,214 C112,244 86,260 54,254 C30,246 24,205 32,168 Z',
        'label': 'მედიალური ქუსლი (PT)',
    },
    'lateral_heel': {
        'd': 'M34,136 C64,132 96,150 102,186 C86,205 52,205 32,184 Z',
        'label': 'ლატერალური ქუსლი (PER)',
    },
    'lateral_ankle': {
        'd': 'M82,66 C114,62 146,80 148,112 C126,126 94,120 80,96 Z',
        'label': 'კოჭის წინა-ლატერალური უბანი (PER)',
    },
}


def _append_foot_lumen(key, deep, smp, d_seg, rad_fn, stenosis, lumens):
    if stenosis == 'occlusion':
        if not d_seg.get('flush'):
            stump_d = _tube_polygon(
                smp,
                lambda t, f=rad_fn: f(t) * max(0.15, 1.0 - t * 5.2),
                lambda t, f=rad_fn: f(t) * max(0.15, 1.0 - t * 5.2),
                0.0, 0.18,
            )
            if stump_d:
                lumens.append({'key': key, 'deep': deep, 'd': stump_d})
        if d_seg.get('reconstitution') in ('yes', 'delayed'):
            rec_d = _tube_polygon(
                smp,
                lambda t, f=rad_fn: f(t) * min(1.0, max(0.15, (t - 0.76) * 4.2)),
                lambda t, f=rad_fn: f(t) * min(1.0, max(0.15, (t - 0.76) * 4.2)),
                0.76, 1.0,
            )
            if rec_d:
                lumens.append({'key': key, 'deep': deep, 'd': rec_d})
        return

    sf = _stenosis_factor(stenosis)
    eccentric = d_seg.get('morph') == 'eccentric'
    r_left = lambda t, f=rad_fn, s=sf, ecc=eccentric: f(t) * max(
        0.15, 1.0 - (s * 1.4 if ecc else s) * math.exp(-((t - 0.5) / 0.28) ** 2),
    )
    r_right = lambda t, f=rad_fn, s=sf, ecc=eccentric: f(t) * max(
        0.15, 1.0 - (s * 0.25 if ecc else s) * math.exp(-((t - 0.5) / 0.28) ** 2),
    )
    lumens.append({'key': key, 'deep': deep, 'd': _tube_polygon(smp, r_left, r_right)})


def _build_foot_interventions(interventions):
    stents, distal_hoods, amputations = [], [], []
    for iv in interventions:
        if iv['type'] == 'stent':
            tone = 'ok' if iv['status'] == 'patent' else ('bad' if iv['status'] == 'occluded' else 'warn')
            for seg_key in iv.get('segments') or ():
                if seg_key not in FOOT_GEOM_BY_KEY:
                    continue
                _, _, r0, r1, _, _, _ = FOOT_GEOM_BY_KEY[seg_key]
                smp = _FOOT_SAMPLES[seg_key]
                zig_a, zig_b = [], []
                for idx, (x, y, nx, ny, t) in enumerate(smp[1:-1], start=1):
                    r = (r0 + (r1 - r0) * t) * 0.95
                    sgn = 1.0 if idx % 2 == 0 else -1.0
                    zig_a.append(f'{x + nx * r * sgn:.1f},{y + ny * r * sgn:.1f}')
                    zig_b.append(f'{x - nx * r * sgn:.1f},{y - ny * r * sgn:.1f}')
                stents.append({'zig_a': 'M' + ' L'.join(zig_a), 'zig_b': 'M' + ' L'.join(zig_b), 'tone': tone})
        elif iv['type'] == 'bypass' and iv['distal'] in FOOT_GEOM_BY_KEY:
            mid = _FOOT_SAMPLES[iv['distal']][11]
            style = 'occluded' if iv['status'] == 'occluded' else ('vein' if iv['conduit_group'] == 'vein' else 'ptfe')
            distal_hoods.append({
                'cx': round(mid[0], 1), 'cy': round(mid[1], 1), 'style': style,
                'miller': iv.get('cuff') == 'miller',
            })
        elif iv['type'] == 'amputation':
            spec = FOOT_AMPUTATION_LINES.get(iv.get('subtype'))
            if spec:
                amputations.append({
                    'd': spec[0], 'text': spec[1], 'x': spec[2], 'y': spec[3],
                    'healed': iv.get('status') != 'nonhealing',
                })
    return stents, distal_hoods, amputations


def build_foot_context(segment_items, norm_segs, interventions, side, wound_angiosome=''):
    """Lateral-oblique pedal loop & 6 angiosome territories context for `_foot_map.html`."""
    item_by_key = {s['key']: s for s in segment_items}
    adventitia, junction_caps, plaque_beds, lumens, calc_plates = [], [], [], [], []
    foot_segments, labels = [], []

    for key, short, r0, r1, deep, _pts, lbl in FOOT_GEOMETRY:
        smp = _FOOT_SAMPLES[key]
        d_seg = norm_segs.get(key, {})
        rad_fn = _vessel_radius_fn(r0, r1, d_seg)
        stenosis = d_seg.get('stenosis', 'none')

        adventitia.append({
            'key': key, 'deep': deep,
            'd': _tube_polygon(smp, lambda t, f=rad_fn: f(t) + 1.3, lambda t, f=rad_fn: f(t) + 1.3),
        })
        if stenosis != 'none':
            plaque_beds.append({'key': key, 'cto': stenosis == 'occlusion', 'd': _tube_polygon(smp, rad_fn, rad_fn)})

        _append_foot_lumen(key, deep, smp, d_seg, rad_fn, stenosis, lumens)

        calc = d_seg.get('calc') or 0
        if calc:
            t0, t1 = (0.28, 0.70) if calc in (1, 3) else (0.10, 0.90)
            wall_off = lambda t, f=rad_fn: f(t) + 0.9
            calc_plates.append(_offset_polyline(smp, lambda t, wf=wall_off: -wf(t), t0, t1))
            if calc >= 3:
                calc_plates.append(_offset_polyline(smp, wall_off, t0, t1))

        base_item = dict(item_by_key.get(key) or {
            'key': key, 'name': short, 'css': 'sg sg-n', 'state': 'n', 'flags': [], 'tooltip': short,
        })
        base_item['d'] = _FOOT_CENTERS[key]
        base_item['w'] = round(r0 * 2.0, 1)
        foot_segments.append(base_item)
        labels.append({'x': lbl[0], 'y': lbl[1], 'anchor': lbl[2], 'text': short})

    for p_key, _c_key in FOOT_JUNCTIONS:
        _, _, _, r1, _, pts, _ = FOOT_GEOM_BY_KEY[p_key]
        junction_caps.append({'cx': pts[-1][0], 'cy': pts[-1][1], 'r': round(r1 + 1.2, 1)})

    stents, distal_hoods, amputations = _build_foot_interventions(interventions)
    peroneal_comm = [] if norm_segs.get('per3', {}).get('stenosis') == 'occlusion' else list(FOOT_PERONEAL_COMM)
    fx, fy, fw, fh = FOOT_VIEW
    return {
        'view': f'{fx} {fy} {fw} {fh}',
        'side': side,
        'foot_d': FOOT_SILHOUETTE,
        'bones': FOOT_BONES,
        'angiosome': ANGIOSOME_ZONES.get(wound_angiosome),
        'adventitia': adventitia,
        'junction_caps': junction_caps,
        'plaque_beds': plaque_beds,
        'lumens': lumens,
        'peroneal_comm': peroneal_comm,
        'calc_plates': calc_plates,
        'stents': stents,
        'distal_hoods': distal_hoods,
        'amputations': amputations,
        'segments': foot_segments,
        'labels': labels,
    }
