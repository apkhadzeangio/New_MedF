"""Full anterior lower-limb SVG context assembler (`_artery_map.html`)."""
import math

from .collaterals import _build_collaterals
from .geometry import (
    _CENTER_PATHS,
    _PRECOMPUTED_SAMPLES,
    BIFURCATION_CAPS,
    GEOM_BY_KEY,
    INGUINAL_LIGAMENT,
    LANDMARKS,
    LIMB_SILHOUETTE,
    LUMEN_JUNCTIONS,
    MAP_VIEW,
    PATELLA,
    SKELETON_PATHS,
    VESSEL_GEOMETRY,
    _offset_polyline,
    _stenosis_factor,
    _stenosis_sigma,
    _tube_polygon,
    _vessel_radius_fn,
)
from .interventions import _build_interventions


def _append_segment_lumen(key, r0, deep, smp, d_seg, rad_fn, stenosis, lumens):
    if stenosis == 'occlusion':
        if not d_seg.get('flush'):
            stump_d = _tube_polygon(
                smp,
                lambda t, f=rad_fn: f(t) * max(0.14, 1.0 - t * 5.6),
                lambda t, f=rad_fn: f(t) * max(0.14, 1.0 - t * 5.6),
                0.0, 0.16,
            )
            if stump_d:
                lumens.append({'key': key, 'deep': deep, 'd': stump_d, 'gloss': ''})
        recon = d_seg.get('reconstitution')
        if recon in ('yes', 'delayed'):
            t_start = 0.76 if recon == 'yes' else 0.85
            scale_k = 4.0 if recon == 'yes' else 5.5
            rec_d = _tube_polygon(
                smp,
                lambda t, f=rad_fn, ts=t_start, sk=scale_k: f(t) * min(1.0, max(0.14, (t - ts) * sk)),
                lambda t, f=rad_fn, ts=t_start, sk=scale_k: f(t) * min(1.0, max(0.14, (t - ts) * sk)),
                t_start, 1.0,
            )
            if rec_d:
                lumens.append({'key': key, 'deep': deep, 'd': rec_d, 'gloss': ''})
        return

    sf = _stenosis_factor(stenosis)
    sigma = _stenosis_sigma(d_seg.get('length_cm'))
    eccentric = d_seg.get('morph') == 'eccentric'
    ulcer = d_seg.get('ulcerated') is True

    def r_left(t, f=rad_fn, s=sf, sg=sigma, ecc=eccentric, ulc=ulcer):
        r = f(t)
        if not s:
            return r
        bell = math.exp(-((t - 0.5) / sg) ** 2)
        pinch = (s * 1.42 if ecc else s) * bell
        val = r * max(0.14, 1.0 - pinch)
        if ulc and 0.44 <= t <= 0.56:
            val += r * 0.24
        return val

    def r_right(t, f=rad_fn, s=sf, sg=sigma, ecc=eccentric):
        r = f(t)
        if not s:
            return r
        bell = math.exp(-((t - 0.5) / sg) ** 2)
        pinch = (s * 0.22 if ecc else s) * bell
        return r * max(0.14, 1.0 - pinch)

    gloss_d = _offset_polyline(smp, lambda t, rl=r_left: -rl(t) * 0.36)
    lumens.append({
        'key': key,
        'deep': deep,
        'd': _tube_polygon(smp, r_left, r_right),
        'gloss': gloss_d,
        'gloss_w': round(max(0.9, r0 * 0.24), 1),
    })


