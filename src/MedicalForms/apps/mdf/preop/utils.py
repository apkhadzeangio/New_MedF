"""Date, numeric parsing, masking, token, dict sanitization, and calendar utility helpers for preop."""
import datetime
import hashlib
import math
import re
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation

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

_NONFINITE_STRS = frozenset({'nan', '+nan', '-nan', 'inf', '+inf', '-inf', 'infinity', '+infinity', '-infinity'})


def _has_raw_scalar(val):
    """Return True if val is a non-empty raw scalar input without invoking str() on huge ints."""
    if val is None or val == '':
        return False
    if isinstance(val, (bool, int, float)):
        return True
    if isinstance(val, str):
        return val.strip() != ''
    return bool(val)


def _safe_display_str(val, max_len=160, default=''):
    """Safely format arbitrary text/values for display: reject containers/NaN/Inf/huge ints and bound length."""
    if val is None or isinstance(val, (bool, dict, list, tuple, set)):
        return default
    if isinstance(val, int) and abs(val) > 10**12:
        return default
    if isinstance(val, float) and not math.isfinite(val):
        return default
    s_v = ' '.join(str(val).split())
    if not s_v or s_v.lower() in _NONFINITE_STRS:
        return default
    return s_v if len(s_v) <= max_len else (s_v[: max_len - 1].rstrip() + '…')


def chip_summary(selected_keys, field_name, fallback_text=''):
    labels = CHIP_LABELS.get(field_name, {})
    seq = selected_keys if isinstance(selected_keys, (list, tuple, set)) else ()
    seq_set = {k for k in seq if isinstance(k, str)}
    picked = [lbl for k, lbl in labels.items() if k in seq_set]
    extra = _safe_display_str(fallback_text, max_len=160) if isinstance(fallback_text, str) else ''
    if extra and extra not in picked:
        picked.append(extra)
    return ', '.join(picked)


def is_self_filled_q(q):
    """Return True if questionnaire was filled/submitted by the patient via self-fill link."""
    if not isinstance(q, dict):
        return False
    return bool(q.get('self_fill_submitted') or q.get('self_filled') or q.get('filled_by') == 'პაციენტი (ბმულით)')


# --------------------------------------------------------------------------
# Parsing & clinical math helpers (with explicit unit-ambiguity guards)
# --------------------------------------------------------------------------

