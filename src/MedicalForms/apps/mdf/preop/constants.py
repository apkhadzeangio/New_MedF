"""Constants, choice catalogs, chip labels, regex tables, and default JSON schemas for preop."""
import re
import threading

from django.db import connection
from django.db.models import F

from ..models import PreopPatient

_PREOP_MUTATION_LOCK = threading.RLock()


def _sqlite_acquire_write_lock(pk):
    """Begin SQLite atomic blocks directly as a WRITE transaction to avoid SQLITE_BUSY_SNAPSHOT under WAL."""
    if pk and connection.vendor == 'sqlite':
        PreopPatient.objects.filter(pk=pk).update(id=F('id'))

WEEKDAYS_KA = ('ორშაბათი', 'სამშაბათი', 'ოთხშაბათი', 'ხუთშაბათი', 'პარასკევი', 'შაბათი', 'კვირა')
MONTHS_SHORT = ('', 'იან', 'თებ', 'მარ', 'აპრ', 'მაი', 'ივნ', 'ივლ', 'აგვ', 'სექ', 'ოქტ', 'ნოე', 'დეკ')

TARGET_PREP_DAYS = 7
DEFAULT_CARDIOLOGISTS = ('ლიკა ინჯია', 'გარე კარდიოლოგი')

SELF_FILL_SALT = 'mdf.preop.self_q'
SELF_FILL_MAX_AGE = 14 * 86400  # 14 days

OP_SUBTYPE_GROUPS = (
    ('ინფრარენალური AAA', (
        ('open', 'AAA — ღია რეზექცია'),
        ('evar', 'AAA — ენდოვასკულური (EVAR)'),
        ('tbd', 'AAA — ტაქტიკა ზუსტდება'),
    )),
    ('კაროტიდული ენდარტერექტომია (CEA)', (
        ('cea_symp', 'CEA — სიმპტომური (TIA / ინსულტი ≤6 თვ)'),
        ('cea_asymp', 'CEA — ასიმპტომური'),
    )),
    ('აორტო-ბიფემორალური შუნტირება (ABF)', (
        ('abf_clti', 'ABF — კრიტიკული იშემია (CLTI)'),
        ('abf_claud', 'ABF — მძიმე კოჭლობა (ლერიში)'),
    )),
    ('პერიფერიული არტერიული შუნტირება', (
        ('bypass_clti', 'პერიფ. შუნტირება — კრიტიკული იშემია (CLTI)'),
        ('bypass_claud', 'პერიფ. შუნტირება — მძიმე კოჭლობა'),
    )),
    ('პერიფერიული ანგიოპლასტიკა / სტენტირება (PTA)', (
        ('pta_clti', 'PTA / სტენტირება — კრიტიკული იშემია (CLTI)'),
        ('pta_claud', 'PTA / სტენტირება — ხანგამოშვებითი კოჭლობა'),
    )),
    ('ვენური ქირურგია (EVLA / ფლებექტომია)', (
        ('evla', 'EVLA — ენდოვენური ლაზერული აბლაცია'),
        ('phlebectomy', 'ფლებექტომია (კომბინირებული / კროსექტომია)'),
    )),
)

PATHWAY_SHORT = {
    PreopPatient.Pathway.AAA_INFRA: 'AAA',
    PreopPatient.Pathway.CEA: 'CEA',
    PreopPatient.Pathway.ABF: 'ABF',
    PreopPatient.Pathway.PAD_BYPASS: 'შუნტირება',
    PreopPatient.Pathway.PAD_PTA: 'PTA',
    PreopPatient.Pathway.VENOUS: 'ვენური',
}

CEAP_CHOICES = (
    ('C2', 'C2 — ვარიკოზული კვანძები'),
    ('C3', 'C3 — ვარიკოზი + კიდურის შეშუპება'),
    ('C4', 'C4 — ტროფიკული კანის ცვლილება (პიგმენტაცია / ეგზემა / ლიპოდერმატოსკლეროზი)'),
    ('C5', 'C5 — შეხორცებული ვენური წყლული'),
    ('C6', 'C6 — აქტიური ვენური წყლული'),
)

CFS_CHOICES = (
    ('1_3', 'CFS 1–3: აქტიური / დამოუკიდებელი'),
    ('4', 'CFS 4: მოწყვლადი (შენელებული)'),
    ('5_plus', 'CFS ≥5: დაუძლურებული (სჭირდება დახმარება)'),
)

ANESTH_PLAN_CHOICES = (
    ('auto', 'ავტომატური (ოპერაციის ტიპით)'),
    ('local', 'ადგილობრივი / ტუმესცენტური'),
    ('plexus', 'რეგიონული / წნულის ბლოკადა'),
    ('neuraxial', 'სპინალური / ეპიდურული (ნეირაქსიალური)'),
    ('general', 'ზოგადი (ენდოტრაქეული / ლარინგეალური)'),
)

ASA_CHOICES = (
    ('auto', 'ავტომატური (კლინიკური ფონით)'),
    ('I', 'ASA I — პრაქტიკულად ჯანმრთელი'),
    ('II', 'ASA II — მსუბუქი სისტემური დაავადება'),
    ('III', 'ASA III — მძიმე სისტემური / ვასკულური დაავადება'),
    ('IV', 'ASA IV — სიცოცხლისთვის მუდმივი საფრთხის მქონე დაავადება'),
)

MALLAMPATI_CHOICES = (
    ('I_II', 'Mallampati I–II (სტანდარტული სასუნთქი გზა)'),
    ('III', 'Mallampati III (საშუალო სირთულე)'),
    ('IV_difficult', 'Mallampati IV / მოსალოდნელი რთული ინტუბაცია (!)'),
)

