"""Limb staging for the conference: the artery map, GLASS, WIfI and the PLAN strategy.

The classification tables follow the Global Vascular Guidelines on CLTI
(Conte et al., SVS/ESVS/WFVS 2019) and the SVS WIfI system (Mills 2014);
see TABLES_SOURCE. Everything is decision support: the surgeon or the
conference decides.
"""

import collections
import datetime
import json
import re

# --------------------------------------------------------------------------
# the artery map (one leg, front view; the left leg is drawn mirrored)
# --------------------------------------------------------------------------

SCHEMA_VERSION = 2
SHARED_SEGMENTS = ('aorta',)

# Right leg, front view, drawn over an anatomical illustration (static/img/limb/leg-anterior.webp,
# generated with Figma AI). Coordinates are the illustration's pixels (285 x 1776); the iliac arteries
# continue above it towards the aortic bifurcation (body midline at x=300). The left leg is mirrored.
# key, Georgian name, label, SVG path, group, vessel width, label position (x, y, anchor[, "v" = vertical])
SEGMENTS = (
    ('aorta', 'ინფრარენული აორტა', 'Ao', 'M300,-230 L300,-175', 'inflow', 16, (282, -205, 'end')),
    ('cia', 'საერთო თეძოს არტერია', 'CIA', 'M300,-175 C288,-140 270,-110 250,-82', 'inflow', 14, (236, -140, 'end')),
    ('iia', 'შიგნითა თეძოს არტერია', 'IIA', 'M250,-82 C262,-50 270,-20 272,20', 'inflow', 9.5, (284, 6, 'start')),
    ('eia', 'გარეთა თეძოს არტერია', 'EIA', 'M250,-82 C222,-46 176,10 150,70 C146,82 142,96 140,108', 'inflow', 13, (176, -30, 'end')),
    ('cfa', 'საერთო ბარძაყის არტერია', 'CFA', 'M140,108 C142,124 144,138 146,152', 'inflow', 13, (122, 132, 'end')),
    ('pfa', 'ბარძაყის ღრმა არტერია', 'PFA', 'M146,152 C130,176 116,220 108,290', 'inflow', 9, (92, 250, 'end')),
    ('sfa1', 'ზედაპირული ბარძაყის — პროქსიმალური', 'SFA', 'M146,152 C150,190 156,228 163,268', 'fp', 11, (176, 214, 'start')),
    ('sfa2', 'ზედაპირული ბარძაყის — შუა', '', 'M163,268 C172,320 182,372 191,424', 'fp', 11, None),
    ('sfa3', 'ზედაპირული ბარძაყის — დისტალური', '', 'M191,424 C198,470 206,520 208,580', 'fp', 11, None),
    ('pop1', 'მუხლქვეშა — მუხლზემოთ (P1)', 'P1', 'M208,580 C206,650 180,730 160,800 C156,814 152,830 150,850', 'fp', 10, (214, 700, 'start')),
    ('pop2', 'მუხლქვეშა — სახსრის დონე (P2)', 'P2', 'M150,850 C148,868 147,884 146,898', 'fp', 10, (172, 874, 'start')),
    ('pop3', 'მუხლქვეშა — მუხლქვემოთ (P3)', 'P3', 'M146,898 C145,912 143,926 142,940', 'fp', 10, (170, 920, 'start')),
    ('tpt', 'წვივ-მცირე წვივის ღერო', 'TPT', 'M142,940 C143,955 145,970 146,985', 'ip', 9, (164, 966, 'start')),
    ('at1', 'წინა წვივის — პროქსიმალური', 'AT', 'M142,940 C124,944 104,952 95,976', 'ip', 8, (78, 990, 'end')),
    ('at2', 'წინა წვივის — შუა', '', 'M95,976 C91,1040 89,1110 90,1180', 'ip', 8, None),
    ('at3', 'წინა წვივის — დისტალური', '', 'M90,1180 C92,1280 100,1400 112,1482', 'ip', 7.5, None),
    ('pt1', 'უკანა წვივის — პროქსიმალური', 'PT', 'M146,985 C152,1030 158,1075 162,1120', 'ip', 8, (196, 1150, 'start')),
    ('pt2', 'უკანა წვივის — შუა', '', 'M162,1120 C167,1180 170,1235 172,1290', 'ip', 8, None),
    ('pt3', 'უკანა წვივის — დისტალური', '', 'M172,1290 C174,1370 175,1450 176,1512', 'ip', 7.5, None),
    ('per1', 'მცირე წვივის — პროქსიმალური', 'PER', 'M146,985 C141,1030 137,1075 134,1120', 'ip', 7.5, (114, 1200, 'middle', 'v')),
    ('per2', 'მცირე წვივის — შუა', '', 'M134,1120 C130,1175 128,1228 126,1280', 'ip', 7.5, None),
    ('per3', 'მცირე წვივის — დისტალური', '', 'M126,1280 C124,1340 122,1390 120,1432', 'ip', 7, None),
    ('dp', 'ტერფის ზურგის არტერია', 'DP', 'M112,1482 C120,1530 130,1585 140,1638', 'pedal', 7, (100, 1580, 'end')),
    ('mpa', 'მედიალური პლანტარული არტერია', 'MPA', 'M176,1512 C180,1552 178,1596 168,1636', 'pedal', 6, (196, 1618, 'start')),
    ('lpa', 'ლატერალური პლანტარული არტერია', 'LPA', 'M176,1512 C174,1560 158,1600 128,1626 C112,1638 98,1642 88,1646', 'pedal', 6.5, (192, 1560, 'start')),
    ('arch', 'ტერფის რკალი', 'ARCH', 'M88,1646 C100,1668 124,1672 140,1638', 'pedal', 6, (114, 1700, 'middle')),
)
SEGMENT_KEYS = {s[0] for s in SEGMENTS}
SEGMENT_ORDER = {s[0]: i for i, s in enumerate(SEGMENTS)}
LEGACY_SEGMENT_MAP = {'plantar': 'lpa'}

# Directed anatomical tree (proximal -> distal) for grouping stents and tracing bypass paths
SEGMENT_NEXT = {
    'aorta': ('cia',),
    'cia': ('iia', 'eia'),
    'iia': (),
    'eia': ('cfa',),
    'cfa': ('pfa', 'sfa1'),
    'pfa': (),
    'sfa1': ('sfa2',),
    'sfa2': ('sfa3',),
    'sfa3': ('pop1',),
    'pop1': ('pop2',),
    'pop2': ('pop3',),
    'pop3': ('at1', 'tpt'),
    'tpt': ('pt1', 'per1'),
    'at1': ('at2',),
    'at2': ('at3',),
    'at3': ('dp',),
    'pt1': ('pt2',),
    'pt2': ('pt3',),
    'pt3': ('mpa', 'lpa'),
    'per1': ('per2',),
    'per2': ('per3',),
    'per3': (),
    'dp': ('arch',),
    'mpa': (),
    'lpa': ('arch',),
    'arch': (),
}

