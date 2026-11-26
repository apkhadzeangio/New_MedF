"""Pure-vector anterior lower-limb geometry constants and Bezier/tube SVG primitives."""
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

# 4-zone zoom presets (symmetric around axis=300 so R and L behave identically)
ZOOM_ZONES = (
    ('all', 'მთლიანი', '-58 -242 416 1966'),
    ('inflow', 'აორტო-თეძო', '-46 -242 392 605'),
    ('fp', 'ბარძაყ-მუხლქვეშა', '-46 115 392 875'),
    ('btk', 'წვივი', '-46 885 392 835'),
)


def _eval_bezier(pts, t):
    u = 1.0 - t
    if len(pts) == 2:
        return (
            pts[0][0] + (pts[1][0] - pts[0][0]) * t,
            pts[0][1] + (pts[1][1] - pts[0][1]) * t,
        )
    if len(pts) == 3:
        return (
            u * u * pts[0][0] + 2 * u * t * pts[1][0] + t * t * pts[2][0],
            u * u * pts[0][1] + 2 * u * t * pts[1][1] + t * t * pts[2][1],
        )
    u2, t2 = u * u, t * t
    return (
        u2 * u * pts[0][0] + 3 * u2 * t * pts[1][0] + 3 * u * t2 * pts[2][0] + t2 * t * pts[3][0],
        u2 * u * pts[0][1] + 3 * u2 * t * pts[1][1] + 3 * u * t2 * pts[2][1] + t2 * t * pts[3][1],
    )


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
