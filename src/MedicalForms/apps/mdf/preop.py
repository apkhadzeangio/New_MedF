"""Nurse-led preoperative assessment ("წინასაოპერაციო") in a Tweek.so-style weekly calendar.

Clinical pathways:
1. Infrarenal Abdominal Aortic Aneurysm (AAA — Open repair & EVAR): ESVS 2024 / ESC 2022 (7-day clock).
2. Carotid Endarterectomy (CEA — Symptomatic & Asymptomatic): ESVS 2023 / SVS 2022 / ESC 2022
   (5-day urgent prep clock + 14-day stroke-prevention window from index symptom onset).
3. Aortobifemoral Bypass (ABF — CLTI & Disabling Leriche Claudication): ESVS 2024 / ESC 2024 / GVG 2019
   (7-day limb-salvage clock for CLTI, 14-day clock for claudication).

Includes deterministic Stage 1 -> Stage 2 threshold checks, automatic structured Georgian
anamnesis generation, and ESC 2022 Cardiology Audit.
"""
import datetime
import hashlib
import os
import uuid

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from .board import clean_text, doctors, person_name
from .cases import MAX_UPLOAD, PhotoError, process_photo
from .duty import MONTHS
from .models import Case, PreopFile, PreopPatient

WEEKDAYS_KA = ('ორშაბათი', 'სამშაბათი', 'ოთხშაბათი', 'ხუთშაბათი', 'პარასკევი', 'შაბათი', 'კვირა')
MONTHS_SHORT = ('', 'იან', 'თებ', 'მარ', 'აპრ', 'მაი', 'ივნ', 'ივლ', 'აგვ', 'სექ', 'ოქტ', 'ნოე', 'დეკ')

TARGET_PREP_DAYS = 7
DEFAULT_CARDIOLOGISTS = ('ლიკა ინჯია', 'გარე კარდიოლოგი')

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
)

PATHWAY_SHORT = {
    PreopPatient.Pathway.AAA_INFRA: 'AAA',
    PreopPatient.Pathway.CEA: 'CEA',
    PreopPatient.Pathway.ABF: 'ABF',
}

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


# --------------------------------------------------------------------------
# Parsing & clinical math helpers
# --------------------------------------------------------------------------

def _num(val, lo=None, hi=None):
    if val is None or val == '':
        return None
    try:
        v = float(str(val).strip().replace(',', '.'))
    except (ValueError, TypeError):
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
    try:
        return datetime.date.fromisoformat((val or '').strip())
    except ValueError:
        return None


def calc_egfr(creat, age, sex='M'):
    """CKD-EPI 2021 race-free eGFR (mL/min/1.73m2). Accepts umol/L (>20) or mg/dL (<=20)."""
    c = _num(creat, lo=0.1, hi=3000)
    if c is None:
        return None
    scr_mgdl = (c / 88.4) if c > 20 else c
    female = (sex == 'F')
    kappa = 0.7 if female else 0.9
    alpha = -0.241 if female else -0.302
    mult = 1.012 if female else 1.0
    years = age if (age and 18 <= age <= 110) else 68
    ratio = scr_mgdl / kappa
    egfr = 142.0 * (min(ratio, 1.0) ** alpha) * (max(ratio, 1.0) ** -1.200) * (0.9938 ** years) * mult
    return int(round(egfr))


def normalize_hb_gl(hb):
    """Return hemoglobin in g/L (converts g/dL like 12.4 -> 124)."""
    v = _num(hb, lo=1, hi=260)
    if v is None:
        return None
    return round(v * 10, 1) if v < 30 else v


def normalize_glucose_mmol(gl):
    """Return fasting glucose in mmol/L (converts mg/dL like 180 -> 10.0)."""
    v = _num(gl, lo=0.5, hi=1000)
    if v is None:
        return None
    return round(v / 18.0, 1) if v > 35 else v


def normalize_albumin_gl(alb):
    """Return serum albumin in g/L (converts g/dL like 3.4 -> 34)."""
    v = _num(alb, lo=0.5, hi=80)
    if v is None:
        return None
    return round(v * 10, 1) if v < 10 else v


def _alerts_signature(red_flags):
    if not red_flags:
        return ''
    joined = '\n'.join(sorted(red_flags)).encode('utf-8')
    return hashlib.sha1(joined).hexdigest()[:16]


def target_prep_days(patient):
    """Pathway-specific preoperative workup target limit (days)."""
    if patient.pathway == PreopPatient.Pathway.CEA:
        return 5 if patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP else 10
    if patient.pathway == PreopPatient.Pathway.ABF:
        return 7 if patient.op_subtype == PreopPatient.OpSubtype.ABF_CLTI else 14
    return TARGET_PREP_DAYS


def prep_days_elapsed(patient, today=None):
    """Days elapsed since preoperative workup began (never resets on reschedule)."""
    today = today or timezone.localdate()
    q_filled = bool((patient.questionnaire or {}).get('filled'))
    candidates = [d for d in (patient.started_on, patient.visit_date) if d]
    if not candidates:
        return 0
    start = min(candidates)
    if start > today and not q_filled and not patient.approved_at:
        return 0
    end_day = timezone.localtime(patient.approved_at).date() if patient.approved_at else today
    return max(1, (end_day - start).days + 1)


def detect_case_preop(case):
    """Infer op_subtype and surgical side from a Conference Case."""
    blob = ' '.join([
        case.diagnosis or '', case.proposal or '', case.decision or '', case.history or ''
    ]).lower()
    dec_prop = ((case.decision or '') + ' ' + (case.proposal or '')).lower()
    side = case.side if case.side in ('R', 'L', 'B') else ''

    if any(k in blob for k in ('კაროტიდ', 'საძილე', 'ენდარტერექტომ', 'cea')):
        is_symp = any(k in blob for k in ('სიმპტომ', 'ინსულტ', 'tia', 'ტ.ი.ა', 'ამავროზ', 'იშემიური შეტევა'))
        return (PreopPatient.OpSubtype.CEA_SYMP if is_symp else PreopPatient.OpSubtype.CEA_ASYMP), side

    if any(k in blob for k in ('ბიფემორ', 'აორტო-ბიფემ', 'აორტობიფემ', 'ლერიშ', 'abf', 'aortobifem')):
        is_clti = any(k in blob for k in ('კრიტიკ', 'clti', 'მოსვენებით', 'წყლულ', 'განგრენ', 'ნეკროზ'))
        return (PreopPatient.OpSubtype.ABF_CLTI if is_clti else PreopPatient.OpSubtype.ABF_CLAUD), side

    op_sub = PreopPatient.OpSubtype.EVAR if ('evar' in dec_prop or 'ენდოვასკ' in dec_prop) else PreopPatient.OpSubtype.OPEN
    return op_sub, side


# --------------------------------------------------------------------------
# Deterministic Clinical Protocol Engine (AAA / CEA / ABF)
# --------------------------------------------------------------------------

DEFAULT_Q = {
    'filled': False,
    # AAA module (5 questions)
    'aaa_pain': False,
    'prev_abdo_surg': False,
    'prev_abdo_text': '',
    'claudication': False,
    'active_infection': False,
    'infection_text': '',
    # CEA module (6 questions)
    'cea_symp_event': False,
    'cea_event_text': '',
    'cea_days_since_event': None,
    'cea_crescendo': False,
    'cea_thrombolysis': False,
    'cea_prior_neck': False,
    'cea_prior_neck_text': '',
    'cea_hoarseness': False,
    'cea_disabling_stroke': False,
    # ABF module (6 questions)
    'abf_claud_m': None,
    'abf_buttock_pain': False,
    'abf_duration': '',
    'abf_rest_pain': False,
    'abf_ulcer': False,
    'abf_ulcer_text': '',
    'abf_wet_infection': False,
    'abf_groin_issue': False,
    'abf_prior_vasc': False,
    'abf_prior_vasc_text': '',
    # General & anesthesia screening (10 questions)
    'mets_ok': True,
    'cad': False,
    'cad_detail': '',
    'recent_stent': False,
    'htn': False,
    'pulm': False,
    'pulm_detail': '',
    'dm': False,
    'dm_insulin': False,
    'metformin': False,
    'sglt2': False,
    'neuro': False,
    'ulcer': False,
    'med_aspirin': False,
    'med_clopidogrel': False,
    'med_doac': False,
    'med_warfarin': False,
    'other_meds': '',
    'allergy_iodine': False,
    'allergy_other': False,
    'allergy_text': '',
    'anesth_issue': False,
    'pacemaker': False,
    'loose_teeth': False,
    'smoker': False,
}