_SEGMENT_RANK = {'aorta': 0}
_SEGMENT_ADJ = {k: set() for k in SEGMENT_KEYS}
_queue = collections.deque(['aorta'])
while _queue:
    _cur = _queue.popleft()
    for _nxt in SEGMENT_NEXT[_cur]:
        _SEGMENT_ADJ[_cur].add(_nxt)
        _SEGMENT_ADJ[_nxt].add(_cur)
        if _nxt not in _SEGMENT_RANK:
            _SEGMENT_RANK[_nxt] = _SEGMENT_RANK[_cur] + 1
            _queue.append(_nxt)
assert len(SEGMENTS) == 26 and set(_SEGMENT_RANK) == SEGMENT_KEYS

# Schema v2 segment attributes (SVS 2016 reporting standards + PACSS, Rocha-Singh et al. 2014)
STENOSIS = (
    ('none', 'ნორმა (<20%)'),
    ('mild', '20–49%'),
    ('moderate', '50–69%'),
    ('severe', '70–99%'),
    ('occlusion', 'ოკლუზია (CTO)'),
)
LENGTH_CHIPS = (
    (1.5, '<2 სმ'),
    (2.5, '2–3 სმ'),
    (4.0, '3–5 სმ'),
    (7.5, '5–10 სმ'),
    (15.0, '10–20 სმ'),
    (25.0, '>20 სმ'),
)
PACSS = (
    (0, 'PACSS 0 — კალციუმი არ ჩანს'),
    (1, 'PACSS 1 — ცალმხრივი <5 სმ'),
    (2, 'PACSS 2 — ცალმხრივი ≥5 სმ'),
    (3, 'PACSS 3 — ორმხრივი <5 სმ'),
    (4, 'PACSS 4 — ორმხრივი ≥5 სმ'),
)
PACSS_SHORT = (
    (0, '0 — არა'),
    (1, '1 — ცალმხ. <5'),
    (2, '2 — ცალმხ. ≥5'),
    (3, '3 — ორმხ. <5'),
    (4, '4 — ორმხ. ≥5'),
)
RECONSTITUTION = (
    ('yes', 'აღდგება კოლატერალებით'),
    ('delayed', 'დაგვიანებული აღდგენა'),
    ('none', 'არ აღდგება'),
)
MORPHOLOGY = (
    ('', 'არ არის მითითებული'),
    ('eccentric', 'ექსცენტრული'),
    ('concentric', 'კონცენტრული'),
)
SEGMENT_FLAGS = (
    ('coral', 'მარჯნისებრი კალციუმი'),
    ('thrombus', 'თრომბოზი'),
    ('aneurysm', 'ანევრიზმა'),
    ('ulcerated', 'დაწყლულებული ფოლაქი'),
    ('small_caliber', 'მცირე კალიბრი (<6 მმ)'),
    ('trifurcation', 'ტრიფურკაციაზე გავრცელება'),
)
SEGMENT_FLAGS_L2 = tuple(x for x in SEGMENT_FLAGS if x[0] != 'trifurcation')

# 6 foot & ankle angiosomes (Taylor & Palmer 1987; Attinger et al. 2006)
ANGIOSOMES = (
    ('', 'არ არის მითითებული'),
    ('none', 'ჭრილობა არ არის'),
    ('dorsal', 'ტერფის ზურგი / დორსალური თითები (AT → DP)'),
    ('medial_plantar', 'მედიალური ძირი / I თითი (PT → MPA)'),
    ('lateral_plantar', 'ლატერალური ძირი / ლატერალური თითები (PT → LPA)'),
    ('medial_heel', 'მედიალური ქუსლი (PT)'),
    ('lateral_heel', 'ლატერალური ქუსლი (PER)'),
    ('lateral_ankle', 'კოჭის წინა-ლატერალური უბანი (PER)'),
)
ANGIOSOME_CARDS = (
    ('none', 'ჭრილობა არ არის', ''),
    ('dorsal', '1. ტერფის ზურგი', 'AT → DP'),
    ('medial_plantar', '2. მედიალური ძირი / I თითი', 'PT → MPA'),
    ('lateral_plantar', '3. ლატერალური ძირი / თითები', 'PT → LPA'),
    ('medial_heel', '4. ქუსლი — მედიალური', 'PT'),
    ('lateral_heel', '5. ქუსლი — ლატერალური', 'PER'),
    ('lateral_ankle', '6. კოჭის წინა-ლატერალური', 'PER'),
)
ANGIOSOME_SOURCE = {
    'dorsal': 'at',
    'medial_plantar': 'pt',
    'lateral_plantar': 'pt',
    'medial_heel': 'pt',
    'lateral_heel': 'per',
    'lateral_ankle': 'per',
}
ANGIOSOME_TERMINAL = {
    'dorsal': 'dp',
    'medial_plantar': 'mpa',
    'lateral_plantar': 'lpa',
}

# Prior limb interventions (6 categories; Tosaka et al. 2012 for FP in-stent restenosis)
MAX_INTERVENTIONS = 20
INTERVENTION_TYPES = (
    ('bypass', 'შუნტირება'),
    ('stent', 'სტენტირება'),
    ('angioplasty', 'ბალონური ანგიოპლასტიკა / ათერექტომია'),
    ('endarterectomy', 'ენდარტერექტომია / პლასტიკა'),
    ('amputation', 'მცირე ამპუტაცია'),
    ('thrombectomy', 'თრომბექტომია / თრომბოლიზისი'),
)
BYPASS_CONDUIT_GROUPS = (
    ('vein', 'აუტოვენა'),
    ('prosthetic', 'სინთეზური პროთეზი'),
    ('other', 'სხვა კონდუიტი'),
    ('unspecified', 'დაუზუსტებელი'),
)
BYPASS_CONDUITS = {
    'vein': (
        ('gsv_reversed', 'დიდი კანქვეშა ვენა — რევერსირებული'),
        ('gsv_insitu', 'დიდი კანქვეშა ვენა — in situ'),
        ('gsv_nonrev', 'დიდი კანქვეშა ვენა — ტრანსლოცირებული'),
        ('ssv', 'მცირე კანქვეშა ვენა'),
        ('arm_vein', 'ზედა კიდურის ვენა'),
        ('spliced', 'შედგენილი ვენური კონდუიტი'),
    ),
    'prosthetic': (
        ('ptfe', 'PTFE'),
        ('ptfe_ringed', 'არმირებული PTFE'),
        ('ptfe_heparin', 'ჰეპარინით დაფარული PTFE'),
        ('dacron', 'დაკრონი'),
    ),
    'other': (
        ('allograft', 'ალოგრაფტი'),
        ('composite', 'კომპოზიტური (პროთეზი + ვენა)'),
    ),
    'unspecified': (),
}
BYPASS_CUFFS = (('', 'მანჟეტის გარეშე'), ('miller', 'ვენური მანჟეტი (Miller / Taylor)'))
EXTRA_ORIGINS = (
    ('contralateral_cfa', 'მოპირდაპირე ბარძაყის არტერია (fem-fem)'),
    ('axillary', 'აქსილარული არტერია (აქსილო-ფემორალური)'),
)
BYPASS_ORIGINS = SEGMENT_KEYS | {k for k, _ in EXTRA_ORIGINS}
BYPASS_STATUS = (
    ('patent', 'გამავალი'),
    ('stenosed', 'სტენოზირებული'),
    ('occluded', 'თრომბირებული'),
)
BYPASS_SITES = (('', 'არ არის მითითებული'), ('proximal', 'პროქსიმალური ანასტომოზი'),
                ('body', 'შუნტის სხეული'), ('distal', 'დისტალური ანასტომოზი'))