STOP_BANG_CHOICES = (
    ('auto', 'ავტომატური (STOP-Bang სკრინინგით)'),
    ('low', 'დაბალი რისკი (0–2 ქ.)'),
    ('intermediate', 'საშუალო რისკი (3–4 ქ.)'),
    ('high', 'მაღალი OSA რისკი (≥5 ქ. — CPAP / მონიტორინგი!)'),
)

SURG_DURATION_CHOICES = (
    ('auto', 'ავტომატური (პროცედურის ტიპით)'),
    ('lt_2h', '< 2 სთ (0 ქ.)'),
    ('2_3h', '2–3 სთ (+16 ქ.)'),
    ('gt_3h', '> 3 სთ (+23 ქ.)'),
)

CIED_PLAN_CHOICES = (
    ('pending', 'დასაზუსტებელია'),
    ('bipolar_only', 'მხოლოდ ბიპოლარული კოაგულაცია (რეპროგრამირების გარეშე)'),
    ('magnet_async', 'მაგნიტის რეჟიმი: პეისმეიკერზე — ასინქრონული; ICD-ზე — მხოლოდ შოკის გამორთვა (პეისინგ-დამოკიდებულ ICD-ზე სავალდებულოა რეპროგრამირება + გარე პედები!)'),
    ('ep_reprogram', 'არითმოლოგის წინასწარი რეპროგრამირება ოპერაციამდე და შემდეგ'),
)

CASE_OUTCOME_CHOICES = (
    ('scheduled', 'იგეგმება / მოსამზადებელი'),
    ('operated', '✓ ოპერაცია შესრულდა'),
    ('deferred', 'გადადებულია — დროებით შეჩერებული'),
    ('cancelled_medical', 'გაუქმდა / გადაიდო — სამედიცინო მიზეზით'),
    ('cancelled_logistics', 'გაუქმდა / გადაიდო — საოპერაციო / ICU / იმპლანტის რესურსი'),
    ('cancelled_patient', 'გაუქმდა / გადაიდო — პაციენტის მიზეზით / არგამოცხადება'),
)
INACTIVE_CASE_OUTCOMES = ('operated', 'deferred', 'cancelled_medical', 'cancelled_logistics', 'cancelled_patient')

VQI_30D_CHOICES = (
    ('pending', '30 დღე ჯერ არ გასულა / მონიტორინგშია'),
    ('uneventful', '✓ გართულების გარეშე (30 დღე)'),
    ('complication', '30-დღიანი გართულება (დაფიქსირებულია ქვემოთ)'),
    ('readmission', 'განმეორებითი ჰოსპიტალიზაცია ≤30 დღეში'),
    ('mortality_30d', '30-დღიანი ლეტალობა'),
)

AAA_MORPHOLOGY_CHOICES = (
    ('fusiform', 'თითისტარისებრი (სტანდარტული)'),
    ('saccular', 'საკულარული (რუპტურის მაღალი რისკი <55 მმ-ზეც)'),
    ('rapid_growth', 'სწრაფი ზრდა (≥5 მმ / 6 თვ ან ≥10 მმ / წ)'),
    ('inflammatory', 'ანთებითი ანევრიზმა — სტერილური პერიაორტიტი (ESVS: EVAR უპირატესი)'),
    ('inflammatory_mycotic', 'საეჭვო მიკოზური / ინფიცირებული ანევრიზმა (!)'),
)

AAA_NECK_CHOICES = (
    ('pending', 'ჯერ არ შეფასებულა'),
    ('friendly_ifu', '✓ სტანდარტული ყელი (IFU: სიგრძე ≥15 მმ, კუთხე ≤60°)'),
    ('hostile_neck', 'Hostile Neck — IFU-ს გარეთ (<10–15 მმ / >60° / თრომბი-კალც.)'),
)

POPLITEAL_SCREEN_CHOICES = (
    ('pending', 'არ ჩატარებულა / დაგეგმილია'),
    ('normal', '✓ ანევრიზმის გარეშე'),
    ('aneurysm_found', 'გამოვლინდა მუხლქვეშა/ბარძაყის ანევრიზმა (≥15–20 მმ!)'),
)

POST_ANTITHROMBOTIC_CHOICES = (
    ('auto', 'ავტომატური (პროტოკოლით)'),
    ('voyager', 'VOYAGER PAD: რივაროქსაბანი 2.5 მგ ×2 + ასპირინი 100 მგ'),
    ('dapt', 'DAPT: ასპირინი + კლოპიდოგრელი (1–3 თვე)'),
    ('sapt', 'მონოთერაპია: კლოპიდოგრელი 75 მგ ან ასპირინი 100 მგ'),
    ('oac_sapt', 'OAC + ერთი ანტიაგრეგანტი (სამმაგი თერაპიის გარეშე!)'),
)

METS_LIMIT_CHOICES = (
    ('cardiac', 'ქოშინი / სტენოკარდია / დაღლა (კარდიო-რესპირატორული)'),
    ('limb', 'მხოლოდ ფეხის ტკივილი / კოჭლობა (არაკარდიული)'),
)

ECG_LABELS = {
    'pending': 'შესატანია',
    'normal': 'სინუსური რიტმი / ნორმა',
    'afib_controlled': 'ნორმოსისტოლური მოციმციმე არითმია (კონტროლირებული)',
    'ischemia_arrhythmia': 'მწვავე იშემია / არაკონტროლირებული არითმია (!)',
}

CXR_LABELS = {
    'pending': 'შესატანია',
    'normal': 'ნორმა (კეროვან-ინფილტრაციული ცვლილების გარეშე)',
    'emphysema': 'ემფიზემა / ქრ. ბრონქიტი',
    'infiltrate': 'ინფილტრაცია / გამონაჟონი (!)',
}

