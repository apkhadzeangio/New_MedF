"""Limb staging for the conference: the artery map, GLASS, WIfI and the PLAN strategy.

The classification tables follow the Global Vascular Guidelines on CLTI
(Conte et al., SVS/ESVS/WFVS 2019) and the SVS WIfI system (Mills 2014);
see TABLES_SOURCE. Everything is decision support: the surgeon or the
conference decides.
"""

# --------------------------------------------------------------------------
# the artery map (one leg, front view; the left leg is drawn mirrored)
# --------------------------------------------------------------------------

# Right leg, front view (viewBox 0 0 260 650; the body midline / aorta at x=236, medial to the right).
# The left leg is the same drawing mirrored.
# key, Georgian name, label, SVG path, group, vessel width, label position (x, y, anchor[, "v" = vertical])
SEGMENTS = (
    ('cia', 'საერთო თეძოს არტერია', 'CIA', 'M236,22 C228,40 210,58 192,72', 'inflow', 7.0, (214, 40, 'end')),
    ('eia', 'გარეთა თეძოს არტერია', 'EIA', 'M192,72 C176,88 160,104 150,124', 'inflow', 6.4, (166, 92, 'end')),
    ('cfa', 'საერთო ბარძაყის არტერია', 'CFA', 'M150,124 C147,134 145,144 144,156', 'inflow', 6.4, (132, 140, 'end')),
    ('pfa', 'ბარძაყის ღრმა არტერია', 'PFA', 'M144,156 C130,170 120,196 114,236', 'inflow', 4.6, (104, 214, 'end')),
    ('sfa1', 'ზედაპირული ბარძაყის — პროქსიმალური', 'SFA', 'M144,156 C146,182 149,206 152,230', 'fp', 5.4, (162, 196, 'start')),
    ('sfa2', 'ზედაპირული ბარძაყის — შუა', '', 'M152,230 C155,254 158,278 160,302', 'fp', 5.4, None),
    ('sfa3', 'ზედაპირული ბარძაყის — დისტალური', '', 'M160,302 C162,324 162,342 159,358', 'fp', 5.4, None),
    ('pop1', 'მუხლქვეშა — მუხლზემოთ (P1)', 'POP', 'M159,358 C155,372 151,384 149,398', 'fp', 5.0, (166, 380, 'start')),
    ('pop2', 'მუხლქვეშა — მუხლქვემოთ (P2–3)', '', 'M149,398 C148,410 147,420 147,432', 'fp', 5.0, None),
    ('tpt', 'წვივ-მცირე წვივის ღერო', 'TPT', 'M147,432 C148,442 149,450 150,458', 'ip', 4.4, (160, 446, 'start')),
    ('at1', 'წინა წვივის — პროქსიმალური', 'AT', 'M147,432 C137,436 129,442 127,456', 'ip', 3.9, (114, 446, 'end')),
    ('at2', 'წინა წვივის — შუა', '', 'M127,456 C125,484 124,510 124,536', 'ip', 3.9, None),
    ('at3', 'წინა წვივის — დისტალური', '', 'M124,536 C124,556 125,572 127,588', 'ip', 3.6, None),
    ('pt1', 'უკანა წვივის — პროქსიმალური', 'PT', 'M150,458 C154,474 157,488 159,504', 'ip', 3.9, (172, 500, 'start')),
    ('pt2', 'უკანა წვივის — შუა', '', 'M159,504 C161,520 163,535 164,550', 'ip', 3.9, None),
    ('pt3', 'უკანა წვივის — დისტალური', '', 'M164,550 C165,565 167,580 170,594', 'ip', 3.6, None),
    ('per1', 'მცირე წვივის — პროქსიმალური', 'PER', 'M150,458 C146,474 144,488 143,502', 'ip', 3.6, (134, 522, 'middle', 'v')),
    ('per2', 'მცირე წვივის — შუა', '', 'M143,502 C142,518 141,532 141,546', 'ip', 3.6, None),
    ('per3', 'მცირე წვივის — დისტალური', '', 'M141,546 C141,558 142,568 143,578', 'ip', 3.4, None),
    ('dp', 'ტერფის ზურგის არტერია', 'DP', 'M127,588 C128,602 130,614 134,624', 'pedal', 3.3, (118, 616, 'end')),
    ('plantar', 'ტერფის ლატერალური ძირის', 'PL', 'M170,594 C169,606 164,616 156,623', 'pedal', 3.0, (176, 616, 'start')),
    ('arch', 'ტერფის რკალი', 'ARCH', 'M134,624 C138,634 150,634 156,623', 'pedal', 2.8, (145, 646, 'middle')),
)
SEGMENT_KEYS = {s[0] for s in SEGMENTS}