_ASCII_NUM_RE = re.compile(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$', re.ASCII)


def _raw_finite_decimal(val):
    if val is None or val == '' or isinstance(val, (bool, list, tuple, dict, set)):
        return None
    if isinstance(val, int):
        return None if abs(val) > 10**18 else Decimal(val)
    if isinstance(val, float):
        if not math.isfinite(val) or abs(val) > 1e18:
            return None
        try:
            return Decimal(str(val))
        except InvalidOperation:
            return None
    s_val = str(val).strip().replace(',', '.')
    if len(s_val) > 40 or not _ASCII_NUM_RE.match(s_val):
        return None
    try:
        d = Decimal(s_val)
    except InvalidOperation:
        return None
    return d if (d.is_finite() and abs(d) <= Decimal('1e18')) else None


def parse_plt_k(raw, allow_negative_zero=False):
    """Parse PLT into x10^9/L using exact Decimal floor (never rounds <50/<100/<150 upward)."""
    from .registry import rule_value

    d = _raw_finite_decimal(raw)
    if d is None:
        return None
    if d < Decimal('1'):
        return 0 if allow_negative_zero else None
    u = rule_value('LAB_UNIT_AUTO_CONVERSION')
    if d > Decimal('2000000'):
        return None
    ambig_lo, ul_ge = Decimal(str(u['plt_ambig_lo'])), Decimal(str(u['plt_ul_ge']))
    if ambig_lo < d < ul_ge:
        return None
    if d >= ul_ge:
        d = d / Decimal('1000')
    if d < Decimal('1') or d > ambig_lo:
        return None
    q = d.quantize(Decimal('0.1'), rounding=ROUND_FLOOR)
    return int(q) if q == int(q) else float(q)


def parse_wbc_k(raw):
    """Parse WBC into x10^9/L preserving abnormal <wbc_lo (floor) and >wbc_hi (ceil) boundaries."""
    from .registry import rule_value

    d = _raw_finite_decimal(raw)
    if d is None or d < Decimal('0.1') or d > Decimal('150000'):
        return None
    u = rule_value('LAB_UNIT_AUTO_CONVERSION')
    coag = rule_value('LAB_WBC_PLT_COAG')
    ul_ge = Decimal(str(u['wbc_ul_ge']))
    if d >= ul_ge:
        d = d / Decimal('1000')
    if d < Decimal('0.1') or d > ul_ge:
        return None
    lo_b, hi_b = Decimal(str(coag['wbc_lo'])), Decimal(str(coag['wbc_hi']))
    if d < lo_b:
        q = d.quantize(Decimal('0.01'), rounding=ROUND_FLOOR)
    elif d > hi_b:
        q = d.quantize(Decimal('0.01'), rounding=ROUND_CEILING)
    else:
        q = d.quantize(Decimal('0.01'))
    return int(q) if q == int(q) else float(q)


def _num(val, lo=None, hi=None):
    if val is None or val == '' or isinstance(val, (bool, list, tuple, dict, set)):
        return None
    if isinstance(val, int):
        if abs(val) > 10**15:
            return None
        v = float(val)
    elif isinstance(val, float):
        v = val
    else:
        s_val = str(val).strip().replace(',', '.')
        if len(s_val) > 40 or not _ASCII_NUM_RE.match(s_val):
            return None
        try:
            v = float(s_val)
        except (ValueError, TypeError, OverflowError):
            return None
    if not math.isfinite(v) or abs(v) > 1e15:
        return None
    if lo is not None and v < lo:
        return None
    if hi is not None and v > hi:
        return None
    return int(v) if (v % 1 == 0) else v


def _int(val, lo=None, hi=None):
    v = _num(val, lo=lo, hi=hi)
    return int(round(v)) if v is not None else None


def _date(val):
    if isinstance(val, datetime.datetime):
        return val.date()
    if isinstance(val, datetime.date):
        return val
    if not isinstance(val, str):
        return None
    raw = val.strip()
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        try:
            return datetime.datetime.fromisoformat(raw.replace('Z', '+00:00')).date()
        except ValueError:
            return None


def _resolve_pathway(patient):
    """Resolve canonical PreopPatient.Pathway even if op_subtype or pathway contains legacy/corrupt values."""
    sub = patient.op_subtype if isinstance(patient.op_subtype, str) else ''
    raw_pw = patient.pathway if isinstance(patient.pathway, str) else ''
    pw = PreopPatient.SUBTYPE_PATHWAY.get(sub, raw_pw or PreopPatient.Pathway.AAA_INFRA)
    return pw if pw in PreopPatient.Pathway.values else PreopPatient.Pathway.AAA_INFRA


def _clean_person_id(val):
    """Return ASCII digits-only Georgian Personal ID (up to 11 digits)."""
    if val is None or isinstance(val, (bool, dict, list, tuple, set)):
        return ''
    if isinstance(val, int) and abs(val) > 10**15:
        return ''
    return re.sub(r'[^0-9]', '', str(val))[:11]


def _fmt_ka_date(val, short=False):
    """Format ISO YYYY-MM-DD date as dd.mm.yyyy (or dd.mm when short=True)."""
    dt = val if isinstance(val, datetime.date) else _date(val)
    if not dt:
        return ''
    return dt.strftime('%d.%m' if short else '%d.%m.%Y')


def is_valid_birth_date(val, today=None):
    """Return True iff `val` parses as a plausible adult/adolescent birth date (year >= 1900, age 12..120)."""
    dob = val if isinstance(val, datetime.date) else _date(val)
    today = _date(today) or timezone.localdate()
    if not dob or dob > today or dob.year < 1900:
        return False
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return 12 <= years <= 120


def _age_from_dob(val, today=None):
    """Compute integer age (12..120) from ISO birth_date."""
    dob = val if isinstance(val, datetime.date) else _date(val)
    today = _date(today) or timezone.localdate()
    if not is_valid_birth_date(dob, today=today):
        return None
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return years if 12 <= years <= 120 else None


def _alerts_signature(red_flags):
    """Stable SHA-1 signature across days (strips volatile elapsed day numbers like '8 დღე')."""
    if not red_flags:
        return ''
    normalized = [re.sub(r'\d+\s*დღ', 'N დღ', f) for f in red_flags]
    joined = '\n'.join(sorted(normalized)).encode('utf-8')
    return hashlib.sha1(joined).hexdigest()[:16]


def mask_person_id(pid):
    """Mask 11-digit personal number on unauthenticated pages (e.g. '010•••••001')."""
    if pid is None or isinstance(pid, (dict, list, tuple, set, bytes)) or (isinstance(pid, int) and not isinstance(pid, bool) and abs(pid) > 10**15):
        return ''
    pid = str(pid).strip()
    if not pid:
        return ''
    if len(pid) >= 7:
        return f'{pid[:3]}•••••{pid[-3:]}'
    return '•' * len(pid)


def mask_phone(phone):
    """Mask phone number on unauthenticated pages."""
    if phone is None or isinstance(phone, (dict, list, tuple, set, bytes)) or (isinstance(phone, int) and not isinstance(phone, bool) and abs(phone) > 10**18):
        return ''
    ph = re.sub(r'\s+', '', str(phone))
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


def is_symp_cea_patient(patient, q=None, today=None, s=None, require_filled=True):
    """Canonical 6-month symptomatic CEA determination (active crescendo TIA is ALWAYS symptomatic)."""
    if _resolve_pathway(patient) != PreopPatient.Pathway.CEA:
        return False
    from .registry import rule_value
    _, _, symp_max_days = rule_value('CEA_SYMP_WINDOW_DAYS')
    q_d = q if isinstance(q, dict) else (patient.questionnaire if isinstance(patient.questionnaire, dict) else {})
    s_d = s if isinstance(s, dict) else (patient.studies if isinstance(patient.studies, dict) else {})
    q_ok = bool(q_d.get('filled')) if require_filled else True
    if q_ok and bool(q_d.get('cea_crescendo')):
        return True
    days_ev = cea_live_days_since_event(q_d, today=today, s=s_d)
    symp_within_6m = (days_ev is None) or (days_ev <= symp_max_days)
    has_symp_flag = bool(q_d.get('cea_symp_event')) or bool(_str_list(q_d.get('cea_event_chips')))
    return bool(
        (patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP and symp_within_6m)
        or (q_ok and has_symp_flag and symp_within_6m)
    )


def has_prior_stroke_or_tia(patient, q=None, s=None, today=None):
    """Return True for any lifelong prior stroke/TIA history (including CEA >180d, event chips, Stage-1 carotid_symp, or brain imaging)."""
    q_d = q if isinstance(q, dict) else (patient.questionnaire if (patient and isinstance(patient.questionnaire, dict)) else {})
    s_d = s if isinstance(s, dict) else (patient.studies if (patient and isinstance(patient.studies, dict)) else {})
    today_dt = _date(today) or timezone.localdate()
    has_ev_days = _int(q_d.get('cea_days_since_event'), lo=0, hi=3650) is not None
    ev_dt = _date(q_d.get('cea_event_date'))
    has_ev_date = ev_dt is not None and ev_dt <= today_dt
    has_ev_chips = any(isinstance(c, str) and c.strip() for c in _str_list(q_d.get('cea_event_chips')))
    is_cea_ctx = patient is None or _resolve_pathway(patient) == PreopPatient.Pathway.CEA
    return bool(
        q_d.get('neuro')
        or q_d.get('cea_symp_event')
        or (is_cea_ctx and q_d.get('cea_crescendo'))
        or q_d.get('cea_disabling_stroke')
        or q_d.get('cea_thrombolysis')
        or has_ev_chips
        or has_ev_date
        or has_ev_days
        or (patient is not None and patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP)
        or bool(s_d.get('carotid_symp'))
        or s_d.get('brain_imaging') == 'large_or_hemorrhage'
    )


def target_prep_days(patient, today=None):
    """Pathway-specific preoperative workup target limit (days), reading canonical WF_PREP_TARGET_DAYS registry."""
    from .registry import rule_value
    cfg = rule_value('WF_PREP_TARGET_DAYS')
    pw = _resolve_pathway(patient)
    q = patient.questionnaire if isinstance(patient.questionnaire, dict) else {}
    s = patient.studies if isinstance(patient.studies, dict) else {}
    q_filled = bool(q.get('filled'))
    if pw == PreopPatient.Pathway.CEA:
        is_symp = is_symp_cea_patient(patient, q, today=today, s=s, require_filled=False)
        return cfg['cea_symp'] if is_symp else cfg['elective_art']
    has_ali_q = (q.get('abf_duration') == '< 2 კვირა')
    has_clti_q = q_filled and (
        bool(q.get('abf_rest_pain')) or bool(q.get('abf_ulcer'))
        or bool(_str_list(q.get('ulcer_chips'))) or bool(q.get('abf_wet_infection'))
    )
    if pw in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA) and has_ali_q:
        return cfg['cea_symp']
    if pw == PreopPatient.Pathway.ABF:
        return cfg['aaa_clti'] if (patient.op_subtype == PreopPatient.OpSubtype.ABF_CLTI or has_clti_q) else cfg['elective_art']
    if pw == PreopPatient.Pathway.PAD_BYPASS:
        return cfg['aaa_clti'] if (patient.op_subtype == PreopPatient.OpSubtype.BYPASS_CLTI or has_clti_q) else cfg['elective_art']
    if pw == PreopPatient.Pathway.PAD_PTA:
        return cfg['aaa_clti'] if (patient.op_subtype == PreopPatient.OpSubtype.PTA_CLTI or has_clti_q) else cfg['elective_art']
    if pw == PreopPatient.Pathway.VENOUS:
        return cfg['venous']
    if pw == PreopPatient.Pathway.AAA_INFRA:
        return cfg['aaa_clti']
    return TARGET_PREP_DAYS