# Zero-Free-Text 1-tap protocol chip catalogs
CHIP_CATALOGS = {
    'cad_chips': (
        ('mi', 'გადატანილი ინფარქტი'),
        ('stent_old', 'სტენტი > 1 წ.'),
        ('stent_6_12m', 'სტენტი 6–12 თვ.'),
        ('cabg', 'შუნტირება (CABG)'),
        ('afib', 'მოციმციმე არითმია'),
        ('mech_valve', 'მექანიკური სარქველი'),
        ('angina', 'სტენოკარდია დატვირთვაზე'),
        ('hf', 'გულის უკმარისობა / შეშუპება'),
    ),
    'med_chips': (
        ('statin', 'სტატინი'),
        ('beta_blocker', 'ბეტა-ბლოკატორი'),
        ('acei_arb', 'ACEi / ARB (პრილი/სარტანი)'),
        ('ccb', 'ამლოდიპინი / CCB'),
        ('diuretic', 'დიურეტიკი'),
        ('cilostazol', 'ცილოსტაზოლი (პლეტალი)'),
        ('rivaroxaban_2_5', 'ქსარელტო 2.5 მგ x2'),
        ('amiodarone', 'ამიოდარონი'),
        ('ppi', 'ომეპრაზოლი / PPI'),
        ('steroid', 'სტეროიდი / იმუნოსუპრესანტი'),
        ('phlebotonic', 'ფლებოტონიკი'),
    ),
    'abdo_chips': (
        ('midline_lap', 'შუა ლაპაროტომია'),
        ('laparoscopy', 'ლაპაროსკოპია (ნაღვლის ბუშტი/აპენდიქსი)'),
        ('hernia_r', 'საზარდულის თიაქარი (R)'),
        ('hernia_l', 'საზარდულის თიაქარი (L)'),
        ('bowel_stoma', 'ნაწლავის რეზექცია / სტომა'),
        ('pelvic_uro', 'მცირე მენჯის / უროლოგიური'),
    ),
    'vasc_chips': (
        ('iliac_stent_r', 'თეძოს არტერიის სტენტი (R)'),
        ('iliac_stent_l', 'თეძოს არტერიის სტენტი (L)'),
        ('fem_pta_stent', 'ბარძაყ-მუხლქვეშა PTA/სტენტი'),
        ('fem_pop_bypass', 'ბარძაყ-მუხლქვეშა შუნტირება'),
        ('vein_strip', 'ვენების ოპერაცია (GSV ამოღებულია)'),
    ),
    'cea_event_chips': (
        ('amaurosis', 'ცალ თვალში დაბინდვა (Amaurosis fugax)'),
        ('aphasia', 'მეტყველების მოშლა'),
        ('tia_limb', 'კიდურის სისუსტე/დაბუჟება <24 სთ (TIA)'),
        ('minor_stroke', 'მცირე იშემიური ინსულტი'),
    ),
    'cea_neck_chips': (
        ('contra_cea', 'მეორე მხარეს CEA'),
        ('ipsi_restenosis', 'ამავე მხარეს რესტენოზი'),
        ('thyroidectomy', 'ფარისებრის ოპერაცია'),
        ('neck_rad', 'კისრის დასხივება'),
    ),
    'ulcer_chips': (
        ('r_foot', 'მარჯვენა ტერფი'),
        ('l_foot', 'მარცხენა ტერფი'),
        ('toes', 'თით(ებ)ი'),
        ('heel', 'ქუსლი / ტერფი'),
        ('shallow', 'ზედაპირული წყლული'),
        ('deep_bone', 'ღრმა წყლული'),
        ('dry_necrosis', 'მშრალი ნეკროზი'),
    ),
    'pulm_chips': (
        ('copd', 'ფქოდ (COPD)'),
        ('asthma', 'ბრონქული ასთმა'),
        ('inhaler', 'მუდმივი ინჰალატორი'),
        ('osa', 'ძილის აპნოე'),
    ),
    'inf_chips': (
        ('dysuria', 'შარდვის წვა / დიზურია'),
        ('fever_resp', 'სიცხე / რესპირატორული ინფექცია'),
        ('dental_acute', 'კბილის / ღრძილის ანთება'),
        ('skin_boil', 'კანის ჩირქოვანი კერა'),
    ),
    'allergy_chips': (
        ('beta_lactam', 'პენიცილინი / ცეფაზოლინი (β-ლაქტამი)'),
        ('penicillin_mild', 'პენიცილინზე მსუბუქი გამონაყარი (არა-ანაფილაქსიური)'),
        ('vancomycin', 'ვანკომიცინი'),
        ('hit_heparin', 'ჰეპარინი (HIT!)'),
        ('lidocaine', 'ლიდოკაინი / ნოვოკაინი'),
        ('nsaid', 'NSAID / ანალგინი'),
        ('clopidogrel_intol', 'კლოპიდოგრელის აუტანლობა'),
        ('latex_chlorhex', 'ლატექსი / ქლორჰექსიდინი'),
    ),
    'ven_dvt_chips': (
        ('vte_recent_3m', 'DVT / PE ბოლო 3 თვეში (<3 თვ!)'),
        ('ipsi_dvt', 'იმავე კიდურის DVT'),
        ('contra_dvt', 'მეორე კიდურის DVT'),
        ('pe', 'ფილტვის ემბოლია (PE)'),
    ),
    'ven_rec_chips': (
        ('prev_phleb', 'წარსულში ფლებექტომია'),
        ('prev_evla', 'წარსულში EVLA / RFA'),
        ('prev_sclero', 'წარსულში სკლეროთერაპია'),
    ),
    'cardio_note_chips': (
        ('cleared_rx', 'დაშვებულია მიმდინარე თერაპიის ფონზე'),
        ('added_bb_statin', 'დაემატა სტატინი / ოპტიმიზებულია თერაპია'),
        ('bp_stabilize', 'წნევის სტაბილიზაციის შემდეგ (<160/100)'),
        ('afib_rate_ok', 'ნორმოსისტოლია მიღწეულია'),
        ('bridging_agreed', 'ანტიკოაგულაციის სქემა შეთანხმებულია'),
    ),
    'nurse_note_chips': (
        ('disc_pending', 'DICOM დისკი მოსატანია'),
        ('waiting_labs', 'ანალიზის პასუხს ველოდებით'),
        ('fasting_instructed', 'უზმოდ მოსვლა და წამლების სქემა განემარტა'),
        ('stocking_instructed', '2 კლასის კომპრესიული წინდა შეძენილია'),
        ('family_contacted', 'ოჯახის წევრი გაფრთხილებულია / დონორი მზადაა'),
    ),
    'anesth_lines_chips': (
        ('art_line', 'ინვაზიური არტ. წნევა (A-line)'),
        ('cvc', 'ცენტრალური ვენური კათეტერი (CVC)'),
        ('epidural', 'ეპიდურული კათეტერი'),
        ('cerebral_oximetry', 'ცერებრული ოქსიმეტრია / შუნტი (CEA)'),
    ),
    'cancel_reason_chips': (
        ('uncontrolled_bp', 'არაკონტროლირებადი ჰიპერტენზია (≥180/110)'),
        ('cardio_workup', 'დამატებითი კარდიოკვლევა / CAG'),
        ('anticoag_not_held', 'ანტიკოაგულანტი / P2Y12 არ იყო შეჩერებული'),
        ('acute_infection', 'მწვავე ინფექცია / ცხელება / UTI'),
        ('hyperglycemia_electrolyte', 'დეკომპენსირებული გლიკემია / K+ / კოაგულოგრამა'),
        ('no_icu_bed', 'თავისუფალი რეანიმაციის (ICU) საწოლის არარსებობა'),
        ('implant_unavailable', 'სტენტ-გრაფტის / იმპლანტის / სისხლის შეფერხება'),
        ('patient_declined', 'პაციენტის უარი / არგამოცხადება / არაუზმოდ მოსვლა'),
    ),
    'vqi_complication_chips': (
        ('stroke_tia', 'ინსულტი / TIA'),
        ('mi_mins', 'მიოკარდიუმის ინფარქტი / MINS'),
        ('bleeding_reop', 'სისხლდენა / რეოპერაცია ჰემატომის გამო'),
        ('graft_thrombosis', 'შუნტის / სტენტის / რეკონსტრუქციის თრომბოზი'),
        ('major_amputation', 'კიდურის დიდი ამპუტაცია'),
        ('aki', 'თირკმლის მწვავე დაზიანება (AKI)'),
        ('wound_ssi', 'ჭრილობის / პროთეზის ინფექცია (SSI)'),
        ('cranial_nerve', 'კრანიალური ნერვის პარეზი (CEA)'),
        ('endoleak', 'ენდოლიკი (EVAR Type I/III)'),
        ('dvt_pe', 'DVT / PE / EHIT'),
    ),
}