def _build_vessel_layers(norm_segs):
    adventitia, junction_caps, plaque_beds, lumens, lumen_caps = [], [], [], [], []
    calc_plates, coral_nodules, thrombi = [], [], []

    for key, r0, r1, deep, _pts, _lbl in VESSEL_GEOMETRY:
        smp = _PRECOMPUTED_SAMPLES[key]
        d_seg = norm_segs.get(key, {})
        rad_fn = _vessel_radius_fn(r0, r1, d_seg)
        stenosis = d_seg.get('stenosis', 'none')

        adventitia.append({
            'key': key,
            'deep': deep,
            'd': _tube_polygon(smp, lambda t, f=rad_fn: f(t) + 1.5, lambda t, f=rad_fn: f(t) + 1.5),
        })
        if stenosis != 'none':
            plaque_beds.append({
                'key': key,
                'cto': stenosis == 'occlusion',
                'd': _tube_polygon(smp, rad_fn, rad_fn),
            })

        _append_segment_lumen(key, r0, deep, smp, d_seg, rad_fn, stenosis, lumens)

        if d_seg.get('thrombus'):
            for x, y, nx, ny, t in (smp[8], smp[13], smp[18]):
                thrombi.append({
                    'cx': round(x + nx * 0.6, 1), 'cy': round(y + ny * 0.6, 1),
                    'r': round(max(1.4, rad_fn(t) * 0.42), 1),
                })

        # PACSS 1-4 wall calcium & coral-reef nodules
        calc = d_seg.get('calc') or 0
        if calc:
            t0, t1 = (0.28, 0.70) if calc in (1, 3) else (0.08, 0.92)
            wall_off = lambda t, f=rad_fn: f(t) + 1.0
            calc_plates.append(_offset_polyline(smp, lambda t, wf=wall_off: -wf(t), t0, t1))
            if calc >= 3:
                calc_plates.append(_offset_polyline(smp, wall_off, t0, t1))
        if d_seg.get('coral'):
            for idx, (x, y, nx, ny, t) in enumerate(smp[7:20:3]):
                sgn = -1.0 if idx % 2 == 0 else 1.0
                rr = rad_fn(t) * 0.45 * sgn
                coral_nodules.append({
                    'cx': round(x + nx * rr, 1), 'cy': round(y + ny * rr, 1),
                    'r': round(max(1.5, rad_fn(t) * 0.38), 1),
                })

    for cap_key in BIFURCATION_CAPS:
        _, _, r1, _, pts, _ = GEOM_BY_KEY[cap_key]
        end_pt = pts[-1]
        junction_caps.append({'cx': end_pt[0], 'cy': end_pt[1], 'r': round(r1 + 1.4, 1)})

    for p_key, c_key in LUMEN_JUNCTIONS:
        pd, cd = norm_segs.get(p_key, {}), norm_segs.get(c_key, {})
        if pd.get('stenosis') != 'occlusion' and not (cd.get('stenosis') == 'occlusion' and cd.get('flush')):
            end_pt = GEOM_BY_KEY[p_key][4][-1]
            c_r0 = GEOM_BY_KEY[c_key][1]
            lumen_caps.append({'cx': end_pt[0], 'cy': end_pt[1], 'r': round(c_r0 * 0.9, 1)})

    return {
        'adventitia': adventitia,
        'junction_caps': junction_caps,
        'plaque_beds': plaque_beds,
        'lumens': lumens,
        'lumen_caps': lumen_caps,
        'calc_plates': calc_plates,
        'coral_nodules': coral_nodules,
        'thrombi': thrombi,
    }


def _build_map_annotations(segment_items, vx, X, flip):
    enriched_segments = []
    for item in segment_items:
        key = item['key']
        _, r0, _, _, _, _ = GEOM_BY_KEY[key]
        enriched = dict(item)
        enriched['d'] = _CENTER_PATHS[key]
        enriched['w'] = round(r0 * 2.0, 1)
        enriched_segments.append(enriched)

    short_by_key = {s['key']: s['short'] for s in segment_items}
    labels = []
    for key, _r0, _r1, _deep, _pts, lbl in VESSEL_GEOMETRY:
        short = short_by_key.get(key)
        if not lbl or not short:
            continue
        x, y, anchor = lbl[:3]
        if flip:
            anchor = {'start': 'end', 'end': 'start'}.get(anchor, anchor)
        labels.append({'x': round(X(x), 1), 'y': y, 'anchor': anchor, 'text': short, 'vertical': len(lbl) > 3})

    marks = [{
        'text': text, 'y': y, 'ty': y - 6,
        'x1': round(X(vx + 6), 1), 'x2': round(X(332), 1),
        'tx': round(X(vx + 8), 1),
        'anchor': 'end' if flip else 'start',
    } for text, y in LANDMARKS]

    ing_text, ing_x, ing_y, ing_anc = INGUINAL_LIGAMENT['label']
    if flip:
        ing_anc = 'end' if ing_anc == 'start' else 'start'
    inguinal = {
        'x1': INGUINAL_LIGAMENT['x1'], 'y1': INGUINAL_LIGAMENT['y1'],
        'x2': INGUINAL_LIGAMENT['x2'], 'y2': INGUINAL_LIGAMENT['y2'],
        'text': ing_text, 'tx': round(X(ing_x), 1), 'ty': ing_y, 'anchor': ing_anc,
    }
    return enriched_segments, labels, marks, inguinal


def build_map_context(segments_meta, segment_items, norm_segs, interventions, side, segment_next):
    """Assemble full pure-vector SVG context for `_artery_map.html`."""
    vx, vy, vw, vh = MAP_VIEW
    axis = 2 * vx + vw
    flip = side == 'L'
    X = (lambda x: axis - x) if flip else (lambda x: x)

    layers = _build_vessel_layers(norm_segs)
    enriched_segments, labels, marks, inguinal = _build_map_annotations(segment_items, vx, X, flip)

    return {
        'segments': enriched_segments,
        'limb_d': LIMB_SILHOUETTE,
        'skeleton': SKELETON_PATHS,
        'patella': PATELLA,
        'inguinal': inguinal,
        **layers,
        'collaterals': _build_collaterals(norm_segs, segment_next),
        'iv': _build_interventions(interventions, X, flip),
        'labels': labels,
        'marks': marks,
        'side': side,
        'view': f'{vx} {vy} {vw} {vh}',
        'axis': axis,
    }