# what can be marked on a segment
STATES = (
    ('n', 'ნორმა'),
    ('mild', 'სტენოზი <50%'),
    ('st', 'სტენოზი ≥50%'),
    ('oc', 'ოკლუზია'),
)
FLAGS = (
    ('ca', 'მძიმე კალციფიკაცია'),
    ('rx', 'სტენტი / ანგიოპლასტიკა'),
    ('by', 'შუნტი'),
)

MAP_W, MAP_H = 260, 650
ANATOMY = {
    'skin': 'M92,100 C122,84 176,84 214,106 C210,122 204,138 198,154 C192,200 188,260 184,320 C182,350 184,372 188,392 C190,410 188,424 186,438 C186,470 182,510 176,548 C174,562 174,574 178,584 C182,598 180,618 172,634 C166,646 146,650 132,646 C120,640 114,624 116,606 C118,596 120,590 120,582 C114,560 110,530 106,496 C102,470 102,450 106,430 C108,414 110,400 110,388 C104,350 100,320 98,290 C94,240 86,190 86,150 C86,128 88,112 92,100 Z',
    'bones': ['M154,104 C166,96 182,104 180,118 C178,128 168,132 160,134 C152,140 148,150 146,162 C148,230 152,300 158,366 C166,372 172,384 168,394 C160,402 136,402 124,396 C118,388 124,376 134,368 C132,300 130,230 128,170 C120,160 112,148 116,136 C120,128 132,128 140,130 C144,118 146,108 154,104 Z', 'M138,384 C138,374 156,374 156,384 C156,394 138,394 138,384 Z', 'M130,408 C142,403 162,403 170,410 C168,420 162,426 160,440 C160,490 162,540 168,576 C170,586 164,590 156,588 C150,586 146,582 146,576 C146,540 144,490 140,440 C138,426 130,420 130,408 Z', 'M118,420 C122,415 127,417 127,423 C125,470 125,530 129,578 C127,587 119,587 117,579 C119,530 119,470 117,426 Z'],
    'pelvis': 'M98,78 C104,46 146,26 184,38 C194,42 198,52 194,62',
    'ligament': 'M102,98 C140,112 180,124 214,134',
    'muscles': ['M92,150 C86,200 90,270 100,330 C104,356 110,378 118,390 C124,372 126,340 124,300 C122,240 116,190 104,160 C100,152 96,148 92,150 Z', 'M120,124 C112,170 114,250 126,330 C130,352 136,370 142,380 C150,366 154,340 152,300 C150,240 144,170 132,126 C128,120 124,120 120,124 Z', 'M160,300 C152,330 152,364 162,388 C170,396 180,392 184,380 C186,356 180,326 168,304 C166,300 162,298 160,300 Z', 'M168,150 C162,190 160,240 164,290 C170,280 178,250 184,220 C188,190 192,166 196,152 C186,150 176,148 168,150 Z', 'M100,102 C120,140 148,220 168,300 C174,330 178,370 180,398 L186,396 C184,366 180,326 174,296 C156,214 128,136 108,100 Z', 'M126,418 C118,440 116,480 118,520 C120,546 124,566 128,580 C132,564 134,540 134,510 C134,470 132,438 126,418 Z', 'M110,424 C104,450 104,490 108,530 C110,546 114,560 118,570 C118,540 116,500 116,466 C116,446 114,432 110,424 Z', 'M168,414 C178,430 184,456 182,486 C180,506 176,520 172,528 C166,508 164,480 164,456 C164,438 166,424 168,414 Z'],
    'toes': 'M118,634 C118,646 126,648 130,642 M131,642 C132,650 140,651 143,645 M143,646 C145,652 152,652 154,646 M155,645 C158,650 164,649 165,643 M165,642 C170,644 173,638 171,632',
    'context': ['M236,0 L236,22', 'M192,72 C198,88 202,104 200,120'],   # aorta and internal iliac (not marked)
}
LANDMARKS = (('მუხლი', 400), ('კოჭი', 584))
LEG_OUTLINE = ANATOMY['skin']