CHIP_LABELS = {
    field: dict(items) for field, items in CHIP_CATALOGS.items()
}

SELF_FILL_MED_HINTS = (
    ('statin', 'სტატინი (კრესტორი / ლიპრიმარი / ატორის)'),
    ('beta_blocker', 'ბეტა-ბლოკატორი (კონკორი / ნებილეტი / ეგიდოკი)'),
    ('acei_arb', 'წნევის წამალი — პრესტარიუმი / ვალსარტანი / ენაპი'),
    ('ccb', 'ამლოდიპინი / ნორვასკი'),
    ('diuretic', 'შარდმდენი (ფუროსემიდი / ვეროშპირონი / ინდაპამიდი)'),
    ('cilostazol', 'ცილოსტაზოლი (პლეტალი)'),
    ('rivaroxaban_2_5', 'ქსარელტო 2.5 მგ (დღეში 2-ჯერ)'),
    ('amiodarone', 'ამიოდარონი (კორდარონი)'),
    ('ppi', 'კუჭის დამცავი (ომეპრაზოლი / ნექსიუმი)'),
    ('steroid', 'ჰორმონი / სტეროიდი (პრედნიზოლონი / დექსა / მედროლი)'),
    ('phlebotonic', 'ვენების წამალი (დეტრალექსი / ფლებოდია)'),
)

DURATION_CHIPS = (
    ('< 2 კვირა', '< 2 კვირა'),
    ('1–3 თვე', '1–3 თვე'),
    ('3–12 თვე', '3–12 თვე'),
    ('> 1 წელი', '> 1 წელი'),
)

STATUS_NO_Q = 'no_questionnaire'
STATUS_PROGRESS = 'in_progress'
STATUS_ALERT = 'needs_doctor'
STATUS_READY = 'ready'

STATUS_LABELS = {
    STATUS_NO_Q: 'კითხვარი შესავსებია',
    STATUS_PROGRESS: 'კვლევებშია',
    STATUS_ALERT: 'ექიმის ჩართვაა საჭირო',
    STATUS_READY: 'მზადაა ექიმისთვის',
}

PRIORITY_LABELS = {
    'urgent': 'სასწრაფო პრიორიტეტი',
    'high': 'მაღალი პრიორიტეტი',
    'moderate': 'გეგმური პრიორიტეტი',
    'low': 'გეგმური (დაბალი რისკი)',
}