def prep_days_elapsed(patient, today=None):
    """Days elapsed since preoperative workup began (pauses on HOLD, freezes on approve/operated/cancelled)."""
    today = _date(today) or timezone.localdate()
    q = patient.questionnaire if isinstance(patient.questionnaire, dict) else {}
    s = patient.studies if isinstance(patient.studies, dict) else {}
    q_filled = bool(q.get('filled'))
    candidates = [d for d in (_date(patient.started_on), _date(patient.visit_date)) if d is not None]
    if not candidates:
        return 0
    start = min(candidates)
    raw_out = s.get('case_outcome_status')
    outcome = raw_out if isinstance(raw_out, str) and raw_out else 'scheduled'
    op_done_dt = _date(s.get('operated_date'))
    out_rec_dt = _date(s.get('outcome_recorded_on'))

    if patient.approved_at:
        if isinstance(patient.approved_at, datetime.datetime):
            end_day = timezone.localtime(patient.approved_at).date() if timezone.is_aware(patient.approved_at) else patient.approved_at.date()
        else:
            end_day = _date(patient.approved_at) or today
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


def is_pregnancy_eligible(patient):
    """Return True if patient is biologically eligible for pregnancy (not male; if age known, registry bounds)."""
    from .registry import rule_value
    if (patient.sex or '') == 'M':
        return False
    lo_age, hi_age = rule_value('VEN_PREGNANCY_STOP')
    age_v = _int(patient.age, lo=1, hi=120)
    if age_v is not None and not (lo_age <= age_v <= hi_age):
        return False
    return True