DEFAULT_STUDIES = {
    # Aneurysm & CTA
    'aaa_mm': None,
    'cta_months': None,
    'cta_disc': False,
    # CEA Stage 1 & Stage 2 specific
    'cea_cta_status': 'pending',    # pending | concordant | high_calcified
    'brain_imaging': 'pending',     # pending | normal_small | large_or_hemorrhage
    'laryngoscopy_res': 'pending',  # pending | normal | palsy
    'neuro_consult_done': False,
    # ABF Stage 1 & Stage 2 specific
    'abi_r': None,
    'abi_l': None,
    'abf_clamp_zone': 'pending',    # pending | infrarenal | juxtarenal_calcified
    'abf_outflow': 'pending',       # pending | patent | profunda_stenosis | poor_outflow
    'wound_care_done': False,
    # Vitals
    'bp_sys': None,
    'bp_dia': None,
    'hr': None,
    'irregular_hr': False,
    'spo2': None,
    'weight_kg': None,
    'height_cm': None,
    # Stage 1 Labs
    'blood_group': '',
    'hb': None,
    'wbc': None,
    'plt': None,
    'inr': None,
    'aptt_prolonged': False,
    'fibrinogen': None,
    'creat': None,
    'k': None,
    'na': None,
    'glucose': None,
    'hba1c': None,
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
    'ecg': 'pending',               # pending | normal | ischemia_arrhythmia
    'lvef': None,
    'echo_valve_issue': False,
    # Stage 2 Protocol Tasks
    'spiro_fev1': None,
    'gastro_result': 'pending',     # pending | remission | acute_ulcer
    'urine_culture_done': False,
    'blood_reserved': False,
    'evar_sized': False,
    'med_hold_agreed': False,
    'anesth_done': False,
    'nurse_note': '',
    'alerts_ack_sig': '',
}


def merged_q(patient):
    data = dict(DEFAULT_Q)
    if isinstance(patient.questionnaire, dict):
        data.update(patient.questionnaire)
    return data


def merged_studies(patient):
    data = dict(DEFAULT_STUDIES)
    if isinstance(patient.studies, dict):
        data.update(patient.studies)
    return data