_FREETEXT_ALLERGY_STEMS = (
    ('beta_lactam', (
        'პენიცილ', 'ამოქსი', 'აუგმენტ', 'ამპიცილ', 'ცეფაზოლ', 'კეფაზოლ', 'კეფზოლ', 'ცეფალექს',
        'ცეფტრიაქს', 'როცეფინ', 'ცეფეპიმ', 'ცეფუროქს', 'ცეფტაზიდიმ', 'ცეფოპერაზონ', 'პიპერაცილინ', 'ტაზოცინ',
        'მეროპენემ', 'იმიპენემ', 'ერტაპენემ', 'ბიცილინ', 'ცეფალოსპ', 'ბეტა-ლაქტ', 'ბეტალაქტ',
        'penicil', 'amoxi', 'augment', 'ampicil', 'cefazol', 'kefzol', 'cephalex', 'cefalex', 'cefurox',
        'ceftriax', 'rocephin', 'cefepim', 'ceftazidim', 'cefoperazon', 'piperacil', 'tazocin',
        'meropenem', 'imipenem', 'ertapenem', 'bicillin', 'cephalosp', 'beta-lact', 'ოგმენტინ', 'ამოქსიკლავ', 'amoxiclav',
    )),
    ('vancomycin', (
        'ვანკომიც', 'vancomyc',
    )),
    ('hit_heparin', (
        'ჰეპარინ', 'კლექსან', 'ფრაქსიპარ', 'ენოქსაპარ', 'ნადროპარ', 'ციბორ', 'ფრაგმინ',
        'თინზაპარ', 'ინოჰეპ', 'დაბალმოლეკულ',
        'heparin', 'clexane', 'fraxipar', 'enoxapar', 'nadropar', 'daltepar', 'bemipar', 'lovenox',
        'tinzapar', 'innohep', 'low molecular', 'low-molecular',
    )),
    ('lidocaine', (
        'ლიდოკაინ', 'ნოვოკაინ', 'ბუპივაკაინ', 'როპივაკაინ', 'არტიკაინ', 'ულტრაკაინ', 'უბისტეზინ',
        'მარკაინ', 'ნაროპინ',
        'lidocain', 'novocain', 'bupivacain', 'ropivacain', 'articain', 'ultracain', 'ubistesin',
        'marcain', 'naropin',
    )),
    ('latex_chlorhex', (
        'ლატექს', 'ქლორჰექს', 'latex', 'chlorhex',
    )),
    ('iodine', (
        'კონტრასტ', 'ომნიპაკ', 'ულტრავისტ', 'ვიზიპაკ', 'ბეტადინ', 'პოვიდონ', 'უროგრაფინ', 'იოჰექსოლ', 'იოპამიდოლ',
        'contrast', 'omnipaque', 'ultravist', 'visipaque', 'betadin', 'povidon', 'urografin', 'iohexol', 'iopamidol',
    )),
    ('nsaid', (
        'ანალგინ', 'კეტოროლ', 'კეტონალ', 'დექსკეტო', 'დექსალგინ', 'დიკლოფენ', 'იბუპროფ', 'აასს',
        'არა-სტეროიდ', 'არასტეროიდ',
        'ასპირინ', 'კარდიომაგნ', 'ასპიკარდ', 'თრომბო-ას', 'თრომბოას', 'ნიმესილ', 'ნიმესულ', 'მელოქსიკ',
        'ვოლტარენ', 'ნუროფენ', 'ნაპროქსენ', 'ნაფროქსენ',
        'nsaid', 'ketorol', 'ketonal', 'dexketo', 'dexalgin', 'diclofen', 'ibuprof', 'analgin',
        'aspirin', 'cardiomagn', 'aspicard', 'nimesil', 'nimesul', 'voltaren', 'nurofen', 'naproxen',
    )),
    ('clopidogrel_intol', (
        'კლოპიდოგ', 'პლავიქს', 'ზილტ', 'ტრომბექს', 'ლოპირელ', 'პლოგრილ', 'კლოპიდექს', 'ეგითრომბ',
        'დუოპლავინ', 'კოპლავიქს',
        'clopidog', 'plavix', 'zylt', 'trombex', 'lopirel', 'plogril', 'clopidex', 'egithromb',
        'duoplavin', 'coplavix',
    )),
)
_HIT_WORD_RE = re.compile(r'(?<![a-zA-Zა-ჰ])hit(?![a-zA-Zა-ჰ])', re.IGNORECASE)
_IODINE_WORD_RE = re.compile(r'(?<![ა-ჰa-zA-Z])(?:იოდ|iodin)', re.IGNORECASE)