STENT_SUBTYPES = (
    ('bms', 'ლითონის სტენტი (BMS)'),
    ('interwoven', 'წნული ნიტინოლი (Supera)'),
    ('des', 'წამლით დაფარული სტენტი (DES)'),
    ('covered', 'დაფარული სტენტ-გრაფტი (Viabahn)'),
    ('unspecified', 'სტენტი / ანგიოპლასტიკა — დაუზუსტებელი'),
)
STENT_STATUS = (
    ('patent', 'გამავალი'),
    ('isr_1', 'Tosaka I — ფოკალური რესტენოზი (≤5 სმ)'),
    ('isr_2', 'Tosaka II — დიფუზური რესტენოზი (>5 სმ)'),
    ('occluded', 'Tosaka III — სტენტის ოკლუზია'),
    ('fractured', 'სტენტის მოტეხილობა'),
)
ANGIOPLASTY_SUBTYPES = (
    ('poba', 'ჩვეულებრივი ბალონი (POBA)'),
    ('dcb', 'წამლით დაფარული ბალონი (DCB)'),
    ('scoring', 'ჭრილიანი ბალონი (Scoring / Cutting)'),
    ('atherectomy', 'ათერექტომია'),
    ('unspecified', 'დაუზუსტებელი'),
)
ENDARTERECTOMY_SUBTYPES = (
    ('patch_vein', 'აუტოვენური პატჩით'),
    ('patch_bovine', 'ქსენოპერიკარდიუმის პატჩით'),
    ('patch_prosthetic', 'სინთეზური პატჩით'),
    ('profundaplasty', 'პროფუნდაპლასტიკა'),
    ('primary', 'პირველადი ნაკერი'),
    ('unspecified', 'დაუზუსტებელი'),
)
AMPUTATION_SUBTYPES = (
    ('toes', 'თით(ებ)ის ამპუტაცია'),
    ('ray', 'სხივური რეზექცია'),
    ('tma', 'ტრანსმეტატარზული (TMA)'),
    ('chopart_lisfranc', 'შოპარი / ლისფრანკი'),
)
AMPUTATION_STATUS = (('healed', 'შეხორცებული'), ('nonhealing', 'შეუხორცებელი'))
THROMBECTOMY_SUBTYPES = (
    ('fogarty', 'ღია თრომბექტომია (Fogarty)'),
    ('mechanical', 'მექანიკური / ასპირაციული თრომბექტომია'),
    ('lysis', 'კათეტერული თრომბოლიზისი'),
    ('unspecified', 'დაუზუსტებელი'),
)
VESSEL_INTERVENTION_STATUS = (
    ('patent', 'გამავალი'),
    ('restenosed', 'რესტენოზი'),
    ('occluded', 'ოკლუზია'),
)

# Quick toolbar options on the map (prior interventions now live in their own structured list)
STATES = (
    ('n', 'ნორმა'),
    ('mild', 'სტენოზი <50%'),
    ('st', 'სტენოზი ≥50%'),
    ('oc', 'ოკლუზია'),
)
FLAGS = (
    ('ca', 'მძიმე კალციფიკაცია'),
)

SEGMENT_GROUPS = (
    ('inflow', 'აორტო-თეძო-ბარძაყი'),
    ('fp', 'ბარძაყ-მუხლქვეშა'),
    ('ip', 'წვივი'),
    ('pedal', 'ტერფი'),
)
SEGMENT_SHORT = {
    'aorta': 'Ao', 'cia': 'CIA', 'iia': 'IIA', 'eia': 'EIA', 'cfa': 'CFA', 'pfa': 'PFA',
    'sfa1': 'SFA პროქს.', 'sfa2': 'SFA შუა', 'sfa3': 'SFA დისტ.',
    'pop1': 'P1', 'pop2': 'P2', 'pop3': 'P3',
    'tpt': 'TPT',
    'at1': 'AT პროქს.', 'at2': 'AT შუა', 'at3': 'AT დისტ.',
    'pt1': 'PT პროქს.', 'pt2': 'PT შუა', 'pt3': 'PT დისტ.',
    'per1': 'PER პროქს.', 'per2': 'PER შუა', 'per3': 'PER დისტ.',
    'dp': 'DP', 'mpa': 'MPA', 'lpa': 'LPA', 'arch': 'ტერფის რკალი',
    'contralateral_cfa': 'მოპირდაპირე CFA (fem-fem)',
    'axillary': 'აქსილარული არტერია',
}

MAP_VIEW = (-70, -230, 420, 2006)          # x, y, width, height of the drawing
LEG_IMAGE = {'src': 'img/limb/leg-anterior.webp', 'width': 285, 'height': 1776}
CONTEXT = []                               # aorta and internal iliac are now interactive segments
LANDMARKS = (('მუხლი', 880), ('კოჭი', 1515))

