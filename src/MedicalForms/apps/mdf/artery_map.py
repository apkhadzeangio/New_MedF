"""Pure-vector surgical anatomy & 3-layer variable-width arterial map generator.

Draws a vascular-atlas anterior limb diagram (Right leg in native coordinates;
Left leg mirrored around `axis` with all text labels placed upright outside the
mirror group):

  Layer 0 — Soft limb silhouette, translucent skeletal landmarks (iliac wing,
            acetabulum & femoral head under CFA, femur, patella, tibia, fibula,
            pedal rays) and surgical boundaries (inguinal ligament, adductor
            hiatus, P1/P2/P3 knee joint lines, ankle malleolar line).
  Layer 1 — Continuous adventitial outer wall (tapering polygons + junction caps
            so every bifurcation joins without gaps or stroke overlaps; fusiform
            bulge for aneurysms; narrow caliber when <6 mm).
  Layer 2 — Intimal atheromatous plaque bed / thrombosed CTO core.
  Layer 3 — Patent blood lumen (concentric vs eccentric stenosis pinch scaled to
            lesion length, CTO ostial flush vs stump meniscus, distal collateral
            reconstitution, corkscrew collateral arcades, specular highlight).
  Layer 4 — PACSS 1–4 unilateral/bilateral wall calcium & coral-reef nodules,
            nitinol diamond stent mesh (with covered-stent sleeve & fracture
            marker), angioplasty/thrombectomy markers, endarterectomy patch with
            suture ticks, curved autologous-vein & prosthetic bypass conduits
            (ringed PTFE, Miller cuff, cobra-head anastomoses, stenosed/occluded
            states, fem-fem & axillo-femoral origins), and minor amputation line.
  Layer 5 — Interactive hit/focus targets (`[data-seg]`) for all 26 segments.
"""

import math

MAP_VIEW = (-58, -242, 416, 1966)       # x, y, width, height -> axis = 300

# (key, r0, r1, deep, control_points, label_spec)
VESSEL_GEOMETRY = (
    ('aorta', 9.4, 8.8, False, ((295, -226), (295, -200), (295, -175)), (276, -202, 'end')),
    ('cia',   7.4, 6.8, False, ((295, -175), (276, -136), (252, -92)), (238, -138, 'end')),
    ('iia',   4.8, 3.5, True,  ((252, -92), (266, -52), (274, -8), (274, 28)), (286, -14, 'start')),
    ('eia',   6.6, 6.2, False, ((252, -92), (216, -36), (172, 32), (148, 108)), (180, -28, 'end')),
    ('cfa',   6.2, 6.0, False, ((148, 108), (149, 135), (150, 162)), (126, 136, 'end')),
    ('pfa',   4.8, 2.4, True,  ((150, 162), (128, 196), (110, 252), (102, 332)), (90, 266, 'end')),
    ('sfa1',  5.4, 5.1, False, ((150, 162), (158, 215), (168, 272), (178, 320)), (190, 236, 'start')),
    ('sfa2',  5.1, 4.8, False, ((178, 320), (190, 382), (200, 440), (206, 490)), None),
    ('sfa3',  4.8, 4.6, False, ((206, 490), (211, 542), (210, 588), (204, 630)), None),
    ('pop1',  4.6, 4.4, True,  ((204, 630), (194, 685), (176, 742), (162, 792)), (212, 712, 'start')),
    ('pop2',  4.4, 4.2, True,  ((162, 792), (155, 824), (150, 856)), (176, 826, 'start')),
    ('pop3',  4.2, 4.0, True,  ((150, 856), (148, 902), (147, 946)), (172, 908, 'start')),
    ('tpt',   3.7, 3.5, True,  ((147, 946), (150, 972), (153, 998)), (168, 974, 'start')),
    ('at1',   3.3, 3.1, False, ((147, 946), (122, 954), (102, 976), (96, 1045)), (78, 1002, 'end')),
    ('at2',   3.1, 2.9, False, ((96, 1045), (92, 1135), (94, 1235)), None),
    ('at3',   2.9, 2.7, False, ((94, 1235), (99, 1345), (108, 1435), (116, 1510)), None),
    ('pt1',   3.2, 3.0, True,  ((153, 998), (162, 1060), (168, 1135)), (196, 1148, 'start')),
    ('pt2',   3.0, 2.8, True,  ((168, 1135), (174, 1225), (178, 1320)), None),
    ('pt3',   2.8, 2.6, True,  ((178, 1320), (181, 1415), (182, 1515)), None),
    ('per1',  3.0, 2.8, True,  ((153, 998), (144, 1055), (138, 1130)), (122, 1210, 'middle', 'v')),
    ('per2',  2.8, 2.6, True,  ((138, 1130), (133, 1215), (129, 1305)), None),
    ('per3',  2.6, 2.2, True,  ((129, 1305), (126, 1380), (124, 1455)), None),
    ('dp',    2.6, 2.2, False, ((116, 1510), (124, 1562), (134, 1622)), (102, 1582, 'end')),
    ('mpa',   2.3, 1.9, True,  ((182, 1515), (178, 1568), (164, 1618)), (196, 1558, 'start')),
    ('lpa',   2.5, 2.1, True,  ((182, 1515), (166, 1575), (128, 1622), (94, 1640)), (192, 1612, 'start')),
    ('arch',  2.1, 2.1, True,  ((94, 1640), (112, 1664), (128, 1654), (134, 1622)), (118, 1686, 'middle')),
)
GEOM_BY_KEY = {row[0]: row for row in VESSEL_GEOMETRY}