_FREETEXT_HIGH_RISK_MED_RULES = (
    ('warfarin', 'ვარფარინი / აცენოკუმაროლი (VKA)', True, (
        'ვარფარინ', 'ვარფარექს', 'ვარფარეკს', 'warfarin', 'warfarex', 'კუმადინ', 'coumadin', 'მარევან', 'marevan',
        'სინკუმარ', 'სინტრომ', 'აცენოკუმარ', 'sintrom', 'acenocoumar', 'syncumar',
        'მარკუმარ', 'marcoumar', 'ფენპროკუმონ', 'phenprocoumon',
    )),
    ('doac', 'Xa-DOAC (ქსარელტო / ელიქვისი / ედიქსანი)', True, (
        'ქსარელტ', 'კსარელტ', 'xarelto', 'რივაროქს', 'რივაროკს', 'rivarox', 'ელიქვის', 'ელიკვის', 'eliquis',
        'აპიქსაბ', 'აპიკსაბ', 'apixab', 'ედიქსან', 'ედოქსაბ', 'ედოკსაბ', 'edoxab', 'ლიქსიან', 'lixiana', 'სავაის', 'savaysa',
    )),
    ('dabigatran', 'დაბიგატრანი (პრადაქსა)', True, ('პრადაქს', 'პრადაკს', 'pradaxa', 'დაბიგატრ', 'dabigatr')),
    ('parenteral_oac', 'პარენტერული ანტიკოაგულანტი (LMWH / ჰეპარინი / ფონდაპარინუქსი)', True, (
        'კლექსან', 'clexane', 'ლოვენოქს', 'lovenox', 'ფრაქსიპარ', 'fraxipar',
        'ენოქსაპარ', 'enoxapar', 'ნადროპარ', 'nadropar', 'ფრაგმინ', 'fragmin',
        'დალტეპარ', 'daltepar', 'ციბორ', 'zibor', 'ბემიპარ', 'bemipar',
        'თინზაპარ', 'tinzapar', 'ინოჰეპ', 'innohep', 'დაბალმოლეკულ', 'low molecular', 'low-molecular',
        'ჰეპარინ', 'heparin', 'არიქსტრ', 'arixtra', 'ფონდაპარინუქს', 'ფონდაპარინუკს', 'fondaparinux',
    )),
    ('parenteral_dti', 'პარენტერული პირდაპირი თრომბინის ინჰიბიტორი (არგატრობანი / ბივალირუდინი)', True, (
        'არგატრობან', 'argatroban', 'ბივალირუდინ', 'bivalirudin',
    )),
    ('clopidogrel', 'კლოპიდოგრელი (პლავიქსი)', True, (
        'პლავიქს', 'პლავიკს', 'plavix', 'კლოპიდოგ', 'clopidog', 'ზილტ', 'zylt', 'ტრომბექს', 'ტრომბეკს', 'trombex',
        'ლოპირელ', 'lopirel', 'პლოგრილ', 'plogril', 'კლოპიდექს', 'კლოპიდეკს', 'clopidex', 'ეგითრომბ', 'egithromb',
        'დუოპლავინ', 'duoplavin', 'კოპლავიქს', 'კოპლავიკს', 'coplavix',
    )),
    ('ticagrelor', 'ტიკაგრელორი (ბრილინტა / ბრილიქი)', True, (
        'ბრილინტ', 'brilinta', 'ბრილიქ', 'brilique', 'ტიკაგრელ', 'ticagrel',
    )),
    ('prasugrel', 'პრასუგრელი (ეფიენტი)', True, ('ეფიენტ', 'effient', 'efient', 'პრასუგრელ', 'prasugrel')),
    ('cilostazol', 'ცილოსტაზოლი (პლეტალი)', True, ('ცილოსტაზოლ', 'cilostazol', 'პლეტალ', 'pletal')),
    ('sglt2', 'SGLT2 ინჰიბიტორი (ჯარდიანსი / ფორსიგა / სტეგლატრო)', False, (
        'ჯარდიანს', 'jardiance', 'ფორსიგ', 'ფორქსიგ', 'ფარქსიგ', 'forxiga', 'farxiga', 'სინჯარდ', 'synjardy',
        'ქსიგდუო', 'xigduo', 'ვოკანამეტ', 'vokanamet', 'ინვოკამეტ', 'invokamet', 'სეგლურომეტ', 'segluromet',
        'სტეგლუჯან', 'steglujan', 'გლიქსამბ', 'glyxambi',
        'ინვოკან', 'invokana', 'ემპაგლიფ', 'empaglif',
        'დაპაგლიფ', 'dapaglif', 'ერტუგლიფ', 'ertuglif', 'სტეგლატრ', 'steglatro', 'კანაგლიფ', 'canaglif',
    )),
    ('glp1', 'GLP-1 აგონისტი (ოზემპიკი / მუნჯარო / საქსენდა)', False, (
        'ოზემპიკ', 'ozempic', 'ვეგოვ', 'wegovy', 'რიბელსუს', 'rybelsus',
        'მუნჯარ', 'mounjaro', 'ზეპბაუნდ', 'zepbound', 'საქსენდ', 'saxenda', 'ვიქტოზ', 'victoza',
        'სემაგლუტ', 'semaglut', 'ლირაგლუტ', 'liraglut', 'ტირზეპატ', 'tirzepat',
        'დულაგლუტ', 'dulaglut', 'ტრულიციტ', 'ტრულისიტ', 'trulicity',
        'ბიეტა', 'byetta', 'ბიდურეონ', 'bydureon', 'ექსენატიდ', 'exenatid', 'ლიქსისენატიდ', 'lixisenatid',
    )),
    ('insulin', 'ინსულინი', False, (
        'ინსულინ', 'insulin', 'ლანტუს', 'lantus', 'ტუჯეო', 'toujeo', 'ტრესიბ', 'tresiba',
        'ნოვორაპიდ', 'novorapid', 'ნოვომიქს', 'novomix', 'ჰუმალოგ', 'humalog', 'ჰუმულინ', 'humulin',
        'აპიდრ', 'apidra', 'ლევამირ', 'ლევემირ', 'levemir', 'აქტრაპიდ', 'actrapid',
    )),
    ('metformin', 'მეტფორმინი (გლუკოფაჟი / სიოფორი)', False, (
        'მეტფორმინ', 'metformin', 'გლუკოფაჟ', 'glucophage', 'სიოფორ', 'siofor',
        'სინჯარდ', 'synjardy', 'ქსიგდუო', 'xigduo', 'ვოკანამეტ', 'vokanamet', 'ინვოკამეტ', 'invokamet',
        'სეგლურომეტ', 'segluromet', 'იანუმეტ', 'ჯანუმეტ', 'janumet',
        'გალვუს მეტ', 'გალვუსმეტ', 'galvus met', 'galvusmet', 'ეუკრეას', 'eucreas',
        'ტრაჯენტა დუო', 'jentadueto', 'გლუკოვანს', 'glucovance',
    )),
    ('steroid', 'სტეროიდი (პრედნიზოლონი / მედროლი)', False, (
        'პრედნიზოლ', 'პრედნიზონ', 'prednisol', 'prednison', 'მედროლ', 'medrol',
        'მეთილპრედ', 'methylpred', 'დექსამეტაზ', 'dexamethas', 'ჰიდროკორტიზონ', 'hydrocortison',
    )),
    ('amiodarone', 'ამიოდარონი (კორდარონი)', False, (
        'ამიოდარონ', 'amiodaron', 'კორდარონ', 'cordaron', 'cordarone', 'paceron',
    )),
    ('omeprazole', 'ომეპრაზოლი / ეზომეპრაზოლი (CYP2C19)', False, (
        'ომეპრაზოლ', 'omeprazol', 'omez', 'ომეზ', 'ნექსიუმ', 'nexium', 'ეზომეპრაზ', 'esomepraz',
        'ემანერ', 'emanera',
    )),
)


