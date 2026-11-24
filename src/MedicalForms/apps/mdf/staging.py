"""Limb staging for the conference: the artery map, GLASS, WIfI and the PLAN strategy.

The classification tables follow the Global Vascular Guidelines on CLTI
(Conte et al., SVS/ESVS/WFVS 2019) and the SVS WIfI system (Mills 2014);
see TABLES_SOURCE. Everything is decision support: the surgeon or the
conference decides.
"""

# --------------------------------------------------------------------------
# the artery map (one leg, front view; the left leg is drawn mirrored)
# --------------------------------------------------------------------------

# key, Georgian name, short label, SVG path (viewBox 0 0 200 560), group
SEGMENTS = (
    ('cia', 'საერთო თეძოს არტერია', 'CIA', 'M100,8 L93,62', 'inflow'),
    ('eia', 'გარეთა თეძოს არტერია', 'EIA', 'M93,62 L86,112', 'inflow'),
    ('cfa', 'საერთო ბარძაყის არტერია', 'CFA', 'M86,112 L86,138', 'inflow'),
    ('pfa', 'ბარძაყის ღრმა არტერია', 'PFA', 'M86,138 C76,160 70,185 64,222', 'inflow'),
    ('sfa1', 'ზედაპირული ბარძაყის — პროქსიმალური', 'SFA¹', 'M86,138 L90,196', 'fp'),
    ('sfa2', 'ზედაპირული ბარძაყის — შუა', 'SFA²', 'M90,196 L95,254', 'fp'),
    ('sfa3', 'ზედაპირული ბარძაყის — დისტალური', 'SFA³', 'M95,254 L100,300', 'fp'),
    ('pop1', 'მუხლქვეშა — მუხლზემოთ (P1)', 'POP¹', 'M100,300 L102,332', 'fp'),
    ('pop2', 'მუხლქვეშა — მუხლქვემოთ (P2–3)', 'POP²', 'M102,332 L102,362', 'fp'),
    ('tpt', 'წვივ-მცირე წვივის ღერო', 'TPT', 'M102,362 L106,388', 'ip'),
    ('at1', 'წინა წვივის — პროქსიმალური', 'AT¹', 'M102,362 C92,366 86,374 85,410', 'ip'),
    ('at2', 'წინა წვივის — შუა', 'AT²', 'M85,410 L83,452', 'ip'),
    ('at3', 'წინა წვივის — დისტალური', 'AT³', 'M83,452 L81,492', 'ip'),
    ('pt1', 'უკანა წვივის — პროქსიმალური', 'PT¹', 'M106,388 L112,424', 'ip'),
    ('pt2', 'უკანა წვივის — შუა', 'PT²', 'M112,424 L115,460', 'ip'),
    ('pt3', 'უკანა წვივის — დისტალური', 'PT³', 'M115,460 L117,494', 'ip'),
    ('per1', 'მცირე წვივის — პროქსიმალური', 'PER¹', 'M106,388 L101,424', 'ip'),
    ('per2', 'მცირე წვივის — შუა', 'PER²', 'M101,424 L99,458', 'ip'),
    ('per3', 'მცირე წვივის — დისტალური', 'PER³', 'M99,458 L98,486', 'ip'),
    ('dp', 'ტერფის ზურგის არტერია', 'DP', 'M81,492 L72,528', 'pedal'),
    ('plantar', 'ტერფის ლატერალური ძირის', 'PL', 'M117,494 C116,512 112,524 106,534', 'pedal'),
    ('arch', 'ტერფის რკალი', 'ARCH', 'M72,528 C80,544 96,544 106,534', 'pedal'),
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

LEG_OUTLINE = ('M60,96 C70,84 128,84 138,98 L130,200 C126,250 124,290 124,322 C128,336 130,350 128,372 '
               'C126,420 122,460 120,484 C124,500 126,516 122,536 C110,552 62,552 52,540 C50,530 66,520 78,508 '
               'C80,496 80,486 80,480 C76,450 72,410 72,372 C70,350 72,336 76,322 C76,290 72,250 66,200 Z')


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
    for key, name, short, d, group in SEGMENTS:
        item = segments.get(key, {})
        classes = ['sg', f'sg-{item.get("s", "n")}'] + [f'sg-{f}' for f, _ in FLAGS if item.get(f)]
        view.append({'key': key, 'name': name, 'short': short, 'd': d, 'group': group,
                     'state': item.get('s', 'n'), 'flags': [f for f, _ in FLAGS if item.get(f)],
                     'css': ' '.join(classes)})
    return view


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