_LEGACY_STENOSIS = {'n': 'none', 'mild': 'mild', 'st': 'severe', 'oc': 'occlusion'}
_CONDUIT_TO_GROUP = {code: grp for grp, items in BYPASS_CONDUITS.items() for code, _ in items}
_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,24}$')


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
    valid_types = {k for k, _ in INTERVENTION_TYPES}
    conduit_groups = {k for k, _ in BYPASS_CONDUIT_GROUPS}
    bypass_statuses = {k for k, _ in BYPASS_STATUS}
    bypass_sites = {'proximal', 'body', 'distal'}
    stent_subtypes = {k for k, _ in STENT_SUBTYPES}
    stent_statuses = {k for k, _ in STENT_STATUS}
    pta_subtypes = {k for k, _ in ANGIOPLASTY_SUBTYPES}
    ea_subtypes = {k for k, _ in ENDARTERECTOMY_SUBTYPES}
    amp_subtypes = {k for k, _ in AMPUTATION_SUBTYPES}
    amp_statuses = {k for k, _ in AMPUTATION_STATUS}
    thromb_subtypes = {k for k, _ in THROMBECTOMY_SUBTYPES}
    vessel_statuses = {k for k, _ in VESSEL_INTERVENTION_STATUS}

    for entry in raw:
        if len(out) >= MAX_INTERVENTIONS:
            break
        if not isinstance(entry, dict):
            continue
        kind = entry.get('type')
        if kind not in valid_types:
            continue
        raw_id = str(entry.get('id') or '').strip()
        if not _ID_RE.match(raw_id) or raw_id in used_ids:
            idx = len(out) + 1
            while f'iv{idx}' in used_ids:
                idx += 1
            raw_id = f'iv{idx}'

        performed_on = clean_partial_date(entry.get('performed_on'), today=today)
        note = _bmp_line(entry.get('note'), 200)

        if kind == 'bypass':
            proximal = LEGACY_SEGMENT_MAP.get(entry.get('proximal'), entry.get('proximal'))
            distal = LEGACY_SEGMENT_MAP.get(entry.get('distal'), entry.get('distal'))
            if proximal not in BYPASS_ORIGINS or distal not in SEGMENT_KEYS:
                continue
            conduit = entry.get('conduit') or ''
            group = entry.get('conduit_group')
            if group not in conduit_groups:
                group = _CONDUIT_TO_GROUP.get(conduit, 'unspecified')
            allowed_conduits = {k for k, _ in BYPASS_CONDUITS[group]}
            if conduit not in allowed_conduits:
                conduit = ''
            status = entry.get('status') if entry.get('status') in bypass_statuses else 'patent'
            site = entry.get('site') if status == 'stenosed' and entry.get('site') in bypass_sites else ''
            item = {
                'id': raw_id, 'type': 'bypass',
                'conduit_group': group, 'conduit': conduit,
                'cuff': 'miller' if entry.get('cuff') == 'miller' else '',
                'proximal': proximal, 'distal': distal,
                'performed_on': performed_on, 'status': status, 'site': site, 'note': note,
            }
        elif kind == 'amputation':
            subtype = entry.get('subtype')
            if subtype not in amp_subtypes:
                continue
            status = entry.get('status') if entry.get('status') in amp_statuses else 'healed'
            item = {
                'id': raw_id, 'type': 'amputation', 'subtype': subtype,
                'performed_on': performed_on, 'status': status, 'note': note,
            }
        else:
            segs = _sort_segments(entry.get('segments') or []) if isinstance(entry.get('segments'), (list, tuple)) else []
            if not segs:
                continue
            if kind == 'stent':
                subtype = entry.get('subtype') if entry.get('subtype') in stent_subtypes else 'unspecified'
                status = entry.get('status') if entry.get('status') in stent_statuses else 'patent'
            elif kind == 'angioplasty':
                subtype = entry.get('subtype') if entry.get('subtype') in pta_subtypes else 'unspecified'
                status = entry.get('status') if entry.get('status') in vessel_statuses else 'patent'
            elif kind == 'endarterectomy':
                subtype = entry.get('subtype') if entry.get('subtype') in ea_subtypes else 'unspecified'
                status = entry.get('status') if entry.get('status') in vessel_statuses else 'patent'
            else:                               # thrombectomy
                subtype = entry.get('subtype') if entry.get('subtype') in thromb_subtypes else 'unspecified'
                raw_st = 'occluded' if entry.get('status') == 'reoccluded' else entry.get('status')
                status = raw_st if raw_st in vessel_statuses else 'patent'
            item = {
                'id': raw_id, 'type': kind, 'subtype': subtype, 'segments': segs,
                'performed_on': performed_on, 'status': status, 'note': note,
            }

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


_INLINE_TYPES = ('stent', 'angioplasty', 'endarterectomy', 'thrombectomy')


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
    for slot, iv in zip(dated_slots, dated_sorted):
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
                kind, type_labels[kind])
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
        items = [[key, SEGMENT_SHORT.get(key, key), name]
                 for key, name, _short, _d, g, _w, _lab in SEGMENTS if g == grp_key]
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
    for key, name, short, d, group, width, label in SEGMENTS:
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
    from . import artery_map

    cleaned_ivs = clean_interventions(interventions)
    cleaned_segs = clean_segments(segments)
    items = segment_view(segments, interventions=cleaned_ivs)
    return artery_map.build_map_context(SEGMENTS, items, cleaned_segs, cleaned_ivs, side, SEGMENT_NEXT)


def foot_context(segments, side, interventions=None, wound_angiosome=''):
    """Lateral-oblique pedal arch & 6 angiosome territories context for `_foot_map.html`."""
    from . import artery_map

    cleaned_ivs = clean_interventions(interventions)
    cleaned_segs = clean_segments(segments)
    items = segment_view(segments, interventions=cleaned_ivs)
    angio = wound_angiosome if wound_angiosome in ANGIOSOME_SOURCE else ''
    return artery_map.build_foot_context(items, cleaned_segs, cleaned_ivs, side, angio)


def zoom_zones():
    from . import artery_map
    return artery_map.ZOOM_ZONES


# --------------------------------------------------------------------------
# The tables (transcribed from the guideline; page numbers of the PMC author
# manuscript nihms-1728674: Tables 3.3-3.5 p.176-178, 5.1-5.4 p.180-183,
# Figs 5.1-5.3 p.155-157, 6.3-6.4 p.164-165)
# --------------------------------------------------------------------------

TABLES_SOURCE = ('Global Vascular Guidelines on the Management of CLTI (Conte et al., SVS/ESVS/WFVS, 2019): '
                 'WIfI — ცხრ. 3.2–3.5, სურ. 6.3; GLASS — ცხრ. 5.1–5.4, სურ. 5.1–5.3; PLAN — სურ. 6.4, რეკ. 6.4–6.5, 6.32–6.37.')

# WIfI wound (Table 3.2)
WOUND = (
    (0, 'ჭრილობა არ არის', 'წყლული და განგრენა არ არის (იშემიური მოსვენების ტკივილი).'),
    (1, 'მცირე', 'მცირე, ზედაპირული წყლული წვივის დისტალურ ნაწილზე ან ტერფზე; ძვალი არ ჩანს '
        '(გარდა დისტალური ფალანგისა); განგრენა არ არის. გადარჩენა — 1–2 თითის მარტივი ამპუტაციით ან კანის დაფარვით.'),
    (2, 'ღრმა / თითების განგრენა', 'ღრმა წყლული — ჩანს ძვალი, სახსარი ან მყესი (ქუსლის გარეშე), ან ზედაპირული ქუსლის '
        'წყლული ქუსლის ძვლის გარეშე; განგრენა მხოლოდ თითებზე. გადარჩენა — ≥3 თითის ამპუტაციით ან სტანდარტული TMA-ით.'),
    (3, 'ვრცელი', 'ვრცელი ღრმა წყლული ტერფის წინა და/ან შუა ნაწილზე, ან ღრმა ქუსლის წყლული (± ქუსლის ძვალი); '
        'ვრცელი განგრენა ან ქუსლის ნეკროზი. გადარჩენა — მხოლოდ რთული რეკონსტრუქციით (Chopart, Lisfranc), ლოსკუტით.'),
)