DEFAULT_Q = {
    'filled': False,
    'filled_by': '',
    'self_fill_submitted': False,
    'nurse_verified': False,
    'nurse_verified_by': '',
    'allergy_iodine_asked': False,
    # AAA module
    'aaa_pain': False,
    'cfs_level': '1_3',             # 1_3 | 4 | 5_plus
    'aaa_pedal_pulse': 'intact',    # intact | reduced
    'prev_abdo_surg': False,
    'abdo_chips': [],
    'prev_abdo_text': '',
    'claudication': False,
    'active_infection': False,
    'inf_chips': [],
    'infection_text': '',
    # CEA module
    'cea_symp_event': False,
    'cea_event_chips': [],
    'cea_event_text': '',
    'cea_event_date': '',
    'cea_days_since_event': None,
    'cea_crescendo': False,
    'cea_thrombolysis': False,
    'cea_prior_neck': False,
    'cea_neck_chips': [],
    'cea_prior_neck_text': '',
    'cea_hoarseness': False,
    'cea_neck_limited': False,
    'cea_disabling_stroke': False,
    # ABF & PAD Bypass / PTA shared limb ischemia module
    'abf_claud_m': None,
    'abf_buttock_pain': False,
    'abf_duration': '',
    'ic_bmt_3m': False,
    'abf_rest_pain': False,
    'abf_ulcer': False,
    'ulcer_chips': [],
    'abf_ulcer_text': '',
    'abf_wet_infection': False,
    'abf_groin_issue': False,
    'abf_prior_vasc': False,
    'vasc_chips': [],
    'abf_prior_vasc_text': '',
    'pta_cant_lie_flat': False,
    # Venous module — EVLA & Phlebectomy
    'ven_ceap': 'C2',               # C2 | C3 | C4 | C5 | C6
    'ven_dvt_history': False,
    'ven_dvt_chips': [],
    'ven_dvt_text': '',
    'ven_thrombophilia': False,
    'ven_acute_svt': False,
    'ven_bleeding': False,
    'ven_lidocaine_allergy': False,
    'ven_skin_infection': False,
    'ven_recurrent': False,
    'ven_rec_chips': [],
    'ven_recurrent_text': '',
    'ven_hormones': False,
    'ven_cancer_immob': False,
    'ven_pregnant': False,
    # General & anesthesia screening (Zero-free-text chip-ready)
    'mets_ok': True,
    'mets_limitation': 'cardiac',   # cardiac | limb
    'cad': False,
    'cad_chips': [],
    'cad_detail': '',
    'recent_stent': False,
    'htn': False,
    'pulm': False,
    'pulm_chips': [],
    'pulm_detail': '',
    'dm': False,
    'dm_insulin': False,
    'metformin': False,
    'sglt2': False,
    'glp1': False,
    'on_dialysis': False,
    'thyroid_disease': False,
    'dental_infection': False,
    'neuro': False,
    'carotid_bruit_symp': False,
    'ulcer': False,
    'ulcer_active_symp': False,
    'med_aspirin': False,
    'med_clopidogrel': False,
    'med_ticagrelor': False,
    'med_prasugrel': False,
    'med_doac': False,
    'med_dabigatran': False,
    'med_warfarin': False,
    'med_chips': [],
    'other_meds': '',
    'allergy_iodine': False,
    'allergy_other': False,
    'allergy_chips': [],
    'allergy_text': '',
    'anesth_issue': False,
    'pacemaker': False,
    'loose_teeth': False,
    'smoker': False,
    'pregnant_possible': False,
}

