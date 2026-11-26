"""Global Vascular Guidelines (GVG 2019) reference tables: WIfI, GLASS, and PLAN."""

TABLES_SOURCE = (
    'Global Vascular Guidelines on the Management of CLTI (Conte et al., SVS/ESVS/WFVS, 2019): '
    'WIfI — ცხრ. 3.2–3.5, სურ. 6.3; GLASS — ცხრ. 5.1–5.4, სურ. 5.1–5.3; PLAN — სურ. 6.4, რეკ. 6.4–6.5, 6.32–6.37.'
)

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
ISCHEMIA_NOTE = (
    'თუ TP და ABI სხვადასხვა ხარისხს იძლევა, მთავარია TP. არაკომპრესირებადი ABI-ის (>1.3) დროს — TP ან TcPO2. '
    'დიაბეტისას TP აუცილებელია. ბრტყელი PVR — ხარისხი 3.'
)


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
RISK = (
    ('avg', 'საშუალო რისკი: პერიოპერაციული სიკვდილობა <5% და 2-წლიანი გადარჩენა >50%'),
    ('high', 'მაღალი რისკი: სიკვდილობა ≥5% ან 2-წლიანი გადარჩენა ≤50%'),
)
VEIN = (('yes', 'ვარგისი აუტოვენა არის'), ('no', 'ვარგისი ვენა არ არის'))

# PLAN (Fig 6.4): average-risk patient with suitable autologous vein; GLASS stage x WIfI stage 1-4
PLAN = {
    'III': ('none', 'unsure', 'open', 'open'),
    'II': ('none', 'endo', 'unsure', 'unsure'),
    'I': ('none', 'endo', 'endo', 'endo'),
}
PLAN_LABEL = {
    'none': 'რევასკულარიზაცია იშვიათად არის ნაჩვენები',
    'endo': 'ენდოვასკულური',
    'open': 'ღია შუნტირება',
    'unsure': 'განუსაზღვრელი (ყველაზე ნაკლები კონსენსუსი)',
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
