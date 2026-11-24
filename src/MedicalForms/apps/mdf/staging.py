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
    (2.0, '<3 სმ'),
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
    """Paths with their CSS classes and v2 metadata for the template."""
    norm_segs = _remap_raw_segments(segments)
    rx_marked, by_marked = set(), set()
    for iv in clean_interventions(interventions):
        if iv['type'] in _INLINE_TYPES:
            rx_marked.update(iv['segments'])
        elif iv['type'] == 'bypass':
            by_marked.update(segment_path(iv['proximal'], iv['distal']))

    view = []
    for key, name, short, d, group, width, label in SEGMENTS:
        item = norm_segs.get(key, {})
        state = _display_state(item)
        flags = []
        calc = _int(item.get('calc'), 0, 4) if 'calc' in item else (3 if item.get('ca') is True else 0)
        if (calc or 0) >= 3 or item.get('coral') is True or item.get('ca') is True:
            flags.append('ca')
        if item.get('rx') is True or key in rx_marked:
            flags.append('rx')
        if item.get('by') is True or key in by_marked:
            flags.append('by')
        classes = ['sg', f'sg-{state}'] + [f'sg-{f}' for f in flags]
        view.append({
            'key': key, 'name': name, 'short': short, 'd': d, 'group': group, 'w': width,
            'state': state, 'stenosis': _raw_stenosis(item), 'calc': calc or 0,
            'length_cm': _float(item.get('length_cm'), 0.1, 50.0),
            'flags': flags, 'css': ' '.join(classes),
        })
    return view


def map_context(segments, side, interventions=None):
    """Everything _artery_map.html draws; the left leg is mirrored (texts are placed, not flipped)."""
    vx, vy, vw, vh = MAP_VIEW
    axis = 2 * vx + vw                       # x -> axis - x mirrors inside the view box
    flip = side == 'L'
    X = (lambda x: axis - x) if flip else (lambda x: x)
    labels = []
    for key, name, short, d, group, width, label in SEGMENTS:
        if not label:
            continue
        x, y, anchor = label[:3]
        if flip:
            anchor = {'start': 'end', 'end': 'start'}.get(anchor, anchor)
        labels.append({'x': X(x), 'y': y, 'anchor': anchor, 'text': short, 'vertical': len(label) > 3})
    marks = [{'text': text, 'y': y, 'ty': y - 8, 'x1': X(vx + 4), 'x2': X(-6),
              'anchor': 'end' if flip else 'start'} for text, y in LANDMARKS]
    return {'segments': segment_view(segments, interventions=interventions), 'labels': labels, 'marks': marks,
            'context': CONTEXT, 'image': LEG_IMAGE, 'side': side, 'view': f'{vx} {vy} {vw} {vh}', 'axis': axis}


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
        'abi': _float(upgraded.get('abi'), 0, 3), 'ap': _float(upgraded.get('ap'), 0, 400), 'tp': _float(upgraded.get('tp'), 0, 400),
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
    """ABI (the higher of PT/DP), the highest ankle pressure and the toe pressure from a Doppler study."""
    if not study_data:
        return {}
    idx = (study_data.get('indices') or {}).get(side) or {}
    prs = (study_data.get('pressures') or {}).get(side) or {}
    abis = [v for v in (idx.get('abi_pt'), idx.get('abi_dp')) if v is not None]
    aps = [v for v in (prs.get('pt'), prs.get('dp')) if v is not None]
    out = {}
    if abis:
        out['abi'] = max(abis)
    if aps:
        out['ap'] = max(aps)
    if prs.get('toe') is not None:
        out['tp'] = prs['toe']
    return out


def compute(limb, measured=None):
    """Everything derived from one limb's entries (all tables above)."""
    measured = measured or {}
    m = {k: limb.get(k) if limb.get(k) is not None else measured.get(k) for k in ('abi', 'ap', 'tp')}
    sug_i, sug_from = suggest_ischemia(**m)
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
    return out
