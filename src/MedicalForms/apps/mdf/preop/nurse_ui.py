"""Nurse Workspace presentation helpers, plain-Georgian labels, and 09:00/03:00 triage queues."""
import datetime
import hashlib
import re

from ..board import person_name
from ..models import StaffProfile
from .constants import STATUS_ALERT, STATUS_NO_Q, STATUS_READY
from .utils import _date, _fmt_ka_date

PREOP_UI_OLD = 'old'
PREOP_UI_NEW = 'new'
PREOP_UI_SESSION_KEY = 'preop_ui_mode'

PLAIN_PATHWAY_KA = {
    'aaa_infra': 'მუცლის აორტის ანევრიზმა',
    'cea': 'საძილე არტერიის ოპერაცია',
    'abf': 'მუცლის აორტის შუნტირება',
    'pad_bypass': 'ფეხის არტერიის შუნტირება',
    'pad_pta': 'სისხლძარღვის ბალონირება / სტენტირება',
    'venous': 'ვენების ოპერაცია / ლაზერი',
}

PLAIN_SUBTYPE_KA = {
    'open': 'მუცლის აორტის ღია ოპერაცია',
    'evar': 'მუცლის აორტის ენდოპროთეზირება (სტენტი)',
    'tbd': 'მუცლის აორტის ოპერაცია (ზუსტდება ტიპი)',
    'cea_symp': 'საძილე არტერიის ოპერაცია — სიმპტომური (სასწრაფო 14 დღე)',
    'cea_asymp': 'საძილე არტერიის ოპერაცია — უსიმპტომო',
    'abf_claud': 'აორტის შუნტირება — სიარულის ტკივილი',
    'abf_clti': 'აორტის შუნტირება — მძიმე იშემია / წყლული',
    'bypass_claud': 'ფეხის არტერიის შუნტირება — სიარულის ტკივილი',
    'bypass_clti': 'ფეხის არტერიის შუნტირება — მძიმე იშემია / წყლული',
    'pta_claud': 'არტერიის ბალონირება / სტენტი — სიარულის ტკივილი',
    'pta_clti': 'არტერიის ბალონირება / სტენტი — მძიმე იშემია / წყლული',
    'evla': 'ვენების ლაზერული ოპერაცია',
    'phlebectomy': 'ვარიკოზული ვენების ამოკვეთა',
}

PLAIN_SIDE_KA = {
    'R': 'მარჯვენა მხარე',
    'L': 'მარცხენა მხარე',
    'B': 'ორივე მხარე',
    '': '',
}

_DATE_DM_RE = re.compile(r'არ მიიღოთ\s+(\d{2})\.(\d{2})-დან|გამოტოვეთ\s+(\d{2})\.(\d{2})|(\d{2})\.(\d{2})-დან')


def get_preop_ui_mode(request):
    """Return 'new' or 'old' for the current authenticated user (default 'old')."""
    user = getattr(request, 'user', None)
    if user and getattr(user, 'is_authenticated', False):
        profile = StaffProfile.objects.filter(user_id=user.pk).only('preop_ui_mode').first()
        if profile and profile.preop_ui_mode in (PREOP_UI_OLD, PREOP_UI_NEW):
            return profile.preop_ui_mode
    sess_mode = request.session.get(PREOP_UI_SESSION_KEY) if hasattr(request, 'session') else None
    return PREOP_UI_NEW if sess_mode == PREOP_UI_NEW else PREOP_UI_OLD


def set_preop_ui_mode(request, mode):
    """Persist user's choice ('old' | 'new') to StaffProfile (if present) and session."""
    clean_mode = PREOP_UI_NEW if mode == PREOP_UI_NEW else PREOP_UI_OLD
    if hasattr(request, 'session'):
        request.session[PREOP_UI_SESSION_KEY] = clean_mode
    user = getattr(request, 'user', None)
    if user and getattr(user, 'is_authenticated', False):
        StaffProfile.objects.filter(user_id=user.pk).update(preop_ui_mode=clean_mode)
    return clean_mode


def med_schedule_signature(ev):
    """Stable short fingerprint of planned surgery date + hold/skip medication instructions."""
    s = ev['s']
    parts = [str(s.get('planned_surgery_date') or '')]
    for row in ev['med_schedule']:
        if row.get('hold_or_skip') or row.get('tone') == 'red':
            parts.append(f"{row.get('drug')}|{row.get('date_plan')}|{row.get('tone')}")
    return hashlib.sha1('\n'.join(parts).encode('utf-8')).hexdigest()[:12]