# WIfI ischemia (Table 3.3): ABI, ankle systolic pressure (mmHg), toe pressure / TcPO2 (mmHg)
ISCHEMIA = (
    (0, 'ABI ≥0.80', 'AP >100', 'TP/TcPO2 ≥60'),
    (1, 'ABI 0.60–0.79', 'AP 70–100', 'TP/TcPO2 40–59'),
    (2, 'ABI 0.40–0.59', 'AP 50–70', 'TP/TcPO2 30–39'),
    (3, 'ABI ≤0.39', 'AP <50', 'TP/TcPO2 <30'),
)
ISCHEMIA_NOTE = ('თუ TP და ABI სხვადასხვა ხარისხს იძლევა, მთავარია TP. არაკომპრესირებადი ABI-ის (>1.3) დროს — TP ან TcPO2. '
                 'დიაბეტისას TP აუცილებელია. ბრტყელი PVR — ხარისხი 3.')


def grade_abi(v):
    if v is None:
        return None
    return 0 if v >= 0.80 else 1 if v >= 0.60 else 2 if v >= 0.40 else 3


def grade_ap(v):
    if v is None:
        return None
    return 0 if v > 100 else 1 if v >= 70 else 2 if v >= 50 else 3


def grade_tp(v):
    if v is None:
        return None
    return 0 if v >= 60 else 1 if v >= 40 else 2 if v >= 30 else 3


def suggest_ischemia(abi=None, ap=None, tp=None):
    """The ischemia grade the measurements point to, and why (Table 3.3 rules)."""
    g_abi, g_ap, g_tp = grade_abi(abi), grade_ap(ap), grade_tp(tp)
    if g_tp is not None:
        return g_tp, 'TP'                       # "TP will be the primary determinant"
    if abi is not None and abi > 1.3:
        return (g_ap, 'AP') if g_ap is not None else (None, 'ABI არაკომპრესირებადია — საჭიროა TP/TcPO2')
    grades = [g for g in (g_abi, g_ap) if g is not None]
    if not grades:
        return None, ''
    return max(grades), 'ABI/AP'


# WIfI foot infection (Table 3.4, IDSA/PEDIS)
INFECTION = (
    (0, 'ინფექცია არ არის', 'ინფექციის სიმპტომები და ნიშნები არ არის.'),
    (1, 'მსუბუქი', 'ადგილობრივი ინფექცია მხოლოდ კანსა და კანქვეშა ქსოვილში (≥2: შეშუპება/ინდურაცია, ერითემა 0.5–2 სმ, '
        'მტკივნეულობა, სიცხე, ჩირქი); სისტემური ნიშნების გარეშე.'),
    (2, 'ზომიერი', 'ერითემა >2 სმ ან ღრმა სტრუქტურები (აბსცესი, ოსტეომიელიტი, სეპტიური ართრიტი, ფასციიტი); SIRS-ის გარეშე.'),
    (3, 'მძიმე', 'ადგილობრივი ინფექცია + SIRS (≥2: t >38°C ან <36°C; HR >90; RR >20 ან PaCO2 <32; WBC >12000 ან <4000 ან ≥10% ჩხირბირთვიანი).'),
)

# WIfI clinical stage (Table 3.5). The printed list leaves W0-I1-fI2 unassigned and lists
# W0-I2-fI1 under both stage 2 and stage 3; as in the original WIfI matrix (and keeping the
# stages non-decreasing with every grade) W0-I1-fI2 is stage 3 and W0-I2-fI1 is stage 2.
_STAGE_LIST = {
    1: ('0 0 0,1', '0 1 0', '1 0 0,1', '1 1 0'),
    2: ('0 0 2', '0 1 1', '0 2 0,1', '0 3 0', '1 0 2', '1 1 1', '1 2 0', '2 0 0,1'),
    3: ('0 0 3', '0 1 2', '0 2 2', '0 3 1,2', '1 0 3', '1 1 2', '1 2 1', '1 3 0,1', '2 0 2', '2 1 0,1', '2 2 0', '3 0 0,1'),
    4: ('0 1,2,3 3', '1 1 3', '1 2,3 2,3', '2 0 3', '2 1 2,3', '2 2 1,2,3', '2 3 0,1,2,3', '3 0 2,3', '3 1,2,3 0,1,2,3'),
}
WIFI_STAGE = {}
for _stage, _items in _STAGE_LIST.items():
    for _item in _items:
        _w, _i, _f = (tuple(int(x) for x in part.split(',')) for part in _item.split())
        for w in _w:
            for i in _i:
                for f in _f:
                    WIFI_STAGE[(w, i, f)] = _stage
assert len(WIFI_STAGE) == 64

WIFI_RISK = {1: 'ძალიან დაბალი', 2: 'დაბალი', 3: 'ზომიერი', 4: 'მაღალი', 5: 'კიდური გადარჩენადი არ არის'}

# benefit of revascularization (Fig 6.3): ischemia grade x WIfI stage 1-4 -> 0 (N/A) .. 4 (high)
_BENEFIT = {3: (0, 4, 4, 4), 2: (0, 2, 3, 4), 1: (1, 2, 3, 3), 0: (1, 1, 1, 1)}
BENEFIT_LABEL = {0: 'არ ეხება', 1: 'დაბალი / არ არის', 2: 'დაბალი', 3: 'ზომიერი', 4: 'მაღალი'}