def evaluate(patient, today=None):
    """Evaluate thresholds, dynamic Stage 2 tasks, Cardiology Audit, and patient status."""
    pathway = PreopPatient.SUBTYPE_PATHWAY.get(patient.op_subtype, patient.pathway or PreopPatient.Pathway.AAA_INFRA)
    q = merged_q(patient)
    s = merged_studies(patient)
    q_filled = bool(q.get('filled'))
    days = prep_days_elapsed(patient, today=today)
    t_days = target_prep_days(patient)

    red_flags = []
    amber_flags = []

    hb_gl = normalize_hb_gl(s.get('hb'))
    gl_mmol = normalize_glucose_mmol(s.get('glucose'))
    alb_gl = normalize_albumin_gl(s.get('albumin'))
    egfr = calc_egfr(s.get('creat'), patient.age, patient.sex or 'M')

    aaa_mm = _num(s.get('aaa_mm'), lo=10, hi=200)
    cta_months = _int(s.get('cta_months'), lo=0, hi=120)
    car_r, car_l = _int(s.get('carotid_r')), _int(s.get('carotid_l'))
    max_car = max([v for v in (car_r, car_l) if v is not None], default=None)
    abi_r = _num(s.get('abi_r'), lo=0.0, hi=2.5)
    abi_l = _num(s.get('abi_l'), lo=0.0, hi=2.5)
    min_abi = min([v for v in (abi_r, abi_l) if v is not None], default=None)

    ipsi_car = None
    contra_car = None
    needs_brain_ct = False
    needs_neuro = False
    needs_laryngoscopy = False
    needs_wound_care = False
    key_badge = ''
    key_badge_urgent = False

    # 1. Pathway-specific Vascular Evaluation & Preparation Clocks
    if pathway == PreopPatient.Pathway.AAA_INFRA:
        if q_filled and q.get('aaa_pain'):
            red_flags.append(
                'საეჭვოა სიმპტომური AAA (ახალი ტკივილი მუცლის/წელის/გვერდის არეში) — '
                'გეგმური კვლევების ნაცვლად დაუყოვნებლივ აცნობეთ ქირურგს!'
            )
        if aaa_mm is not None:
            min_thresh = 50 if patient.sex == 'F' else 55
            mm_fmt = int(aaa_mm) if aaa_mm == int(aaa_mm) else aaa_mm
            key_badge = f'{mm_fmt} მმ'
            key_badge_urgent = bool(aaa_mm >= 70)
            if aaa_mm >= 70:
                red_flags.append(f'გიგანტური ანევრიზმა ({aaa_mm} მმ >= 70 მმ) — მაღალი რუპტურის რისკი, დაჩქარებული მომზადება!')
            elif aaa_mm < min_thresh:
                amber_flags.append(f'ანევრიზმის დიამეტრი ({aaa_mm} მმ) სტანდარტულ ზღვარზე ({min_thresh} მმ) ნაკლებია — გადაამოწმეთ ჩვენება ქირურგთან.')

        if days > t_days and not patient.approved_at:
            red_flags.append(
                f'გახანგრძლივებული წინასაოპერაციო მომზადება: გავიდა {days} დღე (სამიზნე ლიმიტი: {t_days} დღე) — რუპტურის რისკი!'
            )
        elif days in (5, 6) and not patient.approved_at:
            amber_flags.append(f'მომზადების მე-{days} დღე ({t_days} დღიდან) — დააჩქარეთ დარჩენილი კვლევები.')

        if cta_months is not None:
            if cta_months > 6 or (cta_months > 3 and aaa_mm is not None and aaa_mm >= 65):
                red_flags.append(f'კტ-ანგიოგრაფია ხანდაზმულია ({cta_months} თვის წინანდელი) — შეათანხმეთ განმეორებითი CTA ქირურგთან.')
        if q_filled and not s.get('cta_disc'):
            amber_flags.append('აორტის კტ-ანგიოგრაფიის DICOM დისკი ჯერ არ არის მოტანილი / შემოწმებული.')

    elif pathway == PreopPatient.Pathway.CEA:
        is_symp_cea = (patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP) or (q_filled and bool(q.get('cea_symp_event')))
        days_since_ev = _int(q.get('cea_days_since_event'), lo=0, hi=365)

        if patient.op_subtype == PreopPatient.OpSubtype.CEA_ASYMP and q_filled and q.get('cea_symp_event'):
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

        if is_symp_cea and days_since_ev is not None and not patient.approved_at:
            if days_since_ev > 14:
                red_flags.append(
                    f'ინდექსური სიმპტომიდან გავიდა {days_since_ev} დღე — გადაცილებულია ESVS 2023-ის 14-დღიანი ოპტიმალური ფანჯარა! დააჩქარეთ ქირურგის შეფასება.'
                )
            elif days_since_ev >= 10:
                amber_flags.append(
                    f'სიმპტომიდან გასულია {days_since_ev} დღე (ESVS 14-დღიანი ფანჯრიდან დარჩა {14 - days_since_ev} დღე) — დააჩქარეთ კვლევები!'
                )

        if days > t_days and not patient.approved_at:
            if is_symp_cea:
                red_flags.append(
                    f'გახანგრძლივებული მომზადება სიმპტომურ CEA-ზე: გავიდა {days} დღე (სამიზნე ლიმიტი: {t_days} დღე) — განმეორებითი ინსულტის რისკი!'
                )
            else:
                red_flags.append(f'გახანგრძლივებული წინასაოპერაციო მომზადება: გავიდა {days} დღე (სამიზნე ლიმიტი: {t_days} დღე).')
        elif not patient.approved_at and days >= max(4, t_days - 1):
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
                red_flags.append(
                    'საოპერაციო მხარეს ICA-ს სრული ოკლუზია (100%) — CEA სრულ ქრონიკულ ოკლუზიაზე არ ტარდება! '
                    'სასწრაფოდ გადაამოწმეთ CTA ქირურგთან (near-occlusion vs ოკლუზია).'
                )
            elif is_symp_cea and ipsi_car < 50:
                red_flags.append(f'საოპერაციო მხარეს სტენოზი ({ipsi_car}%) < 50% — გადაამოწმეთ CEA-ს ჩვენება ქირურგთან.')
            elif not is_symp_cea and ipsi_car < 60:
                red_flags.append(f'ასიმპტომურ საოპერაციო მხარეს სტენოზი ({ipsi_car}%) < 60% — გადაამოწმეთ CEA-ს ჩვენება ქირურგთან.')
            elif not is_symp_cea and ipsi_car < 70:
                amber_flags.append(f'ასიმპტომური სტენოზი {ipsi_car}% (60–69%) — გადაამოწმეთ მაღალი რისკის ბალთის ნიშნები CTA/დუპლექსზე.')
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

        needs_brain_ct = is_symp_cea or (q_filled and bool(q.get('neuro')))
        brain_im = s.get('brain_imaging') or 'pending'
        if brain_im == 'large_or_hemorrhage':
            red_flags.append(
                'თავის ტვინის CT/MRI-ზე სისხლჩაქცევა ან ფართო მწვავე ინფარქტი (>1/3 MCA) — '
                'ნაადრევი CEA საშიშია, სასწრაფოდ აცნობეთ ქირურგსა და ნევროლოგს!'
            )
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

        if q_filled and not (q.get('med_aspirin') or q.get('med_clopidogrel') or q.get('med_doac') or q.get('med_warfarin')):
            red_flags.append(
                'პაციენტი არ იღებს ანტიაგრეგანტს (ასპირინი / კლოპიდოგრელი)! ESVS 2023 Class I-ით CEA-ს წინ '
                'ანტიაგრეგანტული თერაპია სავალდებულოა ინსულტის პრევენციისთვის — აცნობეთ ქირურგს.'
            )

    elif pathway == PreopPatient.Pathway.ABF:
        is_clti = (patient.op_subtype == PreopPatient.OpSubtype.ABF_CLTI) or (
            q_filled and (bool(q.get('abf_rest_pain')) or bool(q.get('abf_ulcer')))
        )
        if patient.op_subtype == PreopPatient.OpSubtype.ABF_CLAUD and q_filled and (q.get('abf_rest_pain') or q.get('abf_ulcer')):
            amber_flags.append(
                'კითხვარით დაფიქსირდა მოსვენებითი ტკივილი ან წყლული (CLTI) — სასურველია ქვეტიპის გადართვა კრიტიკულ იშემიაზე (7-დღიანი რეჟიმი).'
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

        if days > t_days and not patient.approved_at:
            if is_clti:
                red_flags.append(
                    f'გახანგრძლივებული მომზადება CLTI პაციენტთან: გავიდა {days} დღე (სამიზნე ლიმიტი: {t_days} დღე) — კიდურის დაკარგვის რისკი!'
                )
            else:
                red_flags.append(f'გახანგრძლივებული წინასაოპერაციო მომზადება: გავიდა {days} დღე (სამიზნე ლიმიტი: {t_days} დღე).')
        elif not patient.approved_at and days >= t_days - 2:
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
        elif outflow == 'profunda_stenosis':
            amber_flags.append('CFA / Profunda Femoris სტენოზი CTA-ზე — გასათვალისწინებელია თანმხლები ენდარტერექტომია / პროფუნდოპლასტიკა.')

        if min_abi is not None:
            key_badge = f'ABI {min_abi}'
            key_badge_urgent = bool(is_clti or min_abi < 0.4)
            if min_abi < 0.4 and not is_clti:
                amber_flags.append(f'კრიტიკულად დაბალი გოჯ-მხრის ინდექსი (ABI {min_abi} < 0.40) — შეესაბამება მძიმე იშემიას.')
        elif is_clti:
            key_badge = 'CLTI'
            key_badge_urgent = True
        elif q.get('abf_claud_m'):
            key_badge = f"{q['abf_claud_m']} მ"

        if q_filled and not s.get('cta_disc'):
            amber_flags.append('აორტო-ილიაკური / ქვემო კიდურების CTA DICOM დისკი ჯერ არ არის მოტანილი / შემოწმებული.')

        needs_wound_care = q_filled and (bool(q.get('abf_ulcer')) or bool(q.get('abf_groin_issue')))

    # 2. Vitals & Functional Capacity (ESC 2022 / ESVS 2023 / CPOC)
    bp_sys, bp_dia = _int(s.get('bp_sys')), _int(s.get('bp_dia'))
    hr, spo2 = _int(s.get('hr')), _int(s.get('spo2'))
    if (bp_sys and bp_sys >= 180) or (bp_dia and bp_dia >= 110):
        if pathway == PreopPatient.Pathway.CEA:
            red_flags.append(
                f'მაღალი არტერიული წნევა ({bp_sys or "?"}/{bp_dia or "?"} mmHg >= 180/110) — CEA-ზე ცერებრული ჰიპერპერფუზიის, '
                'სისხლჩაქცევისა და კისრის ჰემატომის უმაღლესი რისკი! სავალდებულოა სტაბილიზაცია ოპერაციამდე.'
            )
        else:
            red_flags.append(f'მაღალი არტერიული წნევა ({bp_sys or "?"}/{bp_dia or "?"} mmHg >= 180/110) — საჭიროა კორექცია ოპერაციამდე.')
    elif bp_sys and bp_sys < 90:
        if pathway == PreopPatient.Pathway.AAA_INFRA:
            red_flags.append(f'ჰიპოტენზია ({bp_sys}/{bp_dia or "?"} mmHg) — სასწრაფოდ გამოირიცხოს რუპტურა / შოკი!')
        else:
            red_flags.append(f'ჰიპოტენზია ({bp_sys}/{bp_dia or "?"} mmHg) — სასწრაფოდ შეფასდეს ჰემოდინამიკა და პერფუზია!')
    elif (bp_sys and bp_sys >= 160) or (bp_dia and bp_dia >= 100):
        amber_flags.append(f'მომატებული არტერიული წნევა ({bp_sys or "?"}/{bp_dia or "?"} mmHg) — ეცნობოს კარდიოლოგს (<160/100 ოპერაციამდე).')

    if s.get('irregular_hr') or (hr and (hr > 110 or hr < 45)):
        red_flags.append(f'პულსის/რიტმის საყურადღებო ცვლილება (HR {hr or "?"}/წთ) — საჭიროა ეკგ და კარდიოლოგის შეფასება.')

    if spo2 is not None:
        if spo2 < 90:
            red_flags.append(f'მძიმე ჰიპოქსემია (SpO2 {spo2}% < 90%) — აცნობეთ ქირურგსა და ანესთეზიოლოგს!')
        elif spo2 < 94:
            amber_flags.append(f'დაქვეითებული სატურაცია (SpO2 {spo2}%) — ავტომატურად დაემატა სპირომეტრია.')

    if q_filled and not q.get('mets_ok', True):
        amber_flags.append('დაქვეითებული ფუნქციური ამტანობა (< 4 METs — ვერ ადის 2 სართულზე გაუჩერებლად).')

    # 3. Hematology & Coagulation
    open_major = (pathway == PreopPatient.Pathway.ABF) or (
        pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype in ('open', 'tbd')
    )
    if hb_gl is not None:
        hb_norm = 120 if patient.sex == 'F' else 130
        if hb_gl < 100:
            red_flags.append(f'მნიშვნელოვანი ანემია (Hb {hb_gl} გ/ლ < 100) — აცნობეთ ქირურგს (მიზეზის ძიება / კორექცია).')
        elif hb_gl < hb_norm:
            amber_flags.append(f'მსუბუქი ანემია (Hb {hb_gl} გ/ლ) — ღია ოპერაციაზე სავალდებულოა ერითრომასის რეზერვი.')

    wbc = _num(s.get('wbc'))
    if wbc is not None and (wbc > 12.0 or wbc < 3.5):
        red_flags.append(f'ლეიკოციტების ცვლილება (WBC {wbc} x10^9/L) — გამოირიცხოს ფარული ინფექცია რეკონსტრუქციამდე!')

    plt = _int(s.get('plt'))
    if plt is not None:
        if plt < 100:
            red_flags.append(f'თრომბოციტოპენია (PLT {plt} x10^9/L < 100) — სისხლდენის მაღალი რისკი! აცნობეთ ქირურგს.')
        elif plt < 150:
            amber_flags.append(f'ზომიერი თრომბოციტოპენია (PLT {plt} x10^9/L) — საყურადღებოა ანესთეზიოლოგისთვის.')

    inr = _num(s.get('inr'))
    fib = _num(s.get('fibrinogen'))
    if (inr and inr > 1.35) or s.get('aptt_prolonged') or (fib is not None and fib < 1.8):
        red_flags.append('კოაგულოგრამის დარღვევა (INR > 1.35 / გახანგრძლივებული APTT / დაბალი ფიბრინოგენი) — აცნობეთ ქირურგს!')

    # 4. Renal Function & Electrolytes
    if egfr is not None:
        if egfr < 30:
            red_flags.append(f'თირკმლის მძიმე უკმარისობა (eGFR {egfr} მლ/წთ < 30) — სავალდებულოა ნეფროლოგი და ტაქტიკის შეთანხმება ქირურგთან!')
        elif egfr < 45:
            red_flags.append(f'დაქვეითებული თირკმლის ფუნქცია (eGFR {egfr} მლ/წთ < 45) — მაღალი AKI რისკი, აცნობეთ ქირურგს!')
        elif egfr < 60:
            amber_flags.append(f'eGFR {egfr} მლ/წთ (CKD 3a) — კონტრასტული კვლევის/EVAR-ისას საჭიროა იზოტონური ჰიდრატაცია.')

    k_val = _num(s.get('k'))
    if k_val is not None:
        if k_val < 3.3 or k_val >= 5.4:
            red_flags.append(f'კალიუმის კრიტიკული დონე (K+ {k_val} მმოლ/ლ) — არითმიის რისკი! აცნობეთ ექიმს.')
        elif k_val < 3.5 or k_val > 5.0:
            amber_flags.append(f'კალიუმის სასაზღვრო დონე (K+ {k_val} მმოლ/ლ) — სასურველია კორექცია ოპერაციამდე.')

    na_val = _num(s.get('na'))
    if na_val is not None and (na_val < 130 or na_val >= 150):
        red_flags.append(f'ნატრიუმის კრიტიკული დარღვევა (Na+ {na_val} მმოლ/ლ) — აცნობეთ ანესთეზიოლოგს.')

    # 5. Glycemia, Liver & Nutrition
    hba1c = _num(s.get('hba1c'))
    if (gl_mmol is not None and (gl_mmol >= 14.0 or gl_mmol < 3.9)) or (hba1c is not None and hba1c >= 8.5):
        red_flags.append(
            f'მოუწესრიგებელი გლიკემია (გლუკოზა {gl_mmol or "—"} მმოლ/ლ, HbA1c {hba1c or "—"}%) — '
            'ჭრილობის/პროთეზის ინფექციის რისკი, საჭიროა ენდოკრინოლოგი!'
        )
    elif (gl_mmol is not None and gl_mmol > 10.0) or (q_filled and q.get('dm') and hba1c is None):
        amber_flags.append('მომატებული გლუკოზა / შაქრიანი დიაბეტი — გადაამოწმეთ HbA1c და პერიოპერაციული გლიკემიის სქემა.')

    if open_major and alb_gl is not None:
        if alb_gl < 30:
            red_flags.append(f'მძიმე ჰიპოალბუმინემია ({alb_gl} გ/ლ < 30) — ღია აორტული ოპერაციის მაღალი რისკი, აცნობეთ ქირურგს!')
        elif alb_gl < 35:
            amber_flags.append(f'დაბალი ალბუმინი ({alb_gl} გ/ლ < 35) — მომატებული ქირურგიული/ჭრილობის გართულების რისკი.')

    if s.get('liver_status') == '3x':
        red_flags.append('ღვიძლის ფერმენტების/ბილირუბინის მნიშვნელოვანი მატება (>=3x ULN) — აცნობეთ ექიმს.')
    if s.get('viral_positive'):
        amber_flags.append('დადებითი ვირუსული მარკერი (B/C/HIV/RW) — საოპერაციო ეპიდ-უსაფრთხოება და ღვიძლის სტატუსის გათვალისწინება.')

    # 6. Infection Control & Urinalysis (Prosthetic Graft / Patch Protection)
    urine_wbc = _int(s.get('urine_wbc'))
    uti_suspected = (
        (urine_wbc is not None and urine_wbc > 10)
        or bool(s.get('urine_nitrite'))
        or (q_filled and bool(q.get('active_infection')))
    )
    if uti_suspected:
        red_flags.append(
            'საეჭვოა საშარდე გზების ან სხვა აქტიური ინფექცია (შარდში ლეიკოციტები >10 / ნიტრიტები+ / ჩივილი) — '
            'სინთეზური პროთეზის/პატჩის ინფიცირების მაღალი რისკი! აცნობეთ ქირურგს.'
        )
    elif urine_wbc is not None and 6 <= urine_wbc <= 10:
        amber_flags.append(f'შარდში ლეიკოციტები {urine_wbc} მხ/არ — სასურველია საკონტროლო შარდის ანალიზი.')

    # 7. Pulmonary, Chest X-Ray & Spirometry
    cxr = s.get('cxr') or 'pending'
    if cxr == 'infiltrate':
        red_flags.append('გულმკერდის რენტგენზე ინფილტრაცია / გამონაჟონი / საყურადღებო კერა — აცნობეთ ქირურგს!')

    if pathway == PreopPatient.Pathway.CEA:
        needs_spirometry = (
            (q_filled and bool(q.get('pulm')))
            or (spo2 is not None and spo2 < 94)
            or cxr == 'emphysema'
        )
    else:
        needs_spirometry = (
            open_major
            or (q_filled and (q.get('pulm') or q.get('smoker')))
            or (spo2 is not None and spo2 < 94)
            or cxr == 'emphysema'
        )
    spiro_fev1 = _int(s.get('spiro_fev1'))
    if needs_spirometry and spiro_fev1 is not None:
        if spiro_fev1 < 50:
            red_flags.append(
                f'სპირომეტრიაზე მძიმე დარღვევა (FEV1 {spiro_fev1}% < 50%) — '
                'მაღალი რესპირატორული რისკი! აცნობეთ ქირურგსა და ანესთეზიოლოგს.'
            )
        elif spiro_fev1 < 70:
            amber_flags.append(f'სპირომეტრიაზე ზომიერი დაქვეითება (FEV1 {spiro_fev1}%) — საყურადღებოა ანესთეზიოლოგისთვის.')

    # 8. Concomitant Carotid Duplex (for AAA & ABF) & Gastroscopy (all 3)
    if pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF) and max_car is not None:
        op_short = 'AAA' if pathway == PreopPatient.Pathway.AAA_INFRA else 'ABF'
        if max_car >= 70 or (max_car >= 50 and s.get('carotid_symp')):
            red_flags.append(
                f'ჰემოდინამიკურად მნიშვნელოვანი კაროტიდული სტენოზი ({max_car}%) — '
                f'აცნობეთ ქირურგს ეტაპობრივი ტაქტიკის (CEA <-> {op_short}) გადასაწყვეტად!'
            )
        elif max_car >= 50:
            amber_flags.append(f'ასიმპტომური კაროტიდული სტენოზი ({max_car}%) — ინტრაოპერაციული ჰიპოტენზიის თავიდან აცილება.')

    needs_gastro = (q_filled and bool(q.get('ulcer'))) or (hb_gl is not None and hb_gl < 110)
    gastro_res = s.get('gastro_result') or 'pending'
    if needs_gastro and gastro_res == 'acute_ulcer':
        red_flags.append('გასტროსკოპიაზე მწვავე წყლული / ეროზიული გასტრიტი — ინტრაოპერაციული ჰეპარინიზაცია საშიშია! აცნობეთ ქირურგს.')

    # 9. Medications & Allergy Warnings
    if q_filled and q.get('allergy_iodine'):
        if patient.op_subtype in ('evar', 'tbd'):
            red_flags.append('ანამნეზში ალერგია იოდზე / კონტრასტზე — EVAR-ისა და ანგიოგრაფიის წინ სავალდებულოა პრემედიკაცია/ქირურგის ჩართვა!')
        else:
            amber_flags.append('ანამნეზში ალერგია იოდზე / კონტრასტზე.')

    needs_med_hold = q_filled and (
        q.get('med_clopidogrel') or q.get('med_doac') or q.get('med_warfarin')
        or q.get('sglt2') or (q.get('metformin') and egfr is not None and egfr < 60)
    )
    if q_filled and q.get('recent_stent'):
        red_flags.append(
            'კორონარული სტენტი ჩადგმულია ბოლო 6 თვეში (DAPT) — თვითნებური მოხსნა საშიშია! '
            'შეათანხმეთ ანტიაგრეგანტული რეჟიმი და ოპერაციის ტაქტიკა ქირურგთან.'
        )
    elif needs_med_hold and not s.get('med_hold_agreed'):
        amber_flags.append('პაციენტი იღებს ანტიკოაგულანტს / კლოპიდოგრელს / SGLT2 / მეტფორმინს — შეათანხმეთ მიღების/შეჩერების დღე ქირურგთან.')

    # 10. Cardiac Findings & Cardiology Audit (ESC 2022 Class I vs Class III)
    lvef = _int(s.get('lvef'))
    ecg = s.get('ecg') or 'pending'
    if (lvef is not None and lvef < 40) or s.get('echo_valve_issue') or ecg == 'ischemia_arrhythmia':
        red_flags.append('კარდიოლოგიური Red Flag (LVEF < 40% / მძიმე სარქვლოვანი მანკი / მწვავე ეკგ ცვლილება) — აცნობეთ ქირურგსა და კარდიოლოგს!')
    elif lvef is not None and lvef < 50:
        amber_flags.append(f'ზომიერად დაქვეითებული განდევნის ფრაქცია (LVEF {lvef}%).')

    guideline_justifies_extra_cardio = (
        (q_filled and not q.get('mets_ok', True))
        or (q_filled and bool(q.get('cad')))
        or (lvef is not None and lvef < 50)
        or bool(s.get('echo_valve_issue'))
        or ecg == 'ischemia_arrhythmia'
        or (egfr is not None and egfr < 60)
        or (q_filled and bool(q.get('dm_insulin')))
    )
    cardio_decision = patient.cardio_decision or ''
    cardio_non_compliant = False
    if cardio_decision in ('stress', 'cag', 'pci'):
        cardio_non_compliant = not guideline_justifies_extra_cardio
        who_cardio = patient.cardiologist or 'კარდიოლოგი'
        audit_tag = ' [ESC 2022 Class III — ასიმპტომურ >=4 METs პაციენტთან!]' if cardio_non_compliant else ''
        red_flags.append(
            f'კარდიოლოგმა ({who_cardio}) მოითხოვა {patient.get_cardio_decision_display()}{audit_tag} — '
            'დაყოვნებამდე ან სტენტირებამდე სავალდებულოა ქირურგთან შეთანხმება!'
        )

    # Stage 1 baseline completeness
    common_s1 = (
        hb_gl is not None and s.get('creat') is not None
        and inr is not None and urine_wbc is not None
        and car_r is not None and car_l is not None
        and cxr != 'pending' and ecg != 'pending' and lvef is not None
    )
    if pathway == PreopPatient.Pathway.CEA:
        s1_core_done = (
            common_s1
            and (s.get('cea_cta_status') or 'pending') != 'pending'
            and (not needs_brain_ct or (s.get('brain_imaging') or 'pending') != 'pending')
        )
    elif pathway == PreopPatient.Pathway.ABF:
        s1_core_done = (
            common_s1
            and (s.get('abf_clamp_zone') or 'pending') != 'pending'
            and (s.get('abf_outflow') or 'pending') != 'pending'
            and (abi_r is not None or abi_l is not None)
        )
    else:
        s1_core_done = common_s1 and (aaa_mm is not None)

    # Dynamic Checklist (Stage 1 + Stage 2)
    pw_short = PATHWAY_SHORT.get(pathway, 'AAA')
    tasks = [
        {'key': 'q', 'label': f'კითხვარების შევსება (ზოგადი + {pw_short})', 'done': q_filled},
        {'key': 's1', 'label': 'საფეხური 1: საბაზისო ანალიზები და სავალდებულო კვლევები', 'done': s1_core_done},
    ]
    if pathway == PreopPatient.Pathway.CEA and needs_neuro:
        tasks.append({'key': 'neuro', 'label': 'ნევროლოგის წინასაოპერაციო კონსულტაცია (mRS / სტატუსი)', 'done': bool(s.get('neuro_consult_done'))})
    if pathway == PreopPatient.Pathway.CEA and needs_laryngoscopy:
        tasks.append({'key': 'laryng', 'label': 'ლარინგოსკოპია — სახმო იოგების შემოწმება (ENT)', 'done': (s.get('laryngoscopy_res') or 'pending') != 'pending'})
    if needs_spirometry:
        tasks.append({'key': 'spiro', 'label': 'სპირომეტრია (FEV1%)', 'done': spiro_fev1 is not None})
    if needs_gastro:
        tasks.append({'key': 'gastro', 'label': 'გასტროსკოპია (ეგდს)', 'done': gastro_res != 'pending'})
    if uti_suspected:
        tasks.append({'key': 'uti', 'label': 'შარდის ბაქტერიოლოგია / სანაციის შეთანხმება ქირურგთან', 'done': bool(s.get('urine_culture_done'))})
    if pathway == PreopPatient.Pathway.ABF and needs_wound_care:
        tasks.append({'key': 'wound', 'label': 'ტერფის ნაცხი / საზარდულის კანის სანაცია', 'done': bool(s.get('wound_care_done'))})
    if open_major:
        tasks.append({'key': 'blood', 'label': 'სისხლის კომპონენტების რეზერვის მომზადება', 'done': bool(s.get('blood_reserved'))})
    if pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype == 'evar':
        tasks.append({'key': 'sizing', 'label': 'EVAR სტენტ-გრაფტის ზომების დათვლა და მზაობა', 'done': bool(s.get('evar_sized'))})
    if needs_med_hold:
        tasks.append({'key': 'med_hold', 'label': 'მედიკამენტების მიღების/შეჩერების დღის შეთანხმება ქირურგთან', 'done': bool(s.get('med_hold_agreed'))})

    tasks.append({
        'key': 'cardio',
        'label': f'კარდიოლოგის კონსულტაცია ({patient.cardiologist or "ლიკა ინჯია"})',
        'done': cardio_decision == 'cleared',
    })
    tasks.append({
        'key': 'anesth',
        'label': 'ანესთეზიოლოგის კონსულტაცია',
        'done': bool(s.get('anesth_done')),
    })

    done_count = sum(1 for t in tasks if t['done'])
    all_tasks_done = (done_count == len(tasks))

    current_sig = _alerts_signature(red_flags)
    alerts_acked = bool(patient.alerts_ack_at and s.get('alerts_ack_sig') == current_sig)
    has_unacked_red = bool(red_flags) and not alerts_acked

    if not q_filled:
        status = STATUS_NO_Q
    elif has_unacked_red and not patient.approved_at:
        status = STATUS_ALERT
    elif patient.approved_at or all_tasks_done:
        status = STATUS_READY
    else:
        status = STATUS_PROGRESS

    over_target = days > t_days and not patient.approved_at

    return {
        'pathway': pathway,
        'pathway_short': pw_short,
        'status': status,
        'status_label': STATUS_LABELS[status],
        'days_elapsed': days,
        'target_days': t_days,
        'over_target_days': over_target,
        'over_7_days': over_target,
        'key_badge': key_badge,
        'key_badge_urgent': key_badge_urgent,
        'red_flags': red_flags,
        'amber_flags': amber_flags,
        'alerts_sig': current_sig,
        'alerts_acked': alerts_acked,
        'has_unacked_red': has_unacked_red,
        'egfr': egfr,
        'hb_gl': hb_gl,
        'gl_mmol': gl_mmol,
        'alb_gl': alb_gl,
        'aaa_mm': aaa_mm,
        'ipsi_car': ipsi_car,
        'contra_car': contra_car,
        'min_abi': min_abi,
        'needs_spirometry': needs_spirometry,
        'needs_gastro': needs_gastro,
        'uti_suspected': uti_suspected,
        'needs_med_hold': needs_med_hold,
        'needs_brain_ct': needs_brain_ct,
        'needs_neuro': needs_neuro,
        'needs_laryngoscopy': needs_laryngoscopy,
        'needs_wound_care': needs_wound_care,
        'needs_blood': open_major,
        'guideline_justifies_extra_cardio': guideline_justifies_extra_cardio,
        'cardio_non_compliant': cardio_non_compliant,
        's1_core_done': s1_core_done,
        'tasks': tasks,
        'done_count': done_count,
        'total_count': len(tasks),
        'q': q,
        's': s,
    }