DEFAULT_STUDIES = {
    # Aneurysm & CTA (ESVS 2024 IFU / Morphology / Popliteal screening)
    'aaa_mm': None,
    'aaa_morphology': 'fusiform',   # fusiform | saccular | rapid_growth | inflammatory | inflammatory_mycotic
    'aaa_neck_status': 'pending',   # pending | friendly_ifu | hostile_neck
    'popliteal_screen_status': 'pending',  # pending | normal | aneurysm_found
    'cta_date': '',
    'cta_months': None,
    'cta_disc': False,
    'aaa_iliac_access': 'pending',  # pending | ok_7mm | narrow_calcified | iliac_aneurysm
    'aaa_renal_vein_anom': False,
    # CEA Stage 1 & Stage 2 specific
    'cea_cta_status': 'pending',    # pending | concordant | high_calcified
    'brain_imaging': 'pending',     # pending | normal_small | large_or_hemorrhage
    'laryngoscopy_res': 'pending',  # pending | normal | palsy
    'neuro_consult_done': False,
    'cea_antiplatelet_started': False,
    # ABF & Limb Hemodynamics (ABI + Toe Pressure / TBI)
    'abi_r': None,
    'abi_l': None,
    'tp_r': None,
    'tp_l': None,
    'tbi_r': None,
    'tbi_l': None,
    'abf_clamp_zone': 'pending',    # pending | infrarenal | juxtarenal_calcified
    'abf_outflow': 'pending',       # pending | patent | profunda_stenosis | poor_outflow
    'abf_mesenteric': 'normal',     # normal | ima_dominant_iia_occ | sma_stenosis
    'abf_renal_stenosis': False,
    'wound_care_done': False,
    # PAD Bypass & PTA specific + VOYAGER PAD post-op plan
    'pad_runoff_status': 'pending', # pending | popliteal_patent | tibial_single | desert_foot
    'pad_gsv_status': 'pending',    # pending | adequate | inadequate
    'pad_gsv_mm': None,
    'alt_vein_mapped': 'pending',   # pending | contra_gsv_ok | arm_ssv_ok | no_autovein_prosthetic
    'pta_lesion_site': 'femoropopliteal',  # iliac | femoropopliteal | infrapopliteal | multilevel
    'pta_access_site': 'pending',   # pending | cfa_clean | cfa_calcified_scar
    'post_antithrombotic_plan': 'auto',    # auto | voyager | dapt | sapt | oac_sapt
    'case_wifi_unsalvageable': False,
    'case_summary_badge': '',
    # Venous Stage 1 & Stage 2 specific
    'ven_duplex_status': 'pending', # pending | deep_patent_reflux | acute_dvt | deep_obstruction | superficial_thrombus
    'ven_target_vein': 'gsv',       # gsv | ssv | gsv_ssv | aagsv_trib
    'ven_gsv_mm': None,
    'ven_epifascial': False,
    'ven_tortuous': False,
    'ven_perforator': False,
    'vte_prophylaxis_planned': False,
    'compression_ready': False,
    # Vitals (including bilateral arm BP)
    'bp_sys': None,
    'bp_dia': None,
    'bp_sys_l': None,
    'bp_dia_l': None,
    'hr': None,
    'irregular_hr': False,
    'spo2': None,
    'weight_kg': None,
    'height_cm': None,
    # Stage 1 Labs & Biomarkers (NT-proBNP, hs-cTn, TSAT)
    'labs_date': '',
    'blood_group': '',
    'hb': None,
    'wbc': None,
    'plt': None,
    'inr': None,
    'aptt_prolonged': False,
    'fibrinogen': None,
    'creat': None,
    'creat_unit': '',               # '' | 'umol' | 'mgdl'
    'k': None,
    'na': None,
    'glucose': None,
    'hba1c': None,
    'ldl': None,
    'ferritin': None,
    'tsat': None,
    'nt_probnp': None,
    'hs_ctn': None,
    'albumin': None,
    'liver_status': 'normal',       # normal | 2x | 3x
    'viral_positive': False,
    'urine_wbc': None,
    'urine_nitrite': False,
    # Stage 1 Imaging
    'carotid_r': None,
    'carotid_l': None,
    'carotid_symp': False,
    'cxr': 'pending',               # pending | normal | emphysema | infiltrate
    'ecg': 'pending',               # pending | normal | afib_controlled | ischemia_arrhythmia
    'lvef': None,
    'echo_valve_issue': False,
    # Stage 2 Protocol, PBM & Standing Reflex Tasks
    'anesthesia_plan': 'auto',      # auto | local | plexus | neuraxial | general
    'spiro_fev1': None,
    'gastro_result': 'pending',     # pending | remission | acute_ulcer
    'ppi_started': False,
    'urine_culture_done': False,
    'anemia_panel_done': False,
    'pbm_iv_iron_done': False,
    'cell_saver_ready': False,
    'endo_consult_done': False,
    'nephro_panel_done': False,
    'hydration_planned': False,
    'tsh_status': 'pending',        # pending | euthyroid | hyperthyroid
    'dental_sanitation_done': False,
    'decolonization_ready': False,
    'contrast_premed_done': False,
    'dapt_ready': False,
    'cied_checked': False,
    'stale_labs_refreshed': False,
    'blood_reserved': False,
    'evar_sized': False,
    'med_hold_agreed': False,
    'anesth_done': False,
    'board_patient_id': None,
    'cardio_note_chips': [],
    'cardio_note_custom': '',
    'nurse_note_chips': [],
    'nurse_note_custom': '',
    'nurse_note': '',
    'alerts_ack_sig': '',
    # Stage P2 & P3: Identifiers, Surgical Date, Pause/Outcome Freeze, Anesthesia, DoS Gate & 30-Day VQI
    'person_id': '',
    'birth_date': '',
    'self_fill_nonce': '',
    'self_fill_revoked': False,
    'planned_surgery_date': '',
    'operated_date': '',
    'outcome_recorded_on': '',
    'hold_since': '',
    'hold_paused_days': 0,
    'asa_class': 'auto',            # auto | I | II | III | IV
    'asa_emergency': False,
    'airway_mallampati': 'I_II',    # I_II | III | IV_difficult
    'stop_bang_risk': 'auto',       # auto | low | intermediate | high
    'surg_duration_est': 'auto',    # auto | lt_2h | 2_3h | gt_3h (Canet 2010 ARISCAT)
    'cied_plan': 'pending',         # pending | bipolar_only | magnet_async | ep_reprogram
    'anesth_lines_chips': [],
    'icu_bed_booked': False,
    'dos_identity_consent': False,
    'dos_site_marked': False,
    'dos_fasting_confirmed': False,
    'dos_med_hold_verified': False,
    'dos_ketones_ok': False,
    'dos_k_verified': False,
    'dos_latex_free_or': False,
    'dos_bhcg_negative': False,
    'dos_abx_ready': False,
    'dos_blood_implant_ready': False,
    'dos_bp_sys': None,
    'dos_bp_dia': None,
    'dos_glucose': None,
    'dos_inr': None,
    'case_outcome_status': 'scheduled',  # scheduled | operated | cancelled_medical | cancelled_logistics | cancelled_patient
    'cancel_reason_chips': [],
    'vqi_discharge_antiplatelet': False,
    'vqi_discharge_statin': False,
    'vqi_smoking_cessation': False,
    'vqi_surveillance_scheduled': False,
    'vqi_30d_status': 'pending',    # pending | uneventful | complication | readmission | mortality_30d
    'vqi_complication_chips': [],
    'self_fill_submitted_nonce': '',
    'self_fill_reopened': False,
}