# GLASS femoropopliteal grade (Fig 5.2)
FP_GRADES = (
    (0, 'მსუბუქი ან არამნიშვნელოვანი (<50%) დაავადება.'),
    (1, 'SFA-ს დაავადების ჯამური სიგრძე <1/3 (<10 სმ); შეიძლება — ერთი ფოკალური CTO (<5 სმ), თუ არ არის ostial (flush) '
        'ოკლუზია; მუხლქვეშა არტერია — მსუბუქი ან არამნიშვნელოვანი დაავადება.'),
    (2, 'SFA-ს დაავადება 1/3–2/3 (10–20 სმ); შეიძლება — CTO ჯამში <1/3 (10 სმ), არა flush; მუხლქვეშა არტერიის '
        'ფოკალური სტენოზი <2 სმ, ტრიფურკაციის გარეშე.'),
    (3, 'SFA-ს დაავადება >2/3 (>20 სმ); შეიძლება — ნებისმიერი flush ოკლუზია <20 სმ ან არა-flush CTO 10–20 სმ; '
        'მუხლქვეშა არტერიის მოკლე სტენოზი 2–5 სმ, ტრიფურკაციის გარეშე.'),
    (4, 'SFA-ს ოკლუზია >20 სმ; მუხლქვეშა არტერიის დაავადება >5 სმ ან ტრიფურკაციაზე გავრცელებით; მუხლქვეშა არტერიის ნებისმიერი CTO.'),
)

# GLASS infrapopliteal grade (Fig 5.3), in the target arterial path
IP_GRADES = (
    (0, 'სამიზნე არტერიის გზაზე მსუბუქი ან არამნიშვნელოვანი დაავადება.'),
    (1, 'წვივის არტერიის ფოკალური სტენოზი <3 სმ.'),
    (2, 'სტენოზი არტერიის სიგრძის 1/3-ზე; შეიძლება — ფოკალური CTO (<3 სმ); TP ღეროსა და წვივის არტერიის სათავის გარეშე.'),
    (3, 'დაავადება არტერიის სიგრძის 2/3-მდე; CTO 1/3-მდე (შეიძლება სათავეც, მაგრამ არა TP ღერო).'),
    (4, 'დიფუზური სტენოზი >2/3; CTO >1/3 (შეიძლება სათავეც); TP ღეროს ნებისმიერი CTO, თუ სამიზნე არ არის AT.'),
)
CALC_NOTE = 'მძიმე კალციფიკაცია (>50% გარშემოწერილობის, დიფუზური, მასიური ან „მარჯნისებრი“) სეგმენტის ხარისხს 1-ით ზრდის.'

# GLASS stage (Table 5.3): [FP grade][IP grade]; None = not applicable
GLASS_MATRIX = {
    4: ('III', 'III', 'III', 'III', 'III'),
    3: ('II', 'II', 'II', 'III', 'III'),
    2: ('I', 'II', 'II', 'II', 'III'),
    1: ('I', 'I', 'II', 'II', 'III'),
    0: (None, 'I', 'I', 'II', 'III'),
}
GLASS_TEXT = {  # Table 5.4
    'I': 'დაბალი სირთულე: ტექნიკური წარუმატებლობა <10%, 1 წლის LBP >70%.',
    'II': 'საშუალო სირთულე: ტექნიკური წარუმატებლობა <20%, 1 წლის LBP 50–70%.',
    'III': 'მაღალი სირთულე: ტექნიკური წარუმატებლობა >20%, 1 წლის LBP <50%.',
}

# inframalleolar / pedal descriptor (Fig 5.1)
PEDAL = (
    ('P0', 'სამიზნე არტერია კოჭს კვეთს და ტერფში გადადის; ტერფის რკალი ინტაქტურია.'),
    ('P1', 'სამიზნე არტერია კოჭს კვეთს და ტერფში გადადის; რკალი არ არის ან მძიმედ დაზიანებულია.'),
    ('P2', 'კოჭს არცერთი სამიზნე არტერია არ კვეთს.'),
)

# inflow (Table 5.2) — staged separately, assumed corrected for GLASS
INFLOW_AI = (
    ('', 'მნიშვნელოვანი დაავადება არ არის'),
    ('I', 'I — CIA/EIA სტენოზი, ერთ-ერთის (არა ორივეს) CTO, ინფრარენული აორტის სტენოზი'),
    ('II', 'II — აორტის CTO, CIA+EIA CTO, მძიმე დიფუზური / <6 მმ, ანევრიზმა, მძიმე დიფუზური in-stent რესტენოზი'),
)
INFLOW_CFA = (('A', 'A — CFA-ს მნიშვნელოვანი დაავადება არ არის'), ('B', 'B — CFA-ს სტენოზი >50%'))

TAP = (('at', 'წინა წვივის (AT)'), ('pt', 'უკანა წვივის (PT)'), ('per', 'მცირე წვივის (პერონეალური)'))
RISK = (('avg', 'საშუალო რისკი: პერიოპერაციული სიკვდილობა <5% და 2-წლიანი გადარჩენა >50%'),
        ('high', 'მაღალი რისკი: სიკვდილობა ≥5% ან 2-წლიანი გადარჩენა ≤50%'))
VEIN = (('yes', 'ვარგისი აუტოვენა არის'), ('no', 'ვარგისი ვენა არ არის'))

# PLAN (Fig 6.4): average-risk patient with suitable autologous vein; GLASS stage x WIfI stage 1-4
PLAN = {
    'III': ('none', 'unsure', 'open', 'open'),
    'II': ('none', 'endo', 'unsure', 'unsure'),
    'I': ('none', 'endo', 'endo', 'endo'),
}
PLAN_LABEL = {'none': 'რევასკულარიზაცია იშვიათად არის ნაჩვენები', 'endo': 'ენდოვასკულური',
              'open': 'ღია შუნტირება', 'unsure': 'განუსაზღვრელი (ყველაზე ნაკლები კონსენსუსი)'}


# --------------------------------------------------------------------------
# calculation
# --------------------------------------------------------------------------

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


_DEFAULT_LENGTH_CM = {
    'aorta': 6.0, 'cia': 6.0, 'iia': 4.0, 'eia': 9.0, 'cfa': 4.0, 'pfa': 8.0,
    'sfa1': 9.0, 'sfa2': 9.0, 'sfa3': 9.0,
    'pop1': 4.0, 'pop2': 4.0, 'pop3': 4.0,
    'tpt': 3.0,
    'at1': 9.0, 'at2': 9.0, 'at3': 9.0,
    'pt1': 9.0, 'pt2': 9.0, 'pt3': 9.0,
    'per1': 9.0, 'per2': 9.0, 'per3': 9.0,
    'dp': 6.0, 'mpa': 6.0, 'lpa': 6.0, 'arch': 6.0,
}


