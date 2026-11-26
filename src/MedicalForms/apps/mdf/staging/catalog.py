"""Arterial anatomy catalog, segment graph, PACSS/angiosome choices, and prior-intervention options."""
import collections
import re

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
BYPASS_SITES = (
    ('', 'არ არის მითითებული'),
    ('proximal', 'პროქსიმალური ანასტომოზი'),
    ('body', 'შუნტის სხეული'),
    ('distal', 'დისტალური ანასტომოზი'),
)
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
_INLINE_TYPES = ('stent', 'angioplasty', 'endarterectomy', 'thrombectomy')
