"""Nurse-led preoperative assessment ("წინასაოპერაციო") in a Tweek.so-style weekly calendar.

Clinical pathways:
1. Infrarenal Abdominal Aortic Aneurysm (AAA — Open repair & EVAR): ESVS 2024 / ESC 2022 (7-day clock).
2. Carotid Endarterectomy (CEA — Symptomatic & Asymptomatic): ESVS 2023 / SVS 2022 / ESC 2022
   (5-day urgent prep clock + 14-day stroke-prevention window from index symptom onset).
3. Aortobifemoral Bypass (ABF — CLTI & Disabling Leriche Claudication): ESVS 2024 / ESC 2024 / GVG 2019
   (7-day limb-salvage clock for CLTI, 14-day clock for claudication).
4. Chronic Venous Disease (EVLA & Phlebectomy): ESVS 2022 / SVS-AVF 2023 / ESC 2022 low-risk (<1% MACE)
   day-surgery pathway with tumescent vs spinal/general anesthesia logic, Caprini VTE screening,
   arterial compression safety (ABI), and signed Patient Self-Fill Questionnaire link.
"""
import datetime
import hashlib
import math
import os
import re
import secrets
import uuid

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from .board import add_event, clean_text, doctors, person_name
from .cases import MAX_UPLOAD, PhotoError, process_photo
from .duty import MONTHS
from .models import BoardEntry, BoardPatient, Case, PreopFile, PreopPatient

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
    ('magnet_async', 'მაგნიტის რეჟიმი საოპერაციოში (ასინქრონული / ICD დეაქტივაცია)'),
    ('ep_reprogram', 'არითმოლოგის წინასწარი რეპროგრამირება ოპერაციამდე და შემდეგ'),
)

CASE_OUTCOME_CHOICES = (
    ('scheduled', 'იგეგმება / მოსამზადებელი'),
    ('operated', '✓ ოპერაცია შესრულდა'),
    ('cancelled_medical', 'გაუქმდა / გადაიდო — სამედიცინო მიზეზით'),
    ('cancelled_logistics', 'გაუქმდა / გადაიდო — საოპერაციო / ICU / იმპლანტის რესურსი'),
    ('cancelled_patient', 'გაუქმდა / გადაიდო — პაციენტის მიზეზით / არგამოცხადება'),
)

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
        ('hit_heparin', 'ჰეპარინი (HIT!)'),
        ('lidocaine', 'ლიდოკაინი / ნოვოკაინი'),
        ('nsaid', 'NSAID / ანალგინი'),
        ('clopidogrel_intol', 'კლოპიდოგრელის აუტანლობა'),
        ('latex_chlorhex', 'ლატექსი / ქლორჰექსიდინი'),
    ),
    'ven_dvt_chips': (
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


def chip_summary(selected_keys, field_name, fallback_text=''):
    labels = CHIP_LABELS.get(field_name, {})
    picked = [labels[k] for k in (selected_keys or []) if k in labels]
    extra = (fallback_text or '').strip()
    if extra and extra not in picked:
        picked.append(extra)
    return ', '.join(picked)


# --------------------------------------------------------------------------
# Parsing & clinical math helpers (with explicit unit-ambiguity guards)
# --------------------------------------------------------------------------

def _num(val, lo=None, hi=None):
    if val is None or val == '' or isinstance(val, (bool, list, tuple, dict, set)):
        return None
    try:
        v = float(str(val).strip().replace(',', '.'))
    except (ValueError, TypeError):
        return None
    if not math.isfinite(v):
        return None
    if lo is not None and v < lo:
        return None
    if hi is not None and v > hi:
        return None
    return round(v, 2) if v % 1 else int(v)


def _int(val, lo=None, hi=None):
    v = _num(val, lo=lo, hi=hi)
    return int(round(v)) if v is not None else None


def _date(val):
    if isinstance(val, datetime.datetime):
        return val.date()
    if isinstance(val, datetime.date):
        return val
    try:
        return datetime.date.fromisoformat(str(val or '').strip())
    except ValueError:
        return None


def _resolve_pathway(patient):
    """Resolve canonical PreopPatient.Pathway even if op_subtype or pathway contains legacy/corrupt values."""
    pw = PreopPatient.SUBTYPE_PATHWAY.get(patient.op_subtype, patient.pathway or PreopPatient.Pathway.AAA_INFRA)
    return pw if pw in PreopPatient.Pathway.values else PreopPatient.Pathway.AAA_INFRA


def _clean_person_id(val):
    """Return digits-only Georgian Personal ID (up to 11 digits)."""
    return re.sub(r'\D', '', str(val or ''))[:11]


def _fmt_ka_date(val, short=False):
    """Format ISO YYYY-MM-DD date as dd.mm.yyyy (or dd.mm when short=True)."""
    dt = val if isinstance(val, datetime.date) else _date(val)
    if not dt:
        return ''
    return dt.strftime('%d.%m' if short else '%d.%m.%Y')


def _age_from_dob(val, today=None):
    """Compute integer age (1..120) from ISO birth_date."""
    dob = val if isinstance(val, datetime.date) else _date(val)
    today = today or timezone.localdate()
    if not dob or dob > today or dob.year < 1900:
        return None
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return years if 1 <= years <= 120 else None


def _parse_scr_mgdl(creat, creat_unit=''):
    """Return (scr_mgdl, is_ambiguous). Range 15.0 < c < 35.0 requires explicit unit ('mgdl'|'umol')."""
    c = _num(creat, lo=0.1, hi=3000)
    if c is None:
        return None, False
    if creat_unit == 'mgdl':
        return c, False
    if creat_unit == 'umol':
        return c / 88.4, False
    if 0.2 <= c <= 15.0:
        return c, False
    if c >= 35.0:
        return c / 88.4, False
    return None, True


def calc_egfr(creat, age, sex='M', creat_unit='', on_dialysis=False):
    """CKD-EPI 2021 race-free eGFR (mL/min/1.73m2). Never guesses missing age/sex or ambiguous units."""
    if on_dialysis:
        return None
    scr_mgdl, _ = _parse_scr_mgdl(creat, creat_unit)
    if scr_mgdl is None or not age or not (12 <= age <= 120) or sex not in ('M', 'F'):
        return None
    female = (sex == 'F')
    kappa = 0.7 if female else 0.9
    alpha = -0.241 if female else -0.302
    mult = 1.012 if female else 1.0
    ratio = scr_mgdl / kappa
    egfr = 142.0 * (min(ratio, 1.0) ** alpha) * (max(ratio, 1.0) ** -1.200) * (0.9938 ** age) * mult
    return int(round(egfr))


def calc_crcl(creat, age, sex='M', weight_kg=None, creat_unit='', on_dialysis=False, height_cm=None):
    """Cockcroft-Gault unindexed CrCl (mL/min) using Adjusted Body Weight in BMI >= 30 obesity."""
    if on_dialysis:
        return None
    scr_mgdl, _ = _parse_scr_mgdl(creat, creat_unit)
    w = _num(weight_kg, lo=25, hi=260)
    if scr_mgdl is None or w is None or not age or not (12 <= age <= 120) or sex not in ('M', 'F'):
        return None
    h = _num(height_cm, lo=100, hi=230)
    eff_w = float(w)
    if h is not None:
        bmi_v = w / ((h / 100.0) ** 2)
        ibw = (50.0 if sex == 'M' else 45.5) + 0.9 * max(0.0, h - 152.4)
        if bmi_v >= 30.0 and w > ibw:
            eff_w = ibw + 0.4 * (w - ibw)
    crcl = ((140.0 - age) * eff_w) / (72.0 * scr_mgdl)
    if sex == 'F':
        crcl *= 0.85
    return int(round(crcl))


def calc_bmi(weight_kg, height_cm):
    """Body Mass Index (kg/m2) or None."""
    w = _num(weight_kg, lo=25, hi=260)
    h = _num(height_cm, lo=100, hi=230)
    if w is None or h is None:
        return None
    return round(w / ((h / 100.0) ** 2), 1)


def normalize_hb_gl(hb):
    """Return hemoglobin in g/L (converts g/dL ONLY in physiologic 3.5..22.0 range; 29 stays 29 g/L)."""
    v = _num(hb, lo=1, hi=260)
    if v is None:
        return None
    return round(v * 10, 1) if 3.5 <= v <= 22.0 else v


def normalize_glucose_mmol(gl):
    """Return glucose in mmol/L (converts mg/dL ONLY when >=45.0; severe 38 mmol/L stays 38.0)."""
    v = _num(gl, lo=0.5, hi=1000)
    if v is None:
        return None
    return round(v / 18.0, 1) if v >= 45.0 else v


def normalize_albumin_gl(alb):
    """Return serum albumin in g/L (converts g/dL ONLY in 1.0..6.0 range)."""
    v = _num(alb, lo=0.5, hi=80)
    if v is None:
        return None
    return round(v * 10, 1) if 1.0 <= v <= 6.0 else v


def calc_max_contrast_ml(egfr, on_dialysis=False):
    """Safe iodinated contrast volume ceiling (mL) using contrast-to-eGFR ratio (ESVS / Gurm)."""
    if on_dialysis or egfr is None:
        return None
    if egfr < 30:
        return max(15, int(round(egfr * 1.0)))
    if egfr < 45:
        return int(round(egfr * 1.5))
    if egfr < 60:
        return int(round(egfr * 2.0))
    return min(250, int(round(egfr * 3.0)))


def _alerts_signature(red_flags):
    """Stable SHA-1 signature across days (strips volatile elapsed day numbers like '8 დღე')."""
    if not red_flags:
        return ''
    normalized = [re.sub(r'\d+\s*დღ', 'N დღ', f) for f in red_flags]
    joined = '\n'.join(sorted(normalized)).encode('utf-8')
    return hashlib.sha1(joined).hexdigest()[:16]


def mask_person_id(pid):
    """Mask 11-digit personal number on unauthenticated pages (e.g. '010•••••001')."""
    pid = str(pid or '').strip()
    if not pid:
        return ''
    if len(pid) >= 7:
        return f'{pid[:3]}•••••{pid[-3:]}'
    return '•' * len(pid)


def mask_phone(phone):
    """Mask phone number on unauthenticated pages."""
    ph = re.sub(r'\s+', '', str(phone or ''))
    if not ph:
        return ''
    if len(ph) >= 6:
        return f'{ph[:3]}••••{ph[-2:]}'
    return '•' * len(ph)


def make_self_fill_token(patient):
    s = patient.studies if isinstance(patient.studies, dict) else {}
    nonce = str(s.get('self_fill_nonce') or '').strip()
    payload = f'{patient.pk}:{nonce}' if nonce else str(patient.pk)
    return TimestampSigner(salt=SELF_FILL_SALT).sign(payload)


def target_prep_days(patient):
    """Pathway-specific preoperative workup target limit (days), honoring questionnaire symptom/CLTI flags."""
    pw = _resolve_pathway(patient)
    q = patient.questionnaire if isinstance(patient.questionnaire, dict) else {}
    q_filled = bool(q.get('filled'))
    if pw == PreopPatient.Pathway.CEA:
        is_symp = patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP or (q_filled and bool(q.get('cea_symp_event')))
        return 5 if is_symp else 10
    has_clti_q = q_filled and (bool(q.get('abf_rest_pain')) or bool(q.get('abf_ulcer')))
    if pw == PreopPatient.Pathway.ABF:
        return 7 if (patient.op_subtype == PreopPatient.OpSubtype.ABF_CLTI or has_clti_q) else 14
    if pw == PreopPatient.Pathway.PAD_BYPASS:
        return 7 if (patient.op_subtype == PreopPatient.OpSubtype.BYPASS_CLTI or has_clti_q) else 14
    if pw == PreopPatient.Pathway.PAD_PTA:
        return 7 if (patient.op_subtype == PreopPatient.OpSubtype.PTA_CLTI or has_clti_q) else 14
    if pw == PreopPatient.Pathway.VENOUS:
        return 14
    return TARGET_PREP_DAYS


def prep_days_elapsed(patient, today=None):
    """Days elapsed since preoperative workup began (pauses on HOLD, freezes on approve/operated/cancelled)."""
    today = today or timezone.localdate()
    q = patient.questionnaire if isinstance(patient.questionnaire, dict) else {}
    s = patient.studies if isinstance(patient.studies, dict) else {}
    q_filled = bool(q.get('filled'))
    candidates = [d for d in (patient.started_on, patient.visit_date) if isinstance(d, datetime.date)]
    if not candidates:
        return 0
    start = min(candidates)
    raw_out = s.get('case_outcome_status')
    outcome = raw_out if isinstance(raw_out, str) and raw_out else 'scheduled'
    op_done_dt = _date(s.get('operated_date'))
    out_rec_dt = _date(s.get('outcome_recorded_on'))

    if patient.approved_at:
        end_day = timezone.localtime(patient.approved_at).date()
    elif outcome == 'operated' and op_done_dt:
        end_day = op_done_dt
    elif outcome.startswith('cancelled_') and (out_rec_dt or op_done_dt):
        end_day = out_rec_dt or op_done_dt
    else:
        end_day = today

    if start > end_day and not q_filled and not patient.approved_at:
        return 0

    paused = _int(s.get('hold_paused_days'), lo=0, hi=3650) or 0
    if patient.someday_list == PreopPatient.SomedayList.HOLD:
        hold_since = _date(s.get('hold_since'))
        if hold_since and hold_since <= end_day:
            paused += max(0, (end_day - hold_since).days)
    raw_days = (end_day - start).days + 1 - paused
    return max(1, raw_days)


def cea_live_days_since_event(q, today=None, s=None):
    """Compute elapsed days from cea_event_date (freezing at operated_date once operated), else static field."""
    if not q.get('cea_symp_event') and not q.get('cea_event_date') and q.get('cea_days_since_event') is None:
        return None
    ref_day = today or timezone.localdate()
    if isinstance(s, dict) and s.get('case_outcome_status') == 'operated':
        op_dt = _date(s.get('operated_date'))
        if op_dt:
            ref_day = op_dt
    ev_dt = _date(q.get('cea_event_date'))
    if ev_dt and ev_dt <= ref_day:
        return (ref_day - ev_dt).days
    return _int(q.get('cea_days_since_event'), lo=0, hi=365)


def cta_live_months_age(s, today=None):
    """Compute live CTA age in months from cta_date if present, else static cta_months."""
    today = today or timezone.localdate()
    cta_dt = _date(s.get('cta_date'))
    if cta_dt and cta_dt <= today:
        return max(0, (today - cta_dt).days // 30)
    return _int(s.get('cta_months'), lo=0, hi=120)


def detect_case_preop(case):
    """Infer op_subtype and surgical side from a Conference Case (including GVG WIfI / PLAN staging)."""
    blob = ' '.join([
        case.diagnosis or '', case.proposal or '', case.decision or '', case.history or ''
    ]).lower()
    dec_prop = ((case.decision or '') + ' ' + (case.proposal or '')).lower()
    side = case.side if case.side in ('R', 'L', 'B') else ''
    if not side:
        has_r = 'მარჯვ' in blob
        has_l = 'მარცხ' in blob
        if 'ორმხრივ' in blob or (has_r and has_l):
            side = 'B'
        elif has_r:
            side = 'R'
        elif has_l:
            side = 'L'
    is_clti = any(k in blob for k in ('კრიტიკ', 'clti', 'მოსვენებით', 'წყლულ', 'განგრენ', 'ნეკროზ'))
    has_limb_staging = False

    staging_map = case.staging if isinstance(case.staging, dict) else {}
    if staging_map:
        from . import staging as st_mod
        for s_key in ('R', 'L'):
            raw_limb = staging_map.get(s_key)
            if raw_limb and st_mod.has_staging(raw_limb):
                has_limb_staging = True
            if raw_limb and st_mod.has_wifi(raw_limb):
                cleaned = st_mod.get_limb(staging_map, s_key)
                comp = st_mod.compute(cleaned)
                if (comp.get('wifi') or 0) >= 2 or (comp.get('w') or 0) >= 1 or (comp.get('fi') or 0) >= 1:
                    is_clti = True

    if any(k in blob for k in ('ლაზერ', 'evla', 'რადიოსიხშირ', 'rfa', 'ენდოვენურ')):
        return PreopPatient.OpSubtype.EVLA, side

    if any(k in blob for k in ('კაროტიდ', 'საძილე', 'ენდარტერექტომ', 'cea')):
        is_symp = any(k in blob for k in ('სიმპტომ', 'ინსულტ', 'tia', 'ტ.ი.ა', 'ამავროზ', 'იშემიური შეტევა'))
        return (PreopPatient.OpSubtype.CEA_SYMP if is_symp else PreopPatient.OpSubtype.CEA_ASYMP), side

    if any(k in blob for k in ('ბიფემორ', 'აორტო-ბიფემ', 'აორტობიფემ', 'ლერიშ', 'abf', 'aortobifem')):
        return (PreopPatient.OpSubtype.ABF_CLTI if is_clti else PreopPatient.OpSubtype.ABF_CLAUD), side

    if any(k in blob for k in ('ანევრიზმ', 'aaa', 'evar', 'მუცლის აორტ')):
        op_sub = PreopPatient.OpSubtype.EVAR if ('evar' in dec_prop or 'ენდოვასკ' in dec_prop) else PreopPatient.OpSubtype.OPEN
        return op_sub, side

    if any(k in blob for k in ('შუნტირებ', 'ბარძაყ-მუხლქვეშ', 'ფემორო-პოპლიტ', 'ფემორო-ტიბიალ', 'აუტოვენ')):
        return (PreopPatient.OpSubtype.BYPASS_CLTI if is_clti else PreopPatient.OpSubtype.BYPASS_CLAUD), side

    if any(k in blob for k in ('ანგიოპლასტიკ', 'ბალონირებ', 'სტენტირებ', 'pta', 'რეკანალიზაც', 'plan: ენდოვასკ')):
        return (PreopPatient.OpSubtype.PTA_CLTI if is_clti else PreopPatient.OpSubtype.PTA_CLAUD), side

    if any(k in blob for k in ('ფლებექტომ', 'კროსექტომ', 'ვარიკოზ', 'საჩინო ვენ')) and not is_clti and not has_limb_staging:
        return PreopPatient.OpSubtype.PHLEB, side

    if has_limb_staging or any(k in blob for k in ('ქვემო კიდურ', 'ბარძაყ', 'მუხლქვეშ', 'წვივ', 'ტერფ', 'sfa', 'cfa', 'პერიფერიულ')):
        if 'ენდოვასკ' in blob:
            return (PreopPatient.OpSubtype.PTA_CLTI if is_clti else PreopPatient.OpSubtype.PTA_CLAUD), side
        return (PreopPatient.OpSubtype.BYPASS_CLTI if is_clti else PreopPatient.OpSubtype.BYPASS_CLAUD), side

    op_sub = PreopPatient.OpSubtype.EVAR if ('evar' in dec_prop or 'ენდოვასკ' in dec_prop) else PreopPatient.OpSubtype.OPEN
    return op_sub, side


def import_case_to_preop(case, patient):
    """Auto-import ABI/TBI/TP Doppler measurements, WIfI limb threat, GLASS runoff & GSV status from a Case."""
    from . import staging as st_mod
    from .cases import case_limb_metrics

    staging_map = case.staging if isinstance(case.staging, dict) else {}
    rows = case_limb_metrics(case)
    has_any = any(
        row['has_any_doppler'] or st_mod.has_staging(staging_map.get(row['side']))
        for row in rows
    )
    if not has_any:
        return False

    q = merged_q(patient)
    s = merged_studies(patient)
    ulcer_chips = set(q.get('ulcer_chips') or [])
    badge_parts = []
    upgrade_clti = False
    runoff_rank = {'pending': 0, 'popliteal_patent': 1, 'tibial_single': 2, 'desert_foot': 3}

    for row in rows:
        side = row['side']
        s_lo = side.lower()
        raw_limb = staging_map.get(side) or {}
        limb = row['limb']
        r = row['r']

        if row['abi'] is not None:
            s[f'abi_{s_lo}'] = row['abi']
        if row['tbi'] is not None:
            s[f'tbi_{s_lo}'] = row['tbi']
        if row['tp'] is not None:
            s[f'tp_{s_lo}'] = row['tp']

        is_target_limb = patient.side in ('', 'B', side)
        if st_mod.has_wifi(raw_limb) and is_target_limb:
            w = r.get('w') if r.get('w') is not None else limb.get('w')
            i = r.get('i') if r.get('i') is not None else limb.get('i')
            fi = r.get('fi') if r.get('fi') is not None else limb.get('fi')
            wifi_st = r.get('wifi')
            if w is not None and w >= 1:
                q['abf_ulcer'] = True
                ulcer_chips.add('r_foot' if side == 'R' else 'l_foot')
                if w >= 2:
                    ulcer_chips.add('deep_bone')
                upgrade_clti = True
            if fi is not None and fi >= 2:
                q['abf_wet_infection'] = True
                upgrade_clti = True
            if i is not None and i >= 2 and (w is None or w == 0):
                q['abf_rest_pain'] = True
                upgrade_clti = True
            if limb.get('unsalvageable') or wifi_st == 5:
                s['case_wifi_unsalvageable'] = True
                upgrade_clti = True
            elif wifi_st is not None and wifi_st >= 2:
                upgrade_clti = True
            if wifi_st is not None:
                badge_parts.append(f'{side}: WIfI Stage {wifi_st} (W{w}I{i}fI{fi})')
            if raw_limb.get('vein') == 'no':
                s['pad_gsv_status'] = 'inadequate'

        if st_mod.has_angiography(raw_limb) and is_target_limb:
            sug = r.get('suggested') or {}
            glass_st = r.get('glass') or (sug.get('glass') if sug.get('has_map') else None)
            if glass_st:
                badge_parts.append(f'{side}: GLASS {glass_st}')
            cfa_val = limb.get('cfa') if 'cfa' in raw_limb else (sug.get('cfa') if sug.get('has_map') else '')
            if cfa_val == 'B':
                s['pta_access_site'] = 'cfa_calcified_scar'
            elif cfa_val == 'A' and sug.get('has_map') and s.get('pta_access_site') != 'cfa_calcified_scar':
                s['pta_access_site'] = 'cfa_clean'
            if sug.get('has_map'):
                rc = sug.get('runoff_count', 0)
                pop_occ = any(
                    (limb['segments'].get(pk_k) or {}).get('stenosis') == 'occlusion'
                    for pk_k in ('pop1', 'pop2', 'pop3')
                )
                cand_run = 'desert_foot' if rc == 0 else ('tibial_single' if (rc == 1 or pop_occ) else 'popliteal_patent')
                if runoff_rank.get(cand_run, 0) > runoff_rank.get(s.get('pad_runoff_status') or 'pending', 0):
                    s['pad_runoff_status'] = cand_run

    if ulcer_chips:
        q['ulcer_chips'] = [k for k, _ in CHIP_CATALOGS['ulcer_chips'] if k in ulcer_chips]

    clti_map = {
        PreopPatient.OpSubtype.ABF_CLAUD: PreopPatient.OpSubtype.ABF_CLTI,
        PreopPatient.OpSubtype.BYPASS_CLAUD: PreopPatient.OpSubtype.BYPASS_CLTI,
        PreopPatient.OpSubtype.PTA_CLAUD: PreopPatient.OpSubtype.PTA_CLTI,
    }
    if upgrade_clti and patient.op_subtype in clti_map:
        patient.op_subtype = clti_map[patient.op_subtype]
        patient.pathway = PreopPatient.SUBTYPE_PATHWAY[patient.op_subtype]

    if badge_parts:
        s['case_summary_badge'] = ' · '.join(badge_parts)
    patient.questionnaire = q
    patient.studies = s
    patient.save()
    return True


def consent_url_for_preop(patient):
    """Return direct ConsentSheetView URL (with ?preop=<pk>) if a matching form exists, else filtered list."""
    from .models import ConsentForm
    pw = PreopPatient.SUBTYPE_PATHWAY.get(patient.op_subtype, patient.pathway)
    kw_map = {
        'cea': ('კაროტიდ', 'საძილე', 'ენდარტერექტომ'),
        'abf': ('ბიფემორ', 'ლერიშ', 'აორტო'),
        'pad_bypass': ('შუნტირ', 'ბარძაყ', 'პოპლიტ'),
        'pad_pta': ('ანგიოპლასტიკ', 'სტენტირ', 'ბალონ'),
        'venous': ('ლაზერ', 'აბლაც', 'evla', 'ვენ') if patient.op_subtype == 'evla' else ('ფლებექტომ', 'ვარიკოზ', 'ვენ'),
        'aaa_infra': ('evar', 'ენდოპროთეზ', 'ანევრიზმ', 'აორტ') if patient.op_subtype == 'evar' else ('ანევრიზმ', 'აორტ'),
    }
    keywords = kw_map.get(pw, ('აორტ',))
    sheets = list(ConsentForm.objects.order_by('-use_count', 'name')[:100])
    for kw in keywords:
        for f in sheets:
            hay = f'{f.name} {f.blocks[0]["title"] if f.blocks else ""}'.lower()
            if kw in hay:
                return f"{reverse('consent-sheet', args=[f.pk])}?preop={patient.pk}"
    return f"{reverse('consent-list')}?preop={patient.pk}"


def _str_list(val):
    return [x for x in val if isinstance(x, str)] if isinstance(val, (list, tuple, set)) else []


def calc_caprini(patient, q, bmi=None, today=None):
    """Auto-calculate numeric Caprini VTE score (with HIT +3 & acute stroke <1m +5) and safe VTE advice."""
    pts = 0
    age = patient.age or 0
    if age >= 75:
        pts += 3
    elif age >= 61:
        pts += 2
    elif age >= 41:
        pts += 1

    pw = _resolve_pathway(patient)
    if pw == PreopPatient.Pathway.VENOUS:
        pts += 1  # varicose veins
        if q.get('ven_ceap') in ('C3', 'C4', 'C5', 'C6'):
            pts += 1  # leg edema / chronic venous stasis
        pts += 2 if patient.op_subtype == PreopPatient.OpSubtype.PHLEB else 1
    elif pw == PreopPatient.Pathway.PAD_PTA:
        pts += 1
    else:
        pts += 2  # major vascular surgery >45 min

    if bmi is not None and bmi > 25:
        pts += 1
    if q.get('ven_hormones'):
        pts += 1
    if q.get('ven_pregnant'):
        pts += 1
    if q.get('pulm'):
        pts += 1

    s_cur = patient.studies if isinstance(patient.studies, dict) else None
    cea_d = cea_live_days_since_event(q, today=today, s=s_cur)
    has_acute_stroke = (
        pw == PreopPatient.Pathway.CEA
        and cea_d is not None and cea_d <= 30
        and ('minor_stroke' in _str_list(q.get('cea_event_chips')) or bool(q.get('cea_disabling_stroke')))
    )
    if has_acute_stroke:
        pts += 5  # acute stroke <1 month
    elif q.get('neuro'):
        pts += 1

    cad_set = set(_str_list(q.get('cad_chips')))
    if 'hf' in cad_set:
        pts += 1
    if 'mi' in cad_set:
        pts += 1
    if q.get('ven_cancer_immob'):
        pts += 2
    if q.get('ven_dvt_history'):
        pts += 3
    if q.get('ven_thrombophilia'):
        pts += 3

    has_hit = 'hit_heparin' in _str_list(q.get('allergy_chips'))
    if has_hit:
        pts += 3  # HIT is a +3 Caprini hypercoagulable state AND contraindicates all heparins!

    if pts >= 5:
        tier = 'მაღალი (≥5)'
        if has_hit:
            advice = 'არაჰეპარინული პროფილაქტიკა: ფონდაპარინუქსი ან DOAC 7–10 დღე (HIT: LMWH/ჰეპარინი აკრძალულია!) + კომპრესია'
        else:
            advice = 'ფარმაკოლოგიური პროფილაქტიკა (LMWH / DOAC 7–10 დღე) + კომპრესია'
    elif pts >= 3:
        tier = 'საშუალო (3–4)'
        if has_hit:
            advice = 'ადრეული მობილიზაცია + კომპრესია ± ფონდაპარინუქსი/DOAC (HIT: ჰეპარინი აკრძალულია!)'
        else:
            advice = 'ადრეული მობილიზაცია + კომპრესია ± LMWH/DOAC ქირურგის შეფასებით'
    else:
        tier = 'დაბალი (0–2)'
        advice = 'ადრეული სიარული + ელასტიური კომპრესია (რუტინული ჰეპარინის გარეშე)'
    return pts, tier, advice


def build_med_schedule(patient, q, s, egfr, crcl=None):
    """Deterministic perioperative medication hold/continue table (ESVS 2023/2024 + ESC 2022 + ASRA 5th Ed)."""
    pw = _resolve_pathway(patient)
    is_evla = (patient.op_subtype == PreopPatient.OpSubtype.EVLA)
    is_phleb = (patient.op_subtype == PreopPatient.OpSubtype.PHLEB)
    is_pta = (pw == PreopPatient.Pathway.PAD_PTA)
    is_symp_cea = (pw == PreopPatient.Pathway.CEA) and (
        patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP or bool(q.get('cea_symp_event'))
    )
    is_open_major = pw in (
        PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS
    ) and patient.op_subtype != 'evar'
    uses_contrast = (patient.op_subtype in ('evar', 'tbd')) or is_pta

    anesth_plan = s.get('anesthesia_plan') or 'auto'
    has_epidural_line = 'epidural' in _str_list(s.get('anesth_lines_chips'))
    is_neuraxial = (anesth_plan == 'neuraxial') or has_epidural_line or (anesth_plan == 'auto' and is_phleb)
    on_dialysis = bool(q.get('on_dialysis'))
    renal_cl = None if on_dialysis else (crcl if crcl is not None else egfr)
    cl_label = f'CrCl {crcl}' if (crcl is not None and not on_dialysis) else (f'eGFR {egfr}' if egfr is not None else '')
    frail_no_wt_note = ' [≥75 წ. — შეიყვანეთ წონა ზუსტი CrCl-ისთვის!]' if (crcl is None and egfr is not None and (patient.age or 0) >= 75) else ''

    op_dt = _date(s.get('planned_surgery_date'))

    def d_str(delta_days):
        return (op_dt + datetime.timedelta(days=delta_days)).strftime('%d.%m') if op_dt else ''

    def hold_str(hold_days, sfx=''):
        if not op_dt:
            return ''
        return f'ბოლო: {d_str(-hold_days - 1)} · არ მიიღოთ {d_str(-hold_days)}-დან{sfx}'

    def add_row(drug, rule, tone, date_plan=''):
        rows.append({
            'drug': drug,
            'rule': rule,
            'tone': tone,
            'date_plan': date_plan if op_dt else '',
        })

    med_chips = set(_str_list(q.get('med_chips')))
    cad_chips = set(_str_list(q.get('cad_chips')))
    allergy_chips = set(_str_list(q.get('allergy_chips')))
    has_acs_stent_12m = ('stent_6_12m' in cad_chips) and ('mi' in cad_chips)
    bp_sys_cur = _int(s.get('bp_sys'), lo=30, hi=320)
    hr_cur = _int(s.get('hr'), lo=15, hi=260)
    lvef_cur = _int(s.get('lvef'), lo=5, hi=95)
    has_hfref = ('hf' in cad_chips) or (lvef_cur is not None and lvef_cur < 40)
    rows = []

    if q.get('med_aspirin'):
        if is_phleb and not q.get('cad') and not q.get('neuro'):
            add_row('ასპირინი (75–100 მგ)', 'შეთანხმება ქირურგთან (დაბალი კარდიორისკისას შესაძლოა მოიხსნას 5–7 დღით ადრე)', 'amber', f'შეათანხმეთ ({d_str(-5)}-დან?)')
        else:
            add_row('ასპირინი (75–100 მგ)', 'გრძელდება უწყვეტად ოპერაციის დილის ჩათვლით (ESVS Class I)', 'ok', f'მიიღეთ {d_str(0)}-ის დილის ჩათვლით')

    if q.get('med_clopidogrel'):
        if q.get('recent_stent') or has_acs_stent_12m:
            stent_note = 'სტენტი <6 თვ' if q.get('recent_stent') else 'ინფარქტის შემდგომი სტენტი 6–12 თვ (DAPT 12 თვე)'
            add_row('კლოპიდოგრელი / P2Y12', f'{stent_note} — თვითნებური მოხსნა აკრძალულია! კარდიოლოგ-ქირურგის კონსილიუმი', 'red', 'არ მოხსნათ კონსილიუმის გარეშე!')
        elif is_symp_cea:
            add_row('კლოპიდოგრელი / P2Y12', 'გრძელდება უწყვეტად CEA-მდე! (ESVS 2023 Class I/IIa — სიმპტომურ CEA-ზე მოხსნა ზრდის ინსულტის რისკს)', 'ok', f'მიიღეთ {d_str(0)}-ის ჩათვლით')
        elif is_pta:
            add_row('კლოპიდოგრელი / P2Y12', 'გრძელდება უწყვეტად (DAPT რეჟიმი სტენტირებისთვის)', 'ok', f'მიიღეთ {d_str(0)}-ის ჩათვლით')
        elif is_evla and not is_neuraxial:
            add_row('კლოპიდოგრელი / P2Y12', 'ESVS 2022: EVLA შესაძლებელია მოუხსნელადაც — დააზუსტეთ ქირურგთან', 'amber', f'დააზუსტეთ ქირურგთან ({d_str(0)})')
        elif not q.get('med_aspirin') and (pw != PreopPatient.Pathway.VENOUS or q.get('cad') or q.get('neuro')):
            asra_tag = ' (ASRA სპინალურ/ეპიდურულამდე მინ. 5–7 დღე)' if is_neuraxial else ''
            hold_d = 7 if is_neuraxial else 5
            add_row('კლოპიდოგრელი / P2Y12', f'შეჩერება ოპერაციამდე 5–7 დღით ადრე{asra_tag} — სავალდებულოა ჩანაცვლება ასპირინი 75–100 მგ-ით (უანტიაგრეგანტოდ დატოვება საშიშია!)', 'amber', hold_str(hold_d, ' (ასპირინით!)'))
        else:
            asra_tag = ' (ASRA ნეირაქსიალურ ბლოკადამდე მინ. 5–7 დღე)' if is_neuraxial else ''
            hold_d = 7 if is_neuraxial else 5
            asa_sfx = ' (ასპირინის ფონზე)' if q.get('med_aspirin') else ''
            add_row('კლოპიდოგრელი / P2Y12', f'შეჩერება ოპერაციამდე 5–7 დღით ადრე{asra_tag}{asa_sfx}', 'amber', hold_str(hold_d))

    if q.get('med_ticagrelor'):
        if q.get('recent_stent') or has_acs_stent_12m:
            add_row('ტიკაგრელორი (ბრილინტა)', 'კორონარული სტენტი DAPT რეჟიმში — თვითნებური მოხსნა აკრძალულია! კარდიოლოგ-ქირურგის კონსილიუმი', 'red', 'არ მოხსნათ კონსილიუმის გარეშე!')
        elif is_symp_cea:
            add_row('ტიკაგრელორი (ბრილინტა)', 'სიმპტომური CEA — თვითნებურად არ მოხსნათ! შეათანხმეთ ქირურგთან (გაგრძელება ან კლოპიდოგრელ/ასპირინზე გადაყვანა)', 'red', 'არ მოხსნათ შეთანხმების გარეშე!')
        elif is_pta:
            add_row('ტიკაგრელორი (ბრილინტა)', 'გრძელდება ან შეთანხმდება ქირურგთან (ენდოვასკულური DAPT)', 'ok', f'მიიღეთ {d_str(0)}-ის ჩათვლით')
        elif is_neuraxial:
            sub_asa = 'ასპირინის ფონზე!' if q.get('med_aspirin') else 'ასპირინი 75–100 მგ-ით ჩანაცვლებით!'
            add_row('ტიკაგრელორი (ბრილინტა)', f'შეჩერება სპინალურ/ეპიდურულ ანესთეზიამდე 5–7 დღით ადრე (ASRA: 5–7 დღე; ESC/FDA: 5 დღე) — {sub_asa}', 'amber', hold_str(5, ' (5–7 დღე)'))
        else:
            sub_asa = 'ასპირინის ფონზე' if q.get('med_aspirin') else 'ასპირინი 75–100 მგ-ით ჩანაცვლებით!'
            add_row('ტიკაგრელორი (ბრილინტა)', f'შეჩერება ოპერაციამდე 3–5 დღით ადრე (ESC 2022 / FDA; {sub_asa})', 'amber', hold_str(4))

    if q.get('med_prasugrel'):
        stroke_warn = ' [გადატანილ ინსულტ/TIA-ზე პრასუგრელი უკუნაჩვენებია — გადაამოწმეთ!]' if (q.get('neuro') or pw == PreopPatient.Pathway.CEA) else ''
        if q.get('recent_stent') or has_acs_stent_12m:
            add_row('პრასუგრელი (ეფიენტი)', f'კორონარული სტენტი — თვითნებური მოხსნა აკრძალულია! კარდიოლოგ-ქირურგის კონსილიუმი{stroke_warn}', 'red', 'არ მოხსნათ კონსილიუმის გარეშე!')
        elif is_pta and not is_neuraxial and not stroke_warn:
            add_row('პრასუგრელი (ეფიენტი)', 'ენდოვასკულური PTA — შეათანხმეთ ქირურგთან (გაგრძელება ან კლოპიდოგრელზე დეესკალაცია)', 'amber', f'შეათანხმეთ ქირურგთან ({d_str(0)})')
        else:
            hold_d = 10 if is_neuraxial else 7
            sub_asa = 'ასპირინის ფონზე!' if q.get('med_aspirin') else 'ასპირინი 75–100 მგ-ით ჩანაცვლებით!'
            add_row('პრასუგრელი (ეფიენტი)', f'შეჩერება ოპერაციამდე და სპინალურ/ეპიდურულ ანესთეზიამდე 7–10 დღით ადრე (ASRA/ESC) — {sub_asa}{stroke_warn}', 'red', hold_str(hold_d))

    if 'cilostazol' in med_chips:
        hf_warn = ' [გულის უკმარისობისას (HF) ცილოსტაზოლი უკუნაჩვენებია!]' if has_hfref else ''
        add_row('ცილოსტაზოლი (პლეტალი)', f'შეჩერება ოპერაციამდე / სპინალურ ბლოკადამდე 48 საათით (2 დღით) ადრე (ASRA){hf_warn}', 'red' if has_hfref else 'amber', hold_str(2, ' (48 სთ)'))

    if q.get('med_doac'):
        if is_evla and not is_neuraxial:
            add_row('DOAC (ქსარელტო / ელიქვისი)', 'ESVS 2022: EVLA-ზე შესაძლებელია მოუხსნელად ან დილის დოზის გამოტოვებით', 'amber', f'გამოტოვეთ {d_str(0)}-ის დილას')
        elif on_dialysis:
            add_row('DOAC (ქსარელტო / ელიქვისი)', 'დიალიზი / თირკმლის ტერმინალური უკმარისობა — შეჩერება ≥96–120 სთ + ანტი-Xa კონტროლი და ნეფროლოგ/ქირურგის კონსილიუმი!', 'red', hold_str(5, ' (კონსილიუმი!)'))
        elif renal_cl is None:
            add_row('DOAC (ქსარელტო / ელიქვისი)', 'შეიყვანეთ კრეატინინი (CrCl / eGFR) — DOAC-ის შეჩერების დრო (48–96+ სთ) დამოკიდებულია თირკმლის ფუნქციაზე!', 'red', 'შეიყვანეთ კრეატინინი თარიღისთვის')
        elif renal_cl < 30:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'{cl_label} (<30) — შეჩერება ≥96 სთ-ით ადრე + ნეფროლოგ/ქირურგთან შეთანხმება (ნეირაქსიალური ბლოკადა მოერიდეთ!){frail_no_wt_note}', 'red', hold_str(4, ' (≥96 სთ)'))
        elif is_neuraxial:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'სპინალური/ეპიდურული ანესთეზია (ASRA 5th Ed): სავალდებულო შეჩერება მინიმუმ 72 საათით (3 დღით) ადრე! ({cl_label}){frail_no_wt_note}', 'amber', hold_str(3, ' (72 სთ)'))
        elif renal_cl < 50:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'{cl_label} (30–49) — შეჩერება ოპერაციამდე 72 საათით (3 დღით) ადრე (EHRA 2021 მინ. ≥48 სთ; რენალური/ASRA ბუფერი: 72 სთ){frail_no_wt_note}', 'amber', hold_str(3, ' (72 სთ)'))
        else:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'შეჩერება ოპერაციამდე 48 საათით (2 დღით) ადრე (EHRA 2021 / PAUSE; {cl_label}){frail_no_wt_note}', 'amber', hold_str(2, ' (48 სთ)'))

    if q.get('med_dabigatran'):
        if is_evla and not is_neuraxial:
            add_row('დაბიგატრანი (პრადაქსა)', 'ESVS 2022: EVLA-ზე შესაძლებელია მოუხსნელად ან დილის დოზის გამოტოვებით — დააზუსტეთ ქირურგთან', 'amber', f'გამოტოვეთ {d_str(0)}-ის დილას')
        elif on_dialysis or renal_cl is None:
            msg = (
                'დიალიზი — დაბიგატრანი უკუნაჩვენებია, სავალდებულოა თრომბინის დროის (TT) კონტროლი და კონსილიუმი!'
                if on_dialysis else
                'შეიყვანეთ კრეატინინი და წონა (CrCl) — პრადაქსას შეჩერების დრო (48–120 სთ) მკაცრად დამოკიდებულია თირკმლის ფუნქციაზე!'
            )
            add_row('დაბიგატრანი (პრადაქსა)', msg, 'red', 'შეიყვანეთ CrCl თარიღისთვის')
        elif is_neuraxial:
            if renal_cl < 30:
                add_row('დაბიგატრანი (პრადაქსა)', f'{cl_label} (<30) — სპინალური/ეპიდურული ბლოკადა უკუნაჩვენებია (ASRA 5th Ed)! შეჩერება ≥120 სთ{frail_no_wt_note}', 'red', hold_str(5, ' (სპინალური არა!)'))
            elif renal_cl < 50:
                add_row('დაბიგატრანი (პრადაქსა)', f'სპინალური/ეპიდურული (ASRA, {cl_label} 30–49): შეჩერება 120 საათით (5 დღით) ადრე!{frail_no_wt_note}', 'red', hold_str(5, ' (120 სთ)'))
            elif renal_cl < 80:
                add_row('დაბიგატრანი (პრადაქსა)', f'სპინალური/ეპიდურული (ASRA, {cl_label} 50–79): შეჩერება 96 საათით (4 დღით) ადრე!{frail_no_wt_note}', 'amber', hold_str(4, ' (96 სთ)'))
            else:
                add_row('დაბიგატრანი (პრადაქსა)', f'სპინალური/ეპიდურული (ASRA, {cl_label} ≥80): შეჩერება 72 საათით (3 დღით) ადრე!{frail_no_wt_note}', 'amber', hold_str(3, ' (72 სთ)'))
        else:
            if renal_cl < 30:
                add_row('დაბიგატრანი (პრადაქსა)', f'{cl_label} (<30) — შეჩერება ≥120 საათით (5 დღით) ადრე + კოაგულოგრამა/TT კონტროლი!{frail_no_wt_note}', 'red', hold_str(5, ' (120 სთ)'))
            elif renal_cl < 50:
                add_row('დაბიგატრანი (პრადაქსა)', f'{cl_label} (30–49) — შეჩერება ოპერაციამდე 96 საათით (4 დღით) ადრე{frail_no_wt_note}', 'red', hold_str(4, ' (96 სთ)'))
            elif renal_cl < 80:
                add_row('დაბიგატრანი (პრადაქსა)', f'{cl_label} (50–79) — შეჩერება ოპერაციამდე 72 საათით (3 დღით) ადრე{frail_no_wt_note}', 'amber', hold_str(3, ' (72 სთ)'))
            else:
                add_row('დაბიგატრანი (პრადაქსა)', f'{cl_label} (≥80) — შეჩერება ოპერაციამდე 48 საათით (2 დღით) ადრე{frail_no_wt_note}', 'amber', hold_str(2, ' (48 სთ)'))

    if 'rivaroxaban_2_5' in med_chips and not q.get('med_doac'):
        if on_dialysis or (renal_cl is not None and renal_cl < 30):
            add_row('ქსარელტო 2.5 მგ x2', 'თირკმლის მძიმე უკმარისობა / დიალიზი — შეჩერება ≥72–96 სთ-ით ადრე და სქემის გადახედვა ქირურგთან!', 'red', hold_str(4))
        elif is_evla and not is_neuraxial:
            add_row('ქსარელტო 2.5 მგ x2', 'EVLA-ზე შესაძლებელია დილის დოზის გამოტოვებით (ასპირინი გრძელდება)', 'ok', f'გამოტოვეთ {d_str(0)}-ის დილას')
        elif is_neuraxial:
            add_row('ქსარელტო 2.5 მგ x2', 'შეჩერება სპინალურ/ეპიდურულ ბლოკადამდე 48–72 საათით ადრე (ASRA) — ასპირინი გრძელდება', 'amber', hold_str(3))
        else:
            add_row('ქსარელტო 2.5 მგ x2', 'შეჩერება ოპერაციამდე 24–48 საათით ადრე (ასპირინი 75–100 მგ გრძელდება უწყვეტად)', 'amber', hold_str(2))

    if q.get('med_warfarin'):
        if is_evla and not is_neuraxial:
            add_row('ვარფარინი', 'ESVS 2022: EVLA შესაძლებელია მოუხსნელად, თუ INR ≤ 3.0', 'amber', f'INR ≤3.0 შემოწმება {d_str(-1)}')
        else:
            inr_target = 'INR ≤ 1.4 (ASRA ნეირაქსიული ბლოკადისთვის; სასურველია ნორმალიზება)' if is_neuraxial else 'INR < 1.5 (ქირურგიული ჰემოსტაზისთვის)'
            if 'hit_heparin' in allergy_chips:
                bridge_txt = 'HIT ანამნეზში — LMWH/ჰეპარინის ხიდი კატეგორიულად აკრძალულია!'
            elif 'mech_valve' in cad_chips or (q.get('ven_dvt_history') and q.get('ven_thrombophilia')):
                bridge_txt = 'მექანიკური სარქველი / მაღალი თრომბოემბოლიური რისკი — საჭიროა LMWH ხიდი (თერაპიული LMWH ჩერდება ოპერაციამდე 24 სთ-ით ადრე!)'
            else:
                bridge_txt = 'რუტინული LMWH ხიდი AFib-ზე არ ტარდება (ESC 2022 Class III); თუ დაინიშნა პროფილაქტიკური LMWH, ჩერდება -12 სთ, თერაპიული -24 სთ'
            add_row('ვარფარინი', f'შეჩერება 5 დღით ადრე + წინა დღეს საკონტროლო {inr_target}. {bridge_txt}', 'red', f'{hold_str(5)} · INR კონტროლი {d_str(-1)}')

    if q.get('dm_insulin'):
        add_row(
            'ინსულინი',
            'ოპერაციის წინა საღამოს/დილას ბაზალური (ხანგრძლივი) ინსულინი მცირდება 75–80%-მდე; სწრაფი (საკვებისწინა) ინსულინი უზმოზე გამოტოვეთ + დილის გლუკოზის კონტროლი!',
            'amber',
            f'{d_str(0)} დილას: სწრაფი არა · ბაზალური 75–80%',
        )

    if q.get('sglt2'):
        add_row(
            'SGLT2 (ჯარდიანსი / ფორსიგა)',
            'სავალდებულო შეჩერება ოპერაციამდე 72 საათით (3 დღით) ადრე (ერტუგლიფლოზინი/სტეგლატრო — 96 სთ / 4 დღით ადრე; ეუგლიკემიური კეტოაციდოზის პრევენცია)!',
            'amber',
            hold_str(3, ' (72 სთ)'),
        )

    if q.get('glp1'):
        add_row(
            'GLP-1 (ოზემპიკი / მუნჯარო / საქსენდა)',
            'Multi-Society 2024 (ASA/AGA/ESAIC): გულისრევა/ღებინებისა და დოზის მატების ფაზის გარეშე უმეტეს პაციენტთან გრძელდება + სავალდებულოა ოპერაციამდე 24 სთ სუფთა თხევადი დიეტა! შეჩერება (კვირეული 7 დღით / დღიური ოპერაციის დღეს) — მხოლოდ ექიმის/ანესთეზიოლოგის გადაწყვეტილებით მაღალი GI რისკისას.',
            'amber',
            f'{d_str(-1)} თხევადი დიეტა (24 სთ) · შეჩერება ({d_str(-7)}-დან) მხოლოდ ექიმით',
        )

    if q.get('metformin'):
        if on_dialysis or (egfr is not None and egfr < 30):
            add_row('მეტფორმინი', 'eGFR < 30 / დიალიზი — მეტფორმინი უკუნაჩვენებია (ლაქტატ-აციდოზის რისკი)! შეწყვიტეთ დაუყოვნებლივ და შეათანხმეთ ენდოკრინოლოგთან!', 'red', 'შეწყვიტეთ დაუყოვნებლივ!')
        elif uses_contrast or (egfr is not None and egfr < 60) or is_open_major:
            add_row('მეტფორმინი', 'შეჩერება ჩარევის/კონტრასტის დღეს (48 სთ-ით, თირკმლის საკონტროლო კრეატინინამდე)', 'amber', f'არ მიიღოთ {d_str(0)}–{d_str(1)} (48 სთ)')
        else:
            add_row('მეტფორმინი', 'გამოტოვება ოპერაციის დილას (უზმოზე)', 'ok', f'გამოტოვეთ {d_str(0)}-ის დილას')

    if 'statin' in med_chips:
        add_row('სტატინი', 'გრძელდება უწყვეტად ოპერაციის დილის ჩათვლით (ESVS/ESC Class I)', 'ok', f'მიიღეთ {d_str(0)}-ის დილის ჩათვლით')
    if 'beta_blocker' in med_chips:
        bb_hold_vitals = (bp_sys_cur is not None and bp_sys_cur < 100) or (hr_cur is not None and hr_cur < 55)
        add_row(
            'ბეტა-ბლოკატორი',
            'გრძელდება უწყვეტად ოპერაციის დილას ყლუპი წყლით, თუ SBP ≥ 100 mmHg და HR ≥ 55/წთ (ESC Class I / POISE-3)',
            'amber' if bb_hold_vitals else 'ok',
            f'მიიღეთ {d_str(0)}-ის დილას (თუ SBP≥100, HR≥55)',
        )
    if 'acei_arb' in med_chips:
        if has_hfref:
            add_row('ACEi / ARB (პრილი/სარტანი)', 'გულის უკმარისობის (HFrEF) გამო გრძელდება ანესთეზიოლოგთან შეთანხმებით (თუ SBP ≥ 100 mmHg — ESC 2022)', 'amber', f'შეათანხმეთ HFrEF-ზე ({d_str(0)})')
        elif is_open_major or is_neuraxial or pw == PreopPatient.Pathway.CEA or anesth_plan == 'general' or patient.op_subtype in ('evar', 'tbd'):
            add_row('ACEi / ARB (პრილი/სარტანი)', 'გამოტოვება ოპერაციის დილას (ინტრაოპერაციული ჰიპოტენზიის პრევენცია — POISE-3)', 'amber', f'გამოტოვეთ {d_str(0)}-ის დილას')
        else:
            add_row('ACEi / ARB (პრილი/სარტანი)', 'გრძელდება ჩვეულ რეჟიმში (თუ წნევა სტაბილურია)', 'ok', f'მიიღეთ ჩვეულ რეჟიმში ({d_str(0)})')
    if 'ccb' in med_chips:
        add_row('ამლოდიპინი / CCB', 'გრძელდება ჩვეულ რეჟიმში ოპერაციის დილას, თუ SBP ≥ 110 mmHg', 'ok', f'მიიღეთ {d_str(0)}-ის დილას (SBP≥110)')
    if 'amiodarone' in med_chips:
        add_row('ამიოდარონი', 'გრძელდება უწყვეტად ანტიარითმიული სქემით ოპერაციის დილის ჩათვლით', 'ok', f'მიიღეთ {d_str(0)}-ის დილას')
    if 'diuretic' in med_chips:
        add_row('დიურეტიკი', 'გამოტოვება ოპერაციის დილას', 'amber', f'გამოტოვეთ {d_str(0)}-ის დილას')
    if q.get('ven_hormones'):
        horm_rule = (
            'EVLA-ზე შესაძლებელია გაგრძელება VTE რისკის შეფასებით (Caprini); ფლებექტომიაზე/მაღალ რისკზე შეათანხმეთ თრომბოპროფილაქტიკა (ESVS 2022)'
            if is_evla else
            'ესტროგენული/ჰორმონული პრეპარატი ზრდის VTE რისკს — შეათანხმეთ ქირურგთან (4 კვ-ით ადრე მოხსნა ან LMWH პროფილაქტიკა)'
        )
        add_row('ჰორმონული პრეპარატი / კონტრაცეპტივი', horm_rule, 'amber', f'VTE შეფასება ({d_str(0)})')
    if 'ppi' in med_chips or q.get('ulcer') or s.get('ppi_started'):
        add_row('ომეპრაზოლი / პანტოპრაზოლი (PPI)', 'გრძელდება / ინიშნება უწყვეტად პერიოპერაციულად კუჭის დაცვისთვის (ESC Class I)', 'ok', f'მიიღეთ {d_str(0)}-ის დილას')
    if 'steroid' in med_chips:
        add_row('სტეროიდი / იმუნოსუპრესანტი', 'თვითნებურად არ მოხსნათ — შეათანხმეთ სტრეს-დოზა ანესთეზიოლოგთან', 'amber', f'არ შეწყვიტოთ ({d_str(0)} სტრეს-დოზა)')
    return rows


def calc_auto_asa(patient, q, s, egfr=None, bmi=None, today=None):
    """Estimate ASA Physical Status ('I'..'IV') deterministically from vascular pathway, comorbidities & acute vitals/labs."""
    pw = _resolve_pathway(patient)
    aaa_mm = _num(s.get('aaa_mm'), lo=10, hi=200)
    lvef = _int(s.get('lvef'), lo=5, hi=95)
    fev1 = _int(s.get('spiro_fev1'), lo=10, hi=160)
    hb_gl = normalize_hb_gl(s.get('hb'))
    hba1c = _num(s.get('hba1c'), lo=3.0, hi=25.0)
    spo2 = _int(s.get('spo2'), lo=30, hi=100)
    bp_sys = _int(s.get('bp_sys'), lo=30, hi=320)
    bp_sys_l = _int(s.get('bp_sys_l'), lo=30, hi=320)
    eff_sbp = max([v for v in (bp_sys, bp_sys_l) if v is not None], default=None)
    k_val = _num(s.get('k'), lo=1.0, hi=12.0)
    nt_probnp = _int(s.get('nt_probnp'), lo=1, hi=50000)
    hs_ctn = _num(s.get('hs_ctn'), lo=0.1, hi=50000)
    cea_d = cea_live_days_since_event(q, today=today, s=s)
    is_symp_cea = (pw == PreopPatient.Pathway.CEA) and (
        patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP or bool(q.get('cea_symp_event'))
    )

    if (
        bool(q.get('aaa_pain'))
        or (aaa_mm is not None and aaa_mm >= 70)
        or (s.get('aaa_morphology') == 'inflammatory_mycotic')
        or bool(q.get('cea_crescendo'))
        or (is_symp_cea and (cea_d is None or cea_d <= 90))
        or bool(q.get('recent_stent'))
        or (lvef is not None and lvef < 40)
        or bool(s.get('echo_valve_issue'))
        or (s.get('ecg') == 'ischemia_arrhythmia')
        or bool(q.get('abf_wet_infection'))
        or (fev1 is not None and fev1 < 50)
        or (spo2 is not None and spo2 < 90)
        or (eff_sbp is not None and eff_sbp < 90)
        or (k_val is not None and (k_val >= 6.0 or k_val < 3.0))
        or (hb_gl is not None and hb_gl < 70)
        or (nt_probnp is not None and nt_probnp >= 900)
        or (hs_ctn is not None and hs_ctn >= 52)
    ):
        return 'IV'

    if (
        pw != PreopPatient.Pathway.VENOUS
        or bool(q.get('on_dialysis'))
        or (egfr is not None and egfr < 60)
        or bool(q.get('cad'))
        or bool(q.get('neuro'))
        or (s.get('ecg') == 'afib_controlled')
        or bool(q.get('ven_dvt_history'))
        or bool(q.get('ven_cancer_immob'))
        or (s.get('liver_status') == '3x')
        or (hb_gl is not None and hb_gl < 100)
        or (hba1c is not None and hba1c >= 8.5)
        or bool(q.get('dm_insulin'))
        or (bmi is not None and bmi >= 40)
        or bool(q.get('pacemaker'))
        or ('copd' in _str_list(q.get('pulm_chips')))
        or (spo2 is not None and spo2 < 94)
        or (eff_sbp is not None and eff_sbp >= 180)
        or (k_val is not None and (k_val >= 5.4 or k_val < 3.3))
    ):
        return 'III'

    if (
        bool(q.get('htn'))
        or bool(q.get('dm'))
        or bool(q.get('smoker'))
        or bool(q.get('pulm'))
        or bool(q.get('thyroid_disease'))
        or bool(q.get('ven_pregnant'))
        or (bmi is not None and bmi >= 30)
    ):
        return 'II'
    return 'I'


def build_surveillance_plan(patient):
    """Return pathway-specific post-operative Duplex/CTA surveillance schedule (ESVS 2022–2024)."""
    pw = _resolve_pathway(patient)
    if pw == PreopPatient.Pathway.AAA_INFRA:
        if patient.op_subtype == 'evar':
            return 'ESVS 2024 EVAR მეთვალყურეობა: კტ-ანგიოგრაფია (CTA) + დუპლექსი 30 დღეში (ენდოლიკის გამორიცხვა), შემდეგ 1 წელიწადში და ყოველწლიურად.'
        return 'ESVS 2024 ღია AAA მეთვალყურეობა: კლინიკური და დუპლექს-შეფასება 6–12 თვეში, შემდეგ აორტა-თეძოს CTA/დუპლექსი ყოველ 5 წელიწადში.'
    if pw == PreopPatient.Pathway.CEA:
        return 'ESVS 2023 CEA მეთვალყურეობა: საძილე არტერიების დუპლექს-სკანირება 1–3 თვეში, 6–12 თვეში და შემდეგ წელიწადში ერთხელ + BP და LDL (<1.4 მმოლ/ლ) კონტროლი.'
    if pw == PreopPatient.Pathway.ABF:
        return 'ESVS 2024 ABF მეთვალყურეობა: კლინიკური შეფასება, ABI და ბარძაყის ანასტომოზების დუპლექსი 1, 6 და 12 თვეში, შემდეგ წელიწადში ერთხელ.'
    if pw == PreopPatient.Pathway.PAD_BYPASS:
        return 'ESVS 2024 შუნტირების მეთვალყურეობა: შუნტის დუპლექს-სკანირება (PSV / რესტენოზი) + ABI/TBI 1, 3, 6 და 12 თვეში, შემდეგ წელიწადში ერთხელ.'
    if pw == PreopPatient.Pathway.PAD_PTA:
        return 'ESVS 2024 PTA/სტენტირების მეთვალყურეობა: კლინიკური შეფასება + ABI/TBI და დუპლექსი 1 თვეში და 12 თვეში (CLTI-ისას — წყლულის შეხორცებამდე).'
    if patient.op_subtype == PreopPatient.OpSubtype.EVLA:
        return 'ESVS 2022 EVLA მეთვალყურეობა: საკონტროლო ვენური დუპლექს-სკანირება 1–4 კვირაში (EHIT-ის გამორიცხვა და ღეროს ოკლუზიის დადასტურება).'
    return 'ESVS 2022 ფლებექტომიის მეთვალყურეობა: კლინიკური და დუპლექს-კონტროლი 4–6 კვირაში.'


# --------------------------------------------------------------------------
# Deterministic Clinical Protocol Engine (AAA / CEA / ABF / VENOUS)
# --------------------------------------------------------------------------

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
}


def _sanitize_merged_dict(defaults, raw_dict):
    """Merge JSON payload against typed defaults, stripping NaN/Inf, booleans-as-numbers, and corrupt non-iterables."""
    data = {}
    src = raw_dict if isinstance(raw_dict, dict) else {}
    for k, dv in defaults.items():
        if k not in src:
            data[k] = list(dv) if isinstance(dv, list) else dv
            continue
        rv = src[k]
        if isinstance(dv, list):
            data[k] = [x for x in rv if isinstance(x, str)] if isinstance(rv, (list, tuple, set)) else []
        elif isinstance(dv, bool):
            data[k] = bool(rv)
        elif isinstance(dv, str):
            data[k] = rv if isinstance(rv, str) else ''
        elif isinstance(dv, int):
            iv = _int(rv, lo=0, hi=36500)
            data[k] = iv if iv is not None else dv
        else:
            if isinstance(rv, (int, float)) and not isinstance(rv, bool):
                data[k] = (0.0 if (isinstance(rv, float) and rv == 0) else rv) if math.isfinite(rv) else None
            else:
                data[k] = _num(rv)
    for k, rv in src.items():
        if k not in data:
            if isinstance(rv, float) and not math.isfinite(rv):
                continue
            data[k] = rv
    return data


def merged_q(patient):
    return _sanitize_merged_dict(DEFAULT_Q, patient.questionnaire)


def merged_studies(patient):
    return _sanitize_merged_dict(DEFAULT_STUDIES, patient.studies)


def _update_hold_pause(s, old_someday, new_someday, today=None):
    """Track paused elapsed days when entering/exiting SomedayList.HOLD so medically deferred time isn't added back."""
    today = today or timezone.localdate()
    hold_val = PreopPatient.SomedayList.HOLD
    if old_someday != hold_val and new_someday == hold_val:
        if not s.get('hold_since'):
            s['hold_since'] = today.isoformat()
    elif old_someday == hold_val and new_someday != hold_val:
        hs = _date(s.get('hold_since'))
        if hs and hs <= today:
            s['hold_paused_days'] = (_int(s.get('hold_paused_days'), lo=0, hi=3650) or 0) + max(0, (today - hs).days)
        s['hold_since'] = ''


def _ihd_and_chf_flags(q, s):
    """Distinguish true ischemic heart disease (IHD) and heart failure (CHF) from lone AFib/valvular disease."""
    q_filled = bool(q.get('filled'))
    cad_chips = set(_str_list(q.get('cad_chips')))
    ecg = s.get('ecg') or 'pending'
    lvef = _int(s.get('lvef'), lo=5, hi=95)
    nt_probnp = _int(s.get('nt_probnp'), lo=1, hi=50000)
    ihd = bool(
        (q_filled and q.get('recent_stent'))
        or bool(cad_chips & {'mi', 'angina', 'cabg', 'stent_old', 'stent_6_12m'})
        or ecg == 'ischemia_arrhythmia'
        or (q_filled and bool(q.get('cad')) and not cad_chips)
    )
    chf = bool(
        ('hf' in cad_chips)
        or (lvef is not None and lvef < 40)
        or (nt_probnp is not None and nt_probnp >= 300)
    )
    return ihd, chf


def calc_rcri(patient, q, s, is_symp_cea=False):
    """Validated 6-factor Revised Cardiac Risk Index (Lee et al., Circulation 1999 / ESC 2022)."""
    pathway = _resolve_pathway(patient)
    q_filled = bool(q.get('filled'))
    ihd, chf = _ihd_and_chf_flags(q, s)
    scr_mgdl, _ = _parse_scr_mgdl(s.get('creat'), s.get('creat_unit') or '')

    factors = []
    # Original Lee 1999 high-risk surgery factor: strictly intraperitoneal, intrathoracic,
    # or suprainguinal vascular surgery (open/TBD AAA or ABF — NOT infrainguinal bypass or CEA).
    is_suprainguinal_vasc_op = (
        pathway == PreopPatient.Pathway.ABF
        or (pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype != 'evar')
    )
    if is_suprainguinal_vasc_op:
        factors.append('მაღალი რისკის სუპრაინგვინალური სისხლძარღვოვანი ოპერაცია (Lee 1999)')
    if ihd:
        factors.append('გულის იშემიური დაავადება (IHD)')
    if chf:
        factors.append('გულის უკმარისობა (CHF / LVEF <40% / BNP ≥300)')
    has_prior_cvd = (
        (q_filled and (bool(q.get('neuro')) or bool(q.get('cea_symp_event'))))
        or is_symp_cea
        or (pathway == PreopPatient.Pathway.CEA and patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP)
        or bool(s.get('carotid_symp'))
    )
    if has_prior_cvd:
        factors.append('ინსულტი / TIA ანამნეზში')
    if q_filled and bool(q.get('dm_insulin')):
        factors.append('ინსულინოთერაპია')
    if (q_filled and bool(q.get('on_dialysis'))) or (scr_mgdl is not None and scr_mgdl > 2.0):
        factors.append('კრეატინინი >2.0 მგ/დლ (>177 მკმოლ/ლ) / დიალიზი')

    score = len(factors)
    if score == 0:
        mace_pct, tier, tier_label = '0.4%', 'low', 'დაბალი (~0.4% MACE)'
    elif score == 1:
        mace_pct, tier, tier_label = '0.9%', 'low', 'დაბალი (~0.9% MACE)'
    elif score == 2:
        mace_pct, tier, tier_label = '6.6%', 'intermediate', 'საშუალო (~6.6% MACE)'
    else:
        mace_pct, tier, tier_label = '≥11.0%', 'high', 'მაღალი (≥11.0% MACE)'
    return {
        'score': score,
        'mace_pct': mace_pct,
        'tier': tier,
        'tier_label': tier_label,
        'factors': factors,
    }


def calc_vsg_cri(patient, q, s):
    """Vascular Study Group of New England Cardiac Risk Index (Bertges et al., J Vasc Surg 2010 — exact published MACE table)."""
    pathway = _resolve_pathway(patient)
    if pathway == PreopPatient.Pathway.VENOUS:
        return None
    q_filled = bool(q.get('filled'))
    cad_chips = set(_str_list(q.get('cad_chips')))
    pulm_chips = set(_str_list(q.get('pulm_chips')))
    med_chips = set(_str_list(q.get('med_chips')))
    ihd, chf = _ihd_and_chf_flags(q, s)
    scr_mgdl, _ = _parse_scr_mgdl(s.get('creat'), s.get('creat_unit') or '')
    spiro_fev1 = _int(s.get('spiro_fev1'), lo=10, hi=160)

    pts = 0
    factors = []
    age = patient.age or 0
    if age >= 80:
        pts += 4
        factors.append('ასაკი ≥80 (+4)')
    elif age >= 70:
        pts += 3
        factors.append('ასაკი 70–79 (+3)')
    elif age >= 60:
        pts += 2
        factors.append('ასაკი 60–69 (+2)')

    if q_filled and q.get('smoker'):
        pts += 1
        factors.append('თამბაქო (+1)')
    if q_filled and q.get('dm_insulin'):
        pts += 1
        factors.append('ინსულინი (+1)')
    if ihd:
        pts += 2
        factors.append('გიდ / CAD (+2)')
    if chf:
        pts += 2
        factors.append('გულის უკმარისობა (+2)')
    has_copd = (
        ('copd' in pulm_chips)
        or (q_filled and bool(q.get('pulm')) and not pulm_chips)
        or (s.get('cxr') == 'emphysema')
        or (spiro_fev1 is not None and spiro_fev1 < 70)
    )
    if has_copd:
        pts += 2
        factors.append('ფქოდ / COPD (+2)')
    if (q_filled and bool(q.get('on_dialysis'))) or (scr_mgdl is not None and scr_mgdl >= 1.8):
        pts += 2
        factors.append('კრეატინინი ≥1.8 მგ/დლ / დიალიზი (+2)')
    if 'beta_blocker' in med_chips:
        pts += 1
        factors.append('მუდმივი β-ბლოკატორი (+1)')
    if (('cabg' in cad_chips) or ('stent_old' in cad_chips)) and not (q_filled and q.get('recent_stent')):
        pts = max(0, pts - 1)
        factors.append('წარსულში რევასკულარიზაცია CABG/PCI (-1)')

    # Exact published Bertges et al. 2010 (J Vasc Surg) 6-category MACE probabilities:
    # 0-3: 2.6%, 4: 3.5%, 5: 6.0%, 6: 6.6%, 7: 8.9%, >=8: 14.3%
    if pts <= 3:
        tier, est, lbl = 'low', '2.6%', 'დაბალი (2.6%)'
    elif pts == 4:
        tier, est, lbl = 'low', '3.5%', 'დაბალი/საშუალო (3.5%)'
    elif pts == 5:
        tier, est, lbl = 'intermediate', '6.0%', 'საშუალო (6.0%)'
    elif pts == 6:
        tier, est, lbl = 'intermediate', '6.6%', 'საშუალო (6.6%)'
    elif pts == 7:
        tier, est, lbl = 'high', '8.9%', 'მაღალი (8.9%)'
    else:
        tier, est, lbl = 'high', '14.3%', 'ძალიან მაღალი (14.3%)'
    return {
        'score': pts,
        'tier': tier,
        'risk_pct': est,
        'tier_label': lbl,
        'factors': factors,
    }


def calc_ariscat_pulm(patient, q, s, hb_gl=None):
    """Validated ARISCAT postoperative pulmonary complication (PPC) score (Canet et al. 2010 / ESAIC 2024)."""
    pathway = _resolve_pathway(patient)
    q_filled = bool(q.get('filled'))
    inf_chips = set(_str_list(q.get('inf_chips')))
    spo2 = _int(s.get('spo2'), lo=30, hi=100)
    open_aortic = (
        (pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype != 'evar')
        or pathway == PreopPatient.Pathway.ABF
    )

    pts = 0
    factors = []
    age = patient.age or 0
    if age > 80:
        pts += 16
        factors.append('ასაკი >80 (+16)')
    elif age >= 51:
        pts += 3
        factors.append('ასაკი 51–80 (+3)')

    if spo2 is not None:
        if spo2 <= 90:
            pts += 24
            factors.append(f'SpO2 {spo2}% ≤90 (+24)')
        elif spo2 <= 95:
            pts += 8
            factors.append(f'SpO2 {spo2}% 91–95 (+8)')

    if (q_filled and 'fever_resp' in inf_chips) or (s.get('cxr') == 'infiltrate'):
        pts += 17
        factors.append('რესპირატორული ინფექცია / ინფილტრაცია (+17)')

    if hb_gl is not None and hb_gl <= 100:
        pts += 11
        factors.append('ანემია Hb ≤100 გ/ლ (+11)')

    if open_aortic:
        pts += 15
        factors.append('ზედა აბდომინალური / აორტული განაკვეთი (+15)')

    dur_est = s.get('surg_duration_est') or 'auto'
    if dur_est not in ('auto', 'lt_2h', '2_3h', 'gt_3h'):
        dur_est = 'auto'
    if dur_est == 'auto':
        if open_aortic:
            dur_eff, dur_auto = 'gt_3h', True
        elif pathway in (PreopPatient.Pathway.CEA, PreopPatient.Pathway.PAD_BYPASS):
            dur_eff, dur_auto = '2_3h', True
        else:
            dur_eff, dur_auto = 'lt_2h', True
    else:
        dur_eff, dur_auto = dur_est, False

    auto_sfx = ' — ავტო' if dur_auto else ''
    if dur_eff == 'gt_3h':
        pts += 23
        factors.append(f'ოპერაციის ხანგრძლივობა >3 სთ{auto_sfx} (+23)')
    elif dur_eff == '2_3h':
        pts += 16
        factors.append(f'ოპერაციის ხანგრძლივობა 2–3 სთ{auto_sfx} (+16)')

    if s.get('asa_emergency') or (q_filled and (q.get('aaa_pain') or q.get('cea_crescendo'))):
        pts += 8
        factors.append('სასწრაფო/გადაუდებელი ჩვენება (+8)')

    if pts < 26:
        tier, ppc_pct, lbl = 'low', '1.6%', 'დაბალი (~1.6% PPC)'
    elif pts <= 44:
        tier, ppc_pct, lbl = 'intermediate', '13.3%', 'საშუალო (~13.3% PPC)'
    else:
        tier, ppc_pct, lbl = 'high', '42.1%', 'მაღალი (~42.1% PPC)'
    return {
        'score': pts,
        'tier': tier,
        'ppc_pct': ppc_pct,
        'tier_label': lbl,
        'factors': factors,
    }


def build_self_fill_highlights(q):
    """Extract concise positive findings reported by the patient via Self-Fill link for 5-second nurse reconciliation."""
    if not q.get('filled'):
        return []
    items = []
    if q.get('aaa_pain'):
        items.append(('urgent', 'მუცლის/წელის ტკივილი (AAA)!'))
    if q.get('cea_crescendo'):
        items.append(('urgent', 'განმეორებითი TIA/სიმპტომი <48 სთ!'))
    elif q.get('cea_symp_event'):
        items.append(('warn', 'ნევროლოგიური სიმპტომი ანამნეზში'))
    if q.get('cea_thrombolysis'):
        items.append(('urgent', 'თრომბოლიზისი ≤6 დღეში!'))
    if q.get('abf_rest_pain'):
        items.append(('warn', 'მოსვენებითი იშემიური ტკივილი'))
    if q.get('abf_ulcer'):
        items.append(('warn', 'ტერფის წყლული/ნეკროზი'))
    if q.get('ven_acute_svt'):
        items.append(('urgent', 'მწვავე თრომბოფლებიტი!'))
    if q.get('ven_pregnant'):
        items.append(('urgent', 'ორსულობა / ლაქტაცია!'))
    if q.get('ven_dvt_history'):
        items.append(('warn', 'გადატანილი DVT / PE'))
    if q.get('active_infection'):
        items.append(('urgent', 'სიცხე / აქტიური ინფექცია!'))
    if q.get('recent_stent'):
        items.append(('urgent', 'კორონარული სტენტი <6 თვეში!'))
    elif q.get('cad'):
        items.append(('warn', 'გულის დაავადება / სტენტი / არითმია'))
    if not q.get('mets_ok', True):
        items.append(('warn', f"დატვირთვის შეზღუდვა ({'კიდურით' if q.get('mets_limitation') == 'limb' else '<4 METs ქოშინი/გული'})"))
    if q.get('on_dialysis'):
        items.append(('urgent', 'პროგრამული დიალიზი'))
    if q.get('dm'):
        items.append(('info', 'შაქრიანი დიაბეტი' + (' (ინსულინზე)' if q.get('dm_insulin') else '')))
    elif q.get('dm_insulin'):
        items.append(('warn', 'ინსულინი'))
    if q.get('htn'):
        items.append(('info', 'ჰიპერტენზია'))
    if q.get('pulm'):
        items.append(('info', 'ფილტვების დაავადება / ასთმა'))
    if q.get('thyroid_disease'):
        items.append(('info', 'ფარისებრი ჯირკვალი'))
    if q.get('neuro'):
        items.append(('warn', 'გადატანილი ინსულტი'))
    if q.get('ulcer_active_symp'):
        items.append(('urgent', 'აქტიური კუჭის ტკივილი / მელენა!'))
    elif q.get('ulcer'):
        items.append(('info', 'კუჭის წყლული ანამნეზში'))
    if q.get('pacemaker'):
        items.append(('warn', 'კარდიოსტიმულატორი / ICD'))

    meds = []
    if q.get('med_warfarin'):
        meds.append('ვარფარინი')
    if q.get('med_dabigatran'):
        meds.append('პრადაქსა')
    if q.get('med_doac'):
        meds.append('ქსარელტო/ელიქვისი')
    if q.get('med_prasugrel'):
        meds.append('პრასუგრელი')
    if q.get('med_ticagrelor'):
        meds.append('ტიკაგრელორი')
    if q.get('med_clopidogrel'):
        meds.append('კლოპიდოგრელი')
    if q.get('med_aspirin'):
        meds.append('ასპირინი')
    if q.get('sglt2'):
        meds.append('SGLT2 (ჯარდიანსი/ფორსიგა)')
    if q.get('glp1'):
        meds.append('GLP-1 (ოზემპიკი/მუნჯარო)')
    if q.get('metformin'):
        meds.append('მეტფორმინი')
    if meds:
        items.append(('warn', 'მედიკამენტები: ' + ', '.join(meds)))
    if q.get('other_meds'):
        items.append(('info', f"სხვა წამალი: {q['other_meds']}"))
    if q.get('allergy_iodine'):
        items.append(('urgent', 'ალერგია იოდზე / კონტრასტზე!'))
    if q.get('allergy_other') or q.get('allergy_chips'):
        alg_txt = chip_summary(q.get('allergy_chips'), 'allergy_chips', q.get('allergy_text')) or 'მედიკამენტური ალერგია'
        items.append(('urgent', f'ალერგია: {alg_txt}'))
    if q.get('smoker'):
        items.append(('info', 'მწეველი'))
    return items


def evaluate(patient, today=None):
    """Evaluate thresholds, Autonomous Nurse Reflex Orders, Stage 2 tasks, Cardiology Audit, and 2D Triage."""
    today = today or timezone.localdate()
    pathway = _resolve_pathway(patient)
    is_venous = (pathway == PreopPatient.Pathway.VENOUS)
    is_phleb = (patient.op_subtype == PreopPatient.OpSubtype.PHLEB)
    is_pta = (pathway == PreopPatient.Pathway.PAD_PTA)
    is_pad_bypass = (pathway == PreopPatient.Pathway.PAD_BYPASS)
    is_on_hold = (patient.someday_list == PreopPatient.SomedayList.HOLD)

    q = merged_q(patient)
    s = merged_studies(patient)
    q_filled = bool(q.get('filled'))
    case_outcome = s.get('case_outcome_status') if s.get('case_outcome_status') in dict(CASE_OUTCOME_CHOICES) else 'scheduled'
    is_active_case = (case_outcome == 'scheduled') and not patient.archived
    days = prep_days_elapsed(patient, today=today)
    t_days = target_prep_days(patient)

    red_flags = []
    amber_flags = []
    reflex_orders = []
    priority_flags = []  # urgency/clock alerts that move from blocking red_flags once workup is 100% ready
    hard_contraindications = []  # absolute anatomical/clinical contraindications that cannot be overridden to STATUS_READY

    hb_gl = normalize_hb_gl(s.get('hb'))
    gl_mmol = normalize_glucose_mmol(s.get('glucose'))
    alb_gl = normalize_albumin_gl(s.get('albumin'))
    on_dialysis = bool(q.get('on_dialysis'))
    creat_unit = s.get('creat_unit') or ''
    _, creat_unit_ambiguous = _parse_scr_mgdl(s.get('creat'), creat_unit)
    egfr = calc_egfr(s.get('creat'), patient.age, patient.sex or '', creat_unit, on_dialysis=on_dialysis)
    crcl = calc_crcl(
        s.get('creat'), patient.age, patient.sex or '', s.get('weight_kg'),
        creat_unit, on_dialysis=on_dialysis, height_cm=s.get('height_cm'),
    )
    bmi = calc_bmi(s.get('weight_kg'), s.get('height_cm'))
    caprini_score, caprini_tier, caprini_advice = calc_caprini(patient, q, bmi, today=today)

    aaa_mm = _num(s.get('aaa_mm'), lo=10, hi=200)
    cta_months = cta_live_months_age(s, today=today)
    car_r, car_l = _int(s.get('carotid_r'), lo=0, hi=100), _int(s.get('carotid_l'), lo=0, hi=100)
    max_car = max([v for v in (car_r, car_l) if v is not None], default=None)

    abi_r = _num(s.get('abi_r'), lo=0.0, hi=3.0)
    abi_l = _num(s.get('abi_l'), lo=0.0, hi=3.0)
    tp_r = _int(s.get('tp_r'), lo=0, hi=400)
    tp_l = _int(s.get('tp_l'), lo=0, hi=400)
    tbi_r = _num(s.get('tbi_r'), lo=0.0, hi=3.0)
    tbi_l = _num(s.get('tbi_l'), lo=0.0, hi=3.0)

    # Side-aware limb hemodynamics: unilateral limbs never let contralateral TP/ABI mask ipsilateral ischemia/calcinosis
    unilateral_limb_pw = pathway in (PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA, PreopPatient.Pathway.VENOUS)
    if unilateral_limb_pw and patient.side == 'R':
        scope_legs = ('R',)
        contra_abi, contra_tp = abi_l, tp_l
    elif unilateral_limb_pw and patient.side == 'L':
        scope_legs = ('L',)
        contra_abi, contra_tp = abi_r, tp_r
    else:
        scope_legs = ('R', 'L')
        contra_abi, contra_tp = None, None

    leg_vals = {
        'R': {'abi': abi_r, 'tp': tp_r, 'tbi': tbi_r},
        'L': {'abi': abi_l, 'tp': tp_l, 'tbi': tbi_l},
    }
    scoped_abis = [leg_vals[lg]['abi'] for lg in scope_legs if leg_vals[lg]['abi'] is not None]
    scoped_tps = [leg_vals[lg]['tp'] for lg in scope_legs if leg_vals[lg]['tp'] is not None]
    scoped_tbis = [leg_vals[lg]['tbi'] for lg in scope_legs if leg_vals[lg]['tbi'] is not None]
    all_abis = [v for v in (abi_r, abi_l) if v is not None]
    all_tps = [v for v in (tp_r, tp_l) if v is not None]
    all_tbis = [v for v in (tbi_r, tbi_l) if v is not None]

    eff_abis = scoped_abis if scoped_abis else all_abis
    eff_tps = scoped_tps if scoped_tps else all_tps
    eff_tbis = scoped_tbis if scoped_tbis else all_tbis

    min_abi = min(eff_abis, default=None)
    max_abi = max(eff_abis, default=None)
    min_comp_abi = min([v for v in eff_abis if v <= 1.40], default=None)
    min_tp = min(eff_tps, default=None)
    min_tbi = min(eff_tbis, default=None)

    nc_legs = [lg for lg in scope_legs if leg_vals[lg]['abi'] is not None and leg_vals[lg]['abi'] > 1.40]
    if not nc_legs and not scoped_abis:
        nc_legs = [lg for lg in ('R', 'L') if leg_vals[lg]['abi'] is not None and leg_vals[lg]['abi'] > 1.40]
    abi_non_compressible = bool(nc_legs)
    if nc_legs:
        tbi_measured = all((leg_vals[lg]['tp'] is not None or leg_vals[lg]['tbi'] is not None) for lg in nc_legs)
        tbi_comp_safe = all(
            ((leg_vals[lg]['tp'] is not None and leg_vals[lg]['tp'] >= 60)
             or (leg_vals[lg]['tbi'] is not None and leg_vals[lg]['tbi'] >= 0.70))
            for lg in nc_legs
        )
    else:
        tbi_measured = bool(scoped_tps or scoped_tbis) if unilateral_limb_pw and patient.side in ('R', 'L') else bool(all_tps or all_tbis)
        tbi_comp_safe = True

    pad_gsv_mm = _num(s.get('pad_gsv_mm'), lo=0.5, hi=30)
    ldl_val = _num(s.get('ldl'), lo=0.2, hi=20)
    weight_num = _num(s.get('weight_kg'), lo=25, hi=260)

    ipsi_car = None
    contra_car = None
    is_symp_cea = False
    is_clti_any = False
    days_since_ev = None
    needs_brain_ct = False
    needs_neuro = False
    needs_laryngoscopy = False
    needs_cea_antiplatelet = False
    needs_wound_care = False
    needs_abi_check = False
    needs_tbi_check = False
    needs_vte_prophylaxis = False
    needs_ecg_venous = False
    needs_alt_vein = False
    cea_high_surg_risk = False
    lidocaine_max_mg = None
    key_badge = ''
    key_badge_urgent = False

    med_chips = set(q.get('med_chips') or [])
    cad_chips = set(q.get('cad_chips') or [])
    allergy_chips = set(q.get('allergy_chips') or [])
    vasc_chips = set(q.get('vasc_chips') or [])
    anesth_lines_set = set(s.get('anesth_lines_chips') or [])
    has_statin = ('statin' in med_chips) or any(
        w in (q.get('other_meds') or '').lower() for w in ('სტატინ', 'ატორვ', 'როზუვ', 'კრესტორ', 'ლიპრიმ')
    )
    has_antiplatelet = bool(
        q.get('med_aspirin') or q.get('med_clopidogrel') or q.get('med_ticagrelor') or q.get('med_prasugrel')
    )
    has_any_antithrombotic = bool(
        has_antiplatelet or q.get('med_doac') or q.get('med_dabigatran')
        or q.get('med_warfarin') or ('rivaroxaban_2_5' in med_chips)
    )

    if s.get('case_wifi_unsalvageable'):
        red_flags.append(
            'კონფერენციის სტადირებით WIfI Stage 5 (კიდური გადარჩენადი არ არის) — '
            'რევასკულარიზაციის ჩვენება გადაამოწმეთ ქირურგთან!'
        )

    if abi_non_compressible:
        needs_tbi_check = True
        nc_side_lbl = f" ({'/'.join(nc_legs)})" if nc_legs else ''
        reflex_orders.append({
            'key': 'tbi',
            'trigger': f'არაკომპრესირებადი არტერია{nc_side_lbl} (ABI {max_abi} > 1.40 — მონკებერგის კალციფიკაცია)',
            'action': 'სამიზნე კიდურზე თითის წნევის (Toe Pressure, mmHg) ან თით-მხრის ინდექსის (TBI) განსაზღვრა რეალური პერფუზიის შესაფასებლად',
            'done': tbi_measured,
        })
        if not is_venous and ((min_tp is not None and min_tp < 30) or (min_tbi is not None and min_tbi < 0.30)):
            amber_flags.append(
                f'კრიტიკულად დაბალი Toe Pressure / TBI (TP {min_tp if min_tp is not None else "—"} mmHg / '
                f'TBI {min_tbi if min_tbi is not None else "—"}) — ადასტურებს მძიმე იშემიას (CLTI).'
            )

    # 1. Pathway-specific Vascular / Venous Evaluation & Preparation Clocks
    if pathway == PreopPatient.Pathway.AAA_INFRA:
        aaa_morph = s.get('aaa_morphology') or 'fusiform'
        aaa_neck = s.get('aaa_neck_status') or 'pending'
        pop_scr = s.get('popliteal_screen_status') or 'pending'

        if q_filled and q.get('aaa_pain'):
            red_flags.append(
                'საეჭვოა სიმპტომური AAA (ახალი ტკივილი მუცლის/წელის/გვერდის არეში) — '
                'გეგმური კვლევების ნაცვლად დაუყოვნებლივ აცნობეთ ქირურგს!'
            )
        if aaa_morph == 'inflammatory_mycotic':
            red_flags.append(
                'საეჭვოა მიკოზური / ინფიცირებული ანევრიზმა (ESVS 2024) — '
                'სასწრაფოდ აცნობეთ ქირურგს (სისხლის ნათესები, ანტიბიოტიკოთერაპია და დაჩქარებული ტაქტიკა)!'
            )
        elif aaa_morph == 'inflammatory':
            amber_flags.append(
                'სტერილური ანთებითი AAA / პერიაორტიტი (ESVS 2024): ღია დისექციისას მომატებულია თორმეტგოჯა ნაწლავისა და შარდსაწვეთების დაზიანების რისკი — ანატომიური შესაბამისობისას უპირატესია EVAR.'
            )
        if aaa_mm is not None:
            min_thresh = 50 if patient.sex == 'F' else 55
            mm_fmt = int(aaa_mm) if aaa_mm == int(aaa_mm) else aaa_mm
            key_badge = f'{mm_fmt} მმ'
            key_badge_urgent = bool(aaa_mm >= 70 or aaa_morph == 'inflammatory_mycotic')
            if aaa_mm >= 70:
                msg = f'გიგანტური ანევრიზმა ({aaa_mm} მმ >= 70 მმ) — მაღალი რუპტურის რისკი, დაჩქარებული მომზადება!'
                red_flags.append(msg)
                priority_flags.append(msg)
            elif aaa_mm < min_thresh:
                if aaa_morph in ('saccular', 'rapid_growth', 'inflammatory'):
                    morph_reason = {
                        'saccular': 'საკულარული მორფოლოგია',
                        'rapid_growth': 'სწრაფი ზრდის დინამიკა (≥10 მმ/წ)',
                        'inflammatory': 'ანთებითი მორფოლოგია / პერიაორტიტი',
                    }[aaa_morph]
                    amber_flags.append(
                        f'ESVS 2024: {morph_reason} ({aaa_mm} მმ) — '
                        f'ოპერაციული ჩვენება დასაბუთებულია სტანდარტულ დიამეტრამდეც ({min_thresh} მმ).'
                    )
                elif aaa_morph != 'inflammatory_mycotic':
                    amber_flags.append(f'ანევრიზმის დიამეტრი ({aaa_mm} მმ) სტანდარტულ ზღვარზე ({min_thresh} მმ) ნაკლებია — გადაამოწმეთ ჩვენება ქირურგთან.')

        if aaa_neck == 'hostile_neck':
            if patient.op_subtype == 'evar':
                red_flags.append(
                    'CTA-ზე Hostile Neck (ყელი <10–15 მმ / კუთხე >60° / თრომბი-კალციფიკაცია — IFU-ს გარეთ)! '
                    'ESVS 2024 Class III: სტანდარტული EVAR არ არის რეკომენდებული — შეათანხმეთ ღია რეზექცია ან FEVAR ქირურგთან!'
                )
            elif patient.op_subtype == 'tbd':
                amber_flags.append(
                    'CTA-ზე Hostile Neck (IFU-ს გარეთ) — ტაქტიკის შერჩევისას სტანდარტული EVAR არ არის რეკომენდებული; განიხილეთ ღია რეზექცია ან FEVAR.'
                )
        if pop_scr == 'aneurysm_found':
            red_flags.append(
                'გამოვლინდა თანმხლები მუხლქვეშა/ბარძაყის ანევრიზმა (ESVS 2024: 10–15% თანაარსებობა AAA-სთან) — '
                'დისტალური თრომბოემბოლიის რისკი, დაგეგმეთ ეტაპობრივი/თანმხლები მკურნალობა ქირურგთან!'
            )

        if days > t_days and not patient.approved_at and not is_on_hold and is_active_case:
            msg = f'გახანგრძლივებული წინასაოპერაციო მომზადება: გავიდა {days} დღე (სამიზნე ლიმიტი: {t_days} დღე) — რუპტურის რისკი!'
            red_flags.append(msg)
            priority_flags.append(msg)
        elif days in (5, 6) and not patient.approved_at and not is_on_hold and is_active_case:
            amber_flags.append(f'მომზადების მე-{days} დღე ({t_days} დღიდან) — დააჩქარეთ დარჩენილი კვლევები.')

        if cta_months is not None:
            if cta_months > 6 or (cta_months > 3 and aaa_mm is not None and aaa_mm >= 65):
                red_flags.append(f'კტ-ანგიოგრაფია ხანდაზმულია ({cta_months} თვის წინანდელი) — შეათანხმეთ განმეორებითი CTA ქირურგთან.')
        if q_filled and not s.get('cta_disc'):
            amber_flags.append('აორტის კტ-ანგიოგრაფიის DICOM დისკი ჯერ არ არის მოტანილი / შემოწმებული.')

        if q_filled and q.get('cfs_level') == '5_plus' and patient.op_subtype == 'open':
            amber_flags.append('დაუძლურების მაღალი ინდექსი (CFS ≥ 5) — ESVS 2024-ით ღია რეზექციასთან შედარებით განიხილეთ EVAR ან რისკის გადაფასება.')
        if s.get('aaa_iliac_access') == 'narrow_calcified':
            amber_flags.append('CTA-ზე ვიწრო (<7 მმ) ან მძიმედ კალციფიცირებული თეძო-ბარძაყის მიდგომა — EVAR-ისას საჭიროა Iliac Conduit / მიდგომის დაგეგმვა.')
        elif s.get('aaa_iliac_access') == 'iliac_aneurysm':
            amber_flags.append('თანმხლები თეძოს არტერიის ანევრიზმა CTA-ზე — დასაგეგმია შიგნითა თეძოს არტერიის (IIA) შენარჩუნება.')
        if s.get('aaa_renal_vein_anom'):
            amber_flags.append('CTA-ზე რეტროაორტული მარცხენა თირკმლის ვენა / ცხენისებრი თირკმელი — საყურადღებოა აორტის ყელის დისექციისა და კლამპირებისას!')
        if q_filled and q.get('aaa_pedal_pulse') == 'reduced':
            needs_abi_check = True
            reflex_orders.append({
                'key': 'aaa_abi',
                'trigger': 'ტერფის პულსაცია მიღებისას დაქვეითებულია',
                'action': 'საბაზისო გოჯ-მხრის ინდექსი (ABI) + მუხლქვეშა არტერიების ექო-სკრინინგი ოპერაციამდე',
                'done': min_abi is not None,
            })

    elif pathway == PreopPatient.Pathway.CEA:
        days_since_ev = cea_live_days_since_event(q, today=today, s=s)
        symp_within_6m = (days_since_ev is None) or (days_since_ev <= 180)
        is_symp_cea = (
            (patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP and symp_within_6m)
            or (q_filled and bool(q.get('cea_symp_event')) and symp_within_6m)
        )

        if days_since_ev is not None and days_since_ev > 180 and (patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP or q.get('cea_symp_event')):
            amber_flags.append(
                f'ნევროლოგიური ეპიზოდიდან გასულია {days_since_ev} დღე (>6 თვე) — ESVS 2023 გაიდლაინით განიხილება როგორც ასიმპტომური სტენოზი.'
            )
        elif patient.op_subtype == PreopPatient.OpSubtype.CEA_ASYMP and q_filled and q.get('cea_symp_event'):
            amber_flags.append(
                'კითხვარით დაფიქსირდა ნევროლოგიური სიმპტომი ბოლო 6 თვეში — გადაამოწმეთ სიმპტომური CEA-ს სტატუსი (14-დღიანი ფანჯარა)!'
            )
        if q_filled and q.get('cea_crescendo'):
            red_flags.append(
                'Crescendo TIA / მერყევი ნევროლოგიური დეფიციტი ბოლო 24–48 სთ-ში — სასწრაფოდ აცნობეთ ქირურგსა და ნევროლოგს!'
            )
        if q_filled and q.get('cea_thrombolysis'):
            red_flags.append(
                'ინტრავენური თრომბოლიზისი ბოლო 6 დღეში (ESVS 2023) — ნაადრევი ჰეპარინიზაციის/სისხლჩაქცევის რისკი, შეათანხმეთ ოპერაციის თარიღი ქირურგთან!'
            )
        if q_filled and q.get('cea_disabling_stroke'):
            red_flags.append(
                'მძიმე ნარჩენი ნევროლოგიური დეფიციტი (mRS >= 3) — გადაამოწმეთ CEA-ს ჩვენება და ვადები ქირურგთან და ნევროლოგთან!'
            )

        if is_symp_cea and days_since_ev is not None and not patient.approved_at and not is_on_hold and is_active_case:
            if days_since_ev > 14:
                msg = f'ინდექსური სიმპტომიდან გავიდა {days_since_ev} დღე — გადაცილებულია ESVS 2023-ის 14-დღიანი ოპტიმალური ფანჯარა! დააჩქარეთ ქირურგის შეფასება.'
                red_flags.append(msg)
                priority_flags.append(msg)
            elif days_since_ev >= 10:
                amber_flags.append(
                    f'სიმპტომიდან გასულია {days_since_ev} დღე (ESVS 14-დღიანი ფანჯრიდან დარჩა {14 - days_since_ev} დღე) — დააჩქარეთ კვლევები!'
                )

        if days > t_days and not patient.approved_at and not is_on_hold and is_active_case:
            if is_symp_cea:
                msg = f'გახანგრძლივებული მომზადება სიმპტომურ CEA-ზე: გავიდა {days} დღე (სამიზნე ლიმიტი: {t_days} დღე) — განმეორებითი ინსულტის რისკი!'
            else:
                msg = f'გახანგრძლივებული წინასაოპერაციო მომზადება: გავიდა {days} დღე (სამიზნე ლიმიტი: {t_days} დღე).'
            red_flags.append(msg)
            priority_flags.append(msg)
        elif not patient.approved_at and not is_on_hold and is_active_case and days >= max(4, t_days - 1):
            amber_flags.append(f'მომზადების მე-{days} დღე ({t_days} დღიდან) — დააჩქარეთ დარჩენილი კვლევები.')

        if q_filled and patient.side not in ('R', 'L'):
            amber_flags.append('მიუთითეთ დაგეგმილი CEA-ს მხარე (მარჯვენა ან მარცხენა) პაციენტის ბარათში.')

        ipsi_car = car_r if patient.side == 'R' else (car_l if patient.side == 'L' else max_car)
        contra_car = car_l if patient.side == 'R' else (car_r if patient.side == 'L' else None)

        if ipsi_car is not None:
            side_prefix = f'{patient.side} ' if patient.side in ('R', 'L') else ''
            key_badge = f'{side_prefix}{ipsi_car}%'
            key_badge_urgent = bool(is_symp_cea or ipsi_car >= 90)
            if ipsi_car >= 100:
                occ_msg = (
                    'საოპერაციო მხარეს ICA-ს სრული ოკლუზია (100%) — CEA სრულ ქრონიკულ ოკლუზიაზე არ ტარდება! '
                    'სასწრაფოდ გადაამოწმეთ CTA ქირურგთან (near-occlusion vs ოკლუზია).'
                )
                red_flags.append(occ_msg)
                hard_contraindications.append(occ_msg)
            elif is_symp_cea and ipsi_car < 50:
                red_flags.append(f'საოპერაციო მხარეს სტენოზი ({ipsi_car}%) < 50% (NASCET) — გადაამოწმეთ CEA-ს ჩვენება ქირურგთან.')
            elif not is_symp_cea and ipsi_car < 60:
                red_flags.append(f'ასიმპტომურ საოპერაციო მხარეს სტენოზი ({ipsi_car}%) < 60% (NASCET) — გადაამოწმეთ CEA-ს ჩვენება ქირურგთან.')
            elif not is_symp_cea and ipsi_car < 70:
                amber_flags.append(f'ასიმპტომური სტენოზი {ipsi_car}% (60–69% NASCET) — გადაამოწმეთ მაღალი რისკის ბალთის ნიშნები CTA/დუპლექსზე.')
        elif is_symp_cea and days_since_ev is not None:
            key_badge = f'{days_since_ev}/14დღ'
            key_badge_urgent = True

        if contra_car is not None:
            if contra_car >= 80:
                red_flags.append(
                    f'კონტრალატერალური საძილე არტერიის კრიტიკული სტენოზი / ოკლუზია ({contra_car}%) — '
                    'ინტრაოპერაციული შუნტის (Shunt) მზადყოფნა და ჰიპერპერფუზიის მაღალი რისკი!'
                )
            elif contra_car >= 50:
                amber_flags.append(f'კონტრალატერალური საძილე არტერიის სტენოზი ({contra_car}%) — საყურადღებოა ცერებრული პერფუზიისთვის.')

        cea_cta = s.get('cea_cta_status') or 'pending'
        if cea_cta == 'high_calcified':
            amber_flags.append('კისრის/რკალის CTA-ზე მაღალი ბიფურკაცია (C2) / მძიმე კალციფიკაცია / კინკინგი — აცნობეთ ქირურგს.')
        if q_filled and q.get('cea_neck_limited'):
            amber_flags.append('კისრის გაშლა მკვეთრად შეზღუდულია — საყურადღებოა საოპერაციო პოზიციონირებისა და ინტუბაციისთვის.')

        needs_brain_ct = is_symp_cea or (q_filled and bool(q.get('neuro')))
        brain_im = s.get('brain_imaging') or 'pending'
        if brain_im == 'large_or_hemorrhage':
            hem_msg = (
                'თავის ტვინის CT/MRI-ზე სისხლჩაქცევა ან ფართო მწვავე ინფარქტი (>1/3 MCA) — '
                'ნაადრევი CEA საშიშია, სასწრაფოდ აცნობეთ ქირურგსა და ნევროლოგს!'
            )
            red_flags.append(hem_msg)
            hard_contraindications.append(hem_msg)
        if q_filled and not s.get('cta_disc'):
            amber_flags.append('კისრის/თავის ტვინის CTA/MRI DICOM დისკი ჯერ არ არის მოტანილი / შემოწმებული.')

        needs_neuro = is_symp_cea or (q_filled and bool(q.get('neuro')))
        needs_laryngoscopy = q_filled and (bool(q.get('cea_prior_neck')) or bool(q.get('cea_hoarseness')))
        laryng_res = s.get('laryngoscopy_res') or 'pending'
        if laryng_res == 'palsy':
            red_flags.append(
                'ლარინგოსკოპიით გამოვლინდა სახმო იოგის პარეზი — ორმხრივი ნერვის დაზიანებისა და ასფიქსიის საფრთხე! '
                'სასწრაფოდ შეათანხმეთ ქირურგთან (CAS vs CEA).'
            )

        cea_neck_set = set(q.get('cea_neck_chips') or [])
        lvef_prelim = _int(s.get('lvef'), lo=5, hi=95)
        cea_high_surg_risk = bool(
            ('neck_rad' in cea_neck_set) or ('ipsi_restenosis' in cea_neck_set)
            or laryng_res == 'palsy' or cea_cta == 'high_calcified'
            or (lvef_prelim is not None and lvef_prelim < 40)
        )
        if cea_high_surg_risk:
            amber_flags.append(
                'ESVS 2023 მაღალი ქირურგიული რისკი ღია CEA-სთვის (დასხივება / რესტენოზი / C2 მაღალი ბიფურკაცია / სახმო იოგი / LVEF <40%) — '
                'ქირურგთან განიხილეთ კაროტიდული სტენტირება (CAS / TCAR).'
            )

        # ESVS 2023 Class I: perioperative antiplatelet is mandatory BEFORE CEA (holding OAC alone leaves patient unprotected),
        # UNLESS acute intracranial hemorrhage/large infarct or recent IV thrombolysis prohibits immediate antiplatelet initiation!
        antiplatelet_prohibited_cea = (brain_im == 'large_or_hemorrhage') or (q_filled and bool(q.get('cea_thrombolysis')))
        if q_filled and not has_antiplatelet and not antiplatelet_prohibited_cea:
            needs_cea_antiplatelet = True
            has_oac_only = bool(q.get('med_doac') or q.get('med_dabigatran') or q.get('med_warfarin'))
            ap_trigger = (
                'პაციენტი იღებს მხოლოდ ანტიკოაგულანტს (რომელიც ოპერაციამდე ჩერდება) ანტიაგრეგანტის გარეშე!'
                if has_oac_only else 'CEA-ს წინ პაციენტი არ იღებს ანტიაგრეგანტს (ESVS 2023 Class I)'
            )
            reflex_orders.append({
                'key': 'cea_antiplatelet',
                'trigger': ap_trigger,
                'action': 'თავის ტვინის CT-ით სისხლჩაქცევის გამორიცხვის შემდეგ დაუყოვნებლივ დაიწყოს ასპირინი (± კლოპიდოგრელი სიმპტომურ CEA-ზე) ქირურგთან შეთანხმებით',
                'done': bool(s.get('cea_antiplatelet_started')),
            })
            if not s.get('cea_antiplatelet_started'):
                red_flags.append(
                    'პაციენტი არ იღებს ანტიაგრეგანტს (ასპირინი / კლოპიდოგრელი)! ESVS 2023 Class I-ით CEA-ს წინ '
                    'ანტიაგრეგანტული თერაპია სავალდებულოა ინსულტის პრევენციისთვის — აცნობეთ ქირურგს.'
                )
        if q_filled and not has_statin:
            amber_flags.append('პაციენტს არ უფიქსირდება სტატინის მიღება (ESVS 2023 Class I: CEA-ს წინ ნაჩვენებია მაღალი ინტენსივობის სტატინი).')
        elif ldl_val is not None and ldl_val >= 1.4:
            amber_flags.append(f'LDL ქოლესტერინი {ldl_val} მმოლ/ლ (>= 1.4 მმოლ/ლ) — ESVS 2023/ESC სამიზნისთვის განიხილეთ სტატინის მაქსიმალური დოზა ± ეზეტიმიბი.')

    elif pathway == PreopPatient.Pathway.ABF:
        is_clti = (patient.op_subtype == PreopPatient.OpSubtype.ABF_CLTI) or (
            q_filled and (bool(q.get('abf_rest_pain')) or bool(q.get('abf_ulcer')))
        )
        is_clti_any = is_clti
        if patient.op_subtype == PreopPatient.OpSubtype.ABF_CLAUD and q_filled and (q.get('abf_rest_pain') or q.get('abf_ulcer')):
            amber_flags.append(
                'კითხვარით დაფიქსირდა მოსვენებითი ტკივილი ან წყლული (CLTI) — სასურველია ქვეტიპის გადართვა კრიტიკულ იშემიაზე (7-დღიანი რეჟიმი).'
            )
        elif patient.op_subtype == PreopPatient.OpSubtype.ABF_CLAUD and q_filled and not is_clti:
            if not q.get('ic_bmt_3m') or q.get('abf_duration') in ('< 2 კვირა', '1–3 თვე'):
                amber_flags.append(
                    'ხანგამოშვებითი კოჭლობა (ESVS 2024 Class I): ინვაზიურ ჩარევამდე რეკომენდებულია ≥3 თვიანი ოპტიმალური მედიკამენტური თერაპია (BMT) და სავარჯიშო თერაპია (SET).'
                )
        if q_filled and q.get('abf_wet_infection'):
            red_flags.append(
                'ტერფის სველი ნეკროზი / ჩირქოვანი გამონადენი / ფლეგმონა — სინთეზური ბიფურკაციული პროთეზის სეფსისის უმაღლესი რისკი! '
                'სასწრაფოდ აცნობეთ ქირურგს სანაციისა და ეტაპობრივი ტაქტიკის გადასაწყვეტად.'
            )
        if q_filled and q.get('abf_groin_issue'):
            red_flags.append(
                'საზარდულის არეში სოკოვანი გამოყელვა (ინტერტრიგო) / გამონაყარი / ანთებითი კვანძი — '
                'ბარძაყის ანასტომოზისა და პროთეზის ინფიცირების უმაღლესი რისკი! აცნობეთ ქირურგს.'
            )

        if days > t_days and not patient.approved_at and not is_on_hold and is_active_case:
            if is_clti:
                msg = f'გახანგრძლივებული მომზადება CLTI პაციენტთან: გავიდა {days} დღე (სამიზნე ლიმიტი: {t_days} დღე) — კიდურის დაკარგვის რისკი!'
            else:
                msg = f'გახანგრძლივებული წინასაოპერაციო მომზადება: გავიდა {days} დღე (სამიზნე ლიმიტი: {t_days} დღე).'
            red_flags.append(msg)
            priority_flags.append(msg)
        elif not patient.approved_at and not is_on_hold and is_active_case and days >= t_days - 2:
            amber_flags.append(f'მომზადების მე-{days} დღე ({t_days} დღიდან) — დააჩქარეთ დარჩენილი კვლევები.')

        clamp_zone = s.get('abf_clamp_zone') or 'pending'
        if clamp_zone == 'juxtarenal_calcified':
            red_flags.append(
                'CTA-ზე იუქსტარენალური აორტის ოკლუზია ან მძიმე კალციფიკაცია — საჭიროა სუპრარენალური კლამპირებისა და თირკმლის დაცვის დაგეგმვა ქირურგთან!'
            )

        outflow = s.get('abf_outflow') or 'pending'
        if outflow == 'poor_outflow':
            red_flags.append(
                'არადამაკმაყოფილებელი დისტალური Outflow (Profunda + SFA მძიმე დაზიანება) — შუნტის თრომბოზის რისკი! '
                'შეათანხმეთ ჰიბრიდული/დისტალური რეკონსტრუქცია ქირურგთან.'
            )
            needs_alt_vein = True
            reflex_orders.append({
                'key': 'abf_gsv',
                'trigger': 'CTA-ზე მძიმე დისტალური Outflow დაზიანება',
                'action': 'დიდი საჩინო ვენის (GSV) რეფლექს ექო-კარტირება (შესაძლო თანმხლები ბარძაყ-მუხლქვეშა შუნტირებისთვის)',
                'done': (s.get('alt_vein_mapped') or 'pending') != 'pending',
            })
        elif outflow == 'profunda_stenosis':
            amber_flags.append('CFA / Profunda Femoris სტენოზი CTA-ზე — გასათვალისწინებელია თანმხლები ენდარტერექტომია / პროფუნდოპლასტიკა.')

        if s.get('abf_mesenteric') == 'sma_stenosis':
            red_flags.append('CTA-ზე ზემო ჯორჯლის არტერიის (SMA) მნიშვნელოვანი სტენოზი/ოკლუზია — მსხვილი/წვრილი ნაწლავის იშემიის უმაღლესი რისკი აორტაზე ჩარევისას!')
        elif s.get('abf_mesenteric') == 'ima_dominant_iia_occ':
            amber_flags.append('დომინანტური IMA კოლატერალი / ორმხრივი IIA ოკლუზია CTA-ზე — დასაგეგმია IMA რეიმპლანტაცია / მენჯის პერფუზიის შენარჩუნება.')
        if s.get('abf_renal_stenosis'):
            amber_flags.append('თანმხლები თირკმლის არტერიის სტენოზი (≥60%) CTA-ზე — განიხილეთ თანმხლები რენალური ჩარევა / ნეფროპროტექცია.')

        if min_comp_abi is not None:
            key_badge = f'ABI {min_comp_abi}'
            key_badge_urgent = bool(is_clti or min_comp_abi < 0.4)
            if min_comp_abi < 0.4 and not is_clti:
                amber_flags.append(f'კრიტიკულად დაბალი გოჯ-მხრის ინდექსი (ABI {min_comp_abi} < 0.40) — შეესაბამება მძიმე იშემიას.')
        elif min_tp is not None:
            key_badge = f'TP {min_tp}'
            key_badge_urgent = bool(is_clti or min_tp < 30)
        elif min_tbi is not None:
            key_badge = f'TBI {min_tbi}'
            key_badge_urgent = bool(is_clti or min_tbi < 0.30)
        elif is_clti:
            key_badge = 'CLTI'
            key_badge_urgent = True
        elif q.get('abf_claud_m'):
            key_badge = f"{q['abf_claud_m']} მ"

        if q_filled and not s.get('cta_disc'):
            amber_flags.append('აორტო-ილიაკური / ქვემო კიდურების CTA DICOM დისკი ჯერ არ არის მოტანილი / შემოწმებული.')
        if q_filled and not has_any_antithrombotic:
            amber_flags.append('პაციენტი არ იღებს ანტიაგრეგანტს (ESVS 2024 Class I: ასპირინი ან კლოპიდოგრელი).')
        if q_filled and not has_statin:
            amber_flags.append('პაციენტს არ უფიქსირდება სტატინი (ESVS 2024 Class I: მაღალი ინტენსივობის სტატინი).')
        elif ldl_val is not None and ldl_val >= 1.4:
            amber_flags.append(f'LDL ქოლესტერინი {ldl_val} მმოლ/ლ (>= 1.4 მმოლ/ლ) — განიხილეთ ლიპიდდამწევი თერაპიის გაძლიერება (ESVS 2024).')

        needs_wound_care = q_filled and (bool(q.get('abf_ulcer')) or bool(q.get('abf_wet_infection')) or bool(q.get('abf_groin_issue')))

    elif is_pad_bypass:
        is_clti = (patient.op_subtype == PreopPatient.OpSubtype.BYPASS_CLTI) or (
            q_filled and (bool(q.get('abf_rest_pain')) or bool(q.get('abf_ulcer')))
        )
        is_clti_any = is_clti
        if q_filled and patient.side not in ('R', 'L'):
            amber_flags.append('მიუთითეთ საოპერაციო კიდურის მხარე (მარჯვენა ან მარცხენა).')
        if q_filled and q.get('abf_wet_infection'):
            red_flags.append('ტერფის სველი განგრენა / ჩირქოვანი ინფექცია — სასწრაფოდ აცნობეთ ქირურგს დრენირებისა და ანტიბიოტიკოთერაპიისთვის!')
        if q_filled and q.get('abf_groin_issue'):
            red_flags.append('საზარდულის არეში ინტერტრიგო / ინფექცია — შუნტის ინფიცირების მაღალი რისკი! აცნობეთ ქირურგს.')
        if days > t_days and not patient.approved_at and not is_on_hold and is_active_case:
            msg = f'გახანგრძლივებული მომზადება პერიფერიულ შუნტირებაზე: გავიდა {days} დღე (ლიმიტი: {t_days} დღე).'
            red_flags.append(msg)
            priority_flags.append(msg)

        runoff = s.get('pad_runoff_status') or 'pending'
        if runoff == 'desert_foot':
            red_flags.append('CTA/ანგიოგრაფიით კრიტიკულად დაზიანებული დისტალური კალაპოტი (Desert foot) — შუნტის ადრეული თრომბოზის უმაღლესი რისკი! აცნობეთ ქირურგს.')
        elif runoff == 'tibial_single':
            amber_flags.append('დისტალური მიმღები მხოლოდ წვივის ერთი არტერიაა — აუცილებელია ხარისხიანი აუტოვენა (GSV ≥ 3.0 მმ).')

        gsv_st = s.get('pad_gsv_status') or 'pending'
        gsv_inadequate = (
            gsv_st == 'inadequate'
            or (pad_gsv_mm is not None and pad_gsv_mm < 3.0)
            or ('vein_strip' in vasc_chips)
        )
        if gsv_inadequate:
            needs_alt_vein = True
            reflex_orders.append({
                'key': 'alt_vein',
                'trigger': 'იფსილატერალური GSV უვარგისია (< 3.0 მმ ან ამოღებულია)',
                'action': 'ალტერნატიული აუტოვენის რეფლექს-კარტირება (კონტრალატერალური GSV, SSV და ხელის ვენები — Cephalic/Basilic)',
                'done': (s.get('alt_vein_mapped') or 'pending') != 'pending',
            })
            if s.get('alt_vein_mapped') == 'contra_gsv_ok' and (
                (contra_abi is not None and contra_abi < 0.50) or (contra_tp is not None and contra_tp < 40)
            ):
                red_flags.append(
                    f'კონტრალატერალურ კიდურზე მძიმე არტერიული იშემიაა (ABI {contra_abi if contra_abi is not None else "—"} / '
                    f'TP {contra_tp if contra_tp is not None else "—"} mmHg) — იშემიური კიდურიდან GSV-ის აღება საშიშია ჭრილობის ნეკროზის გამო! განიხილეთ ხელის ვენა (Arm Vein).'
                )
            if s.get('alt_vein_mapped') == 'no_autovein_prosthetic':
                if not is_clti:
                    red_flags.append(
                        'ხანგამოშვებითი კოჭლობის (IC) დროს აუტოვენის არარსებობისას სინთეზური ინფრაინგვინალური შუნტირება '
                        'უკუნაჩვენებია (ESVS 2024 / ACC-AHA Class III — Harm)! გადაამოწმეთ ჩვენება ქირურგთან!'
                    )
                else:
                    red_flags.append('ვარგისი აუტოვენა ვერცერთ კიდურზე ვერ მოიძებნა — შეათანხმეთ სინთეზური/კომპოზიტური პროთეზის ან ენდოვასკულური ტაქტიკა ქირურგთან!')

        if q_filled and not is_clti:
            if not q.get('ic_bmt_3m') or q.get('abf_duration') in ('< 2 კვირა', '1–3 თვე'):
                amber_flags.append(
                    'ხანგამოშვებითი კოჭლობა (ESVS 2024 Class I): ინვაზიურ ჩარევამდე რეკომენდებულია ≥3 თვიანი ოპტიმალური მედიკამენტური თერაპია (BMT) და სავარჯიშო თერაპია (SET).'
                )

        if pad_gsv_mm is not None:
            key_badge = f'GSV {pad_gsv_mm}მმ'
            key_badge_urgent = bool(pad_gsv_mm < 3.0 or is_clti)
        elif min_comp_abi is not None:
            key_badge = f'ABI {min_comp_abi}'
            key_badge_urgent = bool(is_clti or min_comp_abi < 0.4)
        elif min_tp is not None:
            key_badge = f'TP {min_tp}'
            key_badge_urgent = bool(is_clti or min_tp < 30)
        elif is_clti:
            key_badge = 'CLTI'
            key_badge_urgent = True

        if q_filled and not s.get('cta_disc'):
            amber_flags.append('ქვემო კიდურების ანგიოგრაფიის / CTA DICOM დისკი ჯერ არ არის მოტანილი.')
        if q_filled and not has_any_antithrombotic:
            amber_flags.append('პაციენტი არ იღებს ანტიაგრეგანტს (ESVS 2024 Class I).')
        if q_filled and not has_statin:
            amber_flags.append('პაციენტს არ უფიქსირდება სტატინი (ESVS 2024 Class I).')
        elif ldl_val is not None and ldl_val >= 1.4:
            amber_flags.append(f'LDL ქოლესტერინი {ldl_val} მმოლ/ლ (>= 1.4 მმოლ/ლ) — განიხილეთ სტატინის ინტენსიფიკაცია ± ეზეტიმიბი.')
        needs_wound_care = q_filled and (bool(q.get('abf_ulcer')) or bool(q.get('abf_wet_infection')) or bool(q.get('abf_groin_issue')))

    elif is_pta:
        is_clti = (patient.op_subtype == PreopPatient.OpSubtype.PTA_CLTI) or (
            q_filled and (bool(q.get('abf_rest_pain')) or bool(q.get('abf_ulcer')))
        )
        is_clti_any = is_clti
        if q_filled and patient.side not in ('R', 'L', 'B'):
            amber_flags.append('მიუთითეთ ჩარევის მხარე (მარჯვენა, მარცხენა ან ორმხრივი).')
        if q_filled and q.get('abf_wet_infection'):
            red_flags.append('ტერფის სველი ინფექცია / ფლეგმონა — რევასკულარიზაციასთან ერთად საჭიროა გადაუდებელი ქირურგიული სანაცია!')
        if q_filled and q.get('abf_groin_issue'):
            red_flags.append('საზარდულის არეში ინტერტრიგო / ინფექცია — პუნქციის ზონის ინფიცირების რისკი! განიხილეთ ალტერნატიული მიდგომა და სანაცია.')
        if days > t_days and not patient.approved_at and not is_on_hold and is_active_case:
            msg = f'გახანგრძლივებული მომზადება PTA/სტენტირებაზე: გავიდა {days} დღე (ლიმიტი: {t_days} დღე).'
            red_flags.append(msg)
            priority_flags.append(msg)

        if not is_clti and (s.get('pta_lesion_site') == 'infrapopliteal'):
            red_flags.append(
                'ხანგამოშვებითი კოჭლობის (IC) დროს წვივის/ტერფის არტერიებზე (BTK / Infrapopliteal) ენდოვასკულური ჩარევა '
                'უკუნაჩვენებია (ESVS 2024 / ACC-AHA 2024 Class III — Harm)! გადაამოწმეთ ჩვენება ქირურგთან.'
            )
        elif not is_clti and (s.get('pta_lesion_site') == 'multilevel'):
            amber_flags.append(
                'მრავალდონიანი დაზიანება ხანგამოშვებითი კოჭლობისას — დარწმუნდით, რომ არ იგეგმება იზოლირებული წვივის (BTK) არტერიების ჩარევა (ESVS 2024 Class III).'
            )
        if q_filled and not is_clti:
            if not q.get('ic_bmt_3m') or q.get('abf_duration') in ('< 2 კვირა', '1–3 თვე'):
                amber_flags.append(
                    'ხანგამოშვებითი კოჭლობა (ESVS 2024 Class I): ინვაზიურ ჩარევამდე რეკომენდებულია ≥3 თვიანი ოპტიმალური მედიკამენტური თერაპია (BMT) და სავარჯიშო თერაპია (SET).'
                )

        if s.get('pta_access_site') == 'cfa_calcified_scar':
            amber_flags.append('პუნქციის ზონაში (CFA) ნაწიბური / მძიმე კალციფიკაცია — განიხილეთ კონტრალატერალური, სხივის (Radial) ან მხრის მიდგომა ექო-კონტროლით.')
        if q_filled and q.get('pta_cant_lie_flat'):
            amber_flags.append('პაციენტს უჭირს ზურგზე გაუნძრევლად წოლა 60–90 წთ — ავტომატურად ჩაირთო ანესთეზიოლოგი (სედაციისთვის).')
        if 'clopidogrel_intol' in allergy_chips:
            red_flags.append('ანამნეზში კლოპიდოგრელის აუტანლობა — სტენტირებამდე შეათანხმეთ ალტერნატიული P2Y12 (ტიკაგრელორი) ქირურგთან!')
        if q_filled and not has_any_antithrombotic:
            amber_flags.append('პაციენტი არ იღებს ანტიაგრეგანტს PTA/სტენტირებამდე (ESVS 2024 Class I).')
        if q_filled and not has_statin:
            amber_flags.append('პაციენტს არ უფიქსირდება სტატინი (ESVS 2024 Class I).')
        elif ldl_val is not None and ldl_val >= 1.4:
            amber_flags.append(f'LDL ქოლესტერინი {ldl_val} მმოლ/ლ (>= 1.4 მმოლ/ლ) — განიხილეთ სტატინის ინტენსიფიკაცია ± ეზეტიმიბი.')

        if min_comp_abi is not None:
            key_badge = f'ABI {min_comp_abi}'
            key_badge_urgent = bool(is_clti or min_comp_abi < 0.4)
        elif min_tp is not None:
            key_badge = f'TP {min_tp}'
            key_badge_urgent = bool(is_clti or min_tp < 30)
        elif is_clti:
            key_badge = 'CLTI'
            key_badge_urgent = True
        needs_wound_care = q_filled and (bool(q.get('abf_ulcer')) or bool(q.get('abf_wet_infection')) or bool(q.get('abf_groin_issue')))

    elif is_venous:
        ceap = q.get('ven_ceap') if q.get('ven_ceap') in ('C2', 'C3', 'C4', 'C5', 'C6') else 'C2'
        ven_dup = s.get('ven_duplex_status') or 'pending'
        gsv_mm = _num(s.get('ven_gsv_mm'), lo=1, hi=50)
        if patient.op_subtype == PreopPatient.OpSubtype.EVLA and weight_num is not None:
            lidocaine_max_mg = min(500, int(round(weight_num * 7.0)))

        if ven_dup == 'acute_dvt':
            dvt_msg = 'ვენების დუპლექსით მწვავე ღრმა ვენების თრომბოზი (DVT) — გეგმური EVLA / ფლებექტომია აბსოლუტურად უკუნაჩვენებია! სასწრაფოდ აცნობეთ ქირურგს.'
            red_flags.append(dvt_msg)
            hard_contraindications.append(dvt_msg)
        elif ven_dup == 'deep_obstruction':
            obs_msg = 'ღრმა ვენური სისტემის ოკლუზია (ზედაპირული ვენა მთავარი კოლატერალია) — აბლაცია/ფლებექტომია უკუნაჩვენებია! აცნობეთ ქირურგს.'
            red_flags.append(obs_msg)
            hard_contraindications.append(obs_msg)
        elif ven_dup == 'superficial_thrombus' or (q_filled and q.get('ven_acute_svt')):
            red_flags.append(
                'მწვავე აღმავალი თრომბოფლებიტი (SVT) — გადაამოწმეთ SFJ/SPJ-სთან დისტანცია დუპლექსით და შეათანხმეთ ტაქტიკა ქირურგთან!'
            )

        if q_filled and q.get('ven_pregnant'):
            preg_msg = 'ორსულობა / ლაქტაცია / მშობიარობის შემდგომი პერიოდი (<3 თვ) — გეგმური EVLA / ფლებექტომია უკუნაჩვენებია (ESVS 2022)!'
            red_flags.append(preg_msg)
            hard_contraindications.append(preg_msg)
        if q_filled and (q.get('ven_lidocaine_allergy') or 'lidocaine' in allergy_chips):
            red_flags.append(
                'ალერგია ლიდოკაინზე / ადგილობრივ ანესთეტიკზე — ტუმესცენტური ანესთეზია საშიშია! სავალდებულოა ქირურგისა და ანესთეზიოლოგის ჩართვა.'
            )
        if q_filled and q.get('ven_skin_infection'):
            red_flags.append('მწვავე კანის ინფექცია / წითელი ქარი / ჩირქოვანი ეგზემა პუნქციის/ჭრილობის ზონაში — საჭიროა წინასწარი სანაცია!')
        if q_filled and q.get('ven_bleeding'):
            red_flags.append('ანამნეზში სისხლდენა ვარიკოზული კვანძიდან (ESVS 2022) — აცნობეთ ქირურგს პრიორიტეტული ჩარევისთვის!')

        if gsv_mm is not None and gsv_mm >= 15:
            amber_flags.append(f'საჩინო ვენის დიდი დიამეტრი ({gsv_mm} მმ >= 15 მმ) — გაითვალისწინეთ EHIT-ის პრევენცია და ენერგიის რეჟიმი.')
        if s.get('ven_epifascial'):
            amber_flags.append('დუპლექსით ეპიფასციური / კანთან ახლო ღერო (<5 მმ) — საჭიროა ჭარბი ტუმესცენტური ბალიში კანის დამწვრობის/პიგმენტაციის ასაცილებლად.')
        if s.get('ven_tortuous'):
            amber_flags.append('დუპლექსით ძლიერ დაკლაკნილი ღერო / სინექიები — გაითვალისწინეთ დამატებითი პუნქციის ან ფლებექტომიის საჭიროება.')
        if q_filled and ceap == 'C6' and not q.get('ven_skin_infection'):
            amber_flags.append('აქტიური ვენური წყლული (CEAP C6) — ESVS 2022 Class I-ით ადრეული EVLA აჩქარებს წყლულის შეხორცებას.')
        if q_filled and patient.side not in ('R', 'L', 'B'):
            amber_flags.append('მიუთითეთ საოპერაციო კიდური (მარჯვენა, მარცხენა ან ორმხრივი).')

        needs_abi_check = q_filled and (
            bool(q.get('claudication'))
            or ceap == 'C6'
            or (ceap == 'C5' and (bool(q.get('dm')) or (patient.age or 0) >= 65))
        )
        if min_abi is not None:
            if min_abi < 0.8:
                red_flags.append(
                    f'ქვემო კიდურის არტერიული უკმარისობა (ABI {min_abi} < 0.80) — კომპრესიული ტრიკოტაჟი და ვენური ჩარევა საშიშია! აცნობეთ ქირურგს.'
                )
            elif min_abi < 0.9:
                amber_flags.append(f'სასაზღვრო გოჯ-მხრის ინდექსი (ABI {min_abi}) — კომპრესიის ხარისხი შეათანხმეთ ქირურგთან.')
        if abi_non_compressible:
            if (min_tp is not None and min_tp < 50) or (min_tbi is not None and min_tbi < 0.50):
                red_flags.append(
                    f'არაკომპრესირებადი ABI ({max_abi}) + დაბალი Toe Pressure/TBI (TP {min_tp if min_tp is not None else "—"} / '
                    f'TBI {min_tbi if min_tbi is not None else "—"}) — არტერიული უკმარისობა, კომპრესიული ტრიკოტაჟი უკუნაჩვენებია!'
                )
            elif not tbi_comp_safe:
                amber_flags.append(
                    f'არაკომპრესირებადი არტერია (ABI {max_abi} > 1.40) — 2 კლასის კომპრესიული წინდის დანიშვნამდე '
                    'სამიზნე კიდურზე სავალდებულოა Toe Pressure (≥60 mmHg) ან TBI (≥0.70)!'
                )

        high_vte = q_filled and (
            bool(q.get('ven_dvt_history')) or bool(q.get('ven_thrombophilia'))
            or bool(q.get('ven_cancer_immob')) or bool(q.get('ven_acute_svt'))
            or ('hit_heparin' in allergy_chips)
        )
        mod_vte_count = sum([
            bool((patient.age or 0) >= 60),
            bool(bmi is not None and bmi >= 30),
            bool(q_filled and q.get('ven_hormones')),
            is_phleb,
            bool(q_filled and ceap in ('C5', 'C6')),
        ])
        needs_vte_prophylaxis = high_vte or (q_filled and (mod_vte_count >= 2 or caprini_score >= 5))
        if high_vte and not s.get('vte_prophylaxis_planned'):
            vte_agents = 'ფონდაპარინუქსი / DOAC — HIT: ჰეპარინი აკრძალულია!' if 'hit_heparin' in allergy_chips else 'LMWH / DOAC'
            amber_flags.append(
                f'მაღალი თრომბოემბოლიური რისკი (Caprini {caprini_score}) — შეათანხმეთ ინდივიდუალური თრომბოპროფილაქტიკა ({vte_agents}) ქირურგთან.'
            )

        needs_wound_care = q_filled and (ceap == 'C6' or bool(q.get('ven_skin_infection')))
        needs_ecg_venous = (
            is_phleb
            or ((patient.age or 0) >= 50)
            or (q_filled and (bool(q.get('htn')) or bool(q.get('cad')) or bool(q.get('dm'))))
            or bool(s.get('irregular_hr'))
        )

        if days > t_days and not patient.approved_at and not is_on_hold and is_active_case:
            amber_flags.append(f'წინასაოპერაციო მომზადებიდან გასულია {days} დღე — გადაამოწმეთ ანალიზების აქტუალობა.')

        if q_filled:
            gsv_part = f' · {int(gsv_mm)}მმ' if gsv_mm else ''
            key_badge = f'{ceap}{gsv_part}'
            key_badge_urgent = bool(q.get('ven_acute_svt') or q.get('ven_bleeding') or ceap == 'C6')
        elif gsv_mm:
            key_badge = f'{int(gsv_mm)} მმ'

    # Pre-compute urgency flag early so PBM & GI workups respect urgent vascular windows
    is_urgent_case = bool(
        (q_filled and bool(q.get('aaa_pain')))
        or (pathway == PreopPatient.Pathway.AAA_INFRA and (s.get('aaa_morphology') == 'inflammatory_mycotic'))
        or (aaa_mm is not None and aaa_mm >= 70)
        or is_symp_cea
        or is_clti_any
        or (min_comp_abi is not None and min_comp_abi < 0.4)
        or (min_tp is not None and min_tp < 30)
        or (min_tbi is not None and min_tbi < 0.30)
        or (is_venous and q_filled and (bool(q.get('ven_acute_svt')) or bool(q.get('ven_bleeding'))))
        or (days > t_days and not is_venous and not is_on_hold and is_active_case)
    )

    # 2. Vitals & Bilateral Arm BP (ESC 2022 / ESVS 2023)
    cardio_decision = patient.cardio_decision or ''
    cardio_cleared = (cardio_decision == 'cleared')
    anesth_cleared = bool(s.get('anesth_done'))
    anesth_plan_raw = s.get('anesthesia_plan') or 'auto'
    has_neuraxial_plan = (
        (anesth_plan_raw == 'neuraxial')
        or ('epidural' in anesth_lines_set)
        or (anesth_plan_raw == 'auto' and is_phleb)
    )

    bp_sys, bp_dia = _int(s.get('bp_sys'), lo=30, hi=320), _int(s.get('bp_dia'), lo=20, hi=220)
    bp_sys_l, bp_dia_l = _int(s.get('bp_sys_l'), lo=30, hi=320), _int(s.get('bp_dia_l'), lo=20, hi=220)
    eff_sys = max([v for v in (bp_sys, bp_sys_l) if v is not None], default=None)
    eff_dia = max([v for v in (bp_dia, bp_dia_l) if v is not None], default=None)
    bp_arm_diff = abs(bp_sys - bp_sys_l) if (bp_sys is not None and bp_sys_l is not None) else None

    if bp_arm_diff is not None and bp_arm_diff >= 20:
        higher_arm = 'მარჯვენა' if bp_sys >= bp_sys_l else 'მარცხენა'
        amber_flags.append(
            f'მკლავებს შორის წნევის ასიმეტრია ({bp_arm_diff} mmHg >= 20 mmHg) — საეჭვოა ლავიწქვეშა არტერიის სტენოზი! '
            f'არტერიული წნევის მონიტორინგი უნდა წარმოებდეს მაღალი წნევის ({higher_arm}) მკლავზე.'
        )

    hr, spo2 = _int(s.get('hr'), lo=15, hi=260), _int(s.get('spo2'), lo=30, hi=100)
    severe_htn = bool((eff_sys and eff_sys >= 180) or (eff_dia and eff_dia >= 110))
    if severe_htn:
        if pathway == PreopPatient.Pathway.CEA:
            red_flags.append(
                f'მაღალი არტერიული წნევა ({eff_sys or "?"}/{eff_dia or "?"} mmHg >= 180/110) — CEA-ზე ცერებრული ჰიპერპერფუზიის, '
                'სისხლჩაქცევისა და კისრის ჰემატომის უმაღლესი რისკი! სავალდებულოა სტაბილიზაცია ოპერაციამდე.'
            )
        else:
            red_flags.append(f'მაღალი არტერიული წნევა ({eff_sys or "?"}/{eff_dia or "?"} mmHg >= 180/110) — საჭიროა კორექცია ოპერაციამდე.')
    elif eff_sys and eff_sys < 90:
        if pathway == PreopPatient.Pathway.AAA_INFRA:
            red_flags.append(f'ჰიპოტენზია ({eff_sys}/{eff_dia or "?"} mmHg) — სასწრაფოდ გამოირიცხოს რუპტურა / შოკი!')
        else:
            red_flags.append(f'ჰიპოტენზია ({eff_sys}/{eff_dia or "?"} mmHg) — სასწრაფოდ შეფასდეს ჰემოდინამიკა!')
    elif (eff_sys and eff_sys >= 160) or (eff_dia and eff_dia >= 100):
        amber_flags.append(f'მომატებული არტერიული წნევა ({eff_sys or "?"}/{eff_dia or "?"} mmHg) — სასურველია ამბულატორიული კორექცია (ESC 2022: სტადია 2 ჰიპერტენზია <180/110 ოპერაციას არ აყოვნებს).')

    if hr and (hr > 110 or hr < 45):
        red_flags.append(f'პულსის კრიტიკული ცვლილება (HR {hr}/წთ) — საჭიროა ეკგ და კარდიოლოგის შეფასება.')
    elif s.get('irregular_hr'):
        if cardio_cleared:
            amber_flags.append(f'არარეგულარული რიტმი (HR {hr or "?"}/წთ) — შეფასებულია და დაშვებულია კარდიოლოგის მიერ ✓')
        else:
            red_flags.append(f'პულსის/რიტმის საყურადღებო ცვლილება (HR {hr or "?"}/წთ) — საჭიროა ეკგ და კარდიოლოგის შეფასება.')

    if spo2 is not None:
        if spo2 < 90:
            red_flags.append(f'მძიმე ჰიპოქსემია (SpO2 {spo2}% < 90%) — აცნობეთ ქირურგსა და ანესთეზიოლოგს!')
        elif spo2 < 94:
            amber_flags.append(f'დაქვეითებული სატურაცია (SpO2 {spo2}%).')

    mets_limb_only = q_filled and (not q.get('mets_ok', True)) and (q.get('mets_limitation') == 'limb')
    true_low_mets = q_filled and (not q.get('mets_ok', True)) and (q.get('mets_limitation') != 'limb')
    if q_filled and not q.get('mets_ok', True):
        if mets_limb_only:
            amber_flags.append('2 სართულზე ასვლა შეზღუდულია მხოლოდ ფეხის იშემიური ტკივილით (კოჭლობით) — კარდიო-რესპირატორული ქოშინის გარეშე.')
        else:
            amber_flags.append('დაქვეითებული ფუნქციური ამტანობა (< 4 METs — ქოშინი/დაღლა 2 სართულზე ასვლისას).')

    # 3. Hematology, Coagulation, Anemia Standing Reflex & Patient Blood Management (PBM)
    open_aortic = (
        pathway == PreopPatient.Pathway.ABF
        or (pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype != 'evar')
    )
    open_major = open_aortic or is_pad_bypass
    ferritin_val = _num(s.get('ferritin'), lo=1, hi=5000)
    tsat_val = _num(s.get('tsat'), lo=1, hi=100)
    hb_norm = 120 if patient.sex == 'F' else 130
    needs_anemia_panel = (hb_gl is not None and hb_gl < 115)
    anemia_panel_satisfied = bool(s.get('anemia_panel_done')) or ferritin_val is not None or tsat_val is not None
    pbm_iv_iron_indicated = bool(
        hb_gl is not None and hb_gl < hb_norm and (
            (ferritin_val is not None and ferritin_val < 30)
            or (ferritin_val is not None and ferritin_val < 100 and tsat_val is not None and tsat_val < 20)
        )
    )
    if hb_gl is not None:
        hb_poly_hi = 165 if patient.sex == 'F' else 185
        if needs_anemia_panel:
            reflex_orders.append({
                'key': 'anemia',
                'trigger': f'ანემია (Hb {hb_gl} გ/ლ < 115)',
                'action': 'ფერიტინი + შრატის რკინა/TSAT (%) + რეტიკულოციტები (და განავალი ფარულ სისხლდენაზე)',
                'done': anemia_panel_satisfied,
            })
        if pbm_iv_iron_indicated and not is_urgent_case:
            reflex_orders.append({
                'key': 'pbm_iron',
                'trigger': f'რკინადეფიციტური ანემია (Hb {hb_gl} გ/ლ, ფერიტინი {ferritin_val} ნგ/მლ{f", TSAT {tsat_val}%" if tsat_val is not None else ""})',
                'action': 'PBM პროტოკოლი (ESAIC / ESVS): ინტრავენური რკინის ინფუზია (ფერიკარბოქსიმალტოზა) ალოგენური ტრანსფუზიის შესამცირებლად',
                'done': bool(s.get('pbm_iv_iron_done')),
            })
        if hb_gl < 100:
            red_flags.append(f'მნიშვნელოვანი ანემია (Hb {hb_gl} გ/ლ < 100) — აცნობეთ ქირურგს (მიზეზის ძიება / PBM კორექცია).')
        elif hb_gl > hb_poly_hi:
            amber_flags.append(f'ერითროციტოზი / ჰემოკონცენტრაცია (Hb {hb_gl} გ/ლ > {hb_poly_hi}) — მომატებული თრომბოზული რისკი, საჭიროა ჰიდრატაცია და შეფასება.')
        elif hb_gl < hb_norm and not is_venous:
            amber_flags.append(f'მსუბუქი ანემია (Hb {hb_gl} გ/ლ) — ღია ოპერაციაზე სავალდებულოა ერითრომასის რეზერვი.')

    wbc = _num(s.get('wbc'), lo=0.1, hi=200.0)
    if wbc is not None and (wbc > 12.0 or wbc < 3.5):
        red_flags.append(f'ლეიკოციტების ცვლილება (WBC {wbc} x10^9/L) — გამოირიცხოს მწვავე ინფექცია ჩარევამდე!')

    plt = _int(s.get('plt'), lo=1, hi=2500)
    if plt is not None:
        if plt < 100:
            spinal_note = ' (სპინალური/ეპიდურული ჰემატომისა და სისხლდენის რისკი!)' if has_neuraxial_plan else ''
            red_flags.append(f'თრომბოციტოპენია (PLT {plt} x10^9/L < 100){spinal_note} — აცნობეთ ქირურგს.')
        elif plt < 150:
            amber_flags.append(f'ზომიერი თრომბოციტოპენია (PLT {plt} x10^9/L) — საყურადღებოა ანესთეზიოლოგისთვის.')
    elif hb_gl is not None and not is_venous:
        amber_flags.append('სისხლის საერთო ანალიზში შეიტანეთ თრომბოციტების (PLT) რაოდენობა (ჰეპარინიზაციისა და ნეირაქსიული უსაფრთხოებისთვის).')

    if open_major and hb_gl is not None and not s.get('blood_group'):
        amber_flags.append('ღია სისხლძარღვოვანი ოპერაციის წინ მიუთითეთ სისხლის ჯგუფი და რეზუსი (Type & Screen).')

    inr = _num(s.get('inr'), lo=0.4, hi=20.0)
    fib = _num(s.get('fibrinogen'), lo=0.2, hi=15.0)
    on_oral_anticoag = q_filled and (q.get('med_warfarin') or q.get('med_doac') or q.get('med_dabigatran'))
    if is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA and not has_neuraxial_plan and on_oral_anticoag and inr and inr <= 3.0 and not s.get('aptt_prolonged'):
        if inr > 1.35:
            amber_flags.append(f'INR {inr} ანტიკოაგულანტის ფონზე — EVLA-ს წინ დააზუსტეთ ქირურგთან (ESVS 2022-ით მოხსნა აუცილებელი არ არის, თუ INR <= 3.0).')
    elif (inr and inr > 1.35) or s.get('aptt_prolonged') or (fib is not None and fib < 1.8):
        red_flags.append('კოაგულოგრამის დარღვევა (INR > 1.35 / გახანგრძლივებული APTT / დაბალი ფიბრინოგენი) — აცნობეთ ქირურგს!')

    # 4. Renal Function, Dialysis, Unit Guard, Contrast Budget & Nephrology Standing Reflex
    uses_contrast = (patient.op_subtype in ('evar', 'tbd')) or is_pta
    max_contrast_ml = calc_max_contrast_ml(egfr, on_dialysis=on_dialysis) if uses_contrast else None
    co2_angio_advised = bool(uses_contrast and not on_dialysis and egfr is not None and egfr < 30)
    needs_nephro_panel = on_dialysis or (egfr is not None and egfr < 45)
    needs_hydration = (not on_dialysis) and (egfr is not None and egfr < 60) and uses_contrast
    if on_dialysis:
        reflex_orders.append({
            'key': 'nephro',
            'trigger': 'პროგრამული დიალიზი / თირკმლის ტერმინალური უკმარისობა',
            'action': 'დიალიზის გრაფიკის (ოპერაციის წინა დღეს უჰეპარინოდ), K+ კონტროლისა და მოცულობის შეთანხმება ნეფროლოგთან',
            'done': bool(s.get('nephro_panel_done')),
        })
        if not s.get('nephro_panel_done'):
            red_flags.append('პაციენტი იმყოფება დიალიზზე — სავალდებულოა დიალიზის სესიისა და კალიუმის შეთანხმება ნეფროლოგთან და ქირურგთან!')
    elif creat_unit_ambiguous:
        red_flags.append(
            f'კრეატინინის ერთეული საეჭვოა ({s.get("creat")} — 15–35 შუალედი)! '
            'მიუთითეთ ზუსტი ერთეული (µmol/L თუ mg/dL) eGFR / CrCl უსაფრთხო გამოთვლისთვის.'
        )
    elif s.get('creat') is not None and egfr is None:
        amber_flags.append('კრეატინინის კლირენსის / eGFR გამოსათვლელად მიუთითეთ პაციენტის ასაკი (12–120 წ.) და სქესი.')
    elif egfr is not None:
        if needs_nephro_panel:
            reflex_orders.append({
                'key': 'nephro',
                'trigger': f'დაქვეითებული თირკმლის ფუნქცია (eGFR {egfr} მლ/წთ < 45)',
                'action': 'შარდოვანა, Ca/P, შარდის ცილა + თირკმელების ექოსკოპია + ნეფროლოგის კონსულტაცია',
                'done': bool(s.get('nephro_panel_done')),
            })
        if needs_hydration:
            c_lim = f', მაქს. კონტრასტი ≤ {max_contrast_ml} მლ' if max_contrast_ml else ''
            reflex_orders.append({
                'key': 'hydration',
                'trigger': f'eGFR {egfr} მლ/წთ (< 60) კონტრასტული ჩარევის წინ',
                'action': f'იზოტონური ჰიდრატაციის პროტოკოლი (0.9% NaCl){c_lim} + მეტფორმინის შეჩერება',
                'done': bool(s.get('hydration_planned')),
            })
        if egfr < 30:
            co2_note = f' (მაქს. იოდის კონტრასტი ≤ {max_contrast_ml} მლ — განიხილეთ CO2 ანგიოგრაფია / IVUS!)' if co2_angio_advised else ''
            red_flags.append(f'თირკმლის მძიმე უკმარისობა (eGFR {egfr} მლ/წთ < 30){co2_note} — სავალდებულოა ნეფროლოგი და ტაქტიკის შეთანხმება ქირურგთან!')
        elif egfr < 60 and not is_venous and not needs_hydration:
            amber_flags.append(f'eGFR {egfr} მლ/წთ (CKD 3) — კონტრასტული კვლევისას საჭიროა იზოტონური ჰიდრატაცია.')

    k_val = _num(s.get('k'), lo=1.0, hi=12.0)
    if k_val is not None:
        if k_val < 3.3 or k_val >= 5.4:
            red_flags.append(f'კალიუმის კრიტიკული დონე (K+ {k_val} მმოლ/ლ) — არითმიის რისკი! აცნობეთ ექიმს.')
        elif k_val < 3.5 or k_val > 5.0:
            amber_flags.append(f'კალიუმის სასაზღვრო დონე (K+ {k_val} მმოლ/ლ) — სასურველია კორექცია ოპერაციამდე.')
    elif hb_gl is not None and not is_venous and (
        on_dialysis or (egfr is not None and egfr < 60) or ('acei_arb' in med_chips) or ('ace_arb' in med_chips) or ('diuretic' in med_chips)
    ):
        amber_flags.append('CKD / დიალიზი / ACEi-დიურეტიკის ფონზე შეიტანეთ შრატის კალიუმი (K+) ოპერაციამდე.')

    na_val = _num(s.get('na'), lo=95, hi=190)
    if na_val is not None and (na_val < 130 or na_val >= 150):
        red_flags.append(f'ნატრიუმის კრიტიკული დარღვევა (Na+ {na_val} მმოლ/ლ) — აცნობეთ ანესთეზიოლოგს.')

    # 5. Glycemia, HbA1c / Endocrinology Reflex, Thyroid & Nutrition
    hba1c = _num(s.get('hba1c'), lo=3.0, hi=25.0)
    needs_hba1c_reflex = (gl_mmol is not None and gl_mmol >= 7.0 and hba1c is None)
    needs_endo_reflex = (hba1c is not None and hba1c >= 8.5) or (gl_mmol is not None and gl_mmol >= 11.1)
    if needs_hba1c_reflex:
        reflex_orders.append({
            'key': 'hba1c',
            'trigger': f'უზმოდ გლუკოზა მომატებულია ({gl_mmol} მმოლ/ლ >= 7.0)',
            'action': 'გლიკირებული ჰემოგლობინის (HbA1c %) განსაზღვრა ექიმის ჩართვამდე',
            'done': hba1c is not None,
        })
    if needs_endo_reflex:
        reflex_orders.append({
            'key': 'endo',
            'trigger': f'მაღალი გლიკემია / HbA1c ({gl_mmol or "—"} მმოლ/ლ, HbA1c {hba1c or "—"}%)',
            'action': 'ენდოკრინოლოგის კონსულტაცია და გლიკემიის კორექცია ოპერაციამდე',
            'done': bool(s.get('endo_consult_done')),
        })
    if gl_mmol is not None and (gl_mmol >= 14.0 or gl_mmol < 3.9):
        red_flags.append(
            f'კრიტიკული გლიკემია (გლუკოზა {gl_mmol} მმოლ/ლ) — სასწრაფო კორექცია და ენდოკრინოლოგის ჩართვა!'
        )
    elif (q_filled and q.get('dm') and hba1c is None and not is_venous and not needs_hba1c_reflex):
        amber_flags.append('შაქრიანი დიაბეტი — გადაამოწმეთ HbA1c.')

    needs_tsh = q_filled and (not is_venous) and (bool(q.get('thyroid_disease')) or 'amiodarone' in med_chips)
    tsh_res = s.get('tsh_status') or 'pending'
    if needs_tsh:
        reflex_orders.append({
            'key': 'tsh',
            'trigger': 'ფარისებრი ჯირკვლის პათოლოგია / ამიოდარონი + იოდის კონტრასტი',
            'action': 'TSH და FT4 კონტროლი იოდ-ინდუცირებული თირეოტოქსიკოზის გამოსარიცხად',
            'done': tsh_res != 'pending',
        })
        if tsh_res == 'hyperthyroid':
            red_flags.append('ჰიპერთირეოზი (დაბალი TSH / მაღალი FT4) — იოდის კონტრასტი საშიშია თირეოტოქსიკური კრიზის გამო! აცნობეთ ექიმს.')

    if open_major and alb_gl is not None:
        if alb_gl < 30:
            red_flags.append(f'მძიმე ჰიპოალბუმინემია ({alb_gl} გ/ლ < 30) — ღია აორტული ოპერაციის მაღალი რისკი, აცნობეთ ქირურგს!')
        elif alb_gl < 35:
            amber_flags.append(f'დაბალი ალბუმინი ({alb_gl} გ/ლ < 35) — მომატებული ქირურგიული/ჭრილობის გართულების რისკი.')

    if s.get('liver_status') == '3x':
        red_flags.append('ღვიძლის ფერმენტების/ბილირუბინის მნიშვნელოვანი მატება (>=3x ULN) — აცნობეთ ექიმს.')
    if s.get('viral_positive'):
        amber_flags.append('დადებითი ვირუსული მარკერი (B/C/HIV/RW) — საოპერაციო ეპიდ-უსაფრთხოება და ღვიძლის სტატუსის გათვალისწინება.')

    # 6. Infection Control, IDSA 2019 ASB-Safe Urinalysis & Dental Sanitation Reflex
    has_synthetic_implant = (
        pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.CEA)
        or (is_pad_bypass and s.get('alt_vein_mapped') == 'no_autovein_prosthetic')
    )
    inf_chip_set = set(q.get('inf_chips') or [])
    # Only dysuria or unspecified active_infection (without isolated non-urinary chips) triggers UTI reflex!
    has_uti_symptoms = q_filled and (
        ('dysuria' in inf_chip_set)
        or (bool(q.get('active_infection')) and not inf_chip_set)
    )
    has_non_uti_active_inf = q_filled and bool(q.get('active_infection')) and bool(inf_chip_set - {'dysuria', 'dental_acute'})
    if has_non_uti_active_inf:
        inf_desc = chip_summary(list(inf_chip_set - {'dysuria', 'dental_acute'}), 'inf_chips', q.get('infection_text'))
        red_flags.append(f'აქტიური არასაშარდე ინფექციური კერა ({inf_desc}) — გეგმურ ჩარევამდე აცნობეთ ქირურგს სანაციისთვის!')

    urine_wbc = _int(s.get('urine_wbc'), lo=0, hi=2000)
    uti_suspected = (
        has_uti_symptoms
        or bool(s.get('urine_nitrite'))
        or (has_synthetic_implant and urine_wbc is not None and urine_wbc >= 25)
    )
    if uti_suspected:
        reflex_orders.append({
            'key': 'uti',
            'trigger': 'სიმპტომური საშარდე/აქტიური ინფექცია, ნიტრიტები+ ან გამოხატული პიურია (≥25)',
            'action': 'შარდის ბაქტერიოლოგია + ანტიბიოტიკოგრამა და ინფექციური კერის სანაცია',
            'done': bool(s.get('urine_culture_done')),
        })
        if not s.get('urine_culture_done'):
            if is_venous:
                red_flags.append('აქტიური ინფექციური კერა / საშარდე ინფექცია — გეგმური ვენური ჩარევის წინ აცნობეთ ქირურგს!')
            else:
                red_flags.append(
                    'საეჭვოა სიმპტომური საშარდე ან სხვა აქტიური ინფექცია (ნიტრიტები+ / დიზურია / ლეიკოციტურია ≥25) — '
                    'სინთეზური პროთეზის/პატჩის ინფიცირების მაღალი რისკი! აცნობეთ ქირურგს.'
                )
    elif urine_wbc is not None and urine_wbc > 10 and not is_venous:
        amber_flags.append(
            f'უსიმპტომო ლეიკოციტურია (შარდში ლეიკოციტები {urine_wbc} მხ/არ, ნიტრიტებისა და დიზურიის გარეშე) — '
            'IDSA 2019 გაიდლაინით უსიმპტომო ბაქტერიურიის რუტინული მკურნალობა ოპერაციას არ აყოვნებს.'
        )

    needs_dental = q_filled and has_synthetic_implant and (
        bool(q.get('dental_infection')) or 'dental_acute' in inf_chip_set
    )
    if needs_dental:
        antiseptic_lbl = 'პოვიდონ-იოდის სავლები (ქლორჰექსიდინზე ალერგია!)' if 'latex_chlorhex' in allergy_chips else 'ქლორჰექსიდინის დეკოლონიზაცია'
        reflex_orders.append({
            'key': 'dental',
            'trigger': 'კარიესული/ჩირქოვანი კბილი სინთეზური პროთეზის/პატჩის ჩადგმამდე',
            'action': f'სტომატოლოგის სანაცია (წერილობითი ცნობა) + {antiseptic_lbl}',
            'done': bool(s.get('dental_sanitation_done')),
        })

    # Stale Labs Expiry Guard (>14 days)
    labs_dt = _date(s.get('labs_date'))
    labs_date_ka = _fmt_ka_date(labs_dt)
    labs_age_days = (today - labs_dt).days if (labs_dt and labs_dt <= today) else None
    needs_stale_labs = bool(labs_age_days is not None and labs_age_days > 14 and not patient.approved_at and is_active_case)
    if needs_stale_labs:
        reflex_orders.append({
            'key': 'stale_labs',
            'trigger': f'ანალიზები ჩაბარებულია {labs_age_days} დღის წინ (>14 დღე)',
            'action': 'საკონტროლო სისხლის საერთო, კოაგულოგრამა და კრეატინინი განახლდეს ოპერაციამდე',
            'done': bool(s.get('stale_labs_refreshed')),
        })

    # 7. Pulmonary, Indication-Driven Chest X-Ray & ESAIC 2024 Spirometry
    cxr = s.get('cxr') or 'pending'
    if cxr == 'infiltrate':
        red_flags.append('გულმკერდის რენტგენზე ინფილტრაცია / გამონაჟონი / საყურადღებო კერა — აცნობეთ ქირურგს!')

    if is_venous or is_pta:
        needs_spirometry = is_phleb and (
            (q_filled and bool(q.get('pulm')))
            or (spo2 is not None and spo2 < 94)
            or cxr == 'emphysema'
        )
    else:
        needs_spirometry = (
            (q_filled and bool(q.get('pulm')))
            or (spo2 is not None and spo2 < 94)
            or cxr == 'emphysema'
            or (open_aortic and true_low_mets)
            or (s.get('spiro_fev1') is not None)
        )
    if q_filled and q.get('smoker') and not needs_spirometry and not is_venous:
        amber_flags.append('თამბაქოს მწეველი — რეკომენდებულია თამბაქოს შეწყვეტა და სუნთქვითი ვარჯიშები (ESAIC 2024: უსიმპტომო მწეველთან რუტინული სპირომეტრია ოპერაციას არ აყოვნებს).')

    spiro_fev1 = _int(s.get('spiro_fev1'), lo=10, hi=160)
    if needs_spirometry and spiro_fev1 is not None:
        if spiro_fev1 < 50:
            if anesth_cleared:
                amber_flags.append(f'სპირომეტრიაზე მძიმე დარღვევა (FEV1 {spiro_fev1}% < 50%) — შეფასებულია ანესთეზიოლოგის მიერ ✓')
            else:
                red_flags.append(
                    f'სპირომეტრიაზე მძიმე დარღვევა (FEV1 {spiro_fev1}% < 50%) — '
                    'მაღალი რესპირატორული რისკი! აცნობეთ ქირურგსა და ანესთეზიოლოგს.'
                )
        elif spiro_fev1 < 70:
            amber_flags.append(f'სპირომეტრიაზე ზომიერი დაქვეითება (FEV1 {spiro_fev1}%) — საყურადღებოა ანესთეზიოლოგისთვის.')

    # 8. Indication-Driven Carotid Duplex (AAA, ABF, PAD Bypass) & Smart PPI vs Gastroscopy
    needs_carotid_duplex = (
        pathway == PreopPatient.Pathway.CEA
        or (
            pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS)
            and q_filled and (bool(q.get('neuro')) or bool(q.get('carotid_bruit_symp')) or bool(s.get('carotid_symp')))
        )
    )
    if pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS) and max_car is not None:
        op_short = PATHWAY_SHORT.get(pathway, 'ოპერაცია')
        if max_car >= 70 or (max_car >= 50 and s.get('carotid_symp')):
            red_flags.append(
                f'ჰემოდინამიკურად მნიშვნელოვანი კაროტიდული სტენოზი ({max_car}%) — '
                f'აცნობეთ ქირურგს ეტაპობრივი ტაქტიკის (CEA <-> {op_short}) გადასაწყვეტად!'
            )
        elif max_car >= 50:
            amber_flags.append(f'ასიმპტომური კაროტიდული სტენოზი ({max_car}%) — ინტრაოპერაციული ჰიპოტენზიის თავიდან აცილება.')

    gastro_res = s.get('gastro_result') or 'pending'
    if is_venous or is_pta:
        needs_gastro = (
            (q_filled and bool(q.get('ulcer_active_symp')))
            or ((q_filled and bool(q.get('ulcer'))) and (hb_gl is not None and hb_gl < 100) and not is_urgent_case and not on_dialysis)
            or gastro_res != 'pending'
        )
    else:
        # Urgent vascular windows (symptomatic CEA / CLTI / giant AAA) and ESRD renal anemia do NOT delay surgery for routine EGD unless active GI bleed symptoms!
        needs_gastro = (
            (q_filled and bool(q.get('ulcer_active_symp')))
            or ((hb_gl is not None and hb_gl < 100) and not is_urgent_case and not on_dialysis)
            or gastro_res != 'pending'
        )
    if needs_gastro and gastro_res == 'acute_ulcer':
        red_flags.append('გასტროსკოპიაზე მწვავე წყლული / ეროზიული გასტრიტი — აცნობეთ ქირურგს.')

    needs_ppi = bool(q_filled and q.get('ulcer') and not needs_gastro and 'ppi' not in med_chips)
    if needs_ppi:
        reflex_orders.append({
            'key': 'ppi',
            'trigger': 'ანამნეზში რემისიული წყლულოვანი დაავადება (მწვავე სისხლდენის ნიშნების გარეშე)',
            'action': 'პერიოპერაციული გასტროპროტექცია — PPI (ომეპრაზოლი / პანტოპრაზოლი 40 მგ, ESC 2022 Class I) რუტინული ეგდს-ით დაყოვნების გარეშე',
            'done': bool(s.get('ppi_started')) or ('ppi' in med_chips),
        })

    # 9. Medications, Antibiotic Prophylaxis Switch, HIT, CIED, VOYAGER PAD & Contrast Allergy
    abx_prophylaxis_note = ''
    if 'hit_heparin' in allergy_chips:
        red_flags.append(
            'საეჭვოა ჰეპარინ-ინდუცირებული თრომბოციტოპენია (HIT) — არაფრაქციული ჰეპარინი და LMWH უკუნაჩვენებია! '
            'სასწრაფოდ შეათანხმეთ ალტერნატიული ანტიკოაგულაცია ქირურგთან!'
        )
    if 'beta_lactam' in allergy_chips:
        abx_prophylaxis_note = 'β-ლაქტამზე ალერგია: პროფილაქტიკური ცეფაზოლინის ნაცვლად გამოიყენება ვანკომიცინი ან კლინდამიცინი!'
        amber_flags.append(abx_prophylaxis_note)
    if 'latex_chlorhex' in allergy_chips:
        amber_flags.append('ალერგია ლატექსზე / ქლორჰექსიდინზე — გამოიყენეთ ულატექსო ხელთათმანები/კათეტერი და პოვიდონ-იოდის ანტისეპტიკი!')

    needs_contrast_premed = q_filled and bool(q.get('allergy_iodine')) and uses_contrast
    if q_filled and q.get('allergy_iodine') and not is_venous:
        if uses_contrast:
            reflex_orders.append({
                'key': 'contrast_premed',
                'trigger': 'იოდზე / კონტრასტზე ალერგია ანამნეზში',
                'action': 'კონტრასტის წინასწარი პრემედიკაციის სქემა (სტეროიდი + H1-ბლოკატორი) და შეთანხმება ქირურგთან',
                'done': bool(s.get('contrast_premed_done')),
            })
            if not s.get('contrast_premed_done'):
                amber_flags.append('ანამნეზში ალერგია იოდზე / კონტრასტზე — ჩართულია პრემედიკაციის რეფლექს-პროტოკოლი (სტეროიდი + ანტიჰისტამინი).')
        else:
            amber_flags.append('ანამნეზში ალერგია იოდზე / კონტრასტზე.')

    needs_cied = q_filled and bool(q.get('pacemaker'))
    cied_ok = bool(s.get('cied_checked')) or ((s.get('cied_plan') or 'pending') != 'pending')
    if needs_cied:
        reflex_orders.append({
            'key': 'cied',
            'trigger': 'იმპლანტირებული კარდიოსტიმულატორი / დეფიბრილატორი (CIED)',
            'action': 'მოწყობილობის პასპორტი/ბოლო შემოწმება, მაგნიტის რეჟიმი და ბიპოლარული კოაგულაციის დაგეგმვა კარდიოლოგ/ანესთეზიოლოგთან',
            'done': cied_ok,
        })

    med_schedule = build_med_schedule(patient, q, s, egfr, crcl=crcl) if q_filled else []
    clop_needs_hold = bool(q.get('med_clopidogrel') and not is_pta and not is_symp_cea)
    needs_med_hold = q_filled and (
        clop_needs_hold
        or bool(q.get('med_ticagrelor') and not is_pta)
        or bool(q.get('med_prasugrel'))
        or bool(q.get('med_doac'))
        or bool(q.get('med_dabigatran'))
        or bool(q.get('med_warfarin'))
        or ('cilostazol' in med_chips)
        or ('rivaroxaban_2_5' in med_chips)
        or bool(q.get('glp1'))
        or bool(q.get('sglt2'))
        or bool(q.get('dm_insulin'))
        or (not is_venous and bool(q.get('metformin')) and (
            open_major or uses_contrast or on_dialysis or (egfr is not None and egfr < 60)
        ))
    )

    has_full_oac = on_oral_anticoag
    has_dual_ap_intake = q_filled and bool(q.get('med_aspirin')) and bool(
        q.get('med_clopidogrel') or q.get('med_ticagrelor') or q.get('med_prasugrel')
    )
    if has_full_oac and has_dual_ap_intake and not q.get('recent_stent'):
        red_flags.append(
            'პაციენტი ერთდროულად იღებს ანტიკოაგულანტს (OAC) და ორ ანტიაგრეგანტს (სამმაგი ანტითრომბოზული თერაპია — სისხლდენის მაღალი რისკი)! '
            'ESC / ESVS გაიდლაინით გადაამოწმეთ ასპირინის მოხსნა და OAC + ერთ ანტიაგრეგანტზე გადაყვანა.'
        )

    post_at_raw = s.get('post_antithrombotic_plan') or 'auto'
    post_antithrombotic_label = ''
    voyager_safe = (
        (egfr is None or egfr >= 30)
        and (crcl is None or crcl >= 15)
        and not on_dialysis
        and not q.get('ulcer_active_symp')
        and not q.get('neuro')
        and (s.get('liver_status') != '3x')
        and not has_full_oac
    )
    alt_p2y12_lbl = 'ტიკაგრელორი' if 'clopidogrel_intol' in allergy_chips else 'კლოპიდოგრელი 75 მგ'
    if pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA):
        if post_at_raw != 'auto':
            post_antithrombotic_label = dict(POST_ANTITHROMBOTIC_CHOICES).get(post_at_raw, '')
            if post_at_raw in ('voyager', 'dapt') and has_full_oac:
                red_flags.append(
                    'არჩეულია პოსტოპ. VOYAGER / DAPT პაციენტთან, რომელიც მუდმივ ანტიკოაგულანტზეა (OAC) — სამმაგი თერაპიის სისხლდენის მაღალი რისკი! შეცვალეთ OAC + მონო-ანტიაგრეგანტით.'
                )
            elif post_at_raw == 'voyager' and not voyager_safe:
                red_flags.append(
                    'პოსტოპ. VOYAGER PAD (რივაროქსაბანი 2.5 მგ ×2) უკუნაჩვენებია/საშიშია (დიალიზი, eGFR <30, ანამნეზში ინსულტი, აქტიური წყლული ან ღვიძლის 3x დაზიანება) — შეარჩიეთ DAPT ან SAPT!'
                )
            elif post_at_raw in ('dapt', 'oac_sapt') and 'clopidogrel_intol' in allergy_chips:
                amber_flags.append('კლოპიდოგრელის აუტანლობის გამო პოსტოპ. სქემაში გამოიყენეთ ალტერნატიული P2Y12 (ტიკაგრელორი).')
        elif has_full_oac:
            post_antithrombotic_label = f'OAC + {alt_p2y12_lbl} (სამმაგი თერაპიის გარეშე — ESC/ESVS)'
        elif is_pta:
            if voyager_safe:
                post_antithrombotic_label = f'DAPT (ასპირინი + {alt_p2y12_lbl} 1–3 თვე) ან VOYAGER PAD (რივაროქსაბანი 2.5 მგ ×2 + ასპირინი)'
            else:
                post_antithrombotic_label = f'DAPT (ასპირინი + {alt_p2y12_lbl} 1–3 თვე; VOYAGER შეზღუდულია თირკმლის/ინსულტის/წყლულის გამო)'
        elif voyager_safe:
            post_antithrombotic_label = 'VOYAGER PAD (რივაროქსაბანი 2.5 მგ ×2 + ასპირინი 100 მგ — ACC/AHA 2024 Class I) ან SAPT'
        else:
            post_antithrombotic_label = f'მონო-ანტიაგრეგანტი ({alt_p2y12_lbl} ან ასპირინი 100 მგ)'

    has_acs_stent_12m = ('stent_6_12m' in cad_chips) and ('mi' in cad_chips)
    uninterrupted_p2y12 = q_filled and (
        bool(q.get('med_clopidogrel') or q.get('med_ticagrelor') or q.get('med_prasugrel'))
        and (bool(q.get('recent_stent')) or has_acs_stent_12m or is_pta or (is_symp_cea and not q.get('med_prasugrel')))
    )
    if has_neuraxial_plan and uninterrupted_p2y12:
        red_flags.append(
            'ნეირაქსიული ანესთეზია / ეპიდურული კათეტერი უკუნაჩვენებია უწყვეტი P2Y12 თერაპიის '
            '(კლოპიდოგრელი/ტიკაგრელორი/პრასუგრელი — სტენტი/DAPT/სიმპტ. CEA) ფონზე (ASRA 5th Ed — სპინალური ჰემატომის უმაღლესი რისკი)! '
            'შეცვალეთ გაუტკივარება ზოგადი/ადგილობრივი მეთოდით.'
        )
    if q_filled and q.get('recent_stent'):
        red_flags.append(
            'კორონარული სტენტი ჩადგმულია ბოლო 6 თვეში (DAPT) — თვითნებური მოხსნა საშიშია! '
            'შეათანხმეთ ანტიაგრეგანტული რეჟიმი და ოპერაციის ტაქტიკა ქირურგთან.'
        )
    elif q_filled and has_acs_stent_12m and not cardio_cleared:
        red_flags.append(
            'ინფარქტის შემდგომი კორონარული სტენტი 6–12 თვეში (ESC: სტანდარტული DAPT 12 თვეა!) — '
            'ანტიაგრეგანტის შეჩერებამდე სავალდებულოა კარდიოლოგისა და ქირურგის შეთანხმება!'
        )
    elif needs_med_hold and not s.get('med_hold_agreed'):
        if is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA and not has_neuraxial_plan and not q.get('sglt2') and not q.get('glp1') and not q.get('dm_insulin'):
            amber_flags.append('პაციენტი იღებს ანტიკოაგულანტს / ანტიაგრეგანტს — დააზუსტეთ ქირურგთან (ESVS 2022-ით EVLA შესაძლებელია მოუხსნელადაც).')
        elif has_neuraxial_plan:
            proc_word = 'ეპიდურული / სპინალური ანესთეზიისა და ფლებექტომიის' if is_phleb else 'ეპიდურული / სპინალური ანესთეზიის'
            amber_flags.append(
                f'პაციენტი იღებს ანტიკოაგულანტს / კლოპიდოგრელს / SGLT2 / ინსულინს — {proc_word} წინ '
                'სავალდებულოა შეჩერების დღის შეთანხმება (იხ. ASRA მედიკამენტების ცხრილი)!'
            )
        else:
            amber_flags.append('პაციენტი იღებს ანტიკოაგულანტს / P2Y12 / SGLT2 / GLP-1 / ინსულინს / მეტფორმინს — შეათანხმეთ მიღების/შეჩერების დღე ქირურგთან.')

    # 10. Smart Dual-Track Cardiac Biomarkers (NT-proBNP / hs-cTn OR In-House TTE) & ESC 2022 / CARP Audit
    lvef = _int(s.get('lvef'), lo=5, hi=95)
    nt_probnp = _int(s.get('nt_probnp'), lo=1, hi=50000)
    hs_ctn = _num(s.get('hs_ctn'), lo=0.1, hi=50000)
    ecg = s.get('ecg') or 'pending'

    if nt_probnp is not None and nt_probnp >= 125:
        if cardio_cleared:
            amber_flags.append(f'NT-proBNP {nt_probnp} პგ/მლ (≥125) — შეფასებულია და დაშვებულია კარდიოლოგის მიერ ✓')
        elif nt_probnp >= 300:
            red_flags.append(
                f'მაღალი NT-proBNP ({nt_probnp} პგ/მლ >= 300) — გულის უკმარისობისა და პერიოპერაციული MACE მაღალი რისკი! '
                'სავალდებულოა ექოკარდიოგრაფია და კარდიოლოგის შეფასება.'
            )
        else:
            amber_flags.append(f'მომატებული NT-proBNP ({nt_probnp} პგ/მლ >= 125) — ESC 2022 Class I-ით ნაჩვენებია ექოკარდიოგრაფია და კარდიოლოგი.')

    if hs_ctn is not None and hs_ctn >= 14:
        if cardio_cleared:
            amber_flags.append(f'საბაზისო hs-cTn {hs_ctn} ნგ/ლ — შეფასებულია კარდიოლოგის მიერ ✓')
        elif hs_ctn >= 52:
            red_flags.append(f'მნიშვნელოვნად მომატებული საბაზისო ტროპონინი (hs-cTn {hs_ctn} ნგ/ლ) — გამოირიცხოს მწვავე მიოკარდიუმის დაზიანება კარდიოლოგთან!')
        else:
            amber_flags.append(f'საბაზისო hs-cTn {hs_ctn} ნგ/ლ (>ULN) — დაფიქსირებულია პოსტოპერაციული MINS შედარებისთვის; შეაფასოს კარდიოლოგმა.')

    if (lvef is not None and lvef < 40) or s.get('echo_valve_issue') or ecg == 'ischemia_arrhythmia':
        if cardio_cleared:
            amber_flags.append('კარდიორისკი (LVEF < 40% / სარქვლოვანი პათოლოგია / ეკგ) შეფასებულია და დაშვებულია კარდიოლოგის მიერ ✓')
        else:
            red_flags.append('კარდიოლოგიური Red Flag (LVEF < 40% / მძიმე სარქვლოვანი მანკი / მწვავე ეკგ ცვლილება) — აცნობეთ ქირურგსა და კარდიოლოგს!')
    elif ecg == 'afib_controlled':
        if cardio_cleared:
            amber_flags.append('ნორმოსისტოლური მოციმციმე არითმია — შეფასებულია და დაშვებულია კარდიოლოგის მიერ ✓')
        else:
            amber_flags.append('ეკგ-ზე ნორმოსისტოლური მოციმციმე არითმია — საჭიროა კარდიოლოგის შეფასება.')
    elif lvef is not None and lvef < 50:
        amber_flags.append(f'ზომიერად დაქვეითებული განდევნის ფრაქცია (LVEF {lvef}%).')

    guideline_justifies_cag = (
        true_low_mets
        or (q_filled and bool(q.get('cad')))
        or (lvef is not None and lvef < 50)
        or (nt_probnp is not None and nt_probnp >= 300)
        or (hs_ctn is not None and hs_ctn >= 14)
        or bool(s.get('echo_valve_issue'))
        or ecg == 'ischemia_arrhythmia'
    )
    guideline_justifies_extra_cardio = (
        guideline_justifies_cag
        or needs_cied
        or (nt_probnp is not None and nt_probnp >= 125)
        or ecg == 'afib_controlled'
        or (hr is not None and (hr > 110 or hr < 45))
        or (pathway not in (PreopPatient.Pathway.CEA, PreopPatient.Pathway.VENOUS) and q_filled and bool(q.get('neuro')))
        or (not is_venous and (on_dialysis or (egfr is not None and egfr < 60)))
        or (not is_venous and q_filled and bool(q.get('dm_insulin')))
    )
    cardio_non_compliant = False
    if cardio_decision in ('stress', 'cag', 'pci'):
        if cardio_decision in ('cag', 'pci'):
            cardio_non_compliant = not guideline_justifies_cag
        else:
            cardio_non_compliant = not guideline_justifies_extra_cardio
        who_cardio = patient.cardiologist or 'კარდიოლოგი'
        audit_tag = ' [ESC 2022 Class III / CARP — დაბალი/სტაბილური რისკის პაციენტთან!]' if cardio_non_compliant else ''
        red_flags.append(
            f'კარდიოლოგმა ({who_cardio}) მოითხოვა {patient.get_cardio_decision_display()}{audit_tag} — '
            'დაყოვნებამდე ან სტენტირებამდე სავალდებულოა ქირურგთან შეთანხმება!'
        )

    # Smart Dual-Track: Open aortic repairs always require cardiac consult & echo/BNP;
    # CEA (stroke window!), EVAR, PAD Bypass, PTA & Venous require cardiology ONLY on clinical risk triggers (incl. BP >=180/110)!
    needs_cardio = (
        open_aortic
        or guideline_justifies_extra_cardio
        or bool(s.get('irregular_hr'))
        or severe_htn
        or bool(cardio_decision)
    )
    needs_echo_or_bnp = open_aortic or guideline_justifies_extra_cardio
    cardiac_screen_done = (
        lvef is not None
        or (nt_probnp is not None and nt_probnp < 125 and not s.get('echo_valve_issue'))
    )
    needs_cxr = (
        (q_filled and (bool(q.get('pulm')) or 'hf' in cad_chips or 'fever_resp' in inf_chip_set))
        or (spo2 is not None and spo2 < 94)
    )

    needs_anesth = (
        (not is_venous and not is_pta)
        or is_phleb
        or has_neuraxial_plan
        or (s.get('anesthesia_plan') in ('neuraxial', 'general', 'plexus'))
        or (is_pta and q_filled and bool(q.get('pta_cant_lie_flat')))
        or needs_cied
        or (q_filled and (
            bool(q.get('anesth_issue')) or bool(q.get('glp1'))
            or bool(q.get('ven_lidocaine_allergy'))
            or 'lidocaine' in allergy_chips or true_low_mets
        ))
        or bool(s.get('anesth_done'))
    )

    # Stage 1 baseline completeness (Rational Testing — NICE NG45 / ESC 2022 / IDSA 2019 / ESVS 2023–2024)
    renal_lab_present = on_dialysis or (s.get('creat') is not None and not creat_unit_ambiguous)
    target_limb_hemo_present = bool(scoped_abis or scoped_tps or scoped_tbis)
    if is_venous:
        is_lean_evla = (not is_phleb) and (not has_neuraxial_plan) and (anesth_plan_raw != 'general')
        ven_needs_creat = is_phleb or has_neuraxial_plan or bool(q.get('med_doac') or q.get('med_dabigatran'))
        ven_needs_inr = (not is_lean_evla) or on_oral_anticoag or (s.get('liver_status') in ('2x', '3x'))
        ven_needs_gl = (not is_lean_evla) or bool(q.get('dm')) or (bmi is not None and bmi >= 30) or ((patient.age or 0) >= 45)
        ven_labs_ok = (
            hb_gl is not None and plt is not None
            and (not ven_needs_inr or inr is not None)
            and (not ven_needs_gl or gl_mmol is not None)
            and (not ven_needs_creat or renal_lab_present)
        )
        ven_ecg_ok = (not needs_ecg_venous) or (ecg != 'pending')
        s1_core_done = ven_labs_ok and ven_ecg_ok and ((s.get('ven_duplex_status') or 'pending') != 'pending')
    elif is_pta:
        s1_core_done = (
            hb_gl is not None and plt is not None and inr is not None
            and renal_lab_present and gl_mmol is not None
            and ecg != 'pending'
            and target_limb_hemo_present
            and (s.get('pta_access_site') or 'pending') != 'pending'
        )
    else:
        needs_urin_s1 = has_uti_symptoms
        common_s1 = (
            hb_gl is not None and renal_lab_present and inr is not None
            and (not needs_urin_s1 or urine_wbc is not None)
            and (not needs_carotid_duplex or (car_r is not None and car_l is not None))
            and (not needs_cxr or cxr != 'pending')
            and ecg != 'pending'
            and (not needs_echo_or_bnp or cardiac_screen_done)
        )
        if pathway == PreopPatient.Pathway.CEA:
            s1_core_done = (
                common_s1
                and car_r is not None and car_l is not None
                and (s.get('cea_cta_status') or 'pending') != 'pending'
                and (not needs_brain_ct or (s.get('brain_imaging') or 'pending') != 'pending')
            )
        elif pathway == PreopPatient.Pathway.ABF:
            s1_core_done = (
                common_s1
                and (s.get('abf_clamp_zone') or 'pending') != 'pending'
                and (s.get('abf_outflow') or 'pending') != 'pending'
                and (abi_r is not None or abi_l is not None or tbi_measured)
            )
        elif is_pad_bypass:
            s1_core_done = (
                common_s1
                and (s.get('pad_runoff_status') or 'pending') != 'pending'
                and (s.get('pad_gsv_status') or 'pending') != 'pending'
                and target_limb_hemo_present
            )
        else:
            s1_core_done = common_s1 and (aaa_mm is not None)

    # Dynamic Checklist (Stage 1 + Stage 2 + Standing Reflex Orders)
    pw_short = PATHWAY_SHORT.get(pathway, 'AAA')
    tasks = [
        {'key': 'q', 'label': f'კითხვარების შევსება (ზოგადი + {pw_short})', 'done': q_filled},
        {
            'key': 's1',
            'label': (
                'საფეხური 1: ვენების დუპლექსი და საბაზისო ანალიზები' if is_venous
                else ('საფეხური 1: ანალიზები, eGFR, ეკგ, ABI და მიდგომა' if is_pta
                      else 'საფეხური 1: საბაზისო ანალიზები და ჩვენებითი კვლევები')
            ),
            'done': s1_core_done,
        },
    ]
    if needs_stale_labs:
        tasks.append({'key': 'stale_labs', 'label': 'რეფლექს-განახლება: ხანდაზმული ანალიზების (>14 დღე) გადაბარება', 'done': bool(s.get('stale_labs_refreshed'))})
    if needs_anemia_panel:
        tasks.append({'key': 'anemia', 'label': 'რეფლექს-პაკეტი (Hb < 115): ფერიტინი, შრატის რკინა/TSAT, რეტიკულოციტები', 'done': anemia_panel_satisfied})
    if pbm_iv_iron_indicated and not is_urgent_case:
        tasks.append({'key': 'pbm_iron', 'label': 'PBM პროტოკოლი (რკინადეფიციტი): ინტრავენური რკინის ინფუზია / შეთანხმება', 'done': bool(s.get('pbm_iv_iron_done'))})
    if needs_hba1c_reflex:
        tasks.append({'key': 'hba1c', 'label': 'რეფლექს-ანალიზი (გლუკოზა >= 7.0): HbA1c (%)', 'done': hba1c is not None})
    if needs_endo_reflex:
        tasks.append({'key': 'endo', 'label': 'რეფლექს-კონსულტაცია: ენდოკრინოლოგი (გლიკემიის კორექცია)', 'done': bool(s.get('endo_consult_done'))})
    if needs_nephro_panel:
        n_lbl = 'დიალიზის გრაფიკი (უჰეპარინო სესია) და K+ შეთანხმება ნეფროლოგთან' if on_dialysis else 'რეფლექს-პაკეტი (eGFR < 45): შარდოვანა, Ca/P, თირკმლის ექო და ნეფროლოგი'
        tasks.append({'key': 'nephro', 'label': n_lbl, 'done': bool(s.get('nephro_panel_done'))})
    if needs_hydration:
        c_tag = f' (მაქს. კონტრასტი ≤ {max_contrast_ml} მლ)' if max_contrast_ml else ''
        tasks.append({'key': 'hydration', 'label': f'კონტრასტ-ნეფროპროტექცია (eGFR < 60): იზოტონური ჰიდრატაცია{c_tag}', 'done': bool(s.get('hydration_planned'))})
    if needs_tsh:
        tasks.append({'key': 'tsh', 'label': 'რეფლექს-ანალიზი იოდის კონტრასტამდე: TSH და FT4', 'done': tsh_res != 'pending'})
    if needs_dental:
        tasks.append({'key': 'dental', 'label': 'სტომატოლოგის სანაცია პროთეზირებამდე (წერილობითი ცნობა)', 'done': bool(s.get('dental_sanitation_done'))})
    if needs_contrast_premed:
        tasks.append({'key': 'premed', 'label': 'იოდზე ალერგიის პრემედიკაციის სქემა (სტეროიდი + ანტიჰისტამინი)', 'done': bool(s.get('contrast_premed_done'))})
    if needs_cied:
        tasks.append({'key': 'cied', 'label': 'კარდიოსტიმულატორის / ICD შემოწმება (მაგნიტის რეჟიმი / ბიპოლარი)', 'done': cied_ok})
    if needs_tbi_check:
        tasks.append({'key': 'tbi', 'label': 'რეფლექს-დოპლერი (ABI > 1.40): თითის წნევა (Toe Pressure) / TBI', 'done': tbi_measured})
    if needs_alt_vein:
        tasks.append({'key': 'alt_vein', 'label': 'რეფლექს ექო-კარტირება: კონტრალატერალური GSV / SSV / ხელის ვენები', 'done': (s.get('alt_vein_mapped') or 'pending') != 'pending'})
    if pathway == PreopPatient.Pathway.CEA and needs_cea_antiplatelet:
        tasks.append({'key': 'cea_antiplatelet', 'label': 'CEA ანტიაგრეგანტი (ასპირინი ± კლოპიდოგრელი) დაწყებულია', 'done': bool(s.get('cea_antiplatelet_started'))})
    if pathway == PreopPatient.Pathway.CEA and needs_neuro:
        tasks.append({'key': 'neuro', 'label': 'ნევროლოგის წინასაოპერაციო კონსულტაცია (mRS / სტატუსი)', 'done': bool(s.get('neuro_consult_done'))})
    if pathway == PreopPatient.Pathway.CEA and needs_laryngoscopy:
        tasks.append({'key': 'laryng', 'label': 'ლარინგოსკოპია — სახმო იოგების შემოწმება (ENT)', 'done': (s.get('laryngoscopy_res') or 'pending') != 'pending'})
    if (is_venous or pathway == PreopPatient.Pathway.AAA_INFRA) and needs_abi_check:
        tasks.append({'key': 'abi', 'label': 'გოჯ-მხრის ინდექსი (ABI)', 'done': min_abi is not None})
    if is_venous and needs_vte_prophylaxis:
        vte_lbl = 'ფონდაპარინუქსი / DOAC (HIT!)' if 'hit_heparin' in allergy_chips else 'LMWH / DOAC'
        tasks.append({'key': 'vte', 'label': f'თრომბოპროფილაქტიკის სქემის შეთანხმება (Caprini {caprini_score} — {vte_lbl})', 'done': bool(s.get('vte_prophylaxis_planned'))})
    if needs_spirometry:
        tasks.append({'key': 'spiro', 'label': 'სპირომეტრია (FEV1%)', 'done': spiro_fev1 is not None})
    if needs_gastro:
        tasks.append({'key': 'gastro', 'label': 'გასტროსკოპია (ეგდს)', 'done': gastro_res != 'pending'})
    if needs_ppi:
        tasks.append({'key': 'ppi', 'label': 'გასტროპროტექცია (PPI: ომეპრაზოლი / პანტოპრაზოლი 40 მგ — ეგდს-ის გარეშე)', 'done': bool(s.get('ppi_started')) or ('ppi' in med_chips)})
    if uti_suspected:
        tasks.append({'key': 'uti', 'label': 'შარდის ბაქტერიოლოგია + ანტიბიოტიკოგრამა / სანაცია', 'done': bool(s.get('urine_culture_done'))})
    if (pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA) or is_venous) and needs_wound_care:
        w_lbl = 'ვენური წყლულის / კანის მოვლის შეთანხმება ქირურგთან' if is_venous else 'ტერფის ნაცხი / საზარდულის კანის სანაცია'
        tasks.append({'key': 'wound', 'label': w_lbl, 'done': bool(s.get('wound_care_done'))})
    if is_venous and (min_abi is None or min_abi >= 0.8):
        tbi_comp_ok = (not abi_non_compressible) or tbi_comp_safe
        tasks.append({'key': 'comp', 'label': '2 კლასის კომპრესიული წინდის / გეტრის ზომის შერჩევა', 'done': bool(s.get('compression_ready')) and tbi_comp_ok})
    if is_pta and q_filled:
        dapt_lbl = (
            f'ანტითრომბოზული სქემა OAC ფონზე: ანტიკოაგულანტი + {alt_p2y12_lbl} (სამმაგი თერაპიის გარეშე!)'
            if has_full_oac else
            f'ანტითრომბოზული მზაობა (DAPT: ასპირინი + {alt_p2y12_lbl} ან VOYAGER PAD)'
        )
        tasks.append({'key': 'dapt', 'label': dapt_lbl, 'done': bool(s.get('dapt_ready'))})
    if open_major:
        tasks.append({'key': 'blood', 'label': 'სისხლის კომპონენტების რეზერვის მომზადება', 'done': bool(s.get('blood_reserved'))})
    if pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype == 'evar':
        tasks.append({'key': 'sizing', 'label': 'EVAR სტენტ-გრაფტის ზომების დათვლა და მზაობა', 'done': bool(s.get('evar_sized'))})
    if needs_med_hold:
        tasks.append({'key': 'med_hold', 'label': 'მედიკამენტების მიღების/შეჩერების სქემის დადასტურება', 'done': bool(s.get('med_hold_agreed'))})
    if needs_cardio:
        tasks.append({
            'key': 'cardio',
            'label': f'კარდიოლოგის კონსულტაცია ({patient.cardiologist or "ლიკა ინჯია"})',
            'done': cardio_cleared,
        })
    if needs_anesth:
        tasks.append({
            'key': 'anesth',
            'label': 'ანესთეზიოლოგის კონსულტაცია',
            'done': anesth_cleared,
        })

    done_count = sum(1 for t in tasks if t['done'])
    all_tasks_done = (done_count == len(tasks))
    all_reflex_done = bool(reflex_orders) and all(r['done'] for r in reflex_orders)

    # 11. Stage P2 & P3: Structured Anesthesiology (ESAIC 2024 / RCoA / ASRA 5th Ed), Surgical Wait Guard, DoS & 30-Day VQI
    auto_asa = calc_auto_asa(patient, q, s, egfr=egfr, bmi=bmi, today=today)
    asa_raw = s.get('asa_class') if s.get('asa_class') in ('auto', 'I', 'II', 'III', 'IV') else 'auto'
    asa_roman = auto_asa if asa_raw == 'auto' else asa_raw
    effective_asa = f"ASA {asa_roman}{'-E' if s.get('asa_emergency') else ''}"

    mallampati = s.get('airway_mallampati') if s.get('airway_mallampati') in ('I_II', 'III', 'IV_difficult') else 'I_II'
    mallampati_label = dict(MALLAMPATI_CHOICES).get(mallampati, 'Mallampati I–II')
    difficult_airway_alert = (mallampati == 'IV_difficult') or (q_filled and bool(q.get('cea_neck_limited')) and mallampati == 'III')
    if mallampati == 'IV_difficult':
        amber_flags.append(
            'მოსალოდნელი რთული ინტუბაცია (Mallampati IV) — საოპერაციოში ვიდეოლარინგოსკოპისა და რთული სასუნთქი გზების ნაკრების მზადყოფნა!'
        )

    pulm_chip_set = set(q.get('pulm_chips') or [])
    stop_bang_auto = sum([
        bool(bmi is not None and bmi > 35),
        bool((patient.age or 0) > 50),
        bool(q_filled and q.get('htn')),
        bool(patient.sex == 'M'),
    ]) + (3 if 'osa' in pulm_chip_set else 0)
    auto_sb_tier = 'high' if ('osa' in pulm_chip_set or stop_bang_auto >= 5) else ('intermediate' if stop_bang_auto >= 3 else 'low')
    sb_raw = s.get('stop_bang_risk') if s.get('stop_bang_risk') in ('auto', 'low', 'intermediate', 'high') else 'auto'
    effective_stop_bang = auto_sb_tier if sb_raw == 'auto' else sb_raw
    stop_bang_label = {
        'low': 'დაბალი (0–2)',
        'intermediate': 'საშუალო (3–4)',
        'high': 'მაღალი OSA რისკი (≥5)',
    }[effective_stop_bang]
    if effective_stop_bang == 'high' and (not is_venous or is_phleb):
        amber_flags.append(
            'მაღალი ობსტრუქციული ძილის აპნოეს რისკი (STOP-Bang ≥5 / OSA) — ოპიოიდების დაზოგვა, პოსტოპ. პულსოქსიმეტრია და CPAP მზადყოფნა.'
        )

    if anesth_plan_raw != 'auto':
        effective_anesth_label = dict(ANESTH_PLAN_CHOICES).get(anesth_plan_raw, anesth_plan_raw)
    elif patient.op_subtype == PreopPatient.OpSubtype.EVLA:
        effective_anesth_label = 'ტუმესცენტური ადგილობრივი ანესთეზია'
    elif is_phleb:
        effective_anesth_label = 'სპინალური / ზოგადი ანესთეზია'
    elif is_pta:
        effective_anesth_label = 'ადგილობრივი ანესთეზია (± სედაცია)'
    elif pathway == PreopPatient.Pathway.CEA:
        effective_anesth_label = 'ზოგადი ან რეგიონული ბლოკადა (ცერებრული მონიტორინგით)'
    elif patient.op_subtype == 'evar' or is_pad_bypass:
        effective_anesth_label = 'სპინალურ-ეპიდურული ან ზოგადი ანესთეზია'
    else:
        effective_anesth_label = 'ზოგადი ენდოტრაქეული (± ეპიდურული ანალგეზია)'

    anesth_lines_txt = chip_summary(s.get('anesth_lines_chips'), 'anesth_lines_chips')
    cied_plan_raw = s.get('cied_plan') if s.get('cied_plan') in ('pending', 'bipolar_only', 'magnet_async', 'ep_reprogram') else 'pending'
    cied_plan_label = dict(CIED_PLAN_CHOICES).get(cied_plan_raw, '') if needs_cied and cied_plan_raw != 'pending' else ''
    needs_icu_bed = bool(
        open_aortic
        or asa_roman == 'IV'
        or (lvef is not None and lvef < 40)
        or (q_filled and bool(q.get('cea_crescendo')))
        or bool(anesth_lines_set & {'art_line', 'cvc', 'epidural'})
    )

    person_id = _clean_person_id(s.get('person_id'))
    birth_date_ka = _fmt_ka_date(s.get('birth_date'))
    op_dt = _date(s.get('planned_surgery_date'))
    planned_surgery_ka = _fmt_ka_date(op_dt)
    planned_surgery_short = _fmt_ka_date(op_dt, short=True)
    operated_date_ka = _fmt_ka_date(s.get('operated_date'))
    case_outcome_label = dict(CASE_OUTCOME_CHOICES).get(case_outcome, '')

    if op_dt and is_active_case:
        ref_start = patient.started_on or patient.visit_date or today
        wait_to_op = (op_dt - ref_start).days
        if pathway == PreopPatient.Pathway.CEA and is_symp_cea:
            ev_dt = _date(q.get('cea_event_date'))
            symp_to_op = (
                (op_dt - ev_dt).days if ev_dt
                else ((days_since_ev + (op_dt - today).days) if (days_since_ev is not None and op_dt >= today) else None)
            )
            if symp_to_op is not None and symp_to_op > 14:
                amber_flags.append(
                    f'დაგეგმილი CEA-ს თარიღი ({planned_surgery_ka}) სცდება სიმპტომიდან 14-დღიან ფანჯარას '
                    f'({symp_to_op} დღე — ESVS 2023 / NVR სამიზნე ≤14 დღე)! განიხილეთ უფრო ადრეული საოპერაციო სლოტი.'
                )
        elif pathway == PreopPatient.Pathway.AAA_INFRA and aaa_mm is not None:
            min_aaa_thresh = 50 if patient.sex == 'F' else 55
            if aaa_mm >= 70 and wait_to_op > 14:
                amber_flags.append(
                    f'გიგანტური AAA ({aaa_mm} მმ): დაგეგმილი ოპერაციის თარიღი ({planned_surgery_ka}) დაშორებულია {wait_to_op} დღით (>14 დღე — მაღალი რუპტურის რისკი)!'
                )
            elif aaa_mm >= min_aaa_thresh and wait_to_op > 56:
                amber_flags.append(
                    f'AAA ({aaa_mm} მმ): დაგეგმილი ოპერაციის მოლოდინი {wait_to_op} დღეა (UK NVR / ESVS სამიზნე ≤8 კვირა / 56 დღე).'
                )
        elif is_clti_any:
            clti_lim = 5 if (q_filled and q.get('abf_wet_infection')) else 14
            if wait_to_op > clti_lim:
                amber_flags.append(
                    f'CLTI პაციენტის დაგეგმილი ოპერაცია ({planned_surgery_ka}) დაშორებულია {wait_to_op} დღით (UK NVR სამიზნე ≤{clti_lim} დღე) — კიდურის დაკარგვის რისკი!'
                )

    if q_filled and q.get('glp1'):
        fasting_instruction = (
            'GLP-1 პროტოკოლი (Multi-Society 2024 / ESAIC 2025): გართულების გარეშე უმეტეს პაციენტთან პრეპარატი გრძელდება, '
            'სავალდებულოა ოპერაციამდე 24 სთ მხოლოდ სუფთა თხევადი დიეტა, მყარი საკვები შეწყდეს ≥8 სთ-ით ადრე, სუფთა წყალი — 2 სთ-ით ადრე '
            '(GI სიმპტომებისას — შეჩერების განხილვა და კუჭის ექო-სკრინინგი / RSI).'
        )
    else:
        fasting_instruction = (
            'ESAIC / ERAS უზმოდ ყოფნის წესი: მყარი საკვები შეწყდეს ოპერაციამდე 6 საათით ადრე · '
            'სუფთა გამჭვირვალე წყალი დასაშვებია ოპერაციამდე 2 საათით ადრე (დილის დაშვებული წამლები — ყლუპი წყლით).'
        )
    surveillance_plan = build_surveillance_plan(patient)

    # Compact allergy summary for Passport & WHO Sign-In safety strip
    allergy_bits = []
    if 'hit_heparin' in allergy_chips:
        allergy_bits.append('HIT — ჰეპარინი/LMWH აკრძალულია!')
    if 'beta_lactam' in allergy_chips:
        allergy_bits.append('β-ლაქტამი (ცეფაზოლინი არა!)')
    if q_filled and (q.get('ven_lidocaine_allergy') or 'lidocaine' in allergy_chips):
        allergy_bits.append('ლიდოკაინი / ადგილობრივი ანესთეტიკი!')
    if 'latex_chlorhex' in allergy_chips:
        allergy_bits.append('ლატექსი / ქლორჰექსიდინი!')
    if q_filled and q.get('allergy_iodine'):
        allergy_bits.append('იოდი / კონტრასტი!')
    if 'clopidogrel_intol' in allergy_chips:
        allergy_bits.append('კლოპიდოგრელის აუტანლობა')
    if q_filled and q.get('allergy_text'):
        allergy_bits.append(q['allergy_text'])
    elif q_filled and q.get('allergy_other') and not allergy_bits:
        allergy_bits.append('მედიკამენტური ალერგია')
    allergy_summary = '; '.join(allergy_bits)

    # Day-of-Surgery (DoS / WHO Sign-In) Gate — isolated from Stage 2 tasks so STATUS_READY is preserved
    dos_checks = [
        {
            'key': 'dos_identity_consent',
            'label': 'პაციენტის იდენტიფიკაცია (პირადი N / დაბ. თარიღი) და ხელმოწერილი ინფორმირებული თანხმობა',
            'done': bool(s.get('dos_identity_consent')),
        },
    ]
    if pathway not in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF):
        side_hint = {'R': 'მარჯვენა', 'L': 'მარცხენა', 'B': 'ორმხრივი'}.get(patient.side, 'მხარე მიუთითეთ!')
        dos_checks.append({
            'key': 'dos_site_marked',
            'label': f'საოპერაციო მხარის მარკირება კანზე ({side_hint}) — WHO Sign-In',
            'done': bool(s.get('dos_site_marked')) and (patient.side in ('R', 'L', 'B')),
        })
    dos_checks.append({
        'key': 'dos_fasting_confirmed',
        'label': 'უზმოდ ყოფნის რეჟიმი დაცულია (6 სთ საკვები / 2 სთ წყალი; GLP-1-ზე 24 სთ თხევადი)',
        'done': bool(s.get('dos_fasting_confirmed')),
    })
    if needs_med_hold or has_any_antithrombotic:
        dos_checks.append({
            'key': 'dos_med_hold_verified',
            'label': 'ანტიკოაგულანტის / P2Y12 / SGLT2 / ინსულინის რეჟიმი და დილის დაშვებული წამლების მიღება გადამოწმდა',
            'done': bool(s.get('dos_med_hold_verified')),
        })
    if not (is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA):
        cefaz_dose = '3 გ (≥120 კგ!)' if (weight_num is not None and weight_num >= 120) else '2 გ'
        abx_hint = 'ვანკომიცინი / კლინდამიცინი (β-ლაქტამზე ალერგია!)' if 'beta_lactam' in allergy_chips else f'ცეფაზოლინი {cefaz_dose} (ჭრილობამდე ≤60 წთ)'
        dos_checks.append({
            'key': 'dos_abx_ready',
            'label': f'ანტიბიოტიკოპროფილაქტიკა მზადაა: {abx_hint}',
            'done': bool(s.get('dos_abx_ready')),
        })
    if open_major or patient.op_subtype == 'evar' or has_synthetic_implant:
        dos_checks.append({
            'key': 'dos_blood_implant_ready',
            'label': 'ერითრომასის რეზერვი / სისხლის ჯგუფი და სისხლძარღვოვანი პროთეზი/სტენტ-გრაფტი საოპერაციოშია',
            'done': bool(s.get('dos_blood_implant_ready')),
        })
    if needs_icu_bed:
        dos_checks.append({
            'key': 'icu_bed_booked',
            'label': 'პოსტოპერაციული რეანიმაციის (ICU / HDU) საწოლი დაჯავშნილია და დადასტურებულია',
            'done': bool(s.get('icu_bed_booked')),
        })

    dos_sys = _int(s.get('dos_bp_sys'), lo=40, hi=300)
    dos_dia = _int(s.get('dos_bp_dia'), lo=20, hi=200)
    dos_gl = normalize_glucose_mmol(s.get('dos_glucose'))
    dos_inr = _num(s.get('dos_inr'), lo=0.5, hi=15)
    dos_warnings = list(hard_contraindications)
    if (dos_sys and dos_sys >= 180) or (dos_dia and dos_dia >= 110):
        dos_warnings.append(
            f'ოპერაციის დილის კრიტიკული ჰიპერტენზია ({dos_sys or "?"}/{dos_dia or "?"} mmHg >= 180/110) — გეგმური ინდუქცია საშიშია, საჭიროა სტაბილიზაცია!'
        )
    elif dos_sys and dos_sys < 90:
        dos_warnings.append(f'ოპერაციის დილის ჰიპოტენზია ({dos_sys}/{dos_dia or "?"} mmHg < 90) — სასწრაფოდ შეფასდეს ჰემოდინამიკა!')
    if dos_gl is not None and (dos_gl >= 14.0 or dos_gl < 3.9):
        dos_warnings.append(f'ოპერაციის დილის კრიტიკული გლიკემია ({dos_gl} მმოლ/ლ) — საჭიროა კორექცია საოპერაციოში შეყვანამდე!')
    elif dos_gl is not None and dos_gl > 10.0:
        amber_flags.append(f'ოპერაციის დილის ჰიპერგლიკემია ({dos_gl} მმოლ/ლ > 10.0) — სასურველია ინსულინით კორექცია პერიოპერაციული ინფექციის შესამცირებლად.')
    if dos_inr is not None:
        is_evla_only = is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA and not has_neuraxial_plan
        inr_lim = 3.0 if is_evla_only else (1.4 if has_neuraxial_plan else 1.5)
        if dos_inr > inr_lim:
            dos_warnings.append(f'ოპერაციის დილის INR ({dos_inr}) აჭარბებს უსაფრთხო ზღვარს (≤ {inr_lim}) — სისხლდენის / სპინალური ჰემატომის რისკი!')

    has_hard_contraindication = bool(hard_contraindications)
    dos_hold_danger = bool(dos_warnings)
    dos_done_count = sum(1 for c in dos_checks if c['done'])
    dos_total_count = len(dos_checks)
    dos_ready = (dos_done_count == dos_total_count) and not dos_hold_danger

    vqi_bundle_applicable = not is_venous
    vqi_bundle_done = (
        bool(s.get('vqi_discharge_antiplatelet') and s.get('vqi_discharge_statin'))
        if vqi_bundle_applicable else bool(s.get('vqi_surveillance_scheduled'))
    )
    vqi_30d_status = s.get('vqi_30d_status') if s.get('vqi_30d_status') in dict(VQI_30D_CHOICES) else 'pending'
    vqi_30d_label = dict(VQI_30D_CHOICES).get(vqi_30d_status, '')

    # 2D Triage State Machine: once 100% of preoperative tasks are complete, anatomical/clock urgency
    # moves from blocking red_flags to priority_tier + amber_flags so the patient reaches STATUS_READY!
    if all_tasks_done and priority_flags:
        prio_set = set(priority_flags)
        red_flags = [f for f in red_flags if f not in prio_set]
        for pf in priority_flags:
            amber_flags.insert(0, f'[სასწრაფო პრიორიტეტი] {pf}')

    amber_flags = list(dict.fromkeys(amber_flags))

    if is_urgent_case:
        priority_tier = 'urgent'
    elif (
        (aaa_mm is not None and aaa_mm >= 65)
        or (ipsi_car is not None and ipsi_car >= 90)
        or (is_venous and q_filled and q.get('ven_ceap') == 'C6')
        or (not is_venous and not is_on_hold and is_active_case and days >= max(4, t_days - 1))
    ):
        priority_tier = 'high'
    elif is_venous:
        priority_tier = 'low'
    else:
        priority_tier = 'moderate'

    current_sig = _alerts_signature(red_flags)
    alerts_acked = bool(patient.alerts_ack_at and s.get('alerts_ack_sig') == current_sig)
    has_unacked_red = bool(red_flags) and not alerts_acked

    # Safety: NEW unacknowledged red_flags (even pre-questionnaire or post-approval!) or hard contraindications ALWAYS force STATUS_ALERT!
    if has_unacked_red or has_hard_contraindication:
        status = STATUS_ALERT
    elif not q_filled:
        status = STATUS_NO_Q
    elif patient.approved_at or all_tasks_done:
        status = STATUS_READY
    else:
        status = STATUS_PROGRESS

    over_target = days > t_days and not patient.approved_at and not is_venous and not is_on_hold and is_active_case

    # 12. Pre-Pilot Academic Refinement: Quantitative Risk Scores, Specialist Briefings & Nurse Guided UX
    rcri = calc_rcri(patient, q, s, is_symp_cea=is_symp_cea)
    vsg_cri = calc_vsg_cri(patient, q, s)
    ariscat = calc_ariscat_pulm(patient, q, s, hb_gl=hb_gl)
    ihd_flag, chf_flag = _ihd_and_chf_flags(q, s)

    frailty_notes = []
    if alb_gl is not None and alb_gl < 35:
        frailty_notes.append(f'ჰიპოალბუმინემია ({alb_gl} გ/ლ)')
    if bmi is not None and bmi < 18.5:
        frailty_notes.append(f'დეფიციტური წონა (BMI {bmi})')
    elif bmi is not None and bmi >= 35:
        frailty_notes.append(f'სიმსუქნე II–III ხ. (BMI {bmi})')
    if q_filled and q.get('cea_disabling_stroke'):
        frailty_notes.append('შეზღუდული მობილობა (mRS ≥ 3)')
    if (patient.age or 0) >= 80:
        frailty_notes.append(f'ასაკი {patient.age} წ.')

    # Exact Stage 1 mandatory field keys + nurse jump-chips so nurses never hunt across 20+ inputs
    s1_required_fields = []
    missing_items = []
    if not q_filled:
        missing_items.append({'tab': 'q', 'field': 'mets_ok', 'label': 'კითხვარი'})
    elif q.get('filled_by') == 'პაციენტი (ბმულით)' and not q.get('nurse_verified'):
        missing_items.append({'tab': 'q', 'field': 'btn-verify-self', 'label': 'პაციენტის კითხვარის გადამოწმება'})

    def _req_s1(fname, missing_cond, lbl):
        s1_required_fields.append(fname)
        if missing_cond:
            missing_items.append({'tab': 's1', 'field': fname, 'label': lbl})

    if is_venous:
        is_lean_evla_ux = (not is_phleb) and (not has_neuraxial_plan) and (anesth_plan_raw != 'general')
        ven_needs_creat_ux = is_phleb or has_neuraxial_plan or bool(q.get('med_doac') or q.get('med_dabigatran'))
        ven_needs_inr_ux = (not is_lean_evla_ux) or on_oral_anticoag or (s.get('liver_status') in ('2x', '3x'))
        ven_needs_gl_ux = (not is_lean_evla_ux) or bool(q.get('dm')) or (bmi is not None and bmi >= 30) or ((patient.age or 0) >= 45)
        _req_s1('hb', hb_gl is None, 'Hb')
        _req_s1('plt', plt is None, 'PLT')
        if ven_needs_inr_ux:
            _req_s1('inr', inr is None, 'INR')
        if ven_needs_gl_ux:
            _req_s1('glucose', gl_mmol is None, 'გლუკოზა')
        if ven_needs_creat_ux and not on_dialysis:
            _req_s1('creat', not renal_lab_present, 'კრეატინინი')
        _req_s1('ven_duplex_status', (s.get('ven_duplex_status') or 'pending') == 'pending', 'ვენების დუპლექსი')
        if needs_ecg_venous:
            _req_s1('ecg', ecg == 'pending', 'ეკგ')
    elif is_pta:
        _req_s1('hb', hb_gl is None, 'Hb')
        _req_s1('plt', plt is None, 'PLT')
        _req_s1('inr', inr is None, 'INR')
        if not on_dialysis:
            _req_s1('creat', not renal_lab_present, 'კრეატინინი')
        _req_s1('glucose', gl_mmol is None, 'გლუკოზა')
        _req_s1('abi_r' if patient.side != 'L' else 'abi_l', not target_limb_hemo_present, 'ABI / TP')
        _req_s1('pta_access_site', (s.get('pta_access_site') or 'pending') == 'pending', 'პუნქციის მიდგომა')
        _req_s1('ecg', ecg == 'pending', 'ეკგ')
    else:
        _req_s1('hb', hb_gl is None, 'Hb')
        _req_s1('inr', inr is None, 'INR')
        if not on_dialysis:
            _req_s1('creat', not renal_lab_present, 'კრეატინინი')
        if has_uti_symptoms:
            _req_s1('urine_wbc', urine_wbc is None, 'შარდის ლეიკოციტები')
        _req_s1('ecg', ecg == 'pending', 'ეკგ')
        if needs_echo_or_bnp:
            s1_required_fields.extend(['lvef', 'nt_probnp'])
            if not cardiac_screen_done:
                missing_items.append({'tab': 's1', 'field': 'lvef', 'label': 'ექო (LVEF) ან NT-proBNP'})
        if needs_cxr:
            _req_s1('cxr', cxr == 'pending', 'რენტგენი')
        if pathway == PreopPatient.Pathway.CEA:
            _req_s1('carotid_r', car_r is None, 'მარჯვ. ICA %')
            _req_s1('carotid_l', car_l is None, 'მარცხ. ICA %')
            _req_s1('cea_cta_status', (s.get('cea_cta_status') or 'pending') == 'pending', 'კისრის CTA')
            if needs_brain_ct:
                _req_s1('brain_imaging', (s.get('brain_imaging') or 'pending') == 'pending', 'ტვინის CT/MRI')
        else:
            if needs_carotid_duplex:
                _req_s1('carotid_r', car_r is None, 'მარჯვ. საძილე')
                _req_s1('carotid_l', car_l is None, 'მარცხ. საძილე')
            if pathway == PreopPatient.Pathway.ABF:
                _req_s1('abi_r', (abi_r is None and abi_l is None and not tbi_measured), 'ABI')
                s1_required_fields.append('abi_l')
                _req_s1('abf_clamp_zone', (s.get('abf_clamp_zone') or 'pending') == 'pending', 'აორტის საკლამპე ზონა')
                _req_s1('abf_outflow', (s.get('abf_outflow') or 'pending') == 'pending', 'ბარძაყის Outflow')
            elif is_pad_bypass:
                _req_s1('abi_r' if patient.side != 'L' else 'abi_l', not target_limb_hemo_present, 'ABI / TP')
                _req_s1('pad_runoff_status', (s.get('pad_runoff_status') or 'pending') == 'pending', 'Runoff კალაპოტი')
                _req_s1('pad_gsv_status', (s.get('pad_gsv_status') or 'pending') == 'pending', 'GSV კარტირება')
            else:
                _req_s1('aaa_mm', aaa_mm is None, 'AAA დიამეტრი (მმ)')

    s2_field_map = {
        'stale_labs': ('stale_labs_refreshed', 'ანალიზების განახლება'),
        'anemia': ('ferritin', 'ფერიტინი / TSAT'),
        'pbm_iron': ('pbm_iv_iron_done', 'IV რკინა (PBM)'),
        'hba1c': ('hba1c', 'HbA1c'),
        'endo': ('endo_consult_done', 'ენდოკრინოლოგი'),
        'nephro': ('nephro_panel_done', 'ნეფროლოგი / დიალიზი'),
        'hydration': ('hydration_planned', 'ჰიდრატაციის გეგმა'),
        'tsh': ('tsh_status', 'TSH / FT4'),
        'dental': ('dental_sanitation_done', 'სტომატოლოგი'),
        'premed': ('contrast_premed_done', 'იოდის პრემედიკაცია'),
        'cied': ('cied_checked', 'CIED შემოწმება'),
        'tbi': ('tp_r' if patient.side != 'L' else 'tp_l', 'Toe Pressure / TBI'),
        'alt_vein': ('alt_vein_mapped', 'ალტ. ვენის კარტირება'),
        'cea_antiplatelet': ('cea_antiplatelet_started', 'CEA ანტიაგრეგანტი'),
        'neuro': ('neuro_consult_done', 'ნევროლოგი'),
        'laryng': ('laryngoscopy_res', 'ლარინგოსკოპია'),
        'abi': ('abi_r', 'საბაზისო ABI'),
        'vte': ('vte_prophylaxis_planned', 'VTE პროფილაქტიკა'),
        'spiro': ('spiro_fev1', 'სპირომეტრია'),
        'gastro': ('gastro_result', 'გასტროსკოპია'),
        'ppi': ('ppi_started', 'PPI გასტროპროტექცია'),
        'uti': ('urine_culture_done', 'შარდის ნათესი'),
        'wound': ('wound_care_done', 'ჭრილობის/კანის სანაცია'),
        'comp': ('compression_ready', 'კომპრესიული წინდა'),
        'dapt': ('dapt_ready', 'DAPT / ანტითრომბოზული'),
        'blood': ('blood_reserved', 'სისხლის რეზერვი'),
        'sizing': ('evar_sized', 'EVAR ზომების დათვლა'),
        'decol': ('decolonization_ready', 'ანტისეპტიკური შხაპი'),
        'med_hold': ('med_hold_agreed', 'წამლების სქემის შეთანხმება'),
        'cardio': ('cardio_decision', 'კარდიოლოგი'),
        'anesth': ('anesth_done', 'ანესთეზიოლოგი'),
    }
    for t_obj in tasks:
        tk = t_obj['key']
        if tk in ('q', 's1') or t_obj['done']:
            continue
        fld_name, short_lbl = s2_field_map.get(tk, ('', t_obj['label']))
        missing_items.append({'tab': 's2', 'field': fld_name, 'label': short_lbl})

    if has_hard_contraindication:
        next_step_hint = 'უკუჩვენება — შეაჩერეთ!'
    elif not q_filled:
        next_step_hint = 'აკლია: კითხვარი'
    elif missing_items:
        top_lbls = ', '.join(m['label'] for m in missing_items[:2])
        more_cnt = len(missing_items) - 2
        next_step_hint = f'აკლია: {top_lbls}' + (f' (+{more_cnt})' if more_cnt > 0 else '')
    elif has_unacked_red:
        next_step_hint = 'საჭიროებს ქირურგის გაცნობას'
    elif not patient.approved_at:
        next_step_hint = 'მზადაა ქირურგის დასტურისთვის'
    else:
        next_step_hint = '✓ დადასტურებულია'

    self_fill_highlights = build_self_fill_highlights(q) if q.get('filled_by') == 'პაციენტი (ბმულით)' else []

    side_badge = {
        'R': 'მარჯვენა (R)',
        'L': 'მარცხენა (L)',
        'B': 'ორმხრივი (B)',
    }.get(patient.side or '', 'აორტა / ცენტრალური' if pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF) else 'მხარე დასაზუსტებელია')

    if has_hard_contraindication or dos_hold_danger:
        clearance_seal = {
            'code': 'stop',
            'badge': 'STOP — დაშვება დაბლოკილია',
            'detail': 'ფიქსირდება აბსოლუტური კლინიკური უკუჩვენება ან საოპერაციო დილის საშიში მაჩვენებელი!',
        }
    elif status == STATUS_READY and all_tasks_done:
        clearance_seal = {
            'code': 'go',
            'badge': 'GO — უსაფრთხოდ მზადაა ოპერაციისთვის',
            'detail': 'ყველა პროტოკოლური კვლევა, რეფლექს-ორდერი და კონსულტაცია შესრულებულია.',
        }
    elif patient.approved_at and not has_unacked_red:
        clearance_seal = {
            'code': 'conditional',
            'badge': 'GO (პირობითი) — დაშვებულია ქირურგის მიერ',
            'detail': f'ქირურგმა დაადასტურა; საოპერაციომდე გადაამოწმეთ დარჩენილი პუნქტები ({done_count}/{len(tasks)}).',
        }
    else:
        clearance_seal = {
            'code': 'pending',
            'badge': 'მომზადების პროცესში',
            'detail': f'შესრულებულია {done_count}/{len(tasks)} პროტოკოლური დავალება — საჭიროებს დასრულებას დაშვებამდე.',
        }

    # Specialist Surgical & Anatomical Briefing
    if pathway == PreopPatient.Pathway.CEA:
        cea_cta_lbl = {
            'concordant': 'ტიპური ბიფურკაცია, დუპლექსთან შესაბამისი',
            'high_calcified': 'მაღალი ბიფურკაცია (C2) / მძიმე კალციფიკაცია / კინკინგი (!)',
            'pending': 'CTA შესატანია',
        }.get(s.get('cea_cta_status') or 'pending', 'ზუსტდება')
        br_lbl = {
            'normal_small': 'სისხლჩაქცევის გარეშე / მცირე კერა',
            'large_or_hemorrhage': 'ფართო ინფარქტი (>1/3 MCA) ან ჰემორაგია — უკუჩვენება!',
            'pending': 'შესატანია' if needs_brain_ct else 'არ იყო ნაჩვენები',
        }.get(s.get('brain_imaging') or 'pending', 'ზუსტდება')
        shunt_note = (
            'ინტრაოპერაციული შუნტის (Shunt) სავალდებულო მზადყოფნა (კონტრალატ. სტენოზი/ოკლუზია ≥70% ან გადატანილი ინსულტი)!'
            if ((contra_car is not None and contra_car >= 70) or (q_filled and q.get('neuro')) or is_symp_cea)
            else 'სტანდარტული ცერებრული მონიტორინგი / სელექციური შუნტირება'
        )
        anat_summary = (
            f"NASCET სტენოზი: იფსილატერალური {ipsi_car if ipsi_car is not None else '—'}% / "
            f"კონტრალატერალური {contra_car if contra_car is not None else '—'}% · კისრის CTA: {cea_cta_lbl}"
        )
        tactical_note = f'ტვინის CT/MRI: {br_lbl} · ცერებრული დაცვა: {shunt_note}'
    elif pathway == PreopPatient.Pathway.ABF:
        clamp_lbl = {
            'infrarenal': 'სუფთა ინფრარენალური საკლამპე ზონა',
            'juxtarenal_calcified': 'იუქსტარენალური ოკლუზია / მძიმე კალციფიკაცია (სუპრარენალური კლამპის მზადყოფნა!)',
            'pending': 'CTA საკლამპე ზონა შესატანია',
        }.get(s.get('abf_clamp_zone') or 'pending', 'ზუსტდება')
        out_lbl = {
            'patent': 'CFA და Profunda ორმხრივ გამავალია',
            'profunda_stenosis': 'Profunda სტენოზი — დაგეგმეთ პროფუნდოპლასტიკა!',
            'poor_outflow': 'მძიმე დისტალური კალაპოტი (GSV კარტირება / კომბინირებული შუნტი!)',
            'pending': 'Outflow შესატანია',
        }.get(s.get('abf_outflow') or 'pending', 'ზუსტდება')
        mes_lbl = {
            'normal': 'SMA გამავალია',
            'ima_dominant_iia_occ': 'დომინანტური IMA / ორმხრივი IIA ოკლუზია — IMA რეიმპლანტაციის საკითხი!',
            'sma_stenosis': 'SMA სტენოზი/ოკლუზია — მსხვილი ნაწლავის იშემიის მაღალი რისკი!',
        }.get(s.get('abf_mesenteric') or 'normal', 'SMA გამავალია')
        anat_summary = f"საკლამპე ზონა: {clamp_lbl} · Outflow: {out_lbl}"
        tactical_note = f"ვისცერული/მენჯის კოლატერალები: {mes_lbl} · ABI R {abi_r if abi_r is not None else '—'} / L {abi_l if abi_l is not None else '—'}"
    elif is_pad_bypass:
        run_lbl = {
            'popliteal_patent': 'მუხლქვეშა არტერია + ≥2 წვივის არტერია გამავალია',
            'tibial_single': 'ერთადერთი გამავალი წვივის/ტერფის არტერია (აუცილებელია აუტოვენა ≥3.0 მმ!)',
            'desert_foot': 'Desert Foot — მძიმე დისტალური ოკლუზია (!)',
            'pending': 'Runoff შესატანია',
        }.get(s.get('pad_runoff_status') or 'pending', 'ზუსტდება')
        gsv_lbl = {
            'adequate': f"იფსილატერალური GSV ვარგისია ({pad_gsv_mm if pad_gsv_mm is not None else '≥3.0'} მმ)",
            'inadequate': f"იფსილატერალური GSV უვარგისია (ალტ. ვენა: {s.get('alt_vein_mapped') or 'შესაფასებელია'})",
            'pending': 'GSV კარტირება შესატანია',
        }.get(s.get('pad_gsv_status') or 'pending', 'ზუსტდება')
        anat_summary = f"Runoff კალაპოტი: {run_lbl}"
        tactical_note = f"კონდუიტი: {gsv_lbl} · სამიზნე ჰემოდინამიკა: ABI min {min_abi if min_abi is not None else '—'}" + (f", TP min {min_tp} mmHg" if min_tp is not None else '')
    elif is_pta:
        seg_lbl = {
            'iliac': 'თეძოს არტერიები (Iliac)',
            'femoropopliteal': 'ბარძაყ-მუხლქვეშა სეგმენტი (SFA/Popliteal)',
            'infrapopliteal': 'წვივის/ტერფის არტერიები (BTK)',
            'multilevel': 'მრავალდონიანი დაზიანება',
        }.get(s.get('pta_lesion_site') or 'femoropopliteal', 'SFA/Popliteal')
        acc_lbl = {
            'cfa_clean': 'საერთო ბარძაყის არტერია (CFA) თავისუფალია პუნქციისთვის',
            'cfa_calcified_scar': 'CFA კალციფიცირებული/ნაწიბუროვანია — განიხილეთ კონტრალატერალური ან რადიალური/ბრაქიალური მიდგომა!',
            'pending': 'პუნქციის ზონა შესაფასებელია',
        }.get(s.get('pta_access_site') or 'pending', 'ზუსტდება')
        anat_summary = f"სამიზნე სეგმენტი: {seg_lbl} · ABI min {min_abi if min_abi is not None else '—'}" + (f" · TP min {min_tp} mmHg" if min_tp is not None else '')
        tactical_note = f"არტერიული მიდგომა: {acc_lbl}"
    elif is_venous:
        dup_lbl = {
            'deep_patent_reflux': 'ღრმა ვენები გამავალია, ზედაპირული რეფლუქსი დადასტურებულია',
            'acute_dvt': 'მწვავე DVT — ოპერაცია უკუნაჩვენებია!',
            'deep_obstruction': 'ღრმა ვენების ოკლუზია — აბლაცია/ფლებექტომია უკუნაჩვენებია!',
            'superficial_thrombus': 'მწვავე აღმავალი თრომბოფლებიტი (SFJ/SPJ საყურადღებოა!)',
            'pending': 'დუპლექსი შესატანია',
        }.get(s.get('ven_duplex_status') or 'pending', 'ზუსტდება')
        vein_pool = {
            'gsv': 'GSV', 'ssv': 'SSV', 'gsv_ssv': 'GSV + SSV', 'aagsv_trib': 'AAGSV / შენაკადები / პერფორანტები',
        }.get(s.get('ven_target_vein') or 'gsv', 'GSV')
        anat_bits = []
        if s.get('ven_epifascial'):
            anat_bits.append('ეპიფასციური ღერო (<5 მმ — ჭარბი ტუმესცენცია კანის დამწვრობის პრევენციისთვის!)')
        if s.get('ven_tortuous'):
            anat_bits.append('დაკლაკნილი ღერო / სინექიები (ორმაგი პუნქციის მზადყოფნა)')
        if s.get('ven_perforator'):
            anat_bits.append('ინკომპეტენტური პერფორანტი ≥3.5 მმ')
        anat_summary = f"CEAP {q.get('ven_ceap') or 'C2'} · აუზი: {vein_pool}" + (f" ({s.get('ven_gsv_mm')} მმ)" if s.get('ven_gsv_mm') is not None else '') + f" · დუპლექსი: {dup_lbl}"
        tactical_note = '; '.join(anat_bits) if anat_bits else 'სტანდარტული ენდოვენური / ქირურგიული ანატომია'
    else:
        morph_map = dict(AAA_MORPHOLOGY_CHOICES)
        neck_map = dict(AAA_NECK_CHOICES)
        pop_map = dict(POPLITEAL_SCREEN_CHOICES)
        anat_summary = (
            f"ინფრარენალური AAA: {aaa_mm if aaa_mm is not None else '—'} მმ · "
            f"{morph_map.get(s.get('aaa_morphology') or 'fusiform', 'ფუზიფორმული')}"
        )
        neck_part = f"პროქს. ყელი: {neck_map.get(s.get('aaa_neck_status') or 'pending', '—')} · " if patient.op_subtype in ('evar', 'tbd') else ''
        rv_part = ' · რეტროაორტული/ცირკუმაორტული მარცხენა თირკმლის ვენა (!)' if s.get('aaa_renal_vein_anom') else ''
        tactical_note = f"{neck_part}მუხლქვეშა სკრინინგი: {pop_map.get(s.get('popliteal_screen_status') or 'pending', '—')}{rv_part}"

    wt_num = _num(s.get('weight_kg'), lo=25, hi=260)
    if is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA and not (
        q_filled and (q.get('ven_ceap') == 'C6' or q.get('ven_acute_svt') or q.get('ven_skin_infection'))
    ):
        abx_regimen = 'რუტინული ანტიბიოტიკოპროფილაქტიკა სტანდარტულ EVLA-ზე არ ესაჭიროება (ESVS 2022)'
    elif 'beta_lactam' in allergy_chips:
        gram_neg_addon = (
            '+ აზტრეონამი 2 გ ან ციპროფლოქსაცინი 400 მგ IV საზარდულის მიდგომისას — eGFR <30-ზე გენტამიცინს მოერიდეთ!'
            if (egfr is not None and egfr < 30 and not on_dialysis)
            else '+ გენტამიცინი 5 მგ/კგ IV საზარდულის მიდგომისას'
        )
        abx_regimen = (
            'β-ლაქტამური ალერგია (ცეფაზოლინი აკრძალულია!) → ვანკომიცინი 15–20 მგ/კგ IV (60–90 წთ-ით ადრე ნელი ინფუზიით) '
            f'ან კლინდამიცინი 900 მგ IV ({gram_neg_addon})'
        )
    elif wt_num is not None and wt_num >= 120:
        abx_regimen = 'ცეფაზოლინი 3 გ IV (წონა ≥ 120 კგ) განაკვეთამდე 30–60 წთ-ით ადრე (რე-დოზირება ყოველ 4 სთ-ში ან >1500 მლ სისხლის დანაკარგზე)'
    else:
        abx_regimen = 'ცეფაზოლინი 2 გ IV განაკვეთამდე 30–60 წთ-ით ადრე (რე-დოზირება ყოველ 4 სთ-ში ან >1500 მლ სისხლის დანაკარგზე)'

    if 'hit_heparin' in allergy_chips:
        heparin_plan = 'HIT ანამნეზში! არაფრაქციული ჰეპარინი (UFH) და LMWH კატეგორიულად აკრძალულია → ბივალირუდინი ან არგატრობანი (პოსტოპ. ფონდაპარინუქსი/DOAC)!'
    elif pathway == PreopPatient.Pathway.CEA and (
        s.get('brain_imaging') == 'large_or_hemorrhage' or (q_filled and q.get('cea_thrombolysis'))
    ):
        heparin_plan = 'ყურადღება! ტვინის ჰემორაგია / ფართო ინფარქტი ან თრომბოლიზისი ≤6 დღეში — ნაადრევი სისტემური ჰეპარინიზაცია საშიშია ჰემორაგიული ტრანსფორმაციის გამო (ESVS 2023)!'
    elif is_venous:
        heparin_plan = f'ინტრაოპერაციული UFH არ ესაჭიროება · VTE პროფილაქტიკა: Caprini {caprini_score} ქ. ({caprini_tier}) — {caprini_advice}'
    else:
        plt_s = f'{plt} ×10^9/L' if plt is not None else 'შესატანია!'
        heparin_plan = f'არაფრაქციული ჰეპარინი (UFH) 70–100 სე/კგ IV კლამპირებამდე/ჩარევამდე (სამიზნე ACT > 200–250 წმ · საბაზისო PLT: {plt_s})'

    contrast_plan = ''
    if max_contrast_ml is not None:
        contrast_plan = f'მაქს. იოდის კონტრასტის ბიუჯეტი (Gurm): ≤ {max_contrast_ml} მლ'
        if co2_angio_advised:
            contrast_plan += ' · eGFR < 30: უპირატესია CO2 ანგიოგრაფია / IVUS + იზოტონური ჰიდრატაცია!'
        elif needs_hydration:
            contrast_plan += ' + პერიპროცედურული 0.9% NaCl ჰიდრატაცია'
    elif q_filled and q.get('allergy_iodine') and not is_venous:
        contrast_plan = 'იოდზე ალერგია — სავალდებულო პრემედიკაცია (სტეროიდი + H1-ბლოკატორი) კონტრასტის გამოყენებისას!'

    surgical_brief = {
        'side_badge': side_badge,
        'anatomy_summary': anat_summary,
        'tactical_note': tactical_note,
        'abx_regimen': abx_regimen,
        'heparin_plan': heparin_plan,
        'contrast_plan': contrast_plan,
        'postop_antithrombotic': post_antithrombotic_label or (
            'ასპირინი (ან კლოპიდოგრელი) უწყვეტად + მაღალი ინტენსივობის სტატინი (LDL <1.4 მმოლ/ლ — ESVS Class I)'
            if not is_venous else caprini_advice
        ),
        'surveillance_plan': surveillance_plan,
    }

    # Specialist Anesthetic & Hemodynamic Plan
    neuraxial_blockers = []
    if has_neuraxial_plan:
        if plt is not None and plt < 100:
            neuraxial_blockers.append(f'PLT {plt} < 100')
        elif plt is None:
            neuraxial_blockers.append('PLT უცნობია')
        eff_inr_nx = dos_inr if dos_inr is not None else inr
        if eff_inr_nx is not None and eff_inr_nx > 1.4:
            neuraxial_blockers.append(f'INR {eff_inr_nx} > 1.4')
        elif eff_inr_nx is None:
            neuraxial_blockers.append('INR უცნობია')
        if s.get('aptt_prolonged'):
            neuraxial_blockers.append('APTT გახანგრძლივებულია')
        if uninterrupted_p2y12:
            neuraxial_blockers.append('უწყვეტი P2Y12/DAPT — ნეირაქსიული ბლოკადა უკუნაჩვენებია!')
        elif q_filled and (q.get('med_doac') or q.get('med_dabigatran') or q.get('med_warfarin') or q.get('med_clopidogrel') or q.get('med_ticagrelor') or q.get('med_prasugrel')):
            if not (s.get('med_hold_agreed') or s.get('dos_med_hold_verified')):
                neuraxial_blockers.append('ASRA წამლის შეჩერების ინტერვალი დასადასტურებელია')
        if (q_filled and (q.get('med_doac') or q.get('med_dabigatran'))) and ((crcl is not None and crcl < 30) or (egfr is not None and egfr < 30) or on_dialysis):
            neuraxial_blockers.append('CrCl/eGFR < 30 ანტიკოაგულანტზე — ნეირაქსიული ბლოკადა მოერიდეთ!')
        neuraxial_gate = (
            'საყურადღებო/შეზღუდულია (ASRA 5th Ed): ' + ', '.join(neuraxial_blockers)
            if neuraxial_blockers else
            f'ASRA 5th Ed კრიტერიუმები დაცულია (INR {eff_inr_nx}, PLT {plt}) ✓'
        )
    else:
        neuraxial_gate = 'ნეირაქსიული ბლოკადა/ეპიდურული კათეტერი არ იგეგმება'

    arm_note = ''
    if bp_arm_diff is not None and bp_arm_diff >= 20:
        hi_arm = 'მარჯვენა' if (bp_sys or 0) >= (bp_sys_l or 0) else 'მარცხენა'
        arm_note = f' [წნევის მანჟეტი / A-line სავალდებულოა {hi_arm} მკლავზე — ΔBP {bp_arm_diff} mmHg!]'

    if pathway == PreopPatient.Pathway.CEA:
        bp_corridor = (
            'კლამპირებისას: შეინარჩუნეთ საბაზისო SBP (+0..20%, MAP ≥ 85–90 mmHg) კოლატერალური პერფუზიისთვის · '
            'რეპერფუზიის შემდეგ: მკაცრად SBP < 140–150 mmHg (ჰიპერპერფუზიის სინდრომისა და ჰემატომის პრევენცია — ESVS 2023)!' + arm_note
        )
    elif open_aortic:
        bp_corridor = (
            'MAP ≥ 65–75 mmHg (კორონარული/საძილე სტენოზისას ≥ 75–80 mmHg); აორტის კლამპირების წინ afterload-ის კონტროლი, '
            'დეკლამპირების წინ ინფუზიური მზადყოფნა და ვაზოპრესორი (declamping hypotension პრევენცია)!' + arm_note
        )
    elif pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype == 'evar':
        bp_corridor = 'MAP ≥ 65–75 mmHg; სტენტ-გრაფტის გაშლისას კონტროლირებადი SBP 90–110 mmHg ზუსტი იმპლანტაციისთვის.' + arm_note
    elif is_pad_bypass or is_pta:
        bp_corridor = 'MAP ≥ 70–80 mmHg (ინტრაოპერაციული ჰიპოტენზიისა და რეკონსტრუქციის ადრეული თრომბოზის პრევენცია).' + arm_note
    else:
        bp_corridor = 'სტაბილური ნორმოტენზია' + (f' · ტუმესცენტური ლიდოკაინი (ადრენალინით) ≤ {lidocaine_max_mg} მგ' if lidocaine_max_mg is not None else '') + arm_note

    high_ischemic_rbc = ihd_flag or (pathway == PreopPatient.Pathway.CEA) or is_clti_any
    rbc_thresh = 'Hb ≥ 80–90 გ/ლ (გიდ / ცერებრული / კრიტიკული იშემიის ზღვარი)' if high_ischemic_rbc else 'Hb ≥ 70–80 გ/ლ (რესტრიქციული PBM ზღვარი)'
    bg_lbl = s.get('blood_group') or 'დაუდგენელია'
    if open_major:
        res_lbl = 'ერითრომასის რეზერვი მზადაა ✓' if s.get('blood_reserved') else 'ერითრომასის რეზერვი მოსამზადებელია!'
        cs_lbl = ' · Cell Saver + TXA მზადაა ✓' if s.get('cell_saver_ready') else (' · განიხილეთ Cell Saver + TXA' if open_aortic else '')
        transfusion_pbm = f'ჯგუფი: {bg_lbl} · სამიზნე ტრიგერი: {rbc_thresh} · {res_lbl}{cs_lbl}'
    else:
        transfusion_pbm = f'ჯგუფი: {bg_lbl} · ტრანსფუზიული ტრიგერი საჭიროებისას: {rbc_thresh}'

    airway_bits = [mallampati_label, f'STOP-Bang: {stop_bang_label}']
    if q_filled and q.get('cea_neck_limited'):
        airway_bits.append('კისრის გაშლა შეზღუდულია!')
    if q_filled and q.get('loose_teeth'):
        airway_bits.append('მორყეული კბილები / პროთეზი')
    if (s.get('laryngoscopy_res') or 'pending') == 'palsy':
        airway_bits.append('სახმო იოგის პარეზი (!)')

    anesthetic_brief = {
        'modality': f'{effective_asa} · {effective_anesth_label}',
        'neuraxial_gate': neuraxial_gate,
        'airway_summary': ' · '.join(airway_bits),
        'bp_corridor': bp_corridor,
        'lines_and_cied': (
            (anesth_lines_txt or ('სტანდარტული არაინვაზიური მონიტორინგი (ECG, SpO2, NIBP)' if is_venous or is_pta else 'A-line / ინვაზიური მონიტორინგი ქირურგიული მასშტაბით'))
            + (f' · CIED: {cied_plan_label}' if cied_plan_label else '')
        ),
        'transfusion_pbm': transfusion_pbm,
        'glycemic_target': (
            'პერიოპერაციული გლიკემიური კორიდორი 5.5–10.0 მმოლ/ლ (ჰიპოგლიკემიის <3.9 და >10.0 თავიდან აცილება — ESC/ESAIC)'
            if (q_filled and (q.get('dm') or q.get('dm_insulin') or (gl_mmol is not None and gl_mmol >= 7.0)))
            else 'სტანდარტული ნორმოგლიკემია (<10.0 მმოლ/ლ)'
        ),
        'disposition': (
            ('პოსტოპ. რეანიმაცია / HDU (დაჯავშნილია ✓)' if s.get('icu_bed_booked') else 'პოსტოპ. რეანიმაცია / HDU — საწოლი დასაჯავშნია!')
            if needs_icu_bed else
            ('დღის სტაციონარი / ამბულატორიული გაწერა ადრეული აქტივაციით' if (is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA) else 'სისხლძარღვთა ქირურგიის სტაციონარული პალატა')
        ),
    }

    return {
        'pathway': pathway,
        'pathway_short': pw_short,
        'side_badge': side_badge,
        'rcri': rcri,
        'vsg_cri': vsg_cri,
        'ariscat': ariscat,
        'frailty_notes': frailty_notes,
        's1_required_fields': s1_required_fields,
        'missing_items': missing_items,
        'next_step_hint': next_step_hint,
        'self_fill_highlights': self_fill_highlights,
        'clearance_seal': clearance_seal,
        'surgical_brief': surgical_brief,
        'anesthetic_brief': anesthetic_brief,
        'is_venous': is_venous,
        'is_phleb': is_phleb,
        'is_pta': is_pta,
        'is_pad_bypass': is_pad_bypass,
        'is_on_hold': is_on_hold,
        'is_urgent_case': is_urgent_case,
        'is_symp_cea': is_symp_cea,
        'open_aortic': open_aortic,
        'has_synthetic_implant': has_synthetic_implant,
        'has_full_oac': has_full_oac,
        'has_neuraxial_plan': has_neuraxial_plan,
        'has_hard_contraindication': has_hard_contraindication,
        'cea_high_surg_risk': cea_high_surg_risk,
        'post_antithrombotic_label': post_antithrombotic_label,
        'lidocaine_max_mg': lidocaine_max_mg,
        'max_contrast_ml': max_contrast_ml,
        'co2_angio_advised': co2_angio_advised,
        'pbm_iv_iron_indicated': pbm_iv_iron_indicated,
        'nt_probnp': nt_probnp,
        'hs_ctn': hs_ctn,
        'tsat': tsat_val,
        'ldl': ldl_val,
        'person_id': person_id,
        'birth_date_ka': birth_date_ka,
        'labs_date_ka': labs_date_ka,
        'planned_surgery_ka': planned_surgery_ka,
        'planned_surgery_short': planned_surgery_short,
        'operated_date_ka': operated_date_ka,
        'allergy_summary': allergy_summary,
        'auto_asa': auto_asa,
        'asa_roman': asa_roman,
        'effective_asa': effective_asa,
        'mallampati_label': mallampati_label,
        'difficult_airway_alert': difficult_airway_alert,
        'stop_bang_auto': stop_bang_auto,
        'effective_stop_bang': effective_stop_bang,
        'stop_bang_label': stop_bang_label,
        'effective_anesth_label': effective_anesth_label,
        'anesth_lines_txt': anesth_lines_txt,
        'cied_plan_label': cied_plan_label,
        'needs_icu_bed': needs_icu_bed,
        'fasting_instruction': fasting_instruction,
        'surveillance_plan': surveillance_plan,
        'dos_checks': dos_checks,
        'dos_warnings': dos_warnings,
        'dos_hold_danger': dos_hold_danger,
        'dos_done_count': dos_done_count,
        'dos_total_count': dos_total_count,
        'dos_ready': dos_ready,
        'dos_gl_mmol': dos_gl,
        'case_outcome': case_outcome,
        'case_outcome_label': case_outcome_label,
        'vqi_bundle_applicable': vqi_bundle_applicable,
        'vqi_bundle_done': vqi_bundle_done,
        'vqi_30d_status': vqi_30d_status,
        'vqi_30d_label': vqi_30d_label,
        'status': status,
        'status_label': STATUS_LABELS[status],
        'workup_ready': bool((patient.approved_at or all_tasks_done) and not has_hard_contraindication),
        'priority_tier': priority_tier,
        'priority_label': PRIORITY_LABELS[priority_tier],
        'days_elapsed': days,
        'target_days': t_days,
        'over_target_days': over_target,
        'over_7_days': over_target,
        'key_badge': key_badge,
        'key_badge_urgent': key_badge_urgent,
        'red_flags': red_flags,
        'amber_flags': amber_flags,
        'reflex_orders': reflex_orders,
        'all_reflex_done': all_reflex_done,
        'med_schedule': med_schedule,
        'abx_prophylaxis_note': abx_prophylaxis_note,
        'caprini_score': caprini_score,
        'caprini_tier': caprini_tier,
        'caprini_advice': caprini_advice,
        'bp_arm_diff': bp_arm_diff,
        'labs_age_days': labs_age_days,
        'alerts_sig': current_sig,
        'alerts_acked': alerts_acked,
        'has_unacked_red': has_unacked_red,
        'egfr': egfr,
        'crcl': crcl,
        'creat_unit_ambiguous': creat_unit_ambiguous,
        'bmi': bmi,
        'hb_gl': hb_gl,
        'gl_mmol': gl_mmol,
        'alb_gl': alb_gl,
        'aaa_mm': aaa_mm,
        'cta_months': cta_months,
        'days_since_ev': days_since_ev,
        'ipsi_car': ipsi_car,
        'contra_car': contra_car,
        'min_abi': min_abi,
        'max_abi': max_abi,
        'min_tp': min_tp,
        'min_tbi': min_tbi,
        'abi_non_compressible': abi_non_compressible,
        'tbi_measured': tbi_measured,
        'tbi_comp_safe': tbi_comp_safe,
        'pad_gsv_mm': pad_gsv_mm,
        'needs_anemia_panel': needs_anemia_panel,
        'needs_hba1c_reflex': needs_hba1c_reflex,
        'needs_endo_reflex': needs_endo_reflex,
        'needs_nephro_panel': needs_nephro_panel,
        'needs_hydration': needs_hydration,
        'needs_tsh': needs_tsh,
        'needs_dental': needs_dental,
        'needs_contrast_premed': needs_contrast_premed,
        'needs_cied': needs_cied,
        'needs_tbi_check': needs_tbi_check,
        'needs_cea_antiplatelet': needs_cea_antiplatelet,
        'needs_alt_vein': needs_alt_vein,
        'needs_stale_labs': needs_stale_labs,
        'needs_spirometry': needs_spirometry,
        'needs_gastro': needs_gastro,
        'needs_ppi': needs_ppi,
        'needs_carotid_duplex': needs_carotid_duplex,
        'needs_cxr': needs_cxr,
        'needs_echo_or_bnp': needs_echo_or_bnp,
        'uti_suspected': uti_suspected,
        'needs_med_hold': needs_med_hold,
        'needs_brain_ct': needs_brain_ct,
        'needs_neuro': needs_neuro,
        'needs_laryngoscopy': needs_laryngoscopy,
        'needs_wound_care': needs_wound_care,
        'needs_abi_check': needs_abi_check,
        'needs_vte_prophylaxis': needs_vte_prophylaxis,
        'needs_ecg_venous': needs_ecg_venous,
        'needs_cardio': needs_cardio,
        'needs_anesth': needs_anesth,
        'needs_blood': open_major,
        'guideline_justifies_extra_cardio': guideline_justifies_extra_cardio,
        'cardio_non_compliant': cardio_non_compliant,
        's1_core_done': s1_core_done,
        'tasks': tasks,
        'done_count': done_count,
        'total_count': len(tasks),
        'ecg_label': ECG_LABELS.get(ecg, ecg),
        'cxr_label': CXR_LABELS.get(cxr, cxr),
        'q': q,
        's': s,
    }


def generate_anamnesis(patient, ev=None):
    """Build a concise, structured Georgian preoperative anamnesis across all 6 vascular pathways."""
    ev = ev or evaluate(patient)
    q, s = ev['q'], ev['s']
    if not q.get('filled'):
        return ''

    pathway = ev['pathway']
    sex_label = 'ქალი' if patient.sex == 'F' else ('მამაკაცი' if patient.sex == 'M' else 'პაციენტი')
    age_part = f'{patient.age} წ. ' if patient.age else ''
    surgeon_str = f' (ქირურგი: {person_name(patient.surgeon)})' if patient.surgeon else ''

    abdo_txt = chip_summary(q.get('abdo_chips'), 'abdo_chips', q.get('prev_abdo_text'))
    vasc_txt = chip_summary(q.get('vasc_chips'), 'vasc_chips', q.get('abf_prior_vasc_text'))
    ulcer_txt = chip_summary(q.get('ulcer_chips'), 'ulcer_chips', q.get('abf_ulcer_text'))

    lines = []
    if pathway == PreopPatient.Pathway.CEA:
        side_word = {'R': 'მარჯვენა ', 'L': 'მარცხენა '}.get(patient.side, '')
        kind_word = 'სიმპტომური სტენოზის გამო' if patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP else 'ასიმპტომური სტენოზის გამო'
        lines.append(f'{age_part}{sex_label}, დაგეგმილია {side_word}კაროტიდული ენდარტერექტომია ({kind_word}){surgeon_str}.')

        if patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP or q.get('cea_symp_event'):
            ev_txt = chip_summary(q.get('cea_event_chips'), 'cea_event_chips', q.get('cea_event_text')) or 'TIA / ამავროზი / მცირე იშემიური ინსულტი'
            live_ev_d = ev.get('days_since_ev')
            d_ago = f', ბოლო ეპიზოდი {live_ev_d} დღის წინ' if live_ev_d is not None else ''
            extra_neuro = []
            if q.get('cea_crescendo'):
                extra_neuro.append('აღინიშნება მერყევი/განმეორებითი დეფიციტი ბოლო 24–48 სთ-ში!')
            if q.get('cea_thrombolysis'):
                extra_neuro.append('ჩატარებულია თრომბოლიზისი ბოლო 6 დღეში!')
            if q.get('cea_disabling_stroke'):
                extra_neuro.append('ნარჩენი დეფიციტი mRS >= 3')
            extra_str = f" ({'; '.join(extra_neuro)})" if extra_neuro else ''
            lines.append(f'ნევროლოგიური სტატუსი: სიმპტომური ({ev_txt}{d_ago}){extra_str}.')
        else:
            lines.append('ნევროლოგიური სტატუსი: ასიმპტომურია, ბოლო 6 თვეში TIA-ს, ამავროზსა და ინსულტს უარყოფს.')

        cta_map = {
            'concordant': 'დუპლექსთან შესაბამისი, სტანდარტული ბიფურკაცია',
            'high_calcified': 'მაღალი ბიფურკაცია / კალციფიკაცია / კინკინგი',
            'pending': 'ზუსტდება',
        }
        br_map = {
            'normal_small': 'მწვავე სისხლჩაქცევის გარეშე / მცირე კერა',
            'large_or_hemorrhage': 'ფართო ინფარქტი ან ჰემორაგია (!)',
            'pending': 'ზუსტდება',
        }
        disc_str = 'DICOM დისკი ადგილზეა' if s.get('cta_disc') else 'DICOM დისკი მოსატანია'
        lines.append(
            f"საძილეები და ვიზუალიზაცია (NASCET): R ICA {s.get('carotid_r') if s.get('carotid_r') is not None else '—'}% / "
            f"L ICA {s.get('carotid_l') if s.get('carotid_l') is not None else '—'}%; "
            f"კისრის CTA — {cta_map.get(s.get('cea_cta_status') or 'pending', 'ზუსტდება')}; "
            f"თავის ტვინის CT/MRI — {br_map.get(s.get('brain_imaging') or 'pending', 'ზუსტდება')} ({disc_str})."
        )

        neck_bits = []
        if q.get('cea_prior_neck'):
            neck_txt = chip_summary(q.get('cea_neck_chips'), 'cea_neck_chips', q.get('cea_prior_neck_text')) or 'აღნიშნავს'
            neck_bits.append(f'კისრის წინა ჩარევა/დასხივება: {neck_txt}')
        if q.get('cea_hoarseness'):
            neck_bits.append('აღნიშნავს ხმის ჩახლეჩას / დისფაგიას')
        if q.get('cea_neck_limited'):
            neck_bits.append('კისრის გაშლა შეზღუდულია')
        if (s.get('laryngoscopy_res') or 'pending') != 'pending':
            l_lbl = 'სახმო იოგები მოძრავია' if s.get('laryngoscopy_res') == 'normal' else 'სახმო იოგის პარეზი (!)'
            neck_bits.append(f'ლარინგოსკოპია: {l_lbl}')
        if neck_bits:
            lines.append('; '.join(neck_bits) + '.')

    elif pathway == PreopPatient.Pathway.ABF:
        sub_lbl = 'კრიტიკული იშემიის — CLTI გამო' if patient.op_subtype == PreopPatient.OpSubtype.ABF_CLTI else 'ლერიშის სინდრომის / მძიმე კოჭლობის გამო'
        lines.append(f'{age_part}{sex_label}, დაგეგმილია აორტო-ბიფემორალური შუნტირება ({sub_lbl}){surgeon_str}.')

        claud_part = f"ხანგამოშვებითი კოჭლობა {q['abf_claud_m']} მ-ში" if q.get('abf_claud_m') is not None else 'ხანგამოშვებითი კოჭლობა'
        if q.get('abf_buttock_pain'):
            claud_part += ' (დუნდულოებისა და ბარძაყების არეში — ლერიშის სინდრომი)'
        if q.get('abf_duration'):
            claud_part += f", ხანგრძლივობა: {q['abf_duration']}"
        rest_part = 'აღნიშნავს მოსვენებით/ღამის იშემიურ ტკივილს' if q.get('abf_rest_pain') else 'მოსვენებით ტკივილს უარყოფს'
        lines.append(f'სისხლძარღვოვანი ჩივილები: {claud_part}; {rest_part}.')

        if q.get('abf_ulcer'):
            wet_str = 'სველი/ჩირქოვანი გამონადენით (!)' if q.get('abf_wet_infection') else 'მშრალი, მწვავე ჩირქოვანი ნიშნების გარეშე'
            lines.append(f"ტროფიკული სტატუსი: წყლული/ნეკროზი ({ulcer_txt or 'ტერფზე'}), {wet_str}.")
        groin_str = 'საზარდულის ნაკეცებში ინტერტრიგო/დაზიანება (!)' if q.get('abf_groin_issue') else 'საზარდულის კანი ინტაქტურია'
        surg_bits = [groin_str]
        if q.get('abf_prior_vasc'):
            surg_bits.append(f"წინა სისხლძარღვოვანი ჩარევა: {vasc_txt or 'აღნიშნავს'}")
        if q.get('prev_abdo_surg'):
            surg_bits.append(f"მუცლის ღრუს ოპერაცია: {abdo_txt or 'აღნიშნავს'}")
        lines.append('; '.join(surg_bits) + '.')

        clamp_map = {'infrarenal': 'ინფრარენალური საკლამპე ზონა', 'juxtarenal_calcified': 'იუქსტარენალური ოკლუზია / მძიმე კალციფიკაცია (!)', 'pending': 'ზუსტდება'}
        out_map = {'patent': 'CFA/Profunda ორმხრივ გამავალია', 'profunda_stenosis': 'CFA/Profunda სტენოზი (პროფუნდოპლასტიკა)', 'poor_outflow': 'მძიმე დისტალური დაზიანება (!)', 'pending': 'ზუსტდება'}
        disc_str = 'DICOM დისკი ადგილზეა' if s.get('cta_disc') else 'DICOM დისკი მოსატანია'
        lines.append(
            f"ჰემოდინამიკა და CTA: ABI R {s.get('abi_r') if s.get('abi_r') is not None else '—'} / "
            f"L {s.get('abi_l') if s.get('abi_l') is not None else '—'}; "
            f"აორტა — {clamp_map.get(s.get('abf_clamp_zone') or 'pending', 'ზუსტდება')}; "
            f"Outflow — {out_map.get(s.get('abf_outflow') or 'pending', 'ზუსტდება')} ({disc_str})."
        )

    elif pathway in (PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA):
        side_word = {'R': 'მარჯვენა ქვემო კიდურის ', 'L': 'მარცხენა ქვემო კიდურის ', 'B': 'ორმხრივი '}.get(patient.side, '')
        is_clti = patient.op_subtype in (PreopPatient.OpSubtype.BYPASS_CLTI, PreopPatient.OpSubtype.PTA_CLTI)
        ind_lbl = 'კრიტიკული იშემიის — CLTI გამო' if is_clti else 'ხანგამოშვებითი კოჭლობის გამო'
        op_name = 'პერიფერიული არტერიული შუნტირება' if pathway == PreopPatient.Pathway.PAD_BYPASS else 'პერიფერიული ანგიოპლასტიკა / სტენტირება (PTA)'
        lines.append(f'{age_part}{sex_label}, დაგეგმილია {side_word}{op_name} ({ind_lbl}){surgeon_str}.')

        claud_part = f"კოჭლობა {q['abf_claud_m']} მ-ში" if q.get('abf_claud_m') is not None else 'ხანგამოშვებითი კოჭლობა'
        rest_part = 'მოსვენებითი ტკივილი (+)' if q.get('abf_rest_pain') else 'მოსვენებით ტკივილს უარყოფს'
        lines.append(f'სისხლძარღვოვანი ჩივილები: {claud_part}; {rest_part}.')
        if q.get('abf_ulcer'):
            wet_str = 'სველი/ჩირქოვანი ინფექციით (!)' if q.get('abf_wet_infection') else 'მწვავე ჩირქოვანი გამონადენის გარეშე'
            lines.append(f"ტროფიკული სტატუსი: {ulcer_txt or 'ტერფის წყლული/ნეკროზი'}, {wet_str}.")

        abi_r_s = s.get('abi_r') if s.get('abi_r') is not None else '—'
        abi_l_s = s.get('abi_l') if s.get('abi_l') is not None else '—'
        if pathway == PreopPatient.Pathway.PAD_BYPASS:
            run_map = {'popliteal_patent': 'მუხლქვეშა არტერია გამავალია', 'tibial_single': 'წვივის არტერიაზე დისტალური მიმღები', 'desert_foot': 'არადამაკმაყოფილებელი კალაპოტი (!)', 'pending': 'ზუსტდება'}
            gsv_map = {'adequate': 'ვარგისია (≥3.0 მმ)', 'inadequate': 'უვარგისია (<3.0 მმ / ამოღებულია!)', 'pending': 'ზუსტდება'}
            gsv_d = f" ({s['pad_gsv_mm']} მმ)" if s.get('pad_gsv_mm') is not None else ''
            lines.append(
                f"ანატომია და აუტოვენა: ABI R {abi_r_s} / L {abi_l_s}; "
                f"Runoff — {run_map.get(s.get('pad_runoff_status') or 'pending', 'ზუსტდება')}; "
                f"იფსილატერალური GSV — {gsv_map.get(s.get('pad_gsv_status') or 'pending', 'ზუსტდება')}{gsv_d}."
            )
        else:
            acc_map = {'cfa_clean': 'საერთო ბარძაყის არტერია სუფთაა', 'cfa_calcified_scar': 'CFA ნაწიბური/კალციფიკაცია (ალტერნატიული მიდგომა)', 'pending': 'ზუსტდება'}
            lines.append(
                f"ჰემოდინამიკა და მიდგომა: ABI R {abi_r_s} / L {abi_l_s}; "
                f"პუნქციის ზონა — {acc_map.get(s.get('pta_access_site') or 'pending', 'ზუსტდება')}."
            )

    elif pathway == PreopPatient.Pathway.VENOUS:
        side_word = {'R': 'მარჯვენა ქვემო კიდურზე ', 'L': 'მარცხენა ქვემო კიდურზე ', 'B': 'ორივე ქვემო კიდურზე '}.get(patient.side, '')
        op_lbl = (
            'ენდოვენური ლაზერული აბლაცია (EVLA ± მინიფლებექტომია, ტუმესცენტური ანესთეზია)'
            if patient.op_subtype == PreopPatient.OpSubtype.EVLA
            else 'კომბინირებული ფლებექტომია / კროსექტომია (სპინალური / ზოგადი ანესთეზია)'
        )
        lines.append(f'{age_part}{sex_label}, დაგეგმილია {side_word}{op_lbl}{surgeon_str}.')

        ceap = q.get('ven_ceap') or 'C2'
        vein_map = {
            'gsv': 'დიდი საჩინო ვენა (GSV)',
            'ssv': 'მცირე საჩინო ვენა (SSV)',
            'gsv_ssv': 'დიდი და მცირე საჩინო ვენები (GSV + SSV)',
            'aagsv_trib': 'წინა დამატებითი საჩინო ვენა / შენაკადები',
        }
        dup_map = {
            'deep_patent_reflux': 'ღრმა ვენები გამავალია, ზედაპირული სარქვლოვანი რეფლუქსი დადასტურებულია',
            'acute_dvt': 'მწვავე ღრმა ვენების თრომბოზი (DVT — უკუჩვენება!)',
            'deep_obstruction': 'ღრმა ვენების პოსტთრომბოზული ოკლუზია (უკუჩვენება!)',
            'superficial_thrombus': 'მწვავე აღმავალი თრომბოფლებიტი (!)',
            'pending': 'დუპლექსის მონაცემი შესატანია',
        }
        diam_str = f", მაქს. დიამეტრი {s['ven_gsv_mm']} მმ" if s.get('ven_gsv_mm') is not None else ''
        lines.append(
            f"ვენური სტატუსი: CEAP {ceap} (Caprini ქულა: {ev['caprini_score']} — {ev['caprini_tier']}); "
            f"სამიზნე აუზი — {vein_map.get(s.get('ven_target_vein') or 'gsv', 'GSV')}{diam_str}; "
            f"დუპლექსი — {dup_map.get(s.get('ven_duplex_status') or 'pending', 'ზუსტდება')}."
        )

        ven_notes = []
        if q.get('ven_dvt_history'):
            dvt_txt = chip_summary(q.get('ven_dvt_chips'), 'ven_dvt_chips', q.get('ven_dvt_text')) or 'აღნიშნავს'
            ven_notes.append(f'ანამნეზში DVT/PE ({dvt_txt})')
        if q.get('ven_thrombophilia'):
            ven_notes.append('თრომბოფილია / ოჯახური VTE')
        if q.get('ven_acute_svt'):
            ven_notes.append('მწვავე თრომბოფლებიტი (!)')
        if q.get('ven_bleeding'):
            ven_notes.append('სისხლდენა კვანძიდან ანამნეზში')
        if q.get('ven_recurrent'):
            rec_txt = chip_summary(q.get('ven_rec_chips'), 'ven_rec_chips', q.get('ven_recurrent_text')) or 'წინა ჩარევის შემდეგ'
            ven_notes.append(f'რეციდივული ვარიკოზი ({rec_txt})')
        if q.get('ven_hormones'):
            ven_notes.append('ჰორმონული პრეპარატები/კონტრაცეპტივი')
        if q.get('ven_pregnant'):
            ven_notes.append('ორსულობა / ლაქტაცია (!)')
        if ven_notes:
            lines.append('ვენური და თრომბოემბოლიური ფაქტორები: ' + '; '.join(ven_notes) + '.')
        else:
            lines.append('DVT/PE-ს, მწვავე თრომბოფლებიტსა და თრომბოფილიას უარყოფს.')

    else:
        op_map = {
            'open': 'მუცლის აორტის ინფრარენალური ანევრიზმის ღია რეზექცია / პროთეზირება',
            'evar': 'მუცლის აორტის ინფრარენალური ანევრიზმის ენდოვასკულური პროთეზირება (EVAR)',
            'tbd': 'მუცლის აორტის ინფრარენალური ანევრიზმის ოპერაციული მკურნალობა (ღია / EVAR)',
        }
        lines.append(f'{age_part}{sex_label}, დაგეგმილია {op_map.get(patient.op_subtype, "")}{surgeon_str}.')

        mm_str = f"{ev['aaa_mm']} მმ" if ev['aaa_mm'] is not None else 'ზუსტდება'
        morph_lbl = {
            'saccular': ', საკულარული მორფოლოგია (!)',
            'rapid_growth': ', სწრაფი ზრდის დინამიკა (!)',
            'inflammatory': ', სტერილური ანთებითი მორფოლოგია / პერიაორტიტი (!)',
            'inflammatory_mycotic': ', საეჭვო მიკოზური/ინფიცირებული მორფოლოგია (!)',
        }.get(s.get('aaa_morphology') or 'fusiform', '')
        live_cta_m = ev.get('cta_months')
        cta_str = f'{live_cta_m} თვის წინ' if live_cta_m is not None else 'თარიღი უცნობია'
        disc_str = 'DICOM დისკი ადგილზეა' if s.get('cta_disc') else 'DICOM დისკი მოსატანია'
        pain_str = (
            'აღნიშნავს მუცლის/წელის ტკივილს (საყურადღებოა სიმპტომური AAA!).'
            if q.get('aaa_pain') else 'ამჟამად ასიმპტომურია, მუცლისა და წელის ტკივილს უარყოფს.'
        )
        lines.append(f'ანევრიზმა: მაქს. დიამეტრი {mm_str}{morph_lbl} (CTA: {cta_str}, {disc_str}). {pain_str}')

        surg_extras = []
        if patient.op_subtype in ('evar', 'tbd') and (s.get('aaa_neck_status') or 'pending') != 'pending':
            neck_lbl = 'პროქსიმალური ყელი IFU-ს ფარგლებშია' if s.get('aaa_neck_status') == 'friendly_ifu' else 'Hostile Neck — IFU-ს გარეთ (!)'
            surg_extras.append(f'CTA ყელი: {neck_lbl}')
        if (s.get('popliteal_screen_status') or 'pending') != 'pending':
            pop_lbl = 'მუხლქვეშა ანევრიზმა გამოირიცხა' if s.get('popliteal_screen_status') == 'normal' else 'გამოვლინდა თანმხლები მუხლქვეშა/ბარძაყის ანევრიზმა (!)'
            surg_extras.append(pop_lbl)
        if q.get('prev_abdo_surg'):
            surg_extras.append(f"მუცლის/საზარდულის ოპერაციები: {abdo_txt or 'აღნიშნავს'}")
        if q.get('claudication'):
            surg_extras.append('აღნიშნავს ხანგამოშვებით კოჭლობას სიარულისას')
        if s.get('aaa_renal_vein_anom'):
            surg_extras.append('CTA: რეტროაორტული მარცხენა თირკმლის ვენა / ანატომიური ვარიანტი (!)')
        if surg_extras:
            lines.append('; '.join(surg_extras) + '.')

    if q.get('mets_ok', True):
        mets_str = 'დამაკმაყოფილებელი (>= 4 METs, ადის 2 სართულზე გაუჩერებლად)'
    elif q.get('mets_limitation') == 'limb':
        mets_str = 'შეზღუდულია მხოლოდ ფეხის ტკივილის/კოჭლობის გამო (ქოშინსა და სტენოკარდიას უარყოფს)'
    else:
        mets_str = 'დაქვეითებული (< 4 METs — ქოშინი/დაღლა 2 სართულზე ასვლისას)'
    lines.append(f'ფუნქციური ამტანობა: {mets_str}.')

    comorb = []
    if q.get('htn'):
        comorb.append('არტერიული ჰიპერტენზია')
    if q.get('cad'):
        c_det = chip_summary(q.get('cad_chips'), 'cad_chips', q.get('cad_detail'))
        comorb.append(f"გულის იშემიური დაავადება{f' ({c_det})' if c_det else ''}")
    if q.get('dm'):
        dm_kind = 'ინსულინზე' if q.get('dm_insulin') else 'ტაბლეტებზე'
        comorb.append(f'შაქრიანი დიაბეტი ({dm_kind})')
    if q.get('on_dialysis'):
        comorb.append('პროგრამული დიალიზი')
    if q.get('pulm'):
        p_det = chip_summary(q.get('pulm_chips'), 'pulm_chips', q.get('pulm_detail'))
        comorb.append(f"ფილტვების ქრ. დაავადება{f' ({p_det})' if p_det else ''}")
    if q.get('thyroid_disease'):
        comorb.append('ფარისებრი ჯირკვლის პათოლოგია')
    if q.get('neuro'):
        comorb.append('ანამნეზში ინსულტი/TIA')
    if q.get('ulcer'):
        comorb.append('წყლულოვანი დაავადება (აქტიური ჩივილით)' if q.get('ulcer_active_symp') else 'წყლულოვანი დაავადება ანამნეზში')
    if q.get('smoker'):
        comorb.append('თამბაქოს მწეველი')
    lines.append(f"თანმხლები ფონი: {', '.join(comorb) if comorb else 'მნიშვნელოვანი ქრონიკული დაავადებების გარეშე'}.")

    meds = []
    if q.get('med_aspirin'):
        meds.append('ასპირინი')
    if q.get('med_clopidogrel'):
        meds.append('კლოპიდოგრელი/P2Y12')
    if q.get('med_ticagrelor'):
        meds.append('ტიკაგრელორი')
    if q.get('med_prasugrel'):
        meds.append('პრასუგრელი')
    if q.get('med_doac'):
        meds.append('პერორალური ანტიკოაგულანტი (DOAC)')
    if q.get('med_dabigatran'):
        meds.append('დაბიგატრანი (პრადაქსა)')
    if q.get('med_warfarin'):
        meds.append('ვარფარინი')
    if q.get('dm_insulin'):
        meds.append('ინსულინი')
    if q.get('metformin'):
        meds.append('მეტფორმინი')
    if q.get('sglt2'):
        meds.append('SGLT2 ინჰიბიტორი')
    if q.get('glp1'):
        meds.append('GLP-1 აგონისტი (ოზემპიკი/მუნჯარო)')
    med_extra = chip_summary(q.get('med_chips'), 'med_chips', q.get('other_meds'))
    if med_extra:
        meds.append(med_extra)
    lines.append(f"მუდმივი მედიკამენტები: {', '.join(meds) if meds else 'არ იღებს'}.")

    allergies = []
    if q.get('ven_lidocaine_allergy'):
        allergies.append('ლიდოკაინი / ადგილობრივი ანესთეტიკი!')
    if q.get('allergy_iodine'):
        allergies.append('იოდი / კონტრასტი!')
    alg_extra = chip_summary(q.get('allergy_chips'), 'allergy_chips', q.get('allergy_text'))
    if alg_extra:
        allergies.append(alg_extra)
    elif q.get('allergy_other'):
        allergies.append('მედიკამენტური ალერგია')
    if pathway == PreopPatient.Pathway.VENOUS:
        no_allergy_txt = 'უარყოფს (მ.შ. ლიდოკაინზე)'
    elif q.get('allergy_iodine_asked') or q.get('nurse_verified'):
        no_allergy_txt = 'უარყოფს (მ.შ. იოდზე/კონტრასტზე)'
    else:
        no_allergy_txt = 'უარყოფს (იოდზე ალერგია დასაზუსტებელია)'
    lines.append(f"ალერგია: {', '.join(allergies) if allergies else no_allergy_txt}.")
    if ev.get('abx_prophylaxis_note'):
        lines.append(f"პროფილაქტიკა: {ev['abx_prophylaxis_note']}")
    if ev.get('post_antithrombotic_label'):
        lines.append(f"პოსტოპ. ანტითრომბოზული გეგმა: {ev['post_antithrombotic_label']}.")

    anesth_flags = [f"{ev['effective_asa']}, გაუტკივარება: {ev['effective_anesth_label']}"]
    if ev.get('anesth_lines_txt'):
        anesth_flags.append(f"მონიტორინგი: {ev['anesth_lines_txt']}")
    if ev.get('lidocaine_max_mg') is not None:
        anesth_flags.append(f"ტუმესცენტური ლიდოკაინის მაქს. დოზა: ≤ {ev['lidocaine_max_mg']} მგ (ადრენალინით)")
    if s.get('airway_mallampati') == 'IV_difficult':
        anesth_flags.append('Mallampati IV — მოსალოდნელი რთული ინტუბაცია!')
    if q.get('anesth_issue'):
        anesth_flags.append('გართულება წარსულ ანესთეზიაზე')
    if q.get('pacemaker'):
        cied_sfx = f" ({ev['cied_plan_label']})" if ev.get('cied_plan_label') else ''
        anesth_flags.append(f'კარდიოსტიმულატორი/იმპლანტი{cied_sfx}')
    if q.get('loose_teeth'):
        anesth_flags.append('პროთეზი / მერყევი კბილები')
    if q.get('dental_infection'):
        anesth_flags.append('სტომატოლოგიური სანაცია საჭიროა')
    if q.get('glp1'):
        anesth_flags.append('GLP-1 აგონისტი (კუჭის შენელებული დაცლის რისკი)')
    lines.append(f"ანესთეზიოლოგიური სტატუსი: {'; '.join(anesth_flags)}.")

    lab_bits = []
    if s.get('bp_sys') and s.get('bp_dia'):
        bp_str = f"BP {s['bp_sys']}/{s['bp_dia']}"
        if s.get('bp_sys_l') and s.get('bp_dia_l'):
            bp_str += f" (L: {s['bp_sys_l']}/{s['bp_dia_l']})"
        lab_bits.append(f'{bp_str} mmHg')
    if ev.get('bmi') is not None:
        lab_bits.append(f"BMI {ev['bmi']}")
    if ev['hb_gl'] is not None:
        lab_bits.append(f"Hb {ev['hb_gl']} გ/ლ")
    if s.get('plt') is not None and pathway in (PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA):
        lab_bits.append(f"PLT {s['plt']}")
    if s.get('inr') is not None and pathway in (PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA):
        lab_bits.append(f"INR {s['inr']}")
    if ev['egfr'] is not None:
        crcl_part = f" (CrCl {ev['crcl']})" if ev.get('crcl') is not None else ''
        lab_bits.append(f"eGFR {ev['egfr']} მლ/წთ{crcl_part}")
    if ev.get('max_contrast_ml') is not None:
        lab_bits.append(f"მაქს. კონტრასტი ≤ {ev['max_contrast_ml']} მლ")
    if ev['alb_gl'] is not None:
        lab_bits.append(f"ალბუმინი {ev['alb_gl']} გ/ლ")
    if ev.get('ldl') is not None:
        lab_bits.append(f"LDL {ev['ldl']} მმოლ/ლ")
    if s.get('lvef') is not None:
        lab_bits.append(f"LVEF {s['lvef']}%")
    if ev.get('nt_probnp') is not None:
        lab_bits.append(f"NT-proBNP {ev['nt_probnp']} pg/mL")
    if ev.get('hs_ctn') is not None:
        lab_bits.append(f"hs-cTn {ev['hs_ctn']} ng/L")
    if ev.get('tsat') is not None:
        lab_bits.append(f"TSAT {ev['tsat']}%")
    if pathway not in (PreopPatient.Pathway.CEA, PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA) and (s.get('carotid_r') is not None or s.get('carotid_l') is not None):
        car_r_s = s.get('carotid_r') if s.get('carotid_r') is not None else '—'
        car_l_s = s.get('carotid_l') if s.get('carotid_l') is not None else '—'
        lab_bits.append(f"საძილეები R {car_r_s}% / L {car_l_s}%")
    if ev.get('min_abi') is not None and pathway == PreopPatient.Pathway.VENOUS:
        lab_bits.append(f"ABI min {ev['min_abi']}")
    if ev.get('min_tp') is not None:
        lab_bits.append(f"TP min {ev['min_tp']} mmHg")
    elif ev.get('min_tbi') is not None:
        lab_bits.append(f"TBI min {ev['min_tbi']}")
    if s.get('spiro_fev1') is not None:
        lab_bits.append(f"FEV1 {s['spiro_fev1']}%")
    if lab_bits:
        lines.append(f"კვლევებით: {', '.join(lab_bits)}.")

    return '\n'.join(lines)


# --------------------------------------------------------------------------
# File uploads (images shrunk + EXIF-stripped; PDFs verified & kept private)
# --------------------------------------------------------------------------

def save_preop_file(patient, uploaded, category, caption, user, uploaded_on=None):
    size = getattr(uploaded, 'size', 0) or 0
    if size <= 0:
        raise PhotoError('ფაილი ცარიელია.')
    if size > MAX_UPLOAD:
        raise PhotoError('ფაილი ძალიან დიდია (მაქს. 30 მბ).')

    orig_name = clean_text(os.path.basename(getattr(uploaded, 'name', '') or ''), 180)
    head = uploaded.read(8)
    uploaded.seek(0)
    is_pdf = head.startswith(b'%PDF-') or orig_name.lower().endswith('.pdf')

    uid = uuid.uuid4().hex
    rec = PreopFile(
        patient=patient,
        category=category if category in PreopFile.Category.values else PreopFile.Category.LABS,
        is_pdf=is_pdf,
        original_name=orig_name,
        caption=clean_text(caption, 200),
        uploaded_on=uploaded_on or timezone.localdate(),
        created_by=user,
    )
    if is_pdf:
        if not head.startswith(b'%PDF-'):
            raise PhotoError('ეს PDF ფაილი დაზიანებულია ან არასწორი ფორმატისაა.')
        raw = uploaded.read()
        rec.file.save(f'{uid}.pdf', ContentFile(raw), save=False)
    else:
        full, thumb, _, _ = process_photo(uploaded)
        rec.file.save(f'{uid}.jpg', ContentFile(full), save=False)
        rec.thumb.save(f'{uid}-t.jpg', ContentFile(thumb), save=False)
    rec.save()
    PreopPatient.objects.filter(pk=patient.pk).update(updated_at=timezone.now(), updated_by=user)
    return rec


# --------------------------------------------------------------------------
# Questionnaire parsing helpers (Isolated Nurse Tab 1 vs Patient Self-Fill)
# --------------------------------------------------------------------------

def apply_questionnaire_post(post_data, patient, filled_by_label):
    """Full nurse/staff Tab 1 save (marks nurse_verified=True)."""
    q = merged_q(patient)
    s = merged_studies(patient)
    today = timezone.localdate()
    bool_fields = (
        # AAA
        'aaa_pain', 'prev_abdo_surg', 'claudication', 'active_infection',
        # CEA
        'cea_symp_event', 'cea_crescendo', 'cea_thrombolysis',
        'cea_prior_neck', 'cea_hoarseness', 'cea_neck_limited', 'cea_disabling_stroke',
        # ABF / PAD Bypass / PTA
        'abf_buttock_pain', 'ic_bmt_3m', 'abf_rest_pain', 'abf_ulcer', 'abf_wet_infection',
        'abf_groin_issue', 'abf_prior_vasc', 'pta_cant_lie_flat',
        # Venous (EVLA / Phlebectomy)
        'ven_dvt_history', 'ven_thrombophilia', 'ven_acute_svt', 'ven_bleeding',
        'ven_lidocaine_allergy', 'ven_skin_infection', 'ven_recurrent',
        'ven_hormones', 'ven_cancer_immob', 'ven_pregnant',
        # General & anesthesia
        'cad', 'recent_stent', 'htn', 'pulm', 'dm', 'dm_insulin', 'metformin', 'sglt2',
        'glp1', 'on_dialysis', 'thyroid_disease', 'dental_infection',
        'neuro', 'carotid_bruit_symp', 'ulcer', 'ulcer_active_symp',
        'med_aspirin', 'med_clopidogrel', 'med_ticagrelor', 'med_prasugrel',
        'med_doac', 'med_dabigatran', 'med_warfarin',
        'allergy_iodine', 'allergy_other', 'anesth_issue', 'pacemaker', 'loose_teeth', 'smoker',
    )
    for f in bool_fields:
        q[f] = post_data.get(f) in ('1', 'on', 'true')
    q['mets_ok'] = post_data.get('mets_ok', '1') != '0'
    if post_data.get('mets_limitation') in ('cardiac', 'limb'):
        q['mets_limitation'] = post_data.get('mets_limitation')
    elif q['mets_ok']:
        q['mets_limitation'] = 'cardiac'

    if hasattr(post_data, 'getlist'):
        for chip_field in (
            'cad_chips', 'med_chips', 'abdo_chips', 'vasc_chips',
            'cea_event_chips', 'cea_neck_chips', 'ulcer_chips',
            'pulm_chips', 'inf_chips', 'allergy_chips',
            'ven_dvt_chips', 'ven_rec_chips',
        ):
            valid_keys = CHIP_LABELS[chip_field]
            q[chip_field] = [k for k in post_data.getlist(chip_field) if k in valid_keys]
    if q.get('allergy_chips'):
        q['allergy_other'] = True
    if q.get('cad_chips'):
        q['cad'] = True
    if q.get('pulm_chips'):
        q['pulm'] = True
    if q.get('abdo_chips'):
        q['prev_abdo_surg'] = True
    if q.get('vasc_chips'):
        q['abf_prior_vasc'] = True
    if q.get('inf_chips'):
        q['active_infection'] = True
    if q.get('dm_insulin'):
        q['dm'] = True
    if q.get('ulcer_active_symp'):
        q['ulcer'] = True

    if post_data.get('cfs_level') in ('1_3', '4', '5_plus'):
        q['cfs_level'] = post_data.get('cfs_level')
    if post_data.get('aaa_pedal_pulse') in ('intact', 'reduced'):
        q['aaa_pedal_pulse'] = post_data.get('aaa_pedal_pulse')
    if post_data.get('ven_ceap') in ('C2', 'C3', 'C4', 'C5', 'C6'):
        q['ven_ceap'] = post_data.get('ven_ceap')

    if q.get('cea_symp_event'):
        ev_dt = _date(post_data.get('cea_event_date'))
        if ev_dt and ev_dt <= today:
            q['cea_event_date'] = ev_dt.isoformat()
            q['cea_days_since_event'] = (today - ev_dt).days
        elif 'cea_days_since_event' in post_data:
            d_ev = _int(post_data.get('cea_days_since_event'), lo=0, hi=365)
            q['cea_days_since_event'] = d_ev
            q['cea_event_date'] = (today - datetime.timedelta(days=d_ev)).isoformat() if d_ev is not None else ''
    else:
        q['cea_event_date'] = ''
        q['cea_days_since_event'] = None
        q['cea_event_chips'] = []
        q['cea_crescendo'] = False
        q['cea_thrombolysis'] = False

    if 'abf_claud_m' in post_data:
        q['abf_claud_m'] = _int(post_data.get('abf_claud_m'), lo=0, hi=5000)
    if 'side' in post_data and post_data.get('side') in ('', 'R', 'L', 'B'):
        new_side = post_data.get('side')
        if new_side != patient.side:
            s['dos_site_marked'] = False
        patient.side = new_side

    for tf, lim in (
        ('prev_abdo_text', 250), ('infection_text', 250),
        ('cea_event_text', 250), ('cea_prior_neck_text', 250),
        ('abf_duration', 120), ('abf_ulcer_text', 250), ('abf_prior_vasc_text', 250),
        ('ven_dvt_text', 250), ('ven_recurrent_text', 250),
        ('cad_detail', 250), ('pulm_detail', 250), ('other_meds', 400), ('allergy_text', 250),
    ):
        if tf in post_data:
            q[tf] = clean_text(post_data.get(tf), lim).replace('\n', ' ')

    q['filled'] = True
    q['filled_at'] = timezone.localtime().strftime('%d.%m.%Y %H:%M')
    q['filled_by'] = filled_by_label
    q['nurse_verified'] = True
    q['nurse_verified_by'] = filled_by_label
    q['allergy_iodine_asked'] = True

    if 'weight_kg' in post_data:
        w = _num(post_data.get('weight_kg'), lo=25, hi=260)
        if w is not None:
            s['weight_kg'] = w
    if 'height_cm' in post_data:
        h = _num(post_data.get('height_cm'), lo=100, hi=230)
        if h is not None:
            s['height_cm'] = h
    if 'aaa_mm' in post_data:
        s['aaa_mm'] = _num(post_data.get('aaa_mm'), lo=10, hi=200)
    if 'cta_date' in post_data:
        cta_dt = _date(post_data.get('cta_date'))
        if cta_dt and cta_dt <= today:
            s['cta_date'] = cta_dt.isoformat()
            s['cta_months'] = max(0, (today - cta_dt).days // 30)
        else:
            s['cta_date'] = ''
            if 'cta_months' in post_data:
                s['cta_months'] = _int(post_data.get('cta_months'), lo=0, hi=120)
    elif 'cta_months' in post_data:
        s['cta_months'] = _int(post_data.get('cta_months'), lo=0, hi=120)
    if 'cta_disc' in post_data or patient.pathway not in (PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA):
        s['cta_disc'] = post_data.get('cta_disc') in ('1', 'on', 'true')

    if not patient.started_on:
        patient.started_on = patient.visit_date or today
    patient.questionnaire = q
    patient.studies = s


def apply_self_fill_post(post_data, patient):
    """Isolated patient Self-Fill parser: NEVER wipes nurse-entered fields (cta_disc, pacemaker, dm_insulin, etc.)."""
    q = merged_q(patient)
    s = merged_studies(patient)
    today = timezone.localdate()
    pw = PreopPatient.SUBTYPE_PATHWAY.get(patient.op_subtype, patient.pathway)

    # Common patient-facing switches rendered on self_fill.html across ALL 6 pathways (including active_infection!)
    common_bools = (
        'htn', 'cad', 'recent_stent', 'dm', 'metformin', 'sglt2', 'glp1', 'on_dialysis',
        'pulm', 'thyroid_disease', 'neuro', 'ulcer', 'ulcer_active_symp', 'dental_infection',
        'active_infection',
        'med_aspirin', 'med_clopidogrel', 'med_ticagrelor', 'med_prasugrel',
        'med_doac', 'med_dabigatran', 'med_warfarin',
        'allergy_iodine', 'allergy_other', 'anesth_issue', 'smoker',
    )
    for f in common_bools:
        q[f] = post_data.get(f) in ('1', 'on', 'true')
    if q.get('ulcer_active_symp'):
        q['ulcer'] = True
    # Additive safety: patient can flag insulin or pacemaker on self_fill.html, never wipe nurse's prior flag!
    if post_data.get('dm_insulin') in ('1', 'on', 'true'):
        q['dm_insulin'] = True
        q['dm'] = True
    if post_data.get('pacemaker') in ('1', 'on', 'true'):
        q['pacemaker'] = True
    q['mets_ok'] = post_data.get('mets_ok', '1') != '0'
    if post_data.get('mets_limitation') in ('cardiac', 'limb'):
        q['mets_limitation'] = post_data.get('mets_limitation')
    elif q['mets_ok']:
        q['mets_limitation'] = 'cardiac'

    # Pathway-specific patient-facing questions ONLY for this patient's pathway
    if pw == PreopPatient.Pathway.AAA_INFRA:
        for f in ('aaa_pain', 'claudication', 'prev_abdo_surg'):
            q[f] = post_data.get(f) in ('1', 'on', 'true')
    elif pw == PreopPatient.Pathway.CEA:
        for f in ('cea_symp_event', 'cea_crescendo', 'cea_prior_neck', 'cea_hoarseness'):
            q[f] = post_data.get(f) in ('1', 'on', 'true')
        if q.get('cea_symp_event'):
            ev_dt = _date(post_data.get('cea_event_date'))
            if ev_dt and ev_dt <= today:
                q['cea_event_date'] = ev_dt.isoformat()
                q['cea_days_since_event'] = (today - ev_dt).days
            elif 'cea_days_since_event' in post_data and post_data.get('cea_days_since_event'):
                d_ev = _int(post_data.get('cea_days_since_event'), lo=0, hi=365)
                if d_ev is not None:
                    q['cea_days_since_event'] = d_ev
                    q['cea_event_date'] = (today - datetime.timedelta(days=d_ev)).isoformat()
    elif pw in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA):
        for f in ('abf_buttock_pain', 'abf_rest_pain', 'abf_ulcer', 'abf_wet_infection'):
            q[f] = post_data.get(f) in ('1', 'on', 'true')
        for opt_f in ('abf_prior_vasc', 'ic_bmt_3m', 'pta_cant_lie_flat'):
            if opt_f in post_data or (opt_f == 'pta_cant_lie_flat' and pw == PreopPatient.Pathway.PAD_PTA):
                q[opt_f] = post_data.get(opt_f) in ('1', 'on', 'true')
        if 'abf_claud_m' in post_data and post_data.get('abf_claud_m'):
            q['abf_claud_m'] = _int(post_data.get('abf_claud_m'), lo=0, hi=5000)
        if 'abf_duration' in post_data and post_data.get('abf_duration'):
            q['abf_duration'] = clean_text(post_data.get('abf_duration'), 120).replace('\n', ' ')
    elif pw == PreopPatient.Pathway.VENOUS:
        for f in (
            'ven_dvt_history', 'ven_thrombophilia', 'ven_acute_svt', 'ven_bleeding',
            'ven_lidocaine_allergy', 'ven_skin_infection', 'ven_recurrent',
            'ven_hormones', 'ven_cancer_immob', 'ven_pregnant', 'claudication',
        ):
            q[f] = post_data.get(f) in ('1', 'on', 'true')
        if post_data.get('ven_ceap') in ('C2', 'C3', 'C4', 'C5', 'C6'):
            q['ven_ceap'] = post_data.get('ven_ceap')

    if hasattr(post_data, 'getlist'):
        self_chip_fields = ['cad_chips', 'med_chips', 'allergy_chips']
        if 'pulm_chips' in post_data:
            self_chip_fields.append('pulm_chips')
        if 'inf_chips' in post_data:
            self_chip_fields.append('inf_chips')
        if pw == PreopPatient.Pathway.AAA_INFRA and 'abdo_chips' in post_data:
            self_chip_fields.append('abdo_chips')
        elif pw == PreopPatient.Pathway.CEA:
            self_chip_fields.extend(['cea_event_chips', 'cea_neck_chips'])
        elif pw in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA):
            self_chip_fields.extend(['ulcer_chips', 'vasc_chips'])
        elif pw == PreopPatient.Pathway.VENOUS:
            self_chip_fields.extend(['ven_dvt_chips', 'ven_rec_chips'])
        for chip_field in self_chip_fields:
            valid_keys = CHIP_LABELS[chip_field]
            q[chip_field] = [k for k in post_data.getlist(chip_field) if k in valid_keys]

    if q.get('allergy_chips'):
        q['allergy_other'] = True
    if q.get('cad_chips'):
        q['cad'] = True
    if q.get('pulm_chips'):
        q['pulm'] = True
    if q.get('abdo_chips'):
        q['prev_abdo_surg'] = True
    if q.get('vasc_chips'):
        q['abf_prior_vasc'] = True
    if q.get('inf_chips'):
        q['active_infection'] = True

    # Patient may fill an empty surgical side, NEVER overwrite an already-set surgeon side!
    if not patient.side and 'side' in post_data and post_data.get('side') in ('R', 'L', 'B'):
        patient.side = post_data.get('side')

    for tf, lim in (('other_meds', 400), ('allergy_text', 250)):
        if tf in post_data:
            q[tf] = clean_text(post_data.get(tf), lim).replace('\n', ' ')

    if 'weight_kg' in post_data:
        w = _num(post_data.get('weight_kg'), lo=25, hi=260)
        if w is not None:
            s['weight_kg'] = w
    if 'height_cm' in post_data:
        h = _num(post_data.get('height_cm'), lo=100, hi=230)
        if h is not None:
            s['height_cm'] = h

    q['filled'] = True
    q['self_fill_submitted'] = True
    q['filled_at'] = timezone.localtime().strftime('%d.%m.%Y %H:%M')
    q['filled_by'] = 'პაციენტი (ბმულით)'
    q['allergy_iodine_asked'] = True

    if not patient.started_on:
        patient.started_on = patient.visit_date or today
    patient.questionnaire = q
    patient.studies = s


# --------------------------------------------------------------------------
# Views: Tweek Weekly Calendar, Patient Workspace, Self-Fill, Audit
# --------------------------------------------------------------------------

def _monday_of(day):
    return day - datetime.timedelta(days=day.weekday())


def _week_title(monday):
    sunday = monday + datetime.timedelta(days=6)
    if monday.month == sunday.month:
        return f'{MONTHS[monday.month - 1]} {monday.year} · {monday.day} – {sunday.day} {MONTHS_SHORT[monday.month]}'
    if monday.year != sunday.year:
        return (
            f'{monday.day} {MONTHS_SHORT[monday.month]} {monday.year} – '
            f'{sunday.day} {MONTHS_SHORT[sunday.month]} {sunday.year}'
        )
    return (
        f'{monday.day} {MONTHS_SHORT[monday.month]} – '
        f'{sunday.day} {MONTHS_SHORT[sunday.month]} {sunday.year}'
    )


def _cardiologist_choices():
    existing = list(
        PreopPatient.objects.exclude(cardiologist='')
        .values_list('cardiologist', flat=True).distinct()[:30]
    )
    names = list(DEFAULT_CARDIOLOGISTS)
    for n in existing:
        if n not in names:
            names.append(n)
    return names


class PreopWeekView(LoginRequiredMixin, generic.TemplateView):
    """Tweek.so-style weekly calendar + Someday lists for Nurse-Led Preoperative Assessment."""
    template_name = 'mdf/preop/week.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        req_day = _date(self.request.GET.get('week')) or today
        monday = _monday_of(req_day)
        sunday = monday + datetime.timedelta(days=6)

        status_filter = self.request.GET.get('status', 'all')
        if status_filter not in ('all', STATUS_NO_Q, STATUS_PROGRESS, STATUS_ALERT, STATUS_READY):
            status_filter = 'all'
        pathway_filter = self.request.GET.get('pathway', 'all')
        if pathway_filter not in ('all', *PreopPatient.Pathway.values):
            pathway_filter = 'all'
        outcome_mode = 'done' if self.request.GET.get('outcome') == 'done' else 'active'
        q = clean_text(self.request.GET.get('q'), 100)

        qs = (
            PreopPatient.objects.filter(archived=False)
            .filter(
                Q(visit_date__gte=monday, visit_date__lte=sunday)
                | ~Q(someday_list='')
            )
            .select_related('surgeon', 'surgeon__profile', 'case')
            .order_by('id')
        )
        if pathway_filter != 'all':
            qs = qs.filter(pathway=pathway_filter)
        if q:
            qs = qs.filter(patient_name__icontains=q)

        all_non_archived = list(
            PreopPatient.objects.filter(archived=False)
            .select_related('surgeon', 'surgeon__profile')
            .order_by('visit_date', 'id')[:500]
        )
        pw_counts = {
            'all': 0,
            'aaa_infra': 0, 'cea': 0, 'abf': 0,
            'pad_bypass': 0, 'pad_pta': 0, 'venous': 0,
        }
        counts = {'all': 0, STATUS_NO_Q: 0, STATUS_PROGRESS: 0, STATUS_ALERT: 0, STATUS_READY: 0}
        ev_by_id = {}
        active_scheduled_total = 0
        completed_or_cancelled_total = 0

        for p in all_non_archived:
            ev = evaluate(p, today=today)
            ev_by_id[p.pk] = ev
            is_done_case = ev['case_outcome'] != 'scheduled'
            if is_done_case:
                completed_or_cancelled_total += 1
            else:
                active_scheduled_total += 1
            in_mode = is_done_case if outcome_mode == 'done' else (not is_done_case)
            if not in_mode:
                continue
            pw_counts['all'] += 1
            pw_counts[ev['pathway']] = pw_counts.get(ev['pathway'], 0) + 1
            if pathway_filter == 'all' or ev['pathway'] == pathway_filter:
                counts['all'] += 1
                counts[ev['status']] += 1

        visible_patients = []
        for p in qs:
            p.ev = ev_by_id.get(p.pk) or evaluate(p, today=today)
            is_done_case = p.ev['case_outcome'] != 'scheduled'
            in_mode = is_done_case if outcome_mode == 'done' else (not is_done_case)
            if not in_mode:
                continue
            p.surgeon_name = person_name(p.surgeon)
            if status_filter == 'all' or p.ev['status'] == status_filter:
                visible_patients.append(p)

        days = []
        for offset in range(7):
            d = monday + datetime.timedelta(days=offset)
            items = [p for p in visible_patients if p.visit_date == d and not p.someday_list]
            days.append({
                'date': d,
                'iso': d.isoformat(),
                'weekday': WEEKDAYS_KA[offset],
                'short_date': f'{d.day} {MONTHS_SHORT[d.month]}',
                'is_today': d == today,
                'patients': items,
                'empty_lines': range(max(0, (2 if offset >= 5 else 3) - len(items))),
            })

        someday_cols = []
        for key, label in PreopPatient.SomedayList.choices[1:]:
            items = [p for p in visible_patients if p.someday_list == key]
            someday_cols.append({'key': key, 'label': label, 'patients': items})

        # Surface overdue OR off-week acute vascular emergencies (symptomatic AAA pain / crescendo TIA) so nothing hides in next week!
        overdue_patients = []
        for p in all_non_archived:
            ev = ev_by_id[p.pk]
            if ev['case_outcome'] != 'scheduled':
                continue
            if pathway_filter != 'all' and ev['pathway'] != pathway_filter:
                continue
            off_week_acute = (
                bool(ev['q'].get('aaa_pain') or ev['q'].get('cea_crescendo'))
                and bool(p.visit_date and not (monday <= p.visit_date <= sunday))
            )
            if ev['over_target_days'] or off_week_acute:
                p.ev = ev
                overdue_patients.append(p)

        ctx.update({
            'today': today,
            'monday': monday,
            'prev_week': (monday - datetime.timedelta(days=7)).isoformat(),
            'next_week': (monday + datetime.timedelta(days=7)).isoformat(),
            'week_title': _week_title(monday),
            'days_mon_fri': days[:5],
            'day_sat': days[5],
            'day_sun': days[6],
            'someday_cols': someday_cols,
            'status_filter': status_filter,
            'pathway_filter': pathway_filter,
            'outcome_mode': outcome_mode,
            'active_scheduled_total': active_scheduled_total,
            'completed_or_cancelled_total': completed_or_cancelled_total,
            'q': q,
            'doctors': doctors(),
            'op_subtype_groups': OP_SUBTYPE_GROUPS,
            'overdue_patients': overdue_patients,
            'pathway_pills': [
                ('all', 'ყველა', pw_counts['all']),
                ('aaa_infra', 'AAA', pw_counts['aaa_infra']),
                ('cea', 'CEA', pw_counts['cea']),
                ('abf', 'ABF', pw_counts['abf']),
                ('pad_bypass', 'პერიფ. შუნტირება', pw_counts['pad_bypass']),
                ('pad_pta', 'PTA / სტენტი', pw_counts['pad_pta']),
                ('venous', 'ვენური (EVLA/ფლებ.)', pw_counts['venous']),
            ],
            'filter_pills': [
                ('all', 'ყველა', counts['all']),
                (STATUS_NO_Q, 'კითხვარი შესავსებია', counts[STATUS_NO_Q]),
                (STATUS_PROGRESS, 'კვლევებშია', counts[STATUS_PROGRESS]),
                (STATUS_ALERT, 'ექიმის ჩართვა', counts[STATUS_ALERT]),
                (STATUS_READY, 'მზადაა ექიმისთვის', counts[STATUS_READY]),
            ],
        })
        return ctx


class PreopCreateView(LoginRequiredMixin, generic.View):
    """Inline 1-line Tweek add (POST) or 1-click transfer/re-sync from a Conference Case (GET/POST)."""

    def get(self, request):
        case_id = request.GET.get('case', '')
        if case_id.isdigit():
            case = get_object_or_404(Case, pk=int(case_id))
            existing = PreopPatient.objects.filter(case=case, archived=False).first()
            if existing:
                if request.GET.get('sync') == '1':
                    op_sub, side = detect_case_preop(case)
                    if not existing.side and side:
                        existing.side = side
                        existing.save(update_fields=['side'])
                    import_case_to_preop(case, existing)
                    messages.success(request, 'კონფერენციის ქეისიდან სტადირება და ანატომიური მონაცემები განახლდა.')
                return HttpResponseRedirect(reverse('preop-patient', args=[existing.pk]))
            op_sub, side = detect_case_preop(case)
            p = PreopPatient.objects.create(
                case=case,
                patient_name=case.patient_name,
                age=case.age,
                sex=case.sex or 'M',
                side=side,
                op_subtype=op_sub,
                surgeon=case.doctor or request.user,
                someday_list=PreopPatient.SomedayList.CONF,
                created_by=request.user,
                updated_by=request.user,
            )
            import_case_to_preop(case, p)
            messages.success(request, f'{p.patient_name} გადმოყვანილია წინასაოპერაციო სიაში.')
            return HttpResponseRedirect(reverse('preop-patient', args=[p.pk]))
        return HttpResponseRedirect(reverse('preop'))

    def post(self, request):
        name = clean_text(request.POST.get('patient_name'), 150).replace('\n', ' ')
        slot = (request.POST.get('slot') or '').strip()
        op_subtype = request.POST.get('op_subtype') or PreopPatient.OpSubtype.OPEN
        if op_subtype not in PreopPatient.OpSubtype.values:
            op_subtype = PreopPatient.OpSubtype.OPEN
        side = request.POST.get('side') if request.POST.get('side') in ('R', 'L', 'B') else ''

        surgeon = None
        surgeon_raw = request.POST.get('surgeon') or ''
        if surgeon_raw.isdigit():
            surgeon = get_user_model().objects.filter(pk=int(surgeon_raw)).first()
        if surgeon is None and hasattr(request.user, 'profile'):
            surgeon = request.user

        if not name:
            messages.error(request, 'ჩაწერეთ პაციენტის სახელი და გვარი.')
            return HttpResponseRedirect(reverse('preop'))

        visit_date = None
        someday = ''
        started_on = None
        init_studies = {}
        today = timezone.localdate()
        default_sex = 'F' if op_subtype in ('evla', 'phlebectomy') and request.POST.get('sex') not in ('M', 'F') else 'M'
        sex = request.POST.get('sex') if request.POST.get('sex') in ('M', 'F') else default_sex
        if slot.startswith('someday:'):
            key = slot.split(':', 1)[1]
            someday = key if key in PreopPatient.SomedayList.values else PreopPatient.SomedayList.CONF
            if someday == PreopPatient.SomedayList.HOLD:
                init_studies['hold_since'] = today.isoformat()
        else:
            visit_date = _date(slot) or today
            started_on = visit_date

        p = PreopPatient.objects.create(
            patient_name=name,
            age=_int(request.POST.get('age'), lo=1, hi=120),
            sex=sex,
            side=side,
            op_subtype=op_subtype,
            surgeon=surgeon,
            visit_date=visit_date,
            someday_list=someday,
            started_on=started_on,
            studies=init_studies,
            created_by=request.user,
            updated_by=request.user,
        )
        if request.POST.get('open_card') == '1':
            return HttpResponseRedirect(reverse('preop-patient', args=[p.pk]))
        week_q = f'?week={visit_date.isoformat()}' if visit_date else ''
        return HttpResponseRedirect(reverse('preop') + week_q)


class PreopPatientView(LoginRequiredMixin, generic.View):
    """Individual Preoperative Assessment Workspace for one patient (5 fast tabs)."""
    template_name = 'mdf/preop/patient.html'

    def get(self, request, pk):
        patient = get_object_or_404(
            PreopPatient.objects.select_related('surgeon', 'surgeon__profile', 'approved_by', 'alerts_ack_by', 'case'),
            pk=pk,
        )
        return self._render(request, patient)

    def post(self, request, pk):
        patient = get_object_or_404(PreopPatient, pk=pk)
        section = request.POST.get('section') or 'header'

        if section == 'header':
            self._save_header(request, patient)
            messages.success(request, 'პაციენტის მონაცემები განახლდა.')
            return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + f"?tab={request.POST.get('tab', 'q')}")

        if section == 'questionnaire':
            self._save_questionnaire(request, patient)
            messages.success(request, 'კითხვარები შეინახა — განახლდა ანამნეზი და მომზადების გეგმა.')
            return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=s1')

        if section == 'stage1':
            self._save_stage1(request, patient)
            messages.success(request, 'საფეხური 1-ის ანალიზები და კვლევები შეინახა.')
            return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=s2')

        if section == 'stage2':
            self._save_stage2(request, patient)
            messages.success(request, 'მომზადების გეგმა და კონსულტაციები შეინახა.')
            return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=s2')

        if section == 'signoff':
            return self._save_signoff(request, patient)

        if section == 'dos_vqi':
            return self._save_dos_vqi(request, patient)

        if section == 'self_link':
            return self._save_self_link(request, patient)

        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]))

    def _save_header(self, request, patient):
        s = merged_studies(patient)
        today = timezone.localdate()
        old_someday = patient.someday_list
        name = clean_text(request.POST.get('patient_name'), 150).replace('\n', ' ')
        if name:
            patient.patient_name = name
        age_in = _int(request.POST.get('age'), lo=1, hi=120)
        if 'person_id' in request.POST:
            s['person_id'] = _clean_person_id(request.POST.get('person_id'))
        if 'birth_date' in request.POST:
            bd = _date(request.POST.get('birth_date'))
            s['birth_date'] = bd.isoformat() if (bd and bd <= today) else ''
            if age_in is None and bd:
                age_in = _age_from_dob(bd)
        if age_in is not None or 'age' in request.POST:
            patient.age = age_in
        if 'planned_surgery_date' in request.POST:
            op_d = _date(request.POST.get('planned_surgery_date'))
            s['planned_surgery_date'] = op_d.isoformat() if op_d else ''

        sex = request.POST.get('sex') or ''
        if sex in ('M', 'F', ''):
            patient.sex = sex
        if 'side' in request.POST and request.POST.get('side') in ('', 'R', 'L', 'B'):
            new_side = request.POST.get('side')
            if new_side != patient.side:
                s['dos_site_marked'] = False
            patient.side = new_side
        patient.phone = clean_text(request.POST.get('phone'), 50).replace('\n', ' ')
        op_sub = request.POST.get('op_subtype') or ''
        if op_sub in PreopPatient.OpSubtype.values:
            patient.op_subtype = op_sub
        surg_raw = request.POST.get('surgeon') or ''
        patient.surgeon = get_user_model().objects.filter(pk=int(surg_raw)).first() if surg_raw.isdigit() else None

        slot_kind = request.POST.get('slot_kind') or ''
        if slot_kind.startswith('someday:'):
            key = slot_kind.split(':', 1)[1]
            patient.someday_list = key if key in PreopPatient.SomedayList.values else PreopPatient.SomedayList.CONF
            patient.visit_date = None
        else:
            vd = _date(request.POST.get('visit_date'))
            if vd:
                patient.visit_date = vd
                patient.someday_list = ''
                if not patient.started_on:
                    patient.started_on = vd
        _update_hold_pause(s, old_someday, patient.someday_list, today=today)
        patient.studies = s
        patient.updated_by = request.user
        patient.save()

    def _save_questionnaire(self, request, patient):
        apply_questionnaire_post(request.POST, patient, person_name(request.user))
        patient.updated_by = request.user
        patient.save()

    def _save_stage1(self, request, patient):
        s = merged_studies(patient)
        today = timezone.localdate()

        if 'wbc' in request.POST:
            raw_wbc = _num(request.POST.get('wbc'), lo=0.1, hi=150000)
            if raw_wbc is not None and raw_wbc >= 500:
                raw_wbc = round(raw_wbc / 1000.0, 2)
            s['wbc'] = _num(raw_wbc, lo=0.1, hi=150) if raw_wbc is not None else None
        if 'plt' in request.POST:
            raw_plt = _num(request.POST.get('plt'), lo=1, hi=2000000)
            if raw_plt is not None and raw_plt >= 10000:
                raw_plt = int(round(raw_plt / 1000.0))
            s['plt'] = _num(raw_plt, lo=1, hi=2000) if raw_plt is not None else None

        num_fields = (
            ('aaa_mm', 10, 200), ('cta_months', 0, 120),
            ('ven_gsv_mm', 1, 50), ('pad_gsv_mm', 0.5, 30),
            ('abi_r', 0.0, 3.0), ('abi_l', 0.0, 3.0),
            ('tp_r', 0, 400), ('tp_l', 0, 400),
            ('tbi_r', 0.0, 3.0), ('tbi_l', 0.0, 3.0),
            ('bp_sys', 40, 300), ('bp_dia', 20, 200),
            ('bp_sys_l', 40, 300), ('bp_dia_l', 20, 200),
            ('hr', 20, 250), ('spo2', 50, 100),
            ('weight_kg', 25, 260), ('height_cm', 100, 230),
            ('hb', 1, 260), ('inr', 0.5, 15), ('fibrinogen', 0.2, 15),
            ('creat', 0.1, 3000), ('k', 1.5, 10), ('na', 100, 180),
            ('glucose', 0.5, 1000), ('hba1c', 3, 25), ('ldl', 0.2, 20),
            ('ferritin', 1, 5000), ('tsat', 1, 100),
            ('nt_probnp', 1, 50000), ('hs_ctn', 0.1, 50000),
            ('albumin', 0.5, 80),
            ('urine_wbc', 0, 1000),
            ('carotid_r', 0, 100), ('carotid_l', 0, 100), ('lvef', 5, 90),
        )
        for key, lo, hi in num_fields:
            if key in request.POST:
                s[key] = _num(request.POST.get(key), lo=lo, hi=hi)

        if 'cta_date' in request.POST:
            cd = _date(request.POST.get('cta_date'))
            s['cta_date'] = cd.isoformat() if (cd and cd <= today) else ''
            if cd and cd <= today:
                s['cta_months'] = max(0, (today - cd).days // 30)

        if 'labs_date' in request.POST:
            ld = _date(request.POST.get('labs_date'))
            s['labs_date'] = ld.isoformat() if (ld and ld <= today) else ''
            if ld and ld <= today and (today - ld).days <= 14:
                s['stale_labs_refreshed'] = False

        if 'creat_unit' in request.POST and request.POST.get('creat_unit') in ('', 'umol', 'mgdl'):
            s['creat_unit'] = request.POST.get('creat_unit')

        for bf in (
            'cta_disc', 'irregular_hr', 'aptt_prolonged', 'viral_positive',
            'urine_nitrite', 'carotid_symp', 'echo_valve_issue',
            'aaa_renal_vein_anom', 'abf_renal_stenosis',
            'ven_epifascial', 'ven_tortuous', 'ven_perforator',
        ):
            if bf in request.POST or bf in ('irregular_hr', 'aptt_prolonged', 'viral_positive'):
                s[bf] = request.POST.get(bf) in ('1', 'on', 'true')
            elif bf == 'cta_disc' and patient.pathway not in (PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA):
                s[bf] = False
            elif bf == 'aaa_renal_vein_anom' and patient.pathway == PreopPatient.Pathway.AAA_INFRA:
                s[bf] = False
            elif bf == 'abf_renal_stenosis' and patient.pathway == PreopPatient.Pathway.ABF:
                s[bf] = False
            elif bf in ('ven_epifascial', 'ven_tortuous', 'ven_perforator') and patient.pathway == PreopPatient.Pathway.VENOUS:
                s[bf] = False
            elif bf in ('urine_nitrite', 'carotid_symp', 'echo_valve_issue') and patient.pathway not in (PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA):
                s[bf] = False

        if 'blood_group' in request.POST:
            s['blood_group'] = clean_text(request.POST.get('blood_group'), 30)
        for sel_key, allowed in (
            ('liver_status', ('normal', '2x', '3x')),
            ('cxr', ('pending', 'normal', 'emphysema', 'infiltrate')),
            ('ecg', ('pending', 'normal', 'afib_controlled', 'ischemia_arrhythmia')),
            ('aaa_morphology', ('fusiform', 'saccular', 'rapid_growth', 'inflammatory', 'inflammatory_mycotic')),
            ('aaa_neck_status', ('pending', 'friendly_ifu', 'hostile_neck')),
            ('popliteal_screen_status', ('pending', 'normal', 'aneurysm_found')),
            ('aaa_iliac_access', ('pending', 'ok_7mm', 'narrow_calcified', 'iliac_aneurysm')),
            ('cea_cta_status', ('pending', 'concordant', 'high_calcified')),
            ('brain_imaging', ('pending', 'normal_small', 'large_or_hemorrhage')),
            ('abf_clamp_zone', ('pending', 'infrarenal', 'juxtarenal_calcified')),
            ('abf_outflow', ('pending', 'patent', 'profunda_stenosis', 'poor_outflow')),
            ('abf_mesenteric', ('normal', 'ima_dominant_iia_occ', 'sma_stenosis')),
            ('pad_runoff_status', ('pending', 'popliteal_patent', 'tibial_single', 'desert_foot')),
            ('pad_gsv_status', ('pending', 'adequate', 'inadequate')),
            ('pta_lesion_site', ('iliac', 'femoropopliteal', 'infrapopliteal', 'multilevel')),
            ('pta_access_site', ('pending', 'cfa_clean', 'cfa_calcified_scar')),
            ('ven_duplex_status', ('pending', 'deep_patent_reflux', 'acute_dvt', 'deep_obstruction', 'superficial_thrombus')),
            ('ven_target_vein', ('gsv', 'ssv', 'gsv_ssv', 'aagsv_trib')),
        ):
            if request.POST.get(sel_key) in allowed:
                s[sel_key] = request.POST.get(sel_key)

        if not patient.started_on:
            patient.started_on = patient.visit_date or today
        patient.studies = s
        patient.updated_by = request.user
        patient.save()

    def _save_stage2(self, request, patient):
        s = merged_studies(patient)
        old_nurse_chips = list(s.get('nurse_note_chips') or [])
        old_nurse_chip_txt = chip_summary(old_nurse_chips, 'nurse_note_chips')
        old_cardio_chips = list(s.get('cardio_note_chips') or [])
        old_cardio_chip_txt = chip_summary(old_cardio_chips, 'cardio_note_chips')

        if 'spiro_fev1' in request.POST:
            s['spiro_fev1'] = _int(request.POST.get('spiro_fev1'), lo=10, hi=150)
        if 'abi_r' in request.POST:
            s['abi_r'] = _num(request.POST.get('abi_r'), lo=0.0, hi=3.0)
        if 'abi_l' in request.POST:
            s['abi_l'] = _num(request.POST.get('abi_l'), lo=0.0, hi=3.0)
        if 'tp_r' in request.POST:
            s['tp_r'] = _int(request.POST.get('tp_r'), lo=0, hi=400)
        if 'tp_l' in request.POST:
            s['tp_l'] = _int(request.POST.get('tp_l'), lo=0, hi=400)
        if 'tbi_r' in request.POST:
            s['tbi_r'] = _num(request.POST.get('tbi_r'), lo=0.0, hi=3.0)
        if 'tbi_l' in request.POST:
            s['tbi_l'] = _num(request.POST.get('tbi_l'), lo=0.0, hi=3.0)
        if 'hba1c' in request.POST:
            s['hba1c'] = _num(request.POST.get('hba1c'), lo=3, hi=25)
        if 'ferritin' in request.POST:
            s['ferritin'] = _num(request.POST.get('ferritin'), lo=1, hi=5000)
        if 'tsat' in request.POST:
            s['tsat'] = _num(request.POST.get('tsat'), lo=1, hi=100)

        if 'planned_surgery_date' in request.POST:
            op_d = _date(request.POST.get('planned_surgery_date'))
            s['planned_surgery_date'] = op_d.isoformat() if op_d else ''

        for sel_key, allowed in (
            ('anesthesia_plan', ('auto', 'local', 'plexus', 'neuraxial', 'general')),
            ('asa_class', ('auto', 'I', 'II', 'III', 'IV')),
            ('airway_mallampati', ('I_II', 'III', 'IV_difficult')),
            ('stop_bang_risk', ('auto', 'low', 'intermediate', 'high')),
            ('surg_duration_est', ('auto', 'lt_2h', '2_3h', 'gt_3h')),
            ('cied_plan', ('pending', 'bipolar_only', 'magnet_async', 'ep_reprogram')),
            ('gastro_result', ('pending', 'remission', 'acute_ulcer')),
            ('laryngoscopy_res', ('pending', 'normal', 'palsy')),
            ('tsh_status', ('pending', 'euthyroid', 'hyperthyroid')),
            ('alt_vein_mapped', ('pending', 'contra_gsv_ok', 'arm_ssv_ok', 'no_autovein_prosthetic')),
            ('post_antithrombotic_plan', ('auto', 'voyager', 'dapt', 'sapt', 'oac_sapt')),
        ):
            if request.POST.get(sel_key) in allowed:
                s[sel_key] = request.POST.get(sel_key)

        for bf in (
            'urine_culture_done', 'blood_reserved', 'evar_sized', 'med_hold_agreed',
            'anesth_done', 'neuro_consult_done', 'wound_care_done',
            'vte_prophylaxis_planned', 'compression_ready',
            'anemia_panel_done', 'pbm_iv_iron_done', 'cell_saver_ready', 'ppi_started',
            'endo_consult_done', 'nephro_panel_done',
            'hydration_planned', 'dental_sanitation_done', 'decolonization_ready',
            'contrast_premed_done', 'dapt_ready', 'stale_labs_refreshed',
            'cea_antiplatelet_started', 'cied_checked',
        ):
            s[bf] = request.POST.get(bf) in ('1', 'on', 'true')
        if (s.get('cied_plan') or 'pending') != 'pending':
            s['cied_checked'] = True

        cab_present = 'anesth_cabinet_present' in request.POST
        if cab_present or 'asa_emergency' in request.POST:
            s['asa_emergency'] = request.POST.get('asa_emergency') in ('1', 'on', 'true')
        if 'icu_bed_field_present' in request.POST or 'icu_bed_booked' in request.POST:
            s['icu_bed_booked'] = request.POST.get('icu_bed_booked') in ('1', 'on', 'true')

        if hasattr(request.POST, 'getlist'):
            s['nurse_note_chips'] = [
                k for k in request.POST.getlist('nurse_note_chips')
                if k in CHIP_LABELS['nurse_note_chips']
            ]
            s['cardio_note_chips'] = [
                k for k in request.POST.getlist('cardio_note_chips')
                if k in CHIP_LABELS['cardio_note_chips']
            ]
            if cab_present or 'anesth_lines_chips' in request.POST:
                s['anesth_lines_chips'] = [
                    k for k in request.POST.getlist('anesth_lines_chips')
                    if k in CHIP_LABELS['anesth_lines_chips']
                ]

        new_nurse_chip_txt = chip_summary(s.get('nurse_note_chips'), 'nurse_note_chips')
        if 'nurse_note_custom' in request.POST:
            custom_nn = clean_text(request.POST.get('nurse_note_custom'), 1000)
            s['nurse_note_custom'] = custom_nn
            s['nurse_note'] = '; '.join([x for x in (new_nurse_chip_txt, custom_nn) if x])[:1000]
        else:
            raw_nurse = clean_text(request.POST.get('nurse_note'), 1000)
            if raw_nurse == old_nurse_chip_txt and s['nurse_note_chips'] != old_nurse_chips:
                raw_nurse = ''
            s['nurse_note_custom'] = '' if raw_nurse == new_nurse_chip_txt else raw_nurse
            s['nurse_note'] = raw_nurse or new_nurse_chip_txt

        if 'cardiologist' in request.POST or 'cardiologist_custom' in request.POST:
            cardio_name = clean_text(
                request.POST.get('cardiologist_custom') or request.POST.get('cardiologist'), 100
            ).replace('\n', ' ')
            patient.cardiologist = cardio_name or 'ლიკა ინჯია'
        if 'cardio_decision' in request.POST:
            dec = request.POST.get('cardio_decision') or ''
            if dec in PreopPatient.CardioDecision.values:
                old_dec = patient.cardio_decision
                patient.cardio_decision = dec
                if dec and not patient.cardio_date:
                    patient.cardio_date = timezone.localdate()
                elif not dec:
                    patient.cardio_date = None
                if dec != old_dec and dec in ('stress', 'cag', 'pci'):
                    patient.alerts_ack_at = None
                    s['alerts_ack_sig'] = ''

        new_cardio_chip_txt = chip_summary(s.get('cardio_note_chips'), 'cardio_note_chips')
        if 'cardio_note_custom' in request.POST:
            custom_cn = clean_text(request.POST.get('cardio_note_custom'), 255).replace('\n', ' ')
            s['cardio_note_custom'] = custom_cn
            patient.cardio_note = '; '.join([x for x in (new_cardio_chip_txt, custom_cn) if x])[:255]
        elif 'cardio_note' in request.POST or 'cardio_note_chips' in request.POST:
            raw_cn = clean_text(request.POST.get('cardio_note'), 255).replace('\n', ' ')
            if raw_cn == old_cardio_chip_txt and s['cardio_note_chips'] != old_cardio_chips:
                raw_cn = ''
            s['cardio_note_custom'] = '' if raw_cn == new_cardio_chip_txt else raw_cn
            patient.cardio_note = (raw_cn or new_cardio_chip_txt)[:255]

        patient.studies = s
        patient.updated_by = request.user
        patient.save()

    def _save_dos_vqi(self, request, patient):
        s = merged_studies(patient)
        today = timezone.localdate()
        action = request.POST.get('action') or 'save_dos'
        form_present = 'dos_form_present' in request.POST

        if 'planned_surgery_date' in request.POST:
            op_d = _date(request.POST.get('planned_surgery_date'))
            s['planned_surgery_date'] = op_d.isoformat() if op_d else ''
        if 'operated_date' in request.POST:
            done_d = _date(request.POST.get('operated_date'))
            s['operated_date'] = done_d.isoformat() if (done_d and done_d <= today) else ''

        for key, lo, hi in (
            ('dos_bp_sys', 40, 300),
            ('dos_bp_dia', 20, 200),
            ('dos_glucose', 0.5, 1000),
            ('dos_inr', 0.5, 15),
        ):
            if key in request.POST:
                s[key] = _num(request.POST.get(key), lo=lo, hi=hi)

        for bf in (
            'dos_identity_consent', 'dos_site_marked', 'dos_fasting_confirmed',
            'dos_med_hold_verified', 'dos_abx_ready', 'dos_blood_implant_ready',
            'icu_bed_booked',
            'vqi_discharge_antiplatelet', 'vqi_discharge_statin',
            'vqi_smoking_cessation', 'vqi_surveillance_scheduled',
        ):
            if form_present or bf in request.POST:
                s[bf] = request.POST.get(bf) in ('1', 'on', 'true')

        if request.POST.get('case_outcome_status') in dict(CASE_OUTCOME_CHOICES):
            s['case_outcome_status'] = request.POST.get('case_outcome_status')
        if request.POST.get('vqi_30d_status') in dict(VQI_30D_CHOICES):
            s['vqi_30d_status'] = request.POST.get('vqi_30d_status')

        if hasattr(request.POST, 'getlist'):
            if form_present or 'cancel_reason_chips' in request.POST:
                s['cancel_reason_chips'] = [
                    k for k in request.POST.getlist('cancel_reason_chips')
                    if k in CHIP_LABELS['cancel_reason_chips']
                ]
            if form_present or 'vqi_complication_chips' in request.POST:
                s['vqi_complication_chips'] = [
                    k for k in request.POST.getlist('vqi_complication_chips')
                    if k in CHIP_LABELS['vqi_complication_chips']
                ]
                if s['vqi_complication_chips'] and s.get('vqi_30d_status') in ('pending', 'uneventful', None, ''):
                    s['vqi_30d_status'] = 'complication'

        if action == 'complete_and_board':
            s['case_outcome_status'] = 'operated'
            if not s.get('operated_date'):
                s['operated_date'] = today.isoformat()
            s['outcome_recorded_on'] = today.isoformat()
            patient.studies = s
            patient.updated_by = request.user
            patient.save()
            bp, _, added_tasks = admit_preop_to_board(
                patient,
                request.user,
                unit=request.POST.get('unit') or '',
                bed=request.POST.get('bed') or '',
            )
            messages.success(
                request,
                f'{patient.patient_name} — ოპერაცია დაფიქსირდა და პაციენტი გადაყვანილია დაფაზე ({bp.get_unit_display()}, +{added_tasks} დავალება).',
            )
            return HttpResponseRedirect(reverse('board-patient', args=[bp.pk]))

        if s.get('case_outcome_status') == 'operated' and not s.get('operated_date'):
            plan_dt = _date(s.get('planned_surgery_date'))
            s['operated_date'] = (plan_dt.isoformat() if (plan_dt and plan_dt <= today) else today.isoformat())
        if s.get('case_outcome_status') != 'scheduled' and not s.get('outcome_recorded_on'):
            s['outcome_recorded_on'] = today.isoformat()
        elif s.get('case_outcome_status') == 'scheduled':
            s['outcome_recorded_on'] = ''

        patient.studies = s
        patient.updated_by = request.user
        patient.save()
        messages.success(request, 'ოპერაციის დილის კარიბჭე და 30-დღიანი VQI მონიტორინგი შეინახა.')
        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=dos')

    def _save_signoff(self, request, patient):
        action = request.POST.get('action') or 'save_anamnesis'
        # Only persist anamnesis on explicit save_anamnesis / approve (NEVER freeze auto-anamnesis on Day-1 ack_alerts or unapprove!)
        if 'anamnesis' in request.POST:
            if action == 'regenerate':
                patient.anamnesis = ''
            elif action in ('save_anamnesis', 'approve'):
                patient.anamnesis = clean_text(request.POST.get('anamnesis'), 4000)

        ev = evaluate(patient)
        s = merged_studies(patient)

        if action == 'ack_alerts':
            s['alerts_ack_sig'] = ev['alerts_sig']
            patient.studies = s
            patient.alerts_ack_at = timezone.now()
            patient.alerts_ack_by = request.user
            messages.success(request, 'გაფრთხილებები მონიშნულია როგორც გაცნობილი.')
        elif action == 'approve':
            if ev.get('has_hard_contraindication'):
                messages.error(request, 'აბსოლუტური კლინიკური უკუჩვენების გამო ოპერაციაზე დადასტურება დაბლოკილია!')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=doc')
            s['alerts_ack_sig'] = ev['alerts_sig']
            if request.POST.get('move_ready') == '1':
                _update_hold_pause(s, patient.someday_list, PreopPatient.SomedayList.READY)
                patient.someday_list = PreopPatient.SomedayList.READY
                patient.visit_date = None
            patient.studies = s
            patient.approved_at = timezone.now()
            patient.approved_by = request.user
            messages.success(request, f'{patient.patient_name} — დადასტურებულია ოპერაციისთვის.')
        elif action == 'unapprove':
            patient.approved_at = None
            patient.approved_by = None
            messages.info(request, 'დადასტურება მოიხსნა.')
        else:
            messages.success(request, 'ანამნეზი შეინახა.')

        patient.updated_by = request.user
        patient.save()
        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=doc')

    def _save_self_link(self, request, patient):
        act = request.POST.get('action') or 'rotate'
        q = merged_q(patient)
        s = merged_studies(patient)
        if act == 'revoke':
            s['self_fill_revoked'] = True
            messages.info(request, 'პაციენტის თვითშევსების ბმული გაუქმდა.')
        else:
            s['self_fill_nonce'] = secrets.token_hex(4)
            s['self_fill_revoked'] = False
            q['self_fill_submitted'] = False
            patient.questionnaire = q
            messages.success(request, 'ახალი ერთჯერადი ბმული გენერირდა (წინა ბმული გაუქმდა).')
        patient.studies = s
        patient.updated_by = request.user
        patient.save()
        tab_ret = request.POST.get('tab') or 'q'
        if tab_ret not in ('q', 's1', 's2', 'doc', 'dos'):
            tab_ret = 'q'
        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + f'?tab={tab_ret}')

    def _render(self, request, patient):
        ev = evaluate(patient)
        tab = request.GET.get('tab') or ('q' if not ev['q'].get('filled') else 's1')
        if tab not in ('q', 's1', 's2', 'doc', 'dos'):
            tab = 'q'
        auto_anamnesis = generate_anamnesis(patient, ev)
        files = list(patient.files.select_related('created_by', 'created_by__profile'))
        files_by_cat = []
        for key, label in PreopFile.Category.choices:
            cat_items = [f for f in files if f.category == key]
            if cat_items:
                files_by_cat.append({'key': key, 'label': label, 'files': cat_items})

        from .blood import BLOOD_GROUPS as BG_LIST
        self_token = make_self_fill_token(patient)
        board_patient = resolve_board_patient(patient)
        default_board_unit = board_patient.unit if board_patient else (BoardPatient.ICU if ev['needs_icu_bed'] else BoardPatient.WARD)
        return render(request, self.template_name, {
            'p': patient,
            'ev': ev,
            'q': ev['q'],
            's': ev['s'],
            'tab': tab,
            'auto_anamnesis': auto_anamnesis,
            'shown_anamnesis': patient.anamnesis or auto_anamnesis,
            'self_fill_path': reverse('preop-self-fill', args=[self_token]),
            'self_fill_revoked': bool(ev['s'].get('self_fill_revoked')),
            'self_fill_used': bool(ev['q'].get('self_fill_submitted')),
            'consent_url': consent_url_for_preop(patient),
            'board_patient': board_patient,
            'default_board_unit': default_board_unit,
            'chips': CHIP_CATALOGS,
            'cfs_choices': CFS_CHOICES,
            'anesth_plan_choices': ANESTH_PLAN_CHOICES,
            'asa_choices': ASA_CHOICES,
            'mallampati_choices': MALLAMPATI_CHOICES,
            'stop_bang_choices': STOP_BANG_CHOICES,
            'surg_duration_choices': SURG_DURATION_CHOICES,
            'cied_plan_choices': CIED_PLAN_CHOICES,
            'case_outcome_choices': CASE_OUTCOME_CHOICES,
            'vqi_30d_choices': VQI_30D_CHOICES,
            'aaa_morphology_choices': AAA_MORPHOLOGY_CHOICES,
            'aaa_neck_choices': AAA_NECK_CHOICES,
            'popliteal_screen_choices': POPLITEAL_SCREEN_CHOICES,
            'post_antithrombotic_choices': POST_ANTITHROMBOTIC_CHOICES,
            'mets_limit_choices': METS_LIMIT_CHOICES,
            'duration_chips': DURATION_CHIPS,
            'ceap_choices': CEAP_CHOICES,
            'doctors': doctors(),
            'op_subtype_groups': OP_SUBTYPE_GROUPS,
            'side_choices': Case.Side.choices,
            'someday_choices': PreopPatient.SomedayList.choices[1:],
            'cardio_decisions': PreopPatient.CardioDecision.choices,
            'cardiologists': _cardiologist_choices(),
            'file_categories': PreopFile.Category.choices,
            'files_by_cat': files_by_cat,
            'total_files': len(files),
            'blood_groups': BG_LIST,
            'today': timezone.localdate(),
        })


def resolve_board_patient(patient):
    """Return active BoardPatient linked to this PreopPatient (never hijacking another PreopPatient's Board card)."""
    s = merged_studies(patient)
    bpid = s.get('board_patient_id')
    if bpid:
        bp = BoardPatient.objects.filter(pk=bpid, discharged_at__isnull=True).first()
        if bp:
            return bp
        if BoardPatient.objects.filter(pk=bpid).exists():
            return None
    if patient.patient_name:
        other_preops = (
            PreopPatient.objects.filter(patient_name__iexact=patient.patient_name)
            .exclude(pk=patient.pk)
            .values_list('studies', flat=True)[:30]
        )
        claimed_ids = {
            st.get('board_patient_id') for st in other_preops
            if isinstance(st, dict) and st.get('board_patient_id')
        }
        qs = BoardPatient.objects.filter(discharged_at__isnull=True, name__iexact=patient.patient_name)
        if claimed_ids:
            qs = qs.exclude(pk__in=claimed_ids)
        return qs.order_by('-admitted_at').first()
    return None


def resolve_preop_for_board(board_patient):
    """Return the PreopPatient linked to this BoardPatient (preserving explicit ID links even after Preop archive!)."""
    if not board_patient:
        return None
    same_name = list(
        PreopPatient.objects.filter(patient_name__iexact=board_patient.name)
        .order_by('-updated_at', '-id')[:20]
    )
    for pre in same_name:
        if isinstance(pre.studies, dict) and pre.studies.get('board_patient_id') == board_patient.pk:
            return pre
    recent = PreopPatient.objects.order_by('-updated_at', '-id')[:80]
    for pre in recent:
        if isinstance(pre.studies, dict) and pre.studies.get('board_patient_id') == board_patient.pk:
            return pre
    for pre in same_name:
        if not pre.archived and not (isinstance(pre.studies, dict) and pre.studies.get('board_patient_id')):
            return pre
    return None


def admit_preop_to_board(patient, user, unit='', bed=''):
    """1-click clinical handoff from PreopPatient to BoardPatient + initial STATUS & duty TASKs."""
    ev = evaluate(patient)
    q, s = ev['q'], ev['s']
    pathway = ev['pathway']
    now = timezone.now()

    target_unit = (
        unit if unit in (BoardPatient.ICU, BoardPatient.WARD)
        else (BoardPatient.ICU if ev['needs_icu_bed'] else BoardPatient.WARD)
    )
    bed_clean = clean_text(bed, 20).replace('\n', ' ')
    diag_str = clean_text(patient.planned_operation_label, 255).replace('\n', ' ')
    attending = patient.surgeon or user

    bp = resolve_board_patient(patient)
    was_created = False
    if bp is None:
        bp = BoardPatient.objects.create(
            name=patient.patient_name,
            bed=bed_clean,
            diagnosis=diag_str,
            unit=target_unit,
            doctor=attending,
            created_by=user,
            admitted_at=now,
        )
        add_event(bp, user, f'გადმოყვანილია წინასაოპერაციოდან — {bp.get_unit_display()}', when=now)
        was_created = True
    else:
        old_unit = bp.unit
        changed = False
        if bed_clean and bp.bed != bed_clean:
            bp.bed = bed_clean
            changed = True
        if unit in (BoardPatient.ICU, BoardPatient.WARD) and bp.unit != unit:
            bp.unit = unit
            changed = True
        if diag_str and (not bp.diagnosis or s.get('board_patient_id') == bp.pk):
            if bp.diagnosis != diag_str:
                bp.diagnosis = diag_str
                changed = True
        if patient.surgeon and bp.doctor_id != patient.surgeon_id and (not bp.doctor_id or s.get('board_patient_id') == bp.pk):
            bp.doctor = patient.surgeon
            changed = True
        elif not bp.doctor_id and attending:
            bp.doctor = attending
            changed = True
        if changed:
            bp.save()
        if old_unit != bp.unit:
            add_event(bp, user, f'გადაყვანილია: {"რეანიმაცია" if old_unit == BoardPatient.ICU else "პალატა"} → {bp.get_unit_display()}', when=now)

    # Structured initial BoardEntry.STATUS summary
    anesth_line_sfx = f" ({ev['anesth_lines_txt']})" if ev.get('anesth_lines_txt') else ''
    op_verb = (
        f"ჩატარდა ({ev['operated_date_ka']})" if (ev.get('case_outcome') == 'operated' and ev.get('operated_date_ka'))
        else ('ჩატარდა' if ev.get('case_outcome') == 'operated' else 'დაგეგმილია')
    )
    status_lines = [
        f"[წინასაოპერაციო ჰენდოფი] {op_verb}: {diag_str} · {ev['effective_asa']} · {ev['effective_anesth_label']}{anesth_line_sfx}."
    ]
    vitals_labs = []
    if s.get('bp_sys') and s.get('bp_dia'):
        vitals_labs.append(f"BP {s['bp_sys']}/{s['bp_dia']} mmHg")
    if ev['hb_gl'] is not None:
        vitals_labs.append(f"Hb {ev['hb_gl']} გ/ლ")
    if s.get('plt') is not None:
        vitals_labs.append(f"PLT {s['plt']}")
    if s.get('inr') is not None:
        vitals_labs.append(f"INR {s['inr']}")
    if ev['egfr'] is not None:
        crcl_part = f" / CrCl {ev['crcl']}" if ev.get('crcl') is not None else ''
        vitals_labs.append(f"eGFR {ev['egfr']}{crcl_part}")
    if s.get('blood_group'):
        vitals_labs.append(f"ჯგუფი: {s['blood_group']}")
    if ev.get('min_abi') is not None:
        vitals_labs.append(f"ABI {ev['min_abi']}")
    if ev.get('min_tp') is not None:
        vitals_labs.append(f"TP {ev['min_tp']} mmHg")
    if s.get('lvef') is not None:
        vitals_labs.append(f"LVEF {s['lvef']}%")
    if ev.get('nt_probnp') is not None:
        vitals_labs.append(f"NT-proBNP {ev['nt_probnp']} pg/mL")
    if ev.get('hs_ctn') is not None:
        vitals_labs.append(f"hs-cTn {ev['hs_ctn']} ng/L")
    if (s.get('ecg') or 'pending') != 'pending':
        vitals_labs.append(f"ეკგ: {ev['ecg_label']}")
    if vitals_labs:
        status_lines.append('მონაცემები: ' + ', '.join(vitals_labs) + '.')

    safety_bits = []
    allergy_chips = set(q.get('allergy_chips') or [])
    if 'hit_heparin' in allergy_chips:
        safety_bits.append('HIT ანამნეზში (ჰეპარინი/LMWH აკრძალულია!)')
    if 'beta_lactam' in allergy_chips:
        safety_bits.append('მძიმე β-ლაქტამური ალერგია (ცეფაზოლინი არა!)')
    if 'latex_chlorhex' in allergy_chips:
        safety_bits.append('ალერგია ლატექსზე / ქლორჰექსიდინზე!')
    if q.get('allergy_iodine'):
        safety_bits.append('იოდი/კონტრასტზე ალერგია!')
    if q.get('ven_lidocaine_allergy') or 'lidocaine' in allergy_chips:
        safety_bits.append('ლიდოკაინზე ალერგია!')
    if ev.get('bp_arm_diff') is not None and ev['bp_arm_diff'] >= 20:
        higher_arm = 'მარჯვენა' if (s.get('bp_sys') or 0) >= (s.get('bp_sys_l') or 0) else 'მარცხენა'
        safety_bits.append(f"წნევის მონიტორინგი {higher_arm} მკლავზე (ΔBP {ev['bp_arm_diff']} mmHg)")
    if q.get('pacemaker'):
        cied_sfx = f" — {ev['cied_plan_label']}" if ev.get('cied_plan_label') else ''
        safety_bits.append(f'კარდიოსტიმულატორი / ICD (CIED){cied_sfx}')
    if ev.get('difficult_airway_alert'):
        safety_bits.append('მოსალოდნელი რთული ინტუბაცია (ვიდეოლარინგოსკოპი!)')
    if ev.get('max_contrast_ml') is not None:
        c_str = f"მაქს. იოდის კონტრასტი ≤ {ev['max_contrast_ml']} მლ"
        if ev.get('co2_angio_advised'):
            c_str += ' (CO2 ანგიოგრაფია / IVUS)'
        safety_bits.append(c_str)
    if s.get('cell_saver_ready'):
        safety_bits.append('Cell Saver + TXA მზადყოფნა')
    if safety_bits:
        status_lines.append('უსაფრთხოება: ' + '; '.join(safety_bits) + '.')

    status_text = clean_text('\n'.join(status_lines), 4000)
    existing_handoff = (
        BoardEntry.objects.filter(patient=bp, kind=BoardEntry.STATUS, text__startswith='[წინასაოპერაციო ჰენდოფი]')
        .order_by('-id')
        .first()
    )
    if existing_handoff is None:
        BoardEntry.objects.create(patient=bp, kind=BoardEntry.STATUS, text=status_text, created_by=user, created_at=now)
    elif existing_handoff.text != status_text:
        existing_handoff.text = status_text
        existing_handoff.updated_by = user
        existing_handoff.updated_at = now
        existing_handoff.save(update_fields=['text', 'updated_by', 'updated_at'])

    # Protocol-driven duty doctor TASKs (idempotent & prefix-updatable on re-handoff)
    task_candidates = []
    if pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA):
        task_candidates.append((
            'ანტითრომბოზული სქემა და სტატინი:',
            f"ანტითრომბოზული სქემა და სტატინი: {ev['post_antithrombotic_label']} + მაღალი ინტენსივობის სტატინი (LDL <1.4 მმოლ/ლ) + დისტალური პულსაციის/დოპლერ-მონიტორინგი",
        ))
    elif pathway == PreopPatient.Pathway.CEA:
        task_candidates.append((
            'CEA პოსტოპ. ნევროლოგიური',
            'CEA პოსტოპ. ნევროლოგიური და წნევის მონიტორინგი (SBP <140–160 mmHg ჰიპერპერფუზიის პრევენციისთვის!) + ანტიაგრეგანტი და სტატინი',
        ))
    elif pathway == PreopPatient.Pathway.AAA_INFRA:
        task_candidates.append((
            'პოსტოპ. ჰემოდინამიკის',
            'პოსტოპ. ჰემოდინამიკის, დიურეზის და ქვემო კიდურების პულსაციის მონიტორინგი + ანტიაგრეგანტი/სტატინი (ESVS 2024)',
        ))
    else:
        task_candidates.append((
            'ვენური პოსტოპ. რეჟიმი:',
            'ვენური პოსტოპ. რეჟიმი: ადრეული აქტიური სიარული და ელასტიური კომპრესია პროტოკოლით',
        ))

    if 'hit_heparin' in allergy_chips:
        task_candidates.append((
            'HIT ანამნეზში!',
            'HIT ანამნეზში! არაფრაქციული ჰეპარინი და LMWH აკრძალულია — არაჰეპარინული ანტიკოაგულაცია (ფონდაპარინუქსი / არგატრობანი)',
        ))
    elif ev.get('has_full_oac'):
        task_candidates.append((
            'VTE / ანტიკოაგულაცია:',
            'VTE / ანტიკოაგულაცია: პაციენტი მუდმივ ანტიკოაგულანტზეა (OAC) — სრული ჰემოსტაზის შემდეგ განახლდეს OAC სქემა ქირურგთან შეთანხმებით (ორმაგი ანტიკოაგულაციის გარეშე!)',
        ))
    elif ev['caprini_score'] >= 3 or not ev['is_venous']:
        task_candidates.append((
            'VTE პროფილაქტიკა',
            f"VTE პროფილაქტიკა (Caprini {ev['caprini_score']} ქ. — {ev['caprini_tier']}): ადრეული მობილიზაცია + LMWH პროფილაქტიკური დოზით ჰემოსტაზის შემდეგ",
        ))

    if 'beta_lactam' in allergy_chips:
        task_candidates.append((
            'მძიმე ბეტა-ლაქტამური ალერგია!',
            'მძიმე ბეტა-ლაქტამური ალერგია! ცეფაზოლინი უკუნაჩვენებია — პროფილაქტიკა ვანკომიცინით ან კლინდამიცინით (+ გენტამიცინი)',
        ))

    if q.get('on_dialysis'):
        task_candidates.append((
            'პროგრამული დიალიზი:',
            'პროგრამული დიალიზი: შეათანხმეთ დიალიზის სეანსი და საკონტროლო K+ ოპერაციამდე/შემდეგ (უჰეპარინო რეჟიმი)',
        ))
    elif ev.get('max_contrast_ml') is not None or (ev['egfr'] is not None and ev['egfr'] < 60):
        contrast_sfx = f" (მაქს. კონტრასტი ≤ {ev['max_contrast_ml']} მლ)" if ev.get('max_contrast_ml') is not None else ''
        task_candidates.append((
            'ნეფროპროტექცია:',
            f'ნეფროპროტექცია: იზოტონური ჰიდრატაცია + საკონტროლო კრეატინინი და K+ 24–48 სთ-ში{contrast_sfx}',
        ))

    if ev.get('needs_ppi') or q.get('ulcer'):
        task_candidates.append((
            'გასტროპროტექცია:',
            'გასტროპროტექცია: ომეპრაზოლი / პანტოპრაზოლი (PPI) ანტითრომბოზული თერაპიის ფონზე (ESC 2022 Class I)',
        ))

    if q.get('pacemaker'):
        task_candidates.append((
            'კარდიოსტიმულატორი / ICD',
            'კარდიოსტიმულატორი / ICD (CIED): პოსტოპერაციული რიტმისა და აპარატის რეჟიმის გადამოწმება',
        ))

    task_candidates.append((
        'გაწერისას VQI / მეთვალყურეობის გრაფიკი:',
        f"გაწერისას VQI / მეთვალყურეობის გრაფიკი: {ev['surveillance_plan']}",
    ))

    existing_task_objs = list(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK).order_by('id'))
    added_tasks = 0
    for prefix, t_raw in task_candidates:
        t_clean = clean_text(t_raw, 4000)
        if not t_clean:
            continue
        matched = next((obj for obj in existing_task_objs if obj.text == t_clean or obj.text.startswith(prefix)), None)
        if matched is None:
            new_obj = BoardEntry.objects.create(patient=bp, kind=BoardEntry.TASK, text=t_clean, created_by=user, created_at=now)
            existing_task_objs.append(new_obj)
            added_tasks += 1
        elif matched.text != t_clean and not matched.done:
            matched.text = t_clean
            matched.updated_by = user
            matched.updated_at = now
            matched.save(update_fields=['text', 'updated_by', 'updated_at'])

    bp.save(update_fields=['updated_at'])
    s_mut = merged_studies(patient)
    s_mut['board_patient_id'] = bp.pk
    patient.studies = s_mut
    patient.updated_by = user
    patient.save(update_fields=['studies', 'updated_at', 'updated_by'])
    return bp, was_created, added_tasks


class PreopBoardHandoffView(LoginRequiredMixin, generic.View):
    """1-click POST endpoint transferring a PreopPatient to the Inpatient Board ('პაციენტების დაფა')."""

    def post(self, request, pk):
        patient = get_object_or_404(
            PreopPatient.objects.select_related('surgeon', 'surgeon__profile'),
            pk=pk,
        )
        unit = request.POST.get('unit') or ''
        bed = request.POST.get('bed') or ''
        bp, was_created, added_tasks = admit_preop_to_board(patient, request.user, unit=unit, bed=bed)
        if was_created:
            messages.success(
                request,
                f'{bp.name} გადმოყვანილია პაციენტების დაფაზე ({bp.get_unit_display()}, დაემატა {added_tasks} პროტოკოლური დავალება).',
            )
        else:
            messages.success(request, f'{bp.name} — დაფის ჰენდოფი განახლდა.')
        return HttpResponseRedirect(reverse('board-patient', args=[bp.pk]))


class PreopPassportView(LoginRequiredMixin, generic.TemplateView):
    """1-Page A4 Printable Preoperative Passport ('წინასაოპერაციო პასპორტი') for the patient chart."""
    template_name = 'mdf/preop/passport.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        patient = get_object_or_404(
            PreopPatient.objects.select_related('surgeon', 'surgeon__profile', 'approved_by'),
            pk=kwargs['pk'],
        )
        ev = evaluate(patient)
        auto_anam = generate_anamnesis(patient, ev)
        ctx.update({
            'p': patient,
            'ev': ev,
            'q': ev['q'],
            's': ev['s'],
            'shown_anamnesis': patient.anamnesis or auto_anam,
            'surgeon_name': person_name(patient.surgeon),
            'approved_by_name': person_name(patient.approved_by) if patient.approved_by else '',
            'printed_on': timezone.localtime().strftime('%d.%m.%Y %H:%M'),
        })
        return ctx


class PreopSelfFillView(generic.View):
    """Signed, one-time, revocable Patient Self-Questionnaire link with PII masking & strict privacy headers."""
    template_name = 'mdf/preop/self_fill.html'

    def dispatch(self, request, *args, **kwargs):
        resp = super().dispatch(request, *args, **kwargs)
        resp['Referrer-Policy'] = 'no-referrer'
        resp['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        resp['Cache-Control'] = 'no-store, private'
        return resp

    def _load_patient(self, token):
        signer = TimestampSigner(salt=SELF_FILL_SALT)
        try:
            raw = signer.unsign(token, max_age=SELF_FILL_MAX_AGE)
            pk_str, _, tok_nonce = raw.partition(':')
            pk = int(pk_str)
        except (BadSignature, SignatureExpired, ValueError):
            return None
        patient = PreopPatient.objects.filter(pk=pk, archived=False).first()
        if not patient:
            return None
        s = merged_studies(patient)
        if s.get('self_fill_revoked'):
            return None
        cur_nonce = str(s.get('self_fill_nonce') or '').strip()
        if tok_nonce != cur_nonce:
            return None
        return patient

    def get(self, request, token):
        patient = self._load_patient(token)
        if not patient:
            return render(request, self.template_name, {'invalid_link': True}, status=404)
        ev = evaluate(patient)
        staff_locked = bool(patient.approved_at or ev['q'].get('nurse_verified') or ev['case_outcome'] != 'scheduled')
        submitted_once = bool(ev['q'].get('self_fill_submitted'))
        is_done_screen = (request.GET.get('done') == '1') and submitted_once and not staff_locked
        locked = staff_locked or (submitted_once and not is_done_screen)
        emergency_symptom = bool(ev['q'].get('aaa_pain') or ev['q'].get('cea_crescendo'))
        return render(request, self.template_name, {
            'p': patient,
            'ev': ev,
            'q': ev['q'],
            's': ev['s'],
            'chips': CHIP_CATALOGS,
            'self_med_hints': SELF_FILL_MED_HINTS,
            'mets_limit_choices': METS_LIMIT_CHOICES,
            'duration_chips': DURATION_CHIPS,
            'ceap_choices': CEAP_CHOICES,
            'locked': locked,
            'staff_locked': staff_locked,
            'masked_person_id': mask_person_id(ev['person_id']),
            'masked_phone': mask_phone(patient.phone),
            'has_birth_date': bool(ev['s'].get('birth_date')),
            'emergency_symptom': emergency_symptom,
            'submitted': is_done_screen,
        })

    def post(self, request, token):
        patient = self._load_patient(token)
        if not patient:
            return render(request, self.template_name, {'invalid_link': True}, status=404)
        q_cur = merged_q(patient)
        s_cur = merged_studies(patient)
        if (
            patient.approved_at
            or q_cur.get('nurse_verified')
            or q_cur.get('self_fill_submitted')
            or (s_cur.get('case_outcome_status') or 'scheduled') != 'scheduled'
        ):
            return HttpResponseRedirect(reverse('preop-self-fill', args=[token]) + '?locked=1')
        age_posted = _int(request.POST.get('age'), lo=12, hi=120) if request.POST.get('age') else None
        if age_posted and not patient.age:
            patient.age = age_posted
        if not patient.sex and request.POST.get('sex') in ('M', 'F'):
            patient.sex = request.POST.get('sex')
        if not patient.phone and 'phone' in request.POST:
            ph = clean_text(request.POST.get('phone'), 50).replace('\n', ' ')
            if ph:
                patient.phone = ph
        apply_self_fill_post(request.POST, patient)
        s_mut = merged_studies(patient)
        pid = _clean_person_id(request.POST.get('person_id'))
        if pid and not s_mut.get('person_id'):
            s_mut['person_id'] = pid
        bd = _date(request.POST.get('birth_date'))
        if bd and bd <= timezone.localdate() and not s_mut.get('birth_date'):
            s_mut['birth_date'] = bd.isoformat()
            if not patient.age:
                patient.age = _age_from_dob(bd)
        patient.studies = s_mut
        patient.save()
        return HttpResponseRedirect(reverse('preop-self-fill', args=[token]) + '?done=1')


class PreopMoveView(LoginRequiredMixin, generic.View):
    """Move a patient between dates / Someday lists (drag & drop AJAX or form POST), archive, or delete."""

    def post(self, request, pk):
        patient = get_object_or_404(PreopPatient, pk=pk)
        action = request.POST.get('action') or 'move'
        ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

        if action == 'archive':
            patient.archived = not patient.archived
            patient.updated_by = request.user
            patient.save(update_fields=['archived', 'updated_at', 'updated_by'])
            if ajax:
                return JsonResponse({'ok': True, 'archived': patient.archived})
            messages.info(request, f'{patient.patient_name} {"გადავიდა არქივში" if patient.archived else "დაბრუნდა სიაში"}.')
            return HttpResponseRedirect(reverse('preop'))

        if action == 'delete':
            name = patient.patient_name
            patient.delete()
            if ajax:
                return JsonResponse({'ok': True, 'deleted': True})
            messages.success(request, f'{name} წაიშალა წინასაოპერაციო სიიდან.')
            return HttpResponseRedirect(reverse('preop'))

        old_someday = patient.someday_list
        s_mut = merged_studies(patient)
        slot = (request.POST.get('slot') or '').strip()
        if slot.startswith('someday:'):
            key = slot.split(':', 1)[1]
            if key not in PreopPatient.SomedayList.values or not key:
                return JsonResponse({'error': 'bad_slot'}, status=400) if ajax else HttpResponseRedirect(reverse('preop'))
            patient.someday_list = key
            patient.visit_date = None
        else:
            vd = _date(slot)
            if not vd:
                return JsonResponse({'error': 'bad_date'}, status=400) if ajax else HttpResponseRedirect(reverse('preop'))
            patient.visit_date = vd
            patient.someday_list = ''
            if not patient.started_on:
                patient.started_on = vd

        _update_hold_pause(s_mut, old_someday, patient.someday_list)
        patient.studies = s_mut
        patient.updated_by = request.user
        patient.save(update_fields=['visit_date', 'someday_list', 'started_on', 'studies', 'updated_at', 'updated_by'])
        if ajax:
            ev = evaluate(patient)
            return JsonResponse({'ok': True, 'days_elapsed': ev['days_elapsed'], 'status': ev['status']})
        return HttpResponseRedirect(reverse('preop'))


class PreopFileUploadView(LoginRequiredMixin, generic.View):
    """Upload one or more photos or PDF reports to a preoperative patient card."""

    def post(self, request, pk):
        patient = get_object_or_404(PreopPatient, pk=pk)
        category = request.POST.get('category') or PreopFile.Category.LABS
        caption = request.POST.get('caption') or ''
        uploaded_on = _date(request.POST.get('uploaded_on'))
        if uploaded_on and uploaded_on > timezone.localdate():
            uploaded_on = None

        files = request.FILES.getlist('file')
        added, errors = 0, []
        for f in files[:20]:
            try:
                save_preop_file(patient, f, category, caption, request.user, uploaded_on)
                added += 1
            except PhotoError as e:
                errors.append(f'{f.name}: {e}')

        for err in errors:
            messages.error(request, err)
        if added:
            messages.success(request, f'დაემატა {added} ფაილი.')
        elif not errors:
            messages.error(request, 'ფაილი არ არის არჩეული.')
        tab = request.POST.get('tab') or 's1'
        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + f'?tab={tab}#files')


class PreopFileView(LoginRequiredMixin, generic.View):
    """Serve a private preoperative photo or PDF to authenticated clinic staff only."""

    def get(self, request, pk, size='full'):
        rec = get_object_or_404(PreopFile, pk=pk)
        if size == 'thumb' and not rec.is_pdf and rec.thumb:
            field = rec.thumb
            ctype = 'image/jpeg'
        else:
            field = rec.file
            ctype = 'application/pdf' if rec.is_pdf else 'image/jpeg'
        try:
            fh = field.open('rb')
        except (FileNotFoundError, ValueError):
            raise Http404
        response = FileResponse(fh, content_type=ctype)
        response['Cache-Control'] = 'private, max-age=604800'
        return response


class PreopFileDeleteView(LoginRequiredMixin, generic.View):

    def post(self, request, pk):
        rec = get_object_or_404(PreopFile, pk=pk)
        patient_id = rec.patient_id
        rec.delete()
        messages.success(request, 'ფაილი წაიშალა.')
        return HttpResponseRedirect(reverse('preop-patient', args=[patient_id]) + '?tab=s1#files')


class PreopAuditView(LoginRequiredMixin, generic.TemplateView):
    """Cardiology Consultation, Theatre Cancellation & 30-Day SVS VQI / NVR Quality Audit."""
    template_name = 'mdf/preop/audit.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        pathway_filter = self.request.GET.get('pathway', 'all')
        if pathway_filter not in ('all', *PreopPatient.Pathway.values):
            pathway_filter = 'all'

        base_qs = PreopPatient.objects.select_related('surgeon', 'surgeon__profile').order_by('-created_at', '-id')
        if pathway_filter != 'all':
            base_qs = base_qs.filter(pathway=pathway_filter)
        qs = list(base_qs[:500])

        # Active still-hospitalized BoardPatient IDs (excluded from VQI discharge medication denominator until discharged!)
        active_inpatient_bp_ids = set(
            BoardPatient.objects.filter(discharged_at__isnull=True).values_list('pk', flat=True)
        )

        groups = {}
        for name in DEFAULT_CARDIOLOGISTS:
            groups[name] = {
                'name': name, 'total': 0, 'cleared': 0,
                'extra': 0, 'non_compliant': 0, 'days_sum': 0, 'over_7': 0,
            }

        evaluated_rows = []
        over_target_total = 0
        non_compliant_total = 0
        cardio_seen_total = 0

        # Stage P2 & P3: Theatre Cancellation & 30-Day SVS VQI / UK NVR Registry KPIs
        operated_total = 0
        cancelled_total = 0
        arterial_operated_total = 0
        vqi_statin_ap_count = 0
        cea_symp_operated_total = 0
        cea_symp_within_14d_count = 0
        vqi_30d_tracked_total = 0
        vqi_uneventful_count = 0
        vqi_complication_total = 0
        cancel_counts = {k: 0 for k, _ in CHIP_CATALOGS['cancel_reason_chips']}
        vqi_rows = []

        for p in qs:
            ev = evaluate(p, today=today)
            p.ev = ev
            p.surgeon_name = person_name(p.surgeon)
            s = ev['s']
            q = ev['q']
            if ev['over_target_days']:
                over_target_total += 1
            if p.cardio_decision:
                cardio_seen_total += 1
                cname = p.cardiologist or 'ლიკა ინჯია'
                g = groups.setdefault(cname, {
                    'name': cname, 'total': 0, 'cleared': 0,
                    'extra': 0, 'non_compliant': 0, 'days_sum': 0, 'over_7': 0,
                })
                g['total'] += 1
                if p.cardio_decision == 'cleared':
                    g['cleared'] += 1
                else:
                    g['extra'] += 1
                if ev['cardio_non_compliant']:
                    g['non_compliant'] += 1
                    non_compliant_total += 1
                if ev['over_target_days']:
                    g['over_7'] += 1
                g['days_sum'] += ev['days_elapsed']
                evaluated_rows.append(p)

            outcome = ev['case_outcome']
            p.cancel_reasons_str = chip_summary(s.get('cancel_reason_chips'), 'cancel_reason_chips')
            p.vqi_complications_str = chip_summary(s.get('vqi_complication_chips'), 'vqi_complication_chips')
            if outcome == 'operated':
                operated_total += 1
                bpid = s.get('board_patient_id')
                still_inpatient = bool(bpid and bpid in active_inpatient_bp_ids and not ev['vqi_bundle_done'])
                in_hosp_mortality = (ev['vqi_30d_status'] == 'mortality_30d')
                if not ev['is_venous'] and not still_inpatient and not in_hosp_mortality:
                    arterial_operated_total += 1
                    if ev['vqi_bundle_done']:
                        vqi_statin_ap_count += 1
                if ev.get('is_symp_cea'):
                    ev_dt = _date(q.get('cea_event_date'))
                    op_done_dt = _date(s.get('operated_date')) or _date(s.get('planned_surgery_date'))
                    symp_wait = (
                        (op_done_dt - ev_dt).days if (ev_dt and op_done_dt)
                        else ev.get('days_since_ev')
                    )
                    if symp_wait is not None and 0 <= symp_wait <= 180:
                        cea_symp_operated_total += 1
                        if symp_wait <= 14:
                            cea_symp_within_14d_count += 1
                if ev['vqi_30d_status'] != 'pending':
                    vqi_30d_tracked_total += 1
                    if ev['vqi_30d_status'] == 'uneventful':
                        vqi_uneventful_count += 1
                    else:
                        vqi_complication_total += 1
                vqi_rows.append(p)
            elif outcome.startswith('cancelled_'):
                cancelled_total += 1
                for ck in (s.get('cancel_reason_chips') or []):
                    if ck in cancel_counts:
                        cancel_counts[ck] += 1
                vqi_rows.append(p)

        table_rows = []
        for g in groups.values():
            g['extra_pct'] = round(g['extra'] * 100 / g['total']) if g['total'] else 0
            g['avg_days'] = round(g['days_sum'] / g['total'], 1) if g['total'] else None
            table_rows.append(g)

        theatre_decided_total = operated_total + cancelled_total
        cancel_rate_pct = round(cancelled_total * 100 / theatre_decided_total, 1) if theatre_decided_total else 0
        vqi_statin_ap_pct = round(vqi_statin_ap_count * 100 / arterial_operated_total) if arterial_operated_total else None
        cea_symp_within_14d_pct = round(cea_symp_within_14d_count * 100 / cea_symp_operated_total) if cea_symp_operated_total else None
        vqi_uneventful_30d_pct = round(vqi_uneventful_count * 100 / vqi_30d_tracked_total) if vqi_30d_tracked_total else None
        cancel_reason_rows = [
            {'key': ck, 'label': clbl, 'count': cancel_counts[ck]}
            for ck, clbl in CHIP_CATALOGS['cancel_reason_chips']
            if cancel_counts[ck] > 0
        ]

        ctx.update({
            'pathway_filter': pathway_filter,
            'pathway_pills': [
                ('all', 'ყველა'),
                ('aaa_infra', 'AAA'),
                ('cea', 'CEA'),
                ('abf', 'ABF'),
                ('pad_bypass', 'პერიფ. შუნტირება'),
                ('pad_pta', 'PTA / სტენტი'),
                ('venous', 'ვენური'),
            ],
            'total_patients': len(qs),
            'total_aaa': len(qs),
            'cardio_seen_total': cardio_seen_total,
            'non_compliant_total': non_compliant_total,
            'over_7_total': over_target_total,
            'table_rows': table_rows,
            'patient_rows': evaluated_rows[:150],
            'target_days': TARGET_PREP_DAYS,
            'operated_total': operated_total,
            'cancelled_total': cancelled_total,
            'cancel_rate_pct': cancel_rate_pct,
            'cancel_reason_rows': cancel_reason_rows,
            'arterial_operated_total': arterial_operated_total,
            'vqi_statin_ap_count': vqi_statin_ap_count,
            'vqi_statin_ap_pct': vqi_statin_ap_pct,
            'cea_symp_operated_total': cea_symp_operated_total,
            'cea_symp_within_14d_count': cea_symp_within_14d_count,
            'cea_symp_within_14d_pct': cea_symp_within_14d_pct,
            'vqi_30d_tracked_total': vqi_30d_tracked_total,
            'vqi_uneventful_count': vqi_uneventful_count,
            'vqi_complication_total': vqi_complication_total,
            'vqi_uneventful_30d_pct': vqi_uneventful_30d_pct,
            'vqi_rows': vqi_rows[:150],
        })
        return ctx