def _extract_hold_start_date(row, op_dt):
    """Infer the calendar date when a hold/skip medication action starts relative to op_dt."""
    if not op_dt or not row.get('hold_or_skip'):
        return None
    plan = str(row.get('date_plan') or '')
    m = _DATE_DM_RE.search(plan)
    if not m:
        return op_dt
    pairs = [(m.group(1), m.group(2)), (m.group(3), m.group(4)), (m.group(5), m.group(6))]
    day_s, mon_s = next(((d, mo) for d, mo in pairs if d and mo), (None, None))
    if not day_s:
        return op_dt
    day_i, mon_i = int(day_s), int(mon_s)
    for year_cand in (op_dt.year, op_dt.year - 1, op_dt.year + 1):
        try:
            cand = datetime.date(year_cand, mon_i, day_i)
        except ValueError:
            continue
        if abs((op_dt - cand).days) <= 30:
            return cand
    return op_dt


def _med_call_status(ev, today):
    """Determine whether nurse must call patient about drug holds today/next 48h."""
    s = ev['s']
    op_dt = _date(s.get('planned_surgery_date'))
    hold_rows = [r for r in ev['med_schedule'] if r.get('hold_or_skip')]
    if not hold_rows:
        return {'needs_call': False, 'urgent_call': False, 'called_valid': False, 'outdated_call': False, 'drugs': []}

    cur_sig = med_schedule_signature(ev)
    called_at = str(s.get('nurse_med_called_at') or '')
    called_sig = str(s.get('nurse_med_called_sig') or '')
    called_valid = bool(called_at and called_sig == cur_sig)
    outdated_call = bool(called_at and called_sig != cur_sig)

    due_drugs = []
    urgent_call = outdated_call
    for row in hold_rows:
        start_dt = _extract_hold_start_date(row, op_dt)
        due_drugs.append(row['drug'])
        if start_dt and (start_dt - today).days <= 2 and op_dt and op_dt >= today:
            urgent_call = True
    if not op_dt and ev['needs_med_hold']:
        urgent_call = False
    return {
        'needs_call': not called_valid,
        'urgent_call': (not called_valid) and (urgent_call or bool(op_dt and 0 <= (op_dt - today).days <= 7)),
        'called_valid': called_valid,
        'outdated_call': outdated_call,
        'drugs': due_drugs[:3],
    }


def self_fill_status_info(ev):
    """4-stage nurse-readable status for patient self-fill questionnaire link."""
    q, s = ev['q'], ev['s']
    if q.get('nurse_verified') or (q.get('filled') and not q.get('self_fill_submitted')):
        return {'code': 'verified', 'label': 'ექთანმა გადაამოწმა ✓', 'tone': 'ok'}
    if q.get('self_fill_submitted'):
        return {'code': 'returned', 'label': 'პაციენტმა შეავსო — გადაამოწმეთ!', 'tone': 'warn'}
    if s.get('self_link_sent_at') and not s.get('self_fill_revoked'):
        return {'code': 'sent', 'label': 'გაგზავნილია — ელოდება პაციენტს', 'tone': 'info'}
    return {'code': 'unsent', 'label': 'ბმული არ გაგზავნილა', 'tone': 'muted'}


def get_nurse_readiness_verdict(patient, ev):
    """Single unmistakable 03:00 night-shift verdict: STOP / მზადაა ✓ / აკლია N პუნქტი."""
    seal_code = ev['clearance_seal']['code']
    open_tasks = [t for t in ev['tasks'] if not t['done']]
    missing_cnt = len(ev['missing_items']) + len(open_tasks)
    if seal_code == 'stop' or ev['has_hard_contraindication'] or ev['dos_hold_danger']:
        reason = (
            ev['dos_warnings'][0] if ev['dos_warnings']
            else (ev['red_flags'][0] if ev['red_flags'] else 'ოპერაციაზე დაშვება შეჩერებულია')
        )
        return {
            'code': 'stop', 'badge': 'STOP — დაბლოკილია',
            'detail': reason, 'missing_cnt': missing_cnt, 'rank': 0,
        }
    if ev['status'] == STATUS_READY:
        sub = 'დადასტურებულია ქირურგთან' if patient.approved_at else 'მზადაა ქირურგის დასტურისთვის'
        return {
            'code': 'ready', 'badge': 'მზადაა ✓',
            'detail': sub, 'missing_cnt': 0, 'rank': 3,
        }
    if ev['status'] == STATUS_ALERT:
        reason = ev['red_flags'][0] if ev['red_flags'] else 'საჭიროებს ექიმის შეფასებას'
        return {
            'code': 'alert', 'badge': 'საჭიროა ექიმის ნახვა',
            'detail': reason, 'missing_cnt': max(1, missing_cnt), 'rank': 1,
        }
    if ev['status'] == STATUS_NO_Q:
        return {
            'code': 'pending', 'badge': 'კითხვარი შესავსებია',
            'detail': 'შეავსეთ კითხვარი ან გაუგზავნეთ ბმული პაციენტს',
            'missing_cnt': max(1, missing_cnt), 'rank': 2,
        }
    top_missing = [m['label'] for m in ev['missing_items'][:2]] or [t['label'] for t in open_tasks[:2]]
    return {
        'code': 'pending',
        'badge': f'აკლია {max(1, missing_cnt)} პუნქტი',
        'detail': ' · '.join(top_missing) if top_missing else 'მომზადება მიმდინარეობს',
        'missing_cnt': max(1, missing_cnt),
        'rank': 2,
    }


