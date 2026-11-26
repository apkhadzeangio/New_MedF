"""SVG primitive builders for stents, patches, angioplasty, thrombectomy, bypasses, and amputations."""
import math

from .geometry import (
    _PRECOMPUTED_SAMPLES,
    AMPUTATION_LINES,
    GEOM_BY_KEY,
    _offset_polyline,
    _tube_polygon,
    sample_curve,
)


def _append_stent_primitives(iv, stents):
    tone = 'ok' if iv['status'] == 'patent' else ('bad' if iv['status'] == 'occluded' else 'warn')
    covered = iv.get('subtype') == 'covered'
    fractured = iv['status'] == 'fractured'
    for seg_key in iv.get('segments') or ():
        if seg_key not in GEOM_BY_KEY:
            continue
        _, r0, r1, _, _, _ = GEOM_BY_KEY[seg_key]
        smp = _PRECOMPUTED_SAMPLES[seg_key]
        zig_a, zig_b = [], []
        for idx, (x, y, nx, ny, t) in enumerate(smp[1:-1], start=1):
            r = (r0 + (r1 - r0) * t) * 0.96
            sgn = 1.0 if idx % 2 == 0 else -1.0
            zig_a.append(f'{x + nx * r * sgn:.1f},{y + ny * r * sgn:.1f}')
            zig_b.append(f'{x - nx * r * sgn:.1f},{y - ny * r * sgn:.1f}')
        markers = []
        for end_p in (smp[1], smp[-2]):
            mr = r0 + 1.1
            markers.append((round(end_p[0] - end_p[2] * mr, 1), round(end_p[1] - end_p[3] * mr, 1)))
            markers.append((round(end_p[0] + end_p[2] * mr, 1), round(end_p[1] + end_p[3] * mr, 1)))
        sleeve = _tube_polygon(
            smp,
            lambda t, a=r0, b=r1: (a + (b - a) * t) * 0.94,
            lambda t, a=r0, b=r1: (a + (b - a) * t) * 0.94,
            0.04, 0.96,
        ) if covered else ''
        frac_line = None
        if fractured:
            mid = smp[len(smp) // 2]
            fr = (r0 + r1) * 0.75
            frac_line = (
                round(mid[0] - mid[2] * fr, 1), round(mid[1] - mid[3] * fr, 1),
                round(mid[0] + mid[2] * fr, 1), round(mid[1] + mid[3] * fr, 1),
            )
        stents.append({
            'zig_a': 'M' + ' L'.join(zig_a),
            'zig_b': 'M' + ' L'.join(zig_b),
            'sleeve': sleeve,
            'markers': markers,
            'fracture': frac_line,
            'tone': tone,
        })


def _append_patch_primitives(iv, patches):
    tone = 'ok' if iv['status'] == 'patent' else ('bad' if iv['status'] == 'occluded' else 'warn')
    for seg_key in iv.get('segments') or ():
        if seg_key not in GEOM_BY_KEY:
            continue
        _, r0, r1, _, _, _ = GEOM_BY_KEY[seg_key]
        smp = _PRECOMPUTED_SAMPLES[seg_key]
        patch_r = lambda t, a=r0, b=r1: (a + (b - a) * t) * (1.0 + 0.42 * math.sin(math.pi * t)) + 1.2
        poly = _tube_polygon(smp, patch_r, patch_r, 0.12, 0.88)
        ticks = []
        for x, y, nx, ny, t in smp[4:-4:3]:
            rr = patch_r(t)
            ticks.append((
                round(x - nx * (rr + 1.2), 1), round(y - ny * (rr + 1.2), 1),
                round(x - nx * (rr - 1.6), 1), round(y - ny * (rr - 1.6), 1),
            ))
            ticks.append((
                round(x + nx * (rr - 1.6), 1), round(y + ny * (rr - 1.6), 1),
                round(x + nx * (rr + 1.2), 1), round(y + ny * (rr + 1.2), 1),
            ))
        patches.append({'d': poly, 'ticks': ticks, 'tone': tone})


def _append_balloon_primitives(iv, balloon_marks):
    kind = iv['type']
    for seg_key in iv.get('segments') or ():
        if seg_key not in GEOM_BY_KEY:
            continue
        _, r0, r1, _, _, _ = GEOM_BY_KEY[seg_key]
        smp = _PRECOMPUTED_SAMPLES[seg_key]
        off = lambda t, a=r0, b=r1: (a + (b - a) * t) + 2.4
        balloon_marks.append({
            'kind': kind,
            'left': _offset_polyline(smp, lambda t, f=off: -f(t), 0.12, 0.88),
            'right': _offset_polyline(smp, off, 0.12, 0.88),
        })


def _bypass_control_points(iv, x3, y3, vein_idx, prosthetic_idx):
    prox_key = iv['proximal']
    if prox_key == 'contralateral_cfa':
        x0, y0 = 334.0, y3 - 26.0
        is_vein = iv['conduit_group'] == 'vein'
        ctrl = ((x0, y0), (265.0, y0 + 28.0), (205.0, y3 + 22.0), (x3, y3))
        return ctrl, x0, y0, is_vein, vein_idx, prosthetic_idx
    if prox_key == 'axillary':
        x0, y0 = 50.0, -224.0
        ctrl = ((x0, y0), (44.0, (y0 + y3) * 0.35), (56.0, (y0 + y3) * 0.72), (x3, y3))
        return ctrl, x0, y0, False, vein_idx, prosthetic_idx
    if prox_key not in GEOM_BY_KEY:
        return None
    p_start = _PRECOMPUTED_SAMPLES[prox_key][12]
    x0, y0 = p_start[0], p_start[1]
    is_vein = iv['conduit_group'] == 'vein'
    y_lo, y_hi = min(y0, y3), max(y0, y3)
    spanned_xs = [
        pt[0] for seg_smp in _PRECOMPUTED_SAMPLES.values()
        for pt in seg_smp if y_lo <= pt[1] <= y_hi
    ] or [x0, x3]
    if is_vein:
        bow_x = min(316.0, max(spanned_xs) + 54.0 + vein_idx * 16.0)
        vein_idx += 1
    else:
        bow_x = max(36.0, min(spanned_xs) - 44.0 - prosthetic_idx * 16.0)
        prosthetic_idx += 1
    dy = y3 - y0
    ctrl = ((x0, y0), (bow_x, y0 + dy * 0.24), (bow_x, y0 + dy * 0.76), (x3, y3))
    return ctrl, x0, y0, is_vein, vein_idx, prosthetic_idx


def _append_bypass_primitive(iv, X, flip, vein_idx, prosthetic_idx, bypasses, bypass_labels):
    dist_key = iv['distal']
    if dist_key not in GEOM_BY_KEY:
        return vein_idx, prosthetic_idx
    p_end = _PRECOMPUTED_SAMPLES[dist_key][12]
    x3, y3 = p_end[0], p_end[1]
    geom = _bypass_control_points(iv, x3, y3, vein_idx, prosthetic_idx)
    if geom is None:
        return vein_idx, prosthetic_idx
    ctrl, x0, y0, is_vein, vein_idx, prosthetic_idx = geom

    g_smp = sample_curve(ctrl, 36)
    outer_d = _tube_polygon(g_smp, lambda _t: 4.8, lambda _t: 4.8)
    site = iv.get('site') or 'distal'
    stenosed = iv['status'] == 'stenosed'

    def graft_inner(t, st=stenosed, st_site=site):
        r = 3.1
        if not st:
            return r
        center = 0.14 if st_site == 'proximal' else (0.50 if st_site == 'body' else 0.86)
        return r * (1.0 - 0.68 * math.exp(-((t - center) / 0.10) ** 2))

    inner_d = _tube_polygon(g_smp, graft_inner, graft_inner)
    rings = []
    if iv.get('conduit') == 'ptfe_ringed':
        for rx, ry, rnx, rny, _ in g_smp[3:-3:2]:
            rings.append((
                round(rx - rnx * 5.0, 1), round(ry - rny * 5.0, 1),
                round(rx + rnx * 5.0, 1), round(ry + rny * 5.0, 1),
            ))

    style_cls = 'occluded' if iv['status'] == 'occluded' else ('vein' if is_vein else 'ptfe')
    bypasses.append({
        'style': style_cls,
        'outer_d': outer_d,
        'inner_d': inner_d,
        'center_d': _offset_polyline(g_smp),
        'rings': rings,
        'hoods': [(round(x0, 1), round(y0, 1)), (round(x3, 1), round(y3, 1))],
        'miller': (round(x3, 1), round(y3, 1)) if iv.get('cuff') == 'miller' else None,
    })

    mid = g_smp[len(g_smp) // 2]
    short_lbl = 'აუტოვენა' if is_vein else ('PTFE' if 'ptfe' in (iv.get('conduit') or '') else 'შუნტი')
    dx_lbl = 10.0 if is_vein else -10.0
    anchor = 'start' if is_vein else 'end'
    if flip:
        anchor = 'end' if anchor == 'start' else 'start'
    bypass_labels.append({
        'x': round(X(mid[0] + dx_lbl), 1),
        'y': round(mid[1], 1),
        'anchor': anchor,
        'text': short_lbl,
        'style': style_cls,
    })
    return vein_idx, prosthetic_idx


def _build_interventions(interventions, X, flip):
    """Build SVG primitives for stents, patches, angioplasty, thrombectomy, bypasses, and amputations."""
    stents, patches, balloon_marks = [], [], []
    bypasses, bypass_labels, amputations = [], [], []
    vein_idx = 0
    prosthetic_idx = 0

    for iv in interventions:
        kind = iv['type']
        if kind == 'stent':
            _append_stent_primitives(iv, stents)
        elif kind == 'endarterectomy':
            _append_patch_primitives(iv, patches)
        elif kind in ('angioplasty', 'thrombectomy'):
            _append_balloon_primitives(iv, balloon_marks)
        elif kind == 'bypass':
            vein_idx, prosthetic_idx = _append_bypass_primitive(
                iv, X, flip, vein_idx, prosthetic_idx, bypasses, bypass_labels,
            )
        elif kind == 'amputation':
            spec = AMPUTATION_LINES.get(iv.get('subtype'))
            if spec:
                amputations.append({
                    'd': spec[0],
                    'text': spec[1],
                    'x': round(X(202), 1),
                    'y': spec[2],
                    'anchor': 'end' if flip else 'start',
                    'healed': iv.get('status') != 'nonhealing',
                })

    return {
        'stents': stents,
        'patches': patches,
        'balloon_marks': balloon_marks,
        'bypasses': bypasses,
        'bypass_labels': bypass_labels,
        'amputations': amputations,
    }