def cea_live_days_since_event(q, today=None, s=None):
    """Compute elapsed days from cea_event_date (freezing at operated_date once operated), else static field."""
    if not isinstance(q, dict):
        return None
    if not q.get('cea_symp_event') and not q.get('cea_event_date') and q.get('cea_days_since_event') is None:
        return None
    ref_day = _date(today) or timezone.localdate()
    if isinstance(s, dict) and s.get('case_outcome_status') == 'operated':
        op_dt = _date(s.get('operated_date'))
        if op_dt:
            ref_day = op_dt
    ev_dt = _date(q.get('cea_event_date'))
    if ev_dt and ev_dt <= ref_day:
        return (ref_day - ev_dt).days
    return _int(q.get('cea_days_since_event'), lo=0, hi=3650)


def _add_calendar_months(dt, n):
    """Return dt shifted forward by n calendar months with end-of-month clamping."""
    import calendar
    total_m = (dt.year * 12 + (dt.month - 1)) + n
    y, m0 = divmod(total_m, 12)
    m = m0 + 1
    return datetime.date(y, m, min(dt.day, calendar.monthrange(y, m)[1]))


def cta_live_months_age(s, today=None):
    """Compute strictly monotonic calendar-month CTA age from cta_date, else static cta_months."""
    if not isinstance(s, dict):
        return None
    today = _date(today) or timezone.localdate()
    cta_dt = _date(s.get('cta_date'))
    if cta_dt and cta_dt <= today:
        raw = (today.year - cta_dt.year) * 12 + (today.month - cta_dt.month)
        completed = max(0, raw - 1) if _add_calendar_months(cta_dt, raw) > today else raw
        if completed == 0:
            return 0
        return completed + (1 if today > _add_calendar_months(cta_dt, completed) else 0)
    return _int(s.get('cta_months'), lo=0, hi=240)