def generate_anamnesis(patient, ev=None):
    """Build a concise, structured Georgian preoperative anamnesis for AAA, CEA, or ABF."""
    ev = ev or evaluate(patient)
    q, s = ev['q'], ev['s']
    if not q.get('filled'):
        return ''

    pathway = ev['pathway']
    sex_label = 'ქალი' if patient.sex == 'F' else 'მამაკაცი'
    age_part = f'{patient.age} წ. ' if patient.age else ''
    surgeon_str = f' (ქირურგი: {person_name(patient.surgeon)})' if patient.surgeon else ''

    lines = []
    if pathway == PreopPatient.Pathway.CEA:
        side_word = {'R': 'მარჯვენა ', 'L': 'მარცხენა '}.get(patient.side, '')
        kind_word = 'სიმპტომური სტენოზის გამო' if patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP else 'ასიმპტომური სტენოზის გამო'
        lines.append(f'{age_part}{sex_label}, დაგეგმილია {side_word}კაროტიდული ენდარტერექტომია ({kind_word}){surgeon_str}.')

        if patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP or q.get('cea_symp_event'):
            ev_txt = q.get('cea_event_text') or 'TIA / ამავროზი / მცირე იშემიური ინსულტი'
            d_ago = f", ბოლო ეპიზოდი {q['cea_days_since_event']} დღის წინ" if q.get('cea_days_since_event') is not None else ''
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
            f"საძილეები და ვიზუალიზაცია: R ICA {s.get('carotid_r') if s.get('carotid_r') is not None else '—'}% / "
            f"L ICA {s.get('carotid_l') if s.get('carotid_l') is not None else '—'}%; "
            f"კისრის CTA — {cta_map.get(s.get('cea_cta_status') or 'pending', 'ზუსტდება')}; "
            f"თავის ტვინის CT/MRI — {br_map.get(s.get('brain_imaging') or 'pending', 'ზუსტდება')} ({disc_str})."
        )

        neck_bits = []
        if q.get('cea_prior_neck'):
            neck_bits.append(f"კისრის წინა ჩარევა/დასხივება: {q.get('cea_prior_neck_text') or 'აღნიშნავს'}")
        if q.get('cea_hoarseness'):
            neck_bits.append('აღნიშნავს ხმის ჩახლეჩას / დისფაგიას')
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
            lines.append(f"ტროფიკული სტატუსი: წყლული/ნეკროზი ({q.get('abf_ulcer_text') or 'ტერფზე'}), {wet_str}.")
        groin_str = 'საზარდულის ნაკეცებში ინტერტრიგო/დაზიანება (!)' if q.get('abf_groin_issue') else 'საზარდულის კანი ინტაქტურია'
        surg_bits = [groin_str]
        if q.get('abf_prior_vasc'):
            surg_bits.append(f"წინა სისხლძარღვოვანი ჩარევა: {q.get('abf_prior_vasc_text') or 'აღნიშნავს'}")
        if q.get('prev_abdo_surg'):
            surg_bits.append(f"მუცლის ღრუს ოპერაცია: {q.get('prev_abdo_text') or 'აღნიშნავს'}")
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

    else:
        op_map = {
            'open': 'მუცლის აორტის ინფრარენალური ანევრიზმის ღია რეზექცია / პროთეზირება',
            'evar': 'მუცლის აორტის ინფრარენალური ანევრიზმის ენდოვასკულური პროთეზირება (EVAR)',
            'tbd': 'მუცლის აორტის ინფრარენალური ანევრიზმის ოპერაციული მკურნალობა (ღია / EVAR)',
        }
        lines.append(f'{age_part}{sex_label}, დაგეგმილია {op_map.get(patient.op_subtype, "")}{surgeon_str}.')

        mm_str = f"{ev['aaa_mm']} მმ" if ev['aaa_mm'] else 'ზუსტდება'
        cta_str = f"{s['cta_months']} თვის წინ" if s.get('cta_months') is not None else 'თარიღი უცნობია'
        disc_str = 'DICOM დისკი ადგილზეა' if s.get('cta_disc') else 'DICOM დისკი მოსატანია'
        pain_str = (
            'აღნიშნავს მუცლის/წელის ტკივილს (საყურადღებოა სიმპტომური AAA!).'
            if q.get('aaa_pain') else 'ამჟამად ასიმპტომურია, მუცლისა და წელის ტკივილს უარყოფს.'
        )
        lines.append(f'ანევრიზმა: მაქს. დიამეტრი {mm_str} (CTA: {cta_str}, {disc_str}). {pain_str}')

        surg_extras = []
        if q.get('prev_abdo_surg'):
            surg_extras.append(f"მუცლის/საზარდულის ოპერაციები: {q.get('prev_abdo_text') or 'აღნიშნავს'}")
        if q.get('claudication'):
            surg_extras.append('აღნიშნავს ხანგამოშვებით კოჭლობას სიარულისას')
        if surg_extras:
            lines.append('; '.join(surg_extras) + '.')

    mets_str = (
        'დამაკმაყოფილებელი (>= 4 METs, ადის 2 სართულზე გაუჩერებლად)'
        if q.get('mets_ok', True) else 'დაქვეითებული (< 4 METs — ქოშინი/დაღლა 2 სართულზე ასვლისას)'
    )
    lines.append(f'ფუნქციური ამტანობა: {mets_str}.')

    comorb = []
    if q.get('htn'):
        comorb.append('არტერიული ჰიპერტენზია')
    if q.get('cad'):
        detail = f" ({q['cad_detail']})" if q.get('cad_detail') else ''
        comorb.append(f'გულის იშემიური დაავადება{detail}')
    if q.get('dm'):
        dm_kind = 'ინსულინზე' if q.get('dm_insulin') else 'ტაბლეტებზე'
        comorb.append(f'შაქრიანი დიაბეტი ({dm_kind})')
    if q.get('pulm'):
        p_det = f" ({q['pulm_detail']})" if q.get('pulm_detail') else ''
        comorb.append(f'ფილტვების ქრ. დაავადება{p_det}')
    if q.get('neuro'):
        comorb.append('ანამნეზში ინსულტი/TIA')
    if q.get('ulcer'):
        comorb.append('წყლულოვანი დაავადება')
    if q.get('smoker'):
        comorb.append('თამბაქოს მწეველი')
    lines.append(f"თანმხლები ფონი: {', '.join(comorb) if comorb else 'მნიშვნელოვანი ქრონიკული დაავადებების გარეშე'}.")

    meds = []
    if q.get('med_aspirin'):
        meds.append('ასპირინი')
    if q.get('med_clopidogrel'):
        meds.append('კლოპიდოგრელი/P2Y12')
    if q.get('med_doac'):
        meds.append('პერორალური ანტიკოაგულანტი (DOAC)')
    if q.get('med_warfarin'):
        meds.append('ვარფარინი')
    if q.get('metformin'):
        meds.append('მეტფორმინი')
    if q.get('sglt2'):
        meds.append('SGLT2 ინჰიბიტორი')
    if q.get('other_meds'):
        meds.append(q['other_meds'])
    lines.append(f"მუდმივი მედიკამენტები: {', '.join(meds) if meds else 'არ იღებს'}.")

    allergies = []
    if q.get('allergy_iodine'):
        allergies.append('იოდი / კონტრასტი!')
    if q.get('allergy_other'):
        allergies.append(q.get('allergy_text') or 'მედიკამენტური ალერგია')
    lines.append(f"ალერგია: {', '.join(allergies) if allergies else 'უარყოფს (მ.შ. იოდზე/კონტრასტზე)'}.")

    anesth_flags = []
    if q.get('anesth_issue'):
        anesth_flags.append('გართულება წარსულ ანესთეზიაზე')
    if q.get('pacemaker'):
        anesth_flags.append('კარდიოსტიმულატორი/იმპლანტი')
    if q.get('loose_teeth'):
        anesth_flags.append('პროთეზი / მერყევი კბილები')
    if anesth_flags:
        lines.append(f"ანესთეზიოლოგიური შენიშვნა: {', '.join(anesth_flags)}.")

    lab_bits = []
    if s.get('bp_sys') and s.get('bp_dia'):
        lab_bits.append(f"BP {s['bp_sys']}/{s['bp_dia']} mmHg")
    if ev['hb_gl'] is not None:
        lab_bits.append(f"Hb {ev['hb_gl']} გ/ლ")
    if ev['egfr'] is not None:
        lab_bits.append(f"eGFR {ev['egfr']} მლ/წთ")
    if ev['alb_gl'] is not None:
        lab_bits.append(f"ალბუმინი {ev['alb_gl']} გ/ლ")
    if s.get('lvef') is not None:
        lab_bits.append(f"LVEF {s['lvef']}%")
    if pathway != PreopPatient.Pathway.CEA and (s.get('carotid_r') is not None or s.get('carotid_l') is not None):
        lab_bits.append(f"საძილეები R {s.get('carotid_r') or '—'}% / L {s.get('carotid_l') or '—'}%")
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
# Views: Tweek Weekly Calendar, Patient Workspace, Cardiology Audit
# --------------------------------------------------------------------------

