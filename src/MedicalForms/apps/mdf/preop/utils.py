"""Date, numeric parsing, masking, token, dict sanitization, and calendar utility helpers for preop."""
import datetime
import hashlib
import math
import re

from django.core.signing import TimestampSigner
from django.urls import reverse
from django.utils import timezone

from ..duty import MONTHS
from ..models import PreopPatient
from .constants import (
    CHIP_LABELS,
    DEFAULT_CARDIOLOGISTS,
    DEFAULT_Q,
    DEFAULT_STUDIES,
    MONTHS_SHORT,
    SELF_FILL_SALT,
    TARGET_PREP_DAYS,
)


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


def target_prep_days(patient, today=None):
    """Pathway-specific preoperative workup target limit (days), honoring 6-month CEA symptom & CLTI flags."""
    pw = _resolve_pathway(patient)
    q = patient.questionnaire if isinstance(patient.questionnaire, dict) else {}
    s = patient.studies if isinstance(patient.studies, dict) else {}
    q_filled = bool(q.get('filled'))
    if pw == PreopPatient.Pathway.CEA:
        days_ev = cea_live_days_since_event(q, today=today, s=s)
        symp_within_6m = (days_ev is None) or (days_ev <= 180)
        is_symp = (
            (patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP and symp_within_6m)
            or (q_filled and bool(q.get('cea_symp_event')) and symp_within_6m)
        )
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
        if hold_since:
            eff_hs = max(hold_since, start)
            if eff_hs <= end_day:
                paused += max(0, (end_day - eff_hs).days)
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
    return _int(q.get('cea_days_since_event'), lo=0, hi=3650)


def cta_live_months_age(s, today=None):
    """Compute exact calendar-month CTA age from cta_date if present (rounding up once past exact anniversary), else static cta_months."""
    today = today or timezone.localdate()
    cta_dt = _date(s.get('cta_date'))
    if cta_dt and cta_dt <= today:
        months = (today.year - cta_dt.year) * 12 + (today.month - cta_dt.month)
        if today.day < cta_dt.day:
            months -= 1
        elif months >= 1 and today.day > cta_dt.day:
            months += 1
        return max(0, months)
    return _int(s.get('cta_months'), lo=0, hi=120)


def consent_url_for_preop(patient):
    """Return direct ConsentSheetView URL (with ?preop=<pk>) if a matching form exists, else filtered list."""
    from ..models import ConsentForm
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


def _update_hold_pause(s, old_someday, new_someday, today=None, started_on=False):
    """Track paused elapsed days when entering/exiting SomedayList.HOLD (ignoring pre-workup HOLD before started_on)."""
    today = today or timezone.localdate()
    hold_val = PreopPatient.SomedayList.HOLD
    if old_someday != hold_val and new_someday == hold_val:
        if not s.get('hold_since'):
            s['hold_since'] = today.isoformat()
    elif old_someday == hold_val and new_someday != hold_val:
        hs = _date(s.get('hold_since'))
        if started_on is None:
            # Workup had not started before exiting HOLD -> zero active prep days were paused!
            pass
        elif hs and hs <= today:
            eff_hs = max(hs, started_on) if isinstance(started_on, datetime.date) else hs
            if eff_hs <= today:
                s['hold_paused_days'] = (_int(s.get('hold_paused_days'), lo=0, hi=3650) or 0) + max(0, (today - eff_hs).days)
        s['hold_since'] = ''


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