def _relative_op_label(op_dt, today):
    if not op_dt:
        return 'თარიღი უცნობია'
    diff = (op_dt - today).days
    if diff == 0:
        return 'ოპერაცია: დღეს'
    if diff == 1:
        return 'ოპერაცია: ხვალ'
    if diff < 0:
        return f'ოპერაცია: {_fmt_ka_date(op_dt, short=True)} (გადაცილებული)'
    return f'ოპერაცია: {_fmt_ka_date(op_dt, short=True)}'


def build_nurse_handover_queues(all_non_archived, ev_by_id, today, q_search=''):
    """Build 09:00 handover action queues + 03:00 instant-answer directory for active patients."""
    q_low = (q_search or '').strip().lower()
    or_urgent, med_calls, self_returned, missing_overdue, directory = [], [], [], [], []

    for p in all_non_archived:
        ev = ev_by_id[p.pk]
        if ev['case_outcome'] != 'scheduled':
            continue
        if q_low and q_low not in (p.patient_name or '').lower() and q_low not in (p.phone or '').lower():
            continue

        op_dt = _date(ev['s'].get('planned_surgery_date'))
        days_to_op = (op_dt - today).days if op_dt else None
        verdict = get_nurse_readiness_verdict(p, ev)
        med_call = _med_call_status(ev, today)
        sf_info = self_fill_status_info(ev)
        has_acute = bool(ev['q'].get('aaa_pain') or ev['q'].get('cea_crescendo') or ev['q'].get('abf_wet_infection'))

        item = {
            'p': p,
            'ev': ev,
            'surgeon_name': person_name(p.surgeon),
            'procedure_ka': PLAIN_SUBTYPE_KA.get(p.op_subtype, PLAIN_PATHWAY_KA.get(ev['pathway'], p.planned_operation_label)),
            'side_ka': PLAIN_SIDE_KA.get(p.side or '', ''),
            'op_date': op_dt,
            'op_label': _relative_op_label(op_dt, today),
            'days_to_op': days_to_op,
            'verdict': verdict,
            'med_call': med_call,
            'self_fill': sf_info,
            'dos_progress': f"{ev['dos_done_count']}/{ev['dos_total_count']}",
            'is_or_soon': bool(days_to_op is not None and 0 <= days_to_op <= 1),
            'search_blob': f"{p.patient_name} {p.phone} {PLAIN_PATHWAY_KA.get(ev['pathway'], '')} {person_name(p.surgeon)}".lower(),
        }
        directory.append(item)

        in_urgent = item['is_or_soon'] or verdict['code'] == 'stop' or has_acute
        if in_urgent:
            or_urgent.append(item)
        if med_call['urgent_call']:
            med_calls.append(item)
        if sf_info['code'] == 'returned':
            self_returned.append(item)
        if not in_urgent and (ev['over_target_days'] or ev['needs_stale_labs'] or verdict['code'] in ('alert', 'pending')):
            missing_overdue.append(item)

    def _sort_key(it):
        d = it['days_to_op'] if it['days_to_op'] is not None else 9999
        return (it['verdict']['rank'], d, it['p'].pk)

    or_urgent.sort(key=_sort_key)
    med_calls.sort(key=_sort_key)
    self_returned.sort(key=_sort_key)
    missing_overdue.sort(key=_sort_key)
    directory.sort(key=lambda it: (it['days_to_op'] if it['days_to_op'] is not None else 9999, it['verdict']['rank'], it['p'].pk))

    return {
        'or_urgent': or_urgent,
        'med_calls': med_calls,
        'self_returned': self_returned,
        'missing_overdue': missing_overdue,
        'directory': directory,
        'total_active': len(directory),
        'ready_count': sum(1 for it in directory if it['verdict']['code'] == 'ready'),
        'stop_count': sum(1 for it in directory if it['verdict']['code'] == 'stop'),
    }