def _monday_of(day):
    return day - datetime.timedelta(days=day.weekday())


def _week_title(monday):
    sunday = monday + datetime.timedelta(days=6)
    if monday.month == sunday.month:
        return f'{MONTHS[monday.month]} {monday.year} · {monday.day} – {sunday.day} {MONTHS_SHORT[monday.month]}'
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

        all_active = list(
            PreopPatient.objects.filter(archived=False)
            .select_related('surgeon', 'surgeon__profile')
            .order_by('visit_date', 'id')[:500]
        )
        pw_counts = {'all': len(all_active), 'aaa_infra': 0, 'cea': 0, 'abf': 0}
        counts = {'all': 0, STATUS_NO_Q: 0, STATUS_PROGRESS: 0, STATUS_ALERT: 0, STATUS_READY: 0}
        ev_by_id = {}
        for p in all_active:
            ev = evaluate(p, today=today)
            ev_by_id[p.pk] = ev
            pw_counts[ev['pathway']] = pw_counts.get(ev['pathway'], 0) + 1
            if pathway_filter == 'all' or ev['pathway'] == pathway_filter:
                counts['all'] += 1
                counts[ev['status']] += 1

        visible_patients = []
        for p in qs:
            p.ev = ev_by_id.get(p.pk) or evaluate(p, today=today)
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

        overdue_patients = [
            p for p in all_active
            if ev_by_id[p.pk]['over_target_days']
            and (pathway_filter == 'all' or ev_by_id[p.pk]['pathway'] == pathway_filter)
        ]
        for p in overdue_patients:
            p.ev = ev_by_id[p.pk]

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
            'q': q,
            'doctors': doctors(),
            'op_subtype_groups': OP_SUBTYPE_GROUPS,
            'overdue_patients': overdue_patients,
            'pathway_pills': [
                ('all', 'ყველა ნოზოლოგია', pw_counts['all']),
                ('aaa_infra', 'ინფრარენალური AAA', pw_counts['aaa_infra']),
                ('cea', 'კაროტიდული (CEA)', pw_counts['cea']),
                ('abf', 'აორტო-ბიფემორალური (ABF)', pw_counts['abf']),
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
    """Inline 1-line Tweek add (POST) or 1-click transfer from a Conference Case (GET/POST)."""

    def get(self, request):
        case_id = request.GET.get('case', '')
        if case_id.isdigit():
            case = get_object_or_404(Case, pk=int(case_id))
            existing = PreopPatient.objects.filter(case=case, archived=False).first()
            if existing:
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
        if slot.startswith('someday:'):
            key = slot.split(':', 1)[1]
            someday = key if key in PreopPatient.SomedayList.values else PreopPatient.SomedayList.CONF
        else:
            visit_date = _date(slot) or timezone.localdate()
            started_on = visit_date

        p = PreopPatient.objects.create(
            patient_name=name,
            age=_int(request.POST.get('age'), lo=1, hi=120),
            sex=request.POST.get('sex') if request.POST.get('sex') in ('M', 'F') else 'M',
            side=side,
            op_subtype=op_subtype,
            surgeon=surgeon,
            visit_date=visit_date,
            someday_list=someday,
            started_on=started_on,
            created_by=request.user,
            updated_by=request.user,
        )
        if request.POST.get('open_card') == '1':
            return HttpResponseRedirect(reverse('preop-patient', args=[p.pk]))
        week_q = f'?week={visit_date.isoformat()}' if visit_date else ''
        return HttpResponseRedirect(reverse('preop') + week_q)


class PreopPatientView(LoginRequiredMixin, generic.View):
    """Individual Preoperative Assessment Workspace for one patient (4 fast tabs)."""
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

        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]))

    def _save_header(self, request, patient):
        name = clean_text(request.POST.get('patient_name'), 150).replace('\n', ' ')
        if name:
            patient.patient_name = name
        patient.age = _int(request.POST.get('age'), lo=1, hi=120)
        sex = request.POST.get('sex') or ''
        if sex in ('M', 'F', ''):
            patient.sex = sex
        if 'side' in request.POST and request.POST.get('side') in ('', 'R', 'L', 'B'):
            patient.side = request.POST.get('side')
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
        patient.updated_by = request.user
        patient.save()

    def _save_questionnaire(self, request, patient):
        q = merged_q(patient)
        s = merged_studies(patient)
        bool_fields = (
            # AAA
            'aaa_pain', 'prev_abdo_surg', 'claudication', 'active_infection',
            # CEA
            'cea_symp_event', 'cea_crescendo', 'cea_thrombolysis',
            'cea_prior_neck', 'cea_hoarseness', 'cea_disabling_stroke',
            # ABF
            'abf_buttock_pain', 'abf_rest_pain', 'abf_ulcer', 'abf_wet_infection',
            'abf_groin_issue', 'abf_prior_vasc',
            # General & anesthesia
            'cad', 'recent_stent', 'htn', 'pulm', 'dm', 'dm_insulin', 'metformin', 'sglt2',
            'neuro', 'ulcer', 'med_aspirin', 'med_clopidogrel', 'med_doac', 'med_warfarin',
            'allergy_iodine', 'allergy_other', 'anesth_issue', 'pacemaker', 'loose_teeth', 'smoker',
        )
        for f in bool_fields:
            q[f] = request.POST.get(f) in ('1', 'on', 'true')
        q['mets_ok'] = request.POST.get('mets_ok', '1') != '0'

        if 'cea_days_since_event' in request.POST:
            q['cea_days_since_event'] = _int(request.POST.get('cea_days_since_event'), lo=0, hi=365)
        if 'abf_claud_m' in request.POST:
            q['abf_claud_m'] = _int(request.POST.get('abf_claud_m'), lo=0, hi=5000)
        if 'side' in request.POST and request.POST.get('side') in ('', 'R', 'L', 'B'):
            patient.side = request.POST.get('side')

        for tf, lim in (
            ('prev_abdo_text', 250), ('infection_text', 250),
            ('cea_event_text', 250), ('cea_prior_neck_text', 250),
            ('abf_duration', 120), ('abf_ulcer_text', 250), ('abf_prior_vasc_text', 250),
            ('cad_detail', 250), ('pulm_detail', 250), ('other_meds', 400), ('allergy_text', 250),
        ):
            if tf in request.POST:
                q[tf] = clean_text(request.POST.get(tf), lim).replace('\n', ' ')

        q['filled'] = True
        q['filled_at'] = timezone.localtime().strftime('%d.%m.%Y %H:%M')
        q['filled_by'] = person_name(request.user)

        if 'aaa_mm' in request.POST:
            s['aaa_mm'] = _num(request.POST.get('aaa_mm'), lo=10, hi=200)
        if 'cta_months' in request.POST:
            s['cta_months'] = _int(request.POST.get('cta_months'), lo=0, hi=120)
        s['cta_disc'] = request.POST.get('cta_disc') in ('1', 'on', 'true')

        if not patient.started_on:
            patient.started_on = patient.visit_date or timezone.localdate()
        patient.questionnaire = q
        patient.studies = s
        patient.updated_by = request.user
        patient.save()

    def _save_stage1(self, request, patient):
        s = merged_studies(patient)
        num_fields = (
            ('aaa_mm', 10, 200), ('cta_months', 0, 120),
            ('abi_r', 0.0, 2.5), ('abi_l', 0.0, 2.5),
            ('bp_sys', 40, 300), ('bp_dia', 20, 200), ('hr', 20, 250), ('spo2', 50, 100),
            ('weight_kg', 25, 260), ('height_cm', 100, 230),
            ('hb', 1, 260), ('wbc', 0.1, 150), ('plt', 1, 2000), ('inr', 0.5, 15), ('fibrinogen', 0.2, 15),
            ('creat', 0.1, 3000), ('k', 1.5, 10), ('na', 100, 180),
            ('glucose', 0.5, 1000), ('hba1c', 3, 25), ('albumin', 0.5, 80),
            ('urine_wbc', 0, 1000),
            ('carotid_r', 0, 100), ('carotid_l', 0, 100), ('lvef', 5, 90),
        )
        for key, lo, hi in num_fields:
            if key in request.POST:
                s[key] = _num(request.POST.get(key), lo=lo, hi=hi)

        for bf in ('cta_disc', 'irregular_hr', 'aptt_prolonged', 'viral_positive',
                   'urine_nitrite', 'carotid_symp', 'echo_valve_issue'):
            s[bf] = request.POST.get(bf) in ('1', 'on', 'true')

        s['blood_group'] = clean_text(request.POST.get('blood_group'), 30)
        if request.POST.get('liver_status') in ('normal', '2x', '3x'):
            s['liver_status'] = request.POST.get('liver_status')
        if request.POST.get('cxr') in ('pending', 'normal', 'emphysema', 'infiltrate'):
            s['cxr'] = request.POST.get('cxr')
        if request.POST.get('ecg') in ('pending', 'normal', 'ischemia_arrhythmia'):
            s['ecg'] = request.POST.get('ecg')
        if request.POST.get('cea_cta_status') in ('pending', 'concordant', 'high_calcified'):
            s['cea_cta_status'] = request.POST.get('cea_cta_status')
        if request.POST.get('brain_imaging') in ('pending', 'normal_small', 'large_or_hemorrhage'):
            s['brain_imaging'] = request.POST.get('brain_imaging')
        if request.POST.get('abf_clamp_zone') in ('pending', 'infrarenal', 'juxtarenal_calcified'):
            s['abf_clamp_zone'] = request.POST.get('abf_clamp_zone')
        if request.POST.get('abf_outflow') in ('pending', 'patent', 'profunda_stenosis', 'poor_outflow'):
            s['abf_outflow'] = request.POST.get('abf_outflow')

        if not patient.started_on:
            patient.started_on = patient.visit_date or timezone.localdate()
        patient.studies = s
        patient.updated_by = request.user
        patient.save()

    def _save_stage2(self, request, patient):
        s = merged_studies(patient)
        if 'spiro_fev1' in request.POST:
            s['spiro_fev1'] = _int(request.POST.get('spiro_fev1'), lo=10, hi=150)
        if request.POST.get('gastro_result') in ('pending', 'remission', 'acute_ulcer'):
            s['gastro_result'] = request.POST.get('gastro_result')
        if request.POST.get('laryngoscopy_res') in ('pending', 'normal', 'palsy'):
            s['laryngoscopy_res'] = request.POST.get('laryngoscopy_res')
        for bf in (
            'urine_culture_done', 'blood_reserved', 'evar_sized', 'med_hold_agreed',
            'anesth_done', 'neuro_consult_done', 'wound_care_done',
        ):
            s[bf] = request.POST.get(bf) in ('1', 'on', 'true')
        s['nurse_note'] = clean_text(request.POST.get('nurse_note'), 1000)

        cardio_name = clean_text(request.POST.get('cardiologist_custom') or request.POST.get('cardiologist'), 100).replace('\n', ' ')
        patient.cardiologist = cardio_name or 'ლიკა ინჯია'
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
        patient.cardio_note = clean_text(request.POST.get('cardio_note'), 255).replace('\n', ' ')

        patient.studies = s
        patient.updated_by = request.user
        patient.save()

    def _save_signoff(self, request, patient):
        action = request.POST.get('action') or 'save_anamnesis'
        if 'anamnesis' in request.POST:
            if action == 'regenerate':
                patient.anamnesis = ''
            else:
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
            s['alerts_ack_sig'] = ev['alerts_sig']
            patient.studies = s
            patient.approved_at = timezone.now()
            patient.approved_by = request.user
            if request.POST.get('move_ready') == '1':
                patient.someday_list = PreopPatient.SomedayList.READY
                patient.visit_date = None
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

    def _render(self, request, patient):
        ev = evaluate(patient)
        tab = request.GET.get('tab') or ('q' if not ev['q'].get('filled') else 's1')
        if tab not in ('q', 's1', 's2', 'doc'):
            tab = 'q'
        auto_anamnesis = generate_anamnesis(patient, ev)
        files = list(patient.files.select_related('created_by', 'created_by__profile'))
        files_by_cat = []
        for key, label in PreopFile.Category.choices:
            cat_items = [f for f in files if f.category == key]
            if cat_items:
                files_by_cat.append({'key': key, 'label': label, 'files': cat_items})

        from .blood import BLOOD_GROUPS as BG_LIST
        return render(request, self.template_name, {
            'p': patient,
            'ev': ev,
            'q': ev['q'],
            's': ev['s'],
            'tab': tab,
            'auto_anamnesis': auto_anamnesis,
            'shown_anamnesis': patient.anamnesis or auto_anamnesis,
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

        patient.updated_by = request.user
        patient.save(update_fields=['visit_date', 'someday_list', 'started_on', 'updated_at', 'updated_by'])
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
    """Cardiology Consultation & Prolonged-Workup Audit across AAA, CEA, and ABF."""
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

        for p in qs:
            ev = evaluate(p, today=today)
            p.ev = ev
            p.surgeon_name = person_name(p.surgeon)
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
                if ev['days_elapsed'] > ev['target_days']:
                    g['over_7'] += 1
                g['days_sum'] += ev['days_elapsed']
                evaluated_rows.append(p)

        table_rows = []
        for g in groups.values():
            g['extra_pct'] = round(g['extra'] * 100 / g['total']) if g['total'] else 0
            g['avg_days'] = round(g['days_sum'] / g['total'], 1) if g['total'] else None
            table_rows.append(g)

        ctx.update({
            'pathway_filter': pathway_filter,
            'pathway_pills': [
                ('all', 'ყველა ნოზოლოგია'),
                ('aaa_infra', 'ინფრარენალური AAA'),
                ('cea', 'კაროტიდული (CEA)'),
                ('abf', 'აორტო-ბიფემორალური (ABF)'),
            ],
            'total_patients': len(qs),
            'total_aaa': len(qs),
            'cardio_seen_total': cardio_seen_total,
            'non_compliant_total': non_compliant_total,
            'over_7_total': over_target_total,
            'table_rows': table_rows,
            'patient_rows': evaluated_rows[:150],
            'target_days': TARGET_PREP_DAYS,
        })
        return ctx