# Pure-vector anatomical limb contour & skeletal landmarks (Right leg anterior view)
LIMB_SILHOUETTE = (
    'M94,-42 C68,88 56,280 70,480 C80,630 86,760 82,880 C72,1020 66,1190 82,1360 '
    'C90,1445 94,1505 82,1575 C70,1638 74,1680 104,1694 C138,1708 178,1692 186,1650 '
    'C192,1608 194,1555 198,1505 C208,1360 224,1180 216,995 C212,910 216,820 228,710 '
    'C248,540 268,350 266,170 C264,85 282,-10 318,-66 Z'
)
SKELETON_PATHS = (
    # Iliac wing, acetabular roof & pubic ramus
    'M238,-168 C182,-172 124,-132 110,-78 C102,-40 126,-2 168,22 C204,42 246,24 270,-22',
    'M168,22 C182,56 218,86 258,92 C268,94 276,82 268,66 C248,46 216,32 194,24',
    # Femur (head under CFA, neck, trochanters, shaft, condyles)
    'M146,96 C162,96 170,112 160,126 C152,136 142,144 146,168 L178,754 '
    'C196,784 198,828 182,842 L138,842 C122,828 122,784 134,754 L112,202 '
    'C96,178 92,140 108,124 C118,114 128,124 132,126 C134,108 136,96 146,96 Z',
    # Tibia (plateau, shaft, medial malleolus)
    'M132,858 L188,858 C190,902 180,946 176,1010 L178,1492 C186,1508 184,1524 170,1528 '
    'L132,1522 C126,1508 130,1488 134,1464 L140,990 C136,940 128,896 132,858 Z',
    # Fibula (head, shaft, lateral malleolus)
    'M108,892 L124,892 L120,1518 L102,1524 L104,1490 Z',
    # Tarsal / metatarsal pedal arch bed
    'M96,1536 C118,1532 156,1534 174,1544 L172,1638 C154,1662 114,1668 90,1646 Z',
)
PATELLA = {'cx': 156, 'cy': 818, 'rx': 22, 'ry': 26}
INGUINAL_LIGAMENT = {'x1': 102, 'y1': 58, 'x2': 252, 'y2': 138, 'label': ('საზარდულის იოგი', -50, 56, 'start')}

LANDMARKS = (
    ('ადუქტორული ხვრელი', 630),
    ('P1 / P2', 792),
    ('მუხლი (P2 / P3)', 856),
    ('კოჭი', 1512),
)

BIFURCATION_CAPS = ('aorta', 'cia', 'cfa', 'pop3', 'tpt', 'pt3')
LUMEN_JUNCTIONS = (
    ('aorta', 'cia'), ('cia', 'eia'), ('cfa', 'sfa1'),
    ('pop3', 'at1'), ('pop3', 'tpt'), ('tpt', 'pt1'), ('pt3', 'lpa'),
)
AMPUTATION_LINES = {
    'toes': ('M80,1672 Q134,1688 182,1660', 'თითების ამპ.', 1686),
    'ray': ('M86,1648 L150,1668', 'სხივური რეზ.', 1664),
    'tma': ('M80,1618 Q134,1634 186,1610', 'TMA', 1624),
    'chopart_lisfranc': ('M84,1572 Q138,1586 190,1566', 'შოპარი/ლისფრანკი', 1578),
}