def _fmt_score(val):
    rounded = round(float(val), 1)
    return str(int(rounded)) if rounded.is_integer() else f'{rounded:.1f}'


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
    segs = up['segments']
    ivs = up['interventions']
    has_vessel_iv = any(iv['type'] != 'amputation' for iv in ivs)
    has_map = bool(segs) or has_vessel_iv
    functioning_bypass = any(iv['type'] == 'bypass' and iv['status'] in ('patent', 'stenosed') for iv in ivs)

    sig = lambda seg: seg.get('stenosis') in ('moderate', 'severe', 'occlusion')
    occ = lambda seg: seg.get('stenosis') == 'occlusion'
    estimated = False

    def seg_len(key, track_est=False):
        nonlocal estimated
        item = segs.get(key, {})
        if item.get('length_cm') is not None:
            return float(item['length_cm'])
        if track_est and sig(item) and not (occ(item) and key in ('pop1', 'pop2', 'pop3', 'tpt')):
            estimated = True
        return _DEFAULT_LENGTH_CM[key]

    # 1. Inflow AI & CFA (GVG Table 5.2)
    cfa = 'B' if sig(segs.get('cfa', {})) else 'A'
    ai_keys = ('aorta', 'cia', 'eia')
    ai_diffuse_isr = any(
        iv['type'] == 'stent' and iv['status'] in ('isr_2', 'isr_3', 'occluded')
        and any(k in ai_keys for k in iv.get('segments', ()))
        for iv in ivs
    )
    if (
        occ(segs.get('aorta', {}))
        or (occ(segs.get('cia', {})) and occ(segs.get('eia', {})))
        or any(segs.get(k, {}).get('aneurysm') or segs.get(k, {}).get('small_caliber') for k in ai_keys)
        or ai_diffuse_isr
        or all(segs.get(k, {}).get('stenosis') == 'severe' for k in ('cia', 'eia'))
    ):
        ai = 'II'
    elif any(sig(segs.get(k, {})) for k in ai_keys):
        ai = 'I'
    else:
        ai = ''

    # 2. GLASS Femoropopliteal (FP 0-4 & +1 severe calcification, GVG Fig 5.2)
    sfa_keys = ('sfa1', 'sfa2', 'sfa3')
    pop_keys = ('pop1', 'pop2', 'pop3')
    sfa_disease_len = sum(seg_len(k, True) for k in sfa_keys if sig(segs.get(k, {})))
    sfa_cto_len = sum(seg_len(k, True) for k in sfa_keys if occ(segs.get(k, {})))
    sfa_separate_ctos = (
        int(occ(segs.get('sfa1', {})))
        + int(occ(segs.get('sfa2', {})) and not occ(segs.get('sfa1', {})))
        + int(occ(segs.get('sfa3', {})) and not occ(segs.get('sfa2', {})))
    )
    sfa_flush_cto = occ(segs.get('sfa1', {})) and bool(segs.get('sfa1', {}).get('flush'))
    pop_disease_len = sum(seg_len(k, True) for k in pop_keys if sig(segs.get(k, {})))
    pop_any_cto = any(occ(segs.get(k, {})) for k in pop_keys)
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
    fp_final = min(4, fp_grade + (1 if fp_calc else 0))

    # 3. Ankle crossing & Pedal arch status (GVG Fig 5.1)
    at_cross = not occ(segs.get('at3', {})) and not occ(segs.get('dp', {}))
    pt_cross = not occ(segs.get('pt3', {})) and (not occ(segs.get('mpa', {})) or not occ(segs.get('lpa', {})))
    per_cross = not occ(segs.get('per3', {})) and (
        not occ(segs.get('dp', {})) or not occ(segs.get('mpa', {})) or not occ(segs.get('lpa', {}))
    )
    crosses_map = {'at': at_cross, 'pt': pt_cross, 'per': per_cross}
    arch_complete = (
        not sig(segs.get('arch', {}))
        and not occ(segs.get('dp', {}))
        and (not occ(segs.get('mpa', {})) or not occ(segs.get('lpa', {})))
    )
    pedal_for = lambda t: ('P0' if arch_complete else 'P1') if crosses_map[t] else 'P2'

    # 4. GLASS Infrapopliteal (IP 0-4 & +1 calc) for all 3 pathways (GVG Fig 5.3)
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
        tpt_cto = uses_tpt and occ(tpt_item)
        tpt_sten = uses_tpt and sig(tpt_item) and not tpt_cto
        tpt_len = seg_len('tpt', True) if sig(tpt_item) else 0.0

        total_len = tpt_len + sum(seg_len(k, True) for k in tib_keys if sig(segs.get(k, {})))
        cto_len = sum(seg_len(k, True) for k in tib_keys if occ(segs.get(k, {})))
        sten_cnt = sum(1 for k in tib_keys if sig(segs.get(k, {})) and not occ(segs.get(k, {})))
        cto_runs = (
            int(occ(segs.get(tib_keys[0], {})))
            + int(occ(segs.get(tib_keys[1], {})) and not occ(segs.get(tib_keys[0], {})))
            + int(occ(segs.get(tib_keys[2], {})) and not occ(segs.get(tib_keys[1], {})))
        )
        origin_flush = occ(segs.get(tib_keys[0], {})) and bool(segs.get(tib_keys[0], {}).get('flush'))

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
        ip_f = min(4, ip_g + (1 if ip_c else 0))
        ip_by_tap[t_key] = {
            'tap': t_key,
            'short': tap_short[t_key],
            'label': tap_labels[t_key],
            'ip': ip_g,
            'ip_calc': ip_c,
            'ip_final': ip_f,
            'crosses_ankle': crosses_map[t_key],
            'angiosome_match': angio_source == t_key,
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
    sug_ip = ip_by_tap[sug_tap]['ip']
    sug_ip_calc = ip_by_tap[sug_tap]['ip_calc']
    sug_ip_final = ip_by_tap[sug_tap]['ip_final']
    sug_pedal = pedal_for(sug_tap)
    sug_glass = GLASS_MATRIX[fp_final][sug_ip_final]

    cur_tap = raw_limb.get('tap') if raw_limb.get('tap') in tap_labels else ''
    active_tap = cur_tap or sug_tap
    active_ip_info = ip_by_tap[active_tap]

    # 5. Modified SVS Runoff Score (1.0-19.0) & Foot Score (1.0-10.0) (Stoner et al. 2016)
    def vessel_svs(keys, total_vessel_cm):
        if any(occ(segs.get(k, {})) for k in keys):
            occ_cm = sum(seg_len(k, False) for k in keys if occ(segs.get(k, {})))
            return 3.0 if occ_cm > 0.5 * total_vessel_cm else 2.5
        if any(segs.get(k, {}).get('stenosis') in ('moderate', 'severe') for k in keys):
            return 2.0
        if any(segs.get(k, {}).get('stenosis') == 'mild' for k in keys):
            return 1.0
        return 0.0

    pop_svs = vessel_svs(pop_keys, 12.0) * 3.0 + 1.0
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
        if not any(occ(segs.get(k, {})) for k in keys)
    ]

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
        'has_map': has_map,
        'estimated_length': estimated,
        'functioning_bypass': functioning_bypass,
        'tap': sug_tap,
        'tap_short': tap_short[sug_tap],
        'tap_label': tap_labels[sug_tap],
        'active_tap': active_tap,
        'fp': fp_grade,
        'fp_calc': fp_calc,
        'fp_final': fp_final,
        'ip_by_tap': ip_by_tap,
        'ip_paths': [ip_by_tap['at'], ip_by_tap['pt'], ip_by_tap['per']],
        'ip': sug_ip,
        'ip_calc': sug_ip_calc,
        'ip_final': sug_ip_final,
        'active_ip': active_ip_info['ip'],
        'active_ip_calc': active_ip_info['ip_calc'],
        'glass': sug_glass,
        'pedal': sug_pedal,
        'active_pedal': pedal_for(active_tap),
        'ai': ai,
        'cfa': cfa,
        'runoff_count': len(patent_tibials),
        'patent_tibials': patent_tibials,
        'svs_runoff': svs_runoff,
        'svs_runoff_text': _fmt_score(svs_runoff),
        'foot_runoff': foot_runoff,
        'foot_runoff_text': _fmt_score(foot_runoff),
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
        out.update({'fp_final': fp_f, 'ip_final': ip_f, 'glass': stage,
                    'glass_text': GLASS_TEXT.get(stage, 'ცხრილში არ ეხება (FP 0 და IP 0).')})
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
            text = ('მაღალი რისკი, ზომიერი იშემია: განიხილეთ ენდოვასკულური, თუ ჭრილობა პროგრესირებს ან 4 კვირაში ≤50%-ით '
                    'არ მცირდება (რეკ. 6.35–6.36).')
        else:
            out['plan'] = 'none'
            text = 'იშემია მნიშვნელოვანი არ არის (ხარისხი 0).'
        if stage in (3, 4) and i >= 2 and glass == 'III':
            text += ' შერჩეულ შემთხვევებში — ღია ქირურგია (რეკ. 6.37).'
        out['plan_label'] = text
    elif stage and glass:
        if limb.get('vein') == 'no':
            out['plan'] = 'endo'
            out['plan_label'] = ('ვარგისი ვენის გარეშე: შესაძლებლობისას უპირატესია ენდოვასკულური; პროთეზი / ალოგრაფტი — '
                                 'მხოლოდ შერჩეულ შემთხვევებში (ნაწ. 6, რეკ. 6.41).')
        else:
            key = PLAN[glass][stage - 1]
            out['plan'] = key
            out['plan_label'] = PLAN_LABEL[key] + ' — საშუალო რისკი, ვარგისი ვენა (სურ. 6.4).'
    if limb.get('pedal') == 'P2':
        out['notes'].append('P2: კოჭს სამიზნე არტერია არ კვეთს — WIfI 3–4-ზე შეიძლება იყოს „no-option“ (სურ. 6.4 P2-ს არ მოიცავს).')
    ivs = upgrade_limb(limb).get('interventions') or () if isinstance(limb, dict) else ()
    if any(iv['type'] == 'bypass' and iv['status'] in ('patent', 'stenosed') for iv in ivs):
        out['notes'].append('კიდურზე ფუნქციონირებს შუნტი — გაითვალისწინეთ, რომ GLASS პირველადი ნატიური არტერიების '
                            'შესაფასებლად არის შექმნილი (GVG ნაწ. 5).')
    angio = angiosome_status(limb)
    if angio:
        out['angiosome'] = angio
    out['suggested'] = suggest_from_map(limb)
    return out