def consent_url_for_preop(patient):
    """Return direct ConsentSheetView URL (with ?preop=<pk>) if a matching form exists, else filtered list."""
    from ..models import ConsentForm
    pw = _resolve_pathway(patient)
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


def _strip_non_finite(obj):
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else (0.0 if obj == 0 else obj)
    if isinstance(obj, int) and not isinstance(obj, bool):
        try:
            float(obj)
        except OverflowError:
            return None
        return obj
    if isinstance(obj, dict):
        return {
            dk: _strip_non_finite(dv) for dk, dv in obj.items()
            if not (isinstance(dv, float) and not math.isfinite(dv))
        }
    if isinstance(obj, (list, tuple)):
        return [
            _strip_non_finite(x) for x in obj
            if not (isinstance(x, float) and not math.isfinite(x))
        ]
    return obj


_CEAP_CODE_RE = re.compile(r'^C[0-6][a-zA-Z]{0,3}$')
_BOUNDED_STR_KEYS = {
    'blood_group': 24,
    'abf_duration': 60,
    'nurse_note': 500,
    'cardio_note_custom': 500,
}


_SAN_INV_TRACK_KEYS = frozenset({'dos_inr', 'inr', 'plt', 'fibrinogen'})


def _sanitize_merged_dict(defaults, raw_dict):
    """Merge JSON payload against typed defaults, stripping NaN/Inf, huge ints, and unbounded display strings."""
    data, san_inv = {}, {}
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
            if not isinstance(rv, str):
                data[k] = ''
            elif k == 'ven_ceap':
                s_ceap = rv.strip()
                data[k] = s_ceap if _CEAP_CODE_RE.match(s_ceap) else ''
            elif k in _BOUNDED_STR_KEYS:
                data[k] = _safe_display_str(rv, max_len=_BOUNDED_STR_KEYS[k])
            else:
                data[k] = rv
        elif isinstance(dv, int):
            iv = _int(rv, lo=0, hi=36500)
            data[k] = iv if iv is not None else dv
        else:
            if isinstance(rv, (int, float)) and not isinstance(rv, bool):
                try:
                    rv_f = float(rv)
                except OverflowError:
                    data[k] = None
                else:
                    if not math.isfinite(rv_f) or abs(rv_f) > 1e12:
                        data[k] = None
                    else:
                        data[k] = 0.0 if (isinstance(rv, float) and rv == 0) else rv
            elif k in ('plt', 'wbc'):
                dec_v = _raw_finite_decimal(rv)
                if dec_v is None or abs(dec_v) > Decimal('1e12'):
                    data[k] = None
                else:
                    data[k] = int(dec_v) if dec_v == int(dec_v) else float(dec_v)
            else:
                data[k] = _num(rv)
            if k in _SAN_INV_TRACK_KEYS and data[k] is None and _has_raw_scalar(rv):
                san_inv[k] = _safe_display_str(rv, max_len=24, default='invalid')
    for k, rv in src.items():
        if k not in data:
            if isinstance(rv, float) and not math.isfinite(rv):
                continue
            data[k] = _strip_non_finite(rv)
    if san_inv:
        data['_sanitized_invalid'] = san_inv
    return data


def merged_q(patient):
    return _sanitize_merged_dict(DEFAULT_Q, patient.questionnaire)


def merged_studies(patient):
    return _sanitize_merged_dict(DEFAULT_STUDIES, patient.studies)


def _update_hold_pause(s, old_someday, new_someday, today=None, started_on=False):
    """Track paused elapsed days when entering/exiting SomedayList.HOLD (ignoring pre-workup HOLD before started_on)."""
    today = _date(today) or timezone.localdate()
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
    try:
        return day - datetime.timedelta(days=day.weekday())
    except OverflowError:
        return datetime.date.min


def _week_title(monday):
    try:
        sunday = monday + datetime.timedelta(days=6)
    except OverflowError:
        sunday = datetime.date.max
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