def _eval_bezier(pts, t):
    u = 1.0 - t
    if len(pts) == 2:
        return (pts[0][0] + (pts[1][0] - pts[0][0]) * t,
                pts[0][1] + (pts[1][1] - pts[0][1]) * t)
    if len(pts) == 3:
        return (u * u * pts[0][0] + 2 * u * t * pts[1][0] + t * t * pts[2][0],
                u * u * pts[0][1] + 2 * u * t * pts[1][1] + t * t * pts[2][1])
    u2, t2 = u * u, t * t
    return (u2 * u * pts[0][0] + 3 * u2 * t * pts[1][0] + 3 * u * t2 * pts[2][0] + t2 * t * pts[3][0],
            u2 * u * pts[0][1] + 3 * u2 * t * pts[1][1] + 3 * u * t2 * pts[2][1] + t2 * t * pts[3][1])


def sample_curve(pts, n=26):
    """Sample Bezier control points -> list of (x, y, nx, ny, t) with unit normals."""
    out = []
    for i in range(n + 1):
        t = i / n
        x, y = _eval_bezier(pts, t)
        x0, y0 = _eval_bezier(pts, max(0.0, t - 0.01))
        x1, y1 = _eval_bezier(pts, min(1.0, t + 0.01))
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy) or 1.0
        out.append((x, y, -dy / length, dx / length, t))
    return out


def svg_bezier_path(pts):
    if len(pts) == 2:
        return f'M{pts[0][0]},{pts[0][1]} L{pts[1][0]},{pts[1][1]}'
    if len(pts) == 3:
        return f'M{pts[0][0]},{pts[0][1]} Q{pts[1][0]},{pts[1][1]} {pts[2][0]},{pts[2][1]}'
    return f'M{pts[0][0]},{pts[0][1]} C{pts[1][0]},{pts[1][1]} {pts[2][0]},{pts[2][1]} {pts[3][0]},{pts[3][1]}'


def _tube_polygon(samples, r_left_fn, r_right_fn, t0=0.0, t1=1.0):
    sub = [s for s in samples if t0 - 1e-6 <= s[4] <= t1 + 1e-6]
    if len(sub) < 2:
        return ''
    left = [f'{s[0] - s[2] * r_left_fn(s[4]):.1f},{s[1] - s[3] * r_left_fn(s[4]):.1f}' for s in sub]
    right = [f'{s[0] + s[2] * r_right_fn(s[4]):.1f},{s[1] + s[3] * r_right_fn(s[4]):.1f}' for s in reversed(sub)]
    return 'M' + ' L'.join(left) + ' L' + ' L'.join(right) + ' Z'


def _offset_polyline(samples, offset_fn=None, t0=0.0, t1=1.0):
    sub = [s for s in samples if t0 - 1e-6 <= s[4] <= t1 + 1e-6]
    if len(sub) < 2:
        return ''
    pts = []
    for x, y, nx, ny, t in sub:
        off = offset_fn(t) if offset_fn else 0.0
        pts.append(f'{x + nx * off:.1f},{y + ny * off:.1f}')
    return 'M' + ' L'.join(pts)


_PRECOMPUTED_SAMPLES = {row[0]: sample_curve(row[4], 26) for row in VESSEL_GEOMETRY}
_CENTER_PATHS = {row[0]: svg_bezier_path(row[4]) for row in VESSEL_GEOMETRY}


def _stenosis_factor(grade):
    return {'mild': 0.36, 'moderate': 0.60, 'severe': 0.80}.get(grade, 0.0)


def _stenosis_sigma(length_cm):
    if not length_cm:
        return 0.26
    if length_cm < 3.5:
        return 0.18
    if length_cm >= 10.0:
        return 0.38
    return 0.26


def _vessel_radius_fn(r0, r1, seg_data):
    scale = 0.74 if seg_data.get('small_caliber') else 1.0
    aneurysm = seg_data.get('aneurysm') is True

    def radius(t):
        r = (r0 + (r1 - r0) * t) * scale
        if aneurysm:
            r *= 1.0 + 0.68 * math.exp(-((t - 0.5) / 0.28) ** 2)
        return r

    return radius