def clean_segments(raw):
    """{'sfa2': {'s': 'oc', 'ca': True}, ...} from the page -> only known keys/values."""
    out = {}
    states = {k for k, _ in STATES}
    flags = {k for k, _ in FLAGS}
    for key, value in (raw or {}).items():
        if key not in SEGMENT_KEYS or not isinstance(value, dict):
            continue
        item = {}
        if value.get('s') in states and value.get('s') != 'n':
            item['s'] = value['s']
        for f in flags:
            if value.get(f) is True:
                item[f] = True
        if item:
            out[key] = item
    return out


def segment_view(segments, mirror=False):
    """Paths with their CSS classes for the template."""
    view = []
    for key, name, short, d, group, width, label in SEGMENTS:
        item = segments.get(key, {})
        classes = ['sg', f'sg-{item.get("s", "n")}'] + [f'sg-{f}' for f, _ in FLAGS if item.get(f)]
        view.append({'key': key, 'name': name, 'short': short, 'd': d, 'group': group, 'w': width,
                     'state': item.get('s', 'n'), 'flags': [f for f, _ in FLAGS if item.get(f)],
                     'css': ' '.join(classes)})
    return view


def map_context(segments, side):
    """Everything _artery_map.html draws; the left leg is mirrored (texts are placed, not flipped)."""
    flip = side == 'L'
    labels = []
    for key, name, short, d, group, width, label in SEGMENTS:
        if not label:
            continue
        x, y, anchor = label[:3]
        if flip:
            x = MAP_W - x
            anchor = {'start': 'end', 'end': 'start'}.get(anchor, anchor)
        labels.append({'x': x, 'y': y, 'anchor': anchor, 'text': short, 'vertical': len(label) > 3})
    marks = [{'text': text, 'y': y, 'x1': MAP_W - 12 if flip else 12, 'x2': MAP_W - 56 if flip else 56,
              'anchor': 'end' if flip else 'start'} for text, y in LANDMARKS]
    return {'segments': segment_view(segments), 'labels': labels, 'marks': marks, 'anatomy': ANATOMY,
            'side': side, 'w': MAP_W, 'h': MAP_H}


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
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return v if low <= v <= high else None


def _float(value, low, high):
    try:
        v = float(str(value).replace(',', '.'))
    except (TypeError, ValueError):
        return None
    return v if low <= v <= high else None


def clean(data):
    """Form / JSON data of one limb -> what is stored."""
    choice = lambda key, options: data.get(key) if data.get(key) in {k for k, _ in options} else ''
    return {
        'segments': clean_segments(data.get('segments')),
        'tap': choice('tap', TAP),
        'fp': _int(data.get('fp'), 0, 4), 'ip': _int(data.get('ip'), 0, 4),
        'fp_calc': data.get('fp_calc') in (True, 'on', '1'), 'ip_calc': data.get('ip_calc') in (True, 'on', '1'),
        'pedal': choice('pedal', PEDAL),
        'ai': choice('ai', INFLOW_AI), 'cfa': choice('cfa', INFLOW_CFA) or 'A',
        'w': _int(data.get('w'), 0, 3), 'i': _int(data.get('i'), 0, 3), 'fi': _int(data.get('fi'), 0, 3),
        'unsalvageable': data.get('unsalvageable') in (True, 'on', '1'),
        'abi': _float(data.get('abi'), 0, 3), 'ap': _float(data.get('ap'), 0, 400), 'tp': _float(data.get('tp'), 0, 400),
        'risk': choice('risk', RISK) or 'avg', 'vein': choice('vein', VEIN) or 'yes',
        'note': ' '.join(str(data.get('note') or '').split())[:500],
    }


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
    return out