# --------------------------------------------------------------------------
# Entry presence checks & Georgian angiography protocol generator
# --------------------------------------------------------------------------

def has_angiography(limb):
    """True when any angiographic map segment, prior intervention, or GLASS field has been entered."""
    if not isinstance(limb, dict):
        return False
    up = clean(limb)
    return bool(
        up['segments']
        or up['interventions']
        or up['fp'] is not None
        or up['ip'] is not None
        or up['fp_calc']
        or up['ip_calc']
        or up['tap']
        or up['pedal']
        or up['ai']
        or up['cfa'] == 'B'
    )


def has_wifi(limb):
    """True when any WIfI or clinical PLAN risk/vein/note field has been entered."""
    if not isinstance(limb, dict):
        return False
    up = clean(limb)
    return bool(
        up['w'] is not None
        or up['i'] is not None
        or up['fi'] is not None
        or up['unsalvageable']
        or up['wound_angiosome']
        or up['risk'] == 'high'
        or up['vein'] == 'no'
        or up['note']
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
        while (end < len(keys) and abn[end]
               and (items[end].get('stenosis') or 'none') == (items[idx].get('stenosis') or 'none')
               and (items[end].get('stenosis') or 'none') != 'none'
               and not items[end].get('flush')):
            end += 1
        span_names = _join_ka(part_names[idx:end])
        merged = _merge_segment_items(items[idx:end])
        clauses.append(f'{span_names} {unit_word} — {_format_lesion_ka(merged)}')
        idx = end

    if normal_parts:
        clauses.append(f'{_join_ka(normal_parts)} {unit_word} გამავალია')
    return '; '.join(clauses) + '.'


def _limb_body_lines_ka(segs, ivs):
    lines = []

    # 1. Iliac segment (CIA, EIA, IIA)
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

    # 2. Common & deep femoral arteries (CFA, PFA)
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

    # 3. Superficial femoral artery (SFA 1-3)
    thirds = ('პროქსიმალურ', 'შუა', 'დისტალურ')
    sfa_text = _describe_thirds_ka(
        segs, ('sfa1', 'sfa2', 'sfa3'), thirds, 'მესამედში',
        'მთელ სიგრძეზე გამავალია, ჰემოდინამიკურად მნიშვნელოვანი სტენოზის გარეშე.',
    )
    lines.append(f'• ზედაპირული ბარძაყის არტერია: {sfa_text}')

    # 4. Popliteal artery (P1-P3)
    pop_text = _describe_thirds_ka(
        segs, ('pop1', 'pop2', 'pop3'), ('P1 (მუხლზემოთა)', 'P2 (სახსრის დონის)', 'P3 (მუხლქვემოთა)'),
        'სეგმენტში', 'P1, P2 და P3 სეგმენტები გამავალია, მნიშვნელოვანი სტენოზის გარეშე.',
    )
    lines.append(f'• მუხლქვეშა არტერია: {pop_text}')

    # 5. Crural / tibial arteries (TPT, AT, PT, PER)
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

    # 6. Pedal arteries & plantar arch (DP, MPA, LPA, ARCH)
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

    # 7. Prior limb interventions (bypasses, stents, angioplasties, patches, thrombectomies, amputations)
    if ivs:
        iv_rows = interventions_view(ivs)
        iv_texts = []
        for row in iv_rows:
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
        has_st = any((segs.get(k, {}).get('stenosis') in ('mild', 'moderate', 'severe'))
                     for k in segs if include_aorta or k != 'aorta')
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

    # Distal runoff & GLASS anatomical summary
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