def _build_collaterals(norm_segs, segment_next):
    """Build corkscrew collateral arcades bridging occluded segment chains."""
    occluded = {k for k, v in norm_segs.items() if v.get('stenosis') == 'occlusion'}
    if not occluded:
        return []

    parents = {}
    for p, children in segment_next.items():
        for c in children:
            parents.setdefault(c, []).append(p)

    collaterals = []
    visited_chains = set()
    for key, *_ in VESSEL_GEOMETRY:
        if key not in occluded or key in visited_chains:
            continue
        if any(p in occluded for p in parents.get(key, ())):
            continue
        chain = [key]
        visited_chains.add(key)
        cur = key
        while segment_next.get(cur):
            nxt_occ = next((c for c in segment_next[cur] if c in occluded), None)
            if not nxt_occ:
                break
            chain.append(nxt_occ)
            visited_chains.add(nxt_occ)
            cur = nxt_occ

        last_seg = norm_segs.get(chain[-1], {})
        if last_seg.get('reconstitution') == 'none' and not segment_next.get(chain[-1]):
            continue

        # Profunda femoris serves as the anatomical collateral origin for proximal SFA CTO
        if chain[0] == 'sfa1' and 'pfa' not in occluded:
            start_smp = _PRECOMPUTED_SAMPLES['pfa'][14]
        else:
            start_smp = _PRECOMPUTED_SAMPLES[chain[0]][0]
        end_smp = _PRECOMPUTED_SAMPLES[chain[-1]][-1]
        x0, y0 = start_smp[0], start_smp[1]
        x1, y1 = end_smp[0], end_smp[1]
        bow_sign = 1.0 if chain[0].startswith('pt') or chain[0] == 'iia' else -1.0
        span = max(45.0, abs(y1 - y0))
        bow = bow_sign * min(44.0, max(22.0, span * 0.16))

        waves = []
        steps = 18
        for i in range(steps + 1):
            u = i / steps
            bx = x0 + (x1 - x0) * u + bow * math.sin(math.pi * u)
            by = y0 + (y1 - y0) * u
            wiggle = math.sin(u * math.pi * 6.0) * 5.5 * math.sin(math.pi * u)
            waves.append(f'{bx + wiggle:.1f},{by:.1f}')
        collaterals.append({
            'd': 'M' + ' L'.join(waves),
            'rx': round(x1, 1),
            'ry': round(y1, 1),
        })
    return collaterals


def _build_interventions(interventions, X, flip):
    """Build SVG primitives for stents, patches, angioplasty, thrombectomy, bypasses, and amputations."""
    stents = []
    patches = []
    balloon_marks = []
    bypasses = []
    bypass_labels = []
    amputations = []

    vein_idx = 0
    prosthetic_idx = 0

    for iv in interventions:
        kind = iv['type']
        if kind == 'stent':
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
                sleeve = _tube_polygon(smp, lambda t, a=r0, b=r1: (a + (b - a) * t) * 0.94,
                                       lambda t, a=r0, b=r1: (a + (b - a) * t) * 0.94, 0.04, 0.96) if covered else ''
                frac_line = None
                if fractured:
                    mid = smp[len(smp) // 2]
                    fr = (r0 + r1) * 0.75
                    frac_line = (round(mid[0] - mid[2] * fr, 1), round(mid[1] - mid[3] * fr, 1),
                                 round(mid[0] + mid[2] * fr, 1), round(mid[1] + mid[3] * fr, 1))
                stents.append({
                    'zig_a': 'M' + ' L'.join(zig_a),
                    'zig_b': 'M' + ' L'.join(zig_b),
                    'sleeve': sleeve,
                    'markers': markers,
                    'fracture': frac_line,
                    'tone': tone,
                })

        elif kind == 'endarterectomy':
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
                    ticks.append((round(x - nx * (rr + 1.2), 1), round(y - ny * (rr + 1.2), 1),
                                  round(x - nx * (rr - 1.6), 1), round(y - ny * (rr - 1.6), 1)))
                    ticks.append((round(x + nx * (rr - 1.6), 1), round(y + ny * (rr - 1.6), 1),
                                  round(x + nx * (rr + 1.2), 1), round(y + ny * (rr + 1.2), 1)))
                patches.append({'d': poly, 'ticks': ticks, 'tone': tone})

        elif kind in ('angioplasty', 'thrombectomy'):
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

        elif kind == 'bypass':
            dist_key = iv['distal']
            if dist_key not in GEOM_BY_KEY:
                continue
            p_end = _PRECOMPUTED_SAMPLES[dist_key][12]
            x3, y3 = p_end[0], p_end[1]

            prox_key = iv['proximal']
            if prox_key == 'contralateral_cfa':
                x0, y0 = 334.0, y3 - 26.0
                is_vein = iv['conduit_group'] == 'vein'
                ctrl = ((x0, y0), (265.0, y0 + 28.0), (205.0, y3 + 22.0), (x3, y3))
            elif prox_key == 'axillary':
                x0, y0 = 50.0, -224.0
                is_vein = False
                ctrl = ((x0, y0), (44.0, (y0 + y3) * 0.35), (56.0, (y0 + y3) * 0.72), (x3, y3))
            elif prox_key in GEOM_BY_KEY:
                p_start = _PRECOMPUTED_SAMPLES[prox_key][12]
                x0, y0 = p_start[0], p_start[1]
                is_vein = iv['conduit_group'] == 'vein'
                y_lo, y_hi = min(y0, y3), max(y0, y3)
                spanned_xs = [pt[0] for seg_smp in _PRECOMPUTED_SAMPLES.values()
                              for pt in seg_smp if y_lo <= pt[1] <= y_hi] or [x0, x3]
                if is_vein:
                    bow_x = min(316.0, max(spanned_xs) + 54.0 + vein_idx * 16.0)
                    vein_idx += 1
                else:
                    bow_x = max(36.0, min(spanned_xs) - 44.0 - prosthetic_idx * 16.0)
                    prosthetic_idx += 1
                dy = y3 - y0
                ctrl = ((x0, y0), (bow_x, y0 + dy * 0.24), (bow_x, y0 + dy * 0.76), (x3, y3))
            else:
                continue

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
                    rings.append((round(rx - rnx * 5.0, 1), round(ry - rny * 5.0, 1),
                                  round(rx + rnx * 5.0, 1), round(ry + rny * 5.0, 1)))

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


def build_map_context(segments_meta, segment_items, norm_segs, interventions, side, segment_next):
    """Assemble full pure-vector SVG context for `_artery_map.html`."""
    vx, vy, vw, vh = MAP_VIEW
    axis = 2 * vx + vw
    flip = side == 'L'
    X = (lambda x: axis - x) if flip else (lambda x: x)

    # Layer 1-3 + PACSS calcium per segment
    adventitia = []
    junction_caps = []
    plaque_beds = []
    lumens = []
    lumen_caps = []
    calc_plates = []
    coral_nodules = []
    thrombi = []

    for key, r0, r1, deep, pts, _lbl in VESSEL_GEOMETRY:
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

        # Patent blood lumen
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
        else:
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

        if d_seg.get('thrombus'):
            for x, y, nx, ny, t in (smp[8], smp[13], smp[18]):
                thrombi.append({'cx': round(x + nx * 0.6, 1), 'cy': round(y + ny * 0.6, 1),
                                'r': round(max(1.4, rad_fn(t) * 0.42), 1)})

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
                coral_nodules.append({'cx': round(x + nx * rr, 1), 'cy': round(y + ny * rr, 1),
                                      'r': round(max(1.5, rad_fn(t) * 0.38), 1)})

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

    # Attach center Bezier path `d` onto segment_items for hit/focus/instant feedback
    enriched_segments = []
    for item in segment_items:
        key = item['key']
        _, r0, _, _, _, _ = GEOM_BY_KEY[key]
        enriched = dict(item)
        enriched['d'] = _CENTER_PATHS[key]
        enriched['w'] = round(r0 * 2.0, 1)
        enriched_segments.append(enriched)

    # Labels (placed upright outside mirror transform)
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

    iv_layers = _build_interventions(interventions, X, flip)

    return {
        'segments': enriched_segments,
        'limb_d': LIMB_SILHOUETTE,
        'skeleton': SKELETON_PATHS,
        'patella': PATELLA,
        'inguinal': inguinal,
        'adventitia': adventitia,
        'junction_caps': junction_caps,
        'plaque_beds': plaque_beds,
        'lumens': lumens,
        'lumen_caps': lumen_caps,
        'collaterals': _build_collaterals(norm_segs, segment_next),
        'calc_plates': calc_plates,
        'coral_nodules': coral_nodules,
        'thrombi': thrombi,
        'iv': iv_layers,
        'labels': labels,
        'marks': marks,
        'side': side,
        'view': f'{vx} {vy} {vw} {vh}',
        'axis': axis,
    }
