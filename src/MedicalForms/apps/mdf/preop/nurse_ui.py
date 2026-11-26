"""Nurse Workspace presentation helpers, plain-Georgian labels, and 09:00/03:00 triage queues."""
import datetime
import hashlib
import re

from ..board import person_name
from ..models import StaffProfile
from .constants import STATUS_ALERT, STATUS_NO_Q, STATUS_READY
from .utils import _date, _fmt_ka_date, is_self_filled_q

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
    """Stable short fingerprint of planned surgery date + full medication instructions & safety flags."""
    s = ev['s']
    parts = [str(s.get('planned_surgery_date') or '')]
    for row in ev['med_schedule']:
        parts.append(
            f"{row.get('drug')}|{row.get('date_plan')}|{row.get('tone')}|{row.get('rule')}"
            f"|{bool(row.get('contraindicated'))}|{bool(row.get('acute_hold'))}|{bool(row.get('hold_or_skip'))}"
        )
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
    from django.utils import timezone
    today = _date(today) or timezone.localdate()
    if ev['clearance_seal']['code'] == 'stop' or ev.get('has_hard_contraindication'):
        return {'needs_call': False, 'urgent_call': False, 'called_valid': False, 'outdated_call': False, 'drugs': []}
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
    if q.get('nurse_verified') or (q.get('filled') and not is_self_filled_q(q)):
        return {'code': 'verified', 'label': 'ექთანმა გადაამოწმა ✓', 'tone': 'ok'}
    if is_self_filled_q(q):
        return {'code': 'returned', 'label': 'პაციენტმა შეავსო — გადაამოწმეთ!', 'tone': 'warn'}
    if s.get('self_link_sent_at') and not s.get('self_fill_revoked'):
        return {'code': 'sent', 'label': 'გაგზავნილია — ელოდება პაციენტს', 'tone': 'info'}
    return {'code': 'unsent', 'label': 'ბმული არ გაგზავნილა', 'tone': 'muted'}


def get_nurse_readiness_verdict(patient, ev):
    """Single unmistakable 03:00 night-shift verdict: STOP / ექიმის ნახვა / მზადაა ✓ / აკლია N პუნქტი."""
    seal_code = ev['clearance_seal']['code']
    open_tasks = [t for t in ev['tasks'] if not t['done']]
    missing_cnt = max(len(ev['missing_items']), len(open_tasks))
    if seal_code == 'stop' or ev['has_hard_contraindication'] or ev['dos_hold_danger']:
        reason = (
            ev['dos_warnings'][0] if ev['dos_warnings']
            else (ev['red_flags'][0] if ev['red_flags'] else 'ოპერაციაზე დაშვება შეჩერებულია')
        )
        return {
            'code': 'stop', 'badge': 'STOP — დაბლოკილია',
            'detail': reason, 'missing_cnt': missing_cnt, 'rank': 0,
        }
    if ev['status'] == STATUS_ALERT or ev.get('has_unacked_red'):
        reason = ev['red_flags'][0] if ev['red_flags'] else 'საჭიროებს ექიმის შეფასებას'
        return {
            'code': 'alert', 'badge': 'საჭიროა ექიმის ნახვა',
            'detail': reason, 'missing_cnt': max(1, missing_cnt), 'rank': 1,
        }
    if ev['status'] == STATUS_NO_Q and not ev.get('has_blocking_unknown_state'):
        return {
            'code': 'pending', 'badge': 'კითხვარი შესავსებია',
            'detail': 'შეავსეთ კითხვარი ან გაუგზავნეთ ბმული პაციენტს',
            'missing_cnt': max(1, missing_cnt), 'rank': 2,
        }
    if ev.get('has_blocking_unknown_state') or ev.get('neuraxial_hard_blocked') or ev.get('needs_stale_labs') or seal_code == 'pending':
        miss_fields = {m.get('field') for m in ev['missing_items']}
        blk_all = [u for u in (ev.get('unknown_state_flags') or []) if u.get('blocking')]
        extra_blk = [u for u in blk_all if u.get('field') not in miss_fields]
        detail = ' · '.join(u['label'] for u in blk_all[:2]) if blk_all else ev['clearance_seal'].get('detail', 'შეფასება არასრულია')
        total_cnt = max(1, missing_cnt + len(extra_blk))
        return {
            'code': 'pending',
            'badge': f'აკლია {total_cnt} პუნქტი',
            'detail': detail,
            'missing_cnt': total_cnt,
            'rank': 2,
        }
    if ev['status'] == STATUS_READY:
        sub = 'დადასტურებულია ქირურგთან' if patient.approved_at else 'მზადაა ქირურგის დასტურისთვის'
        return {
            'code': 'ready', 'badge': 'მზადაა ✓',
            'detail': sub, 'missing_cnt': 0, 'rank': 3,
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
    from django.utils import timezone
    today = _date(today) or timezone.localdate()
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
    from django.utils import timezone
    today = _date(today) or timezone.localdate()
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
        has_acute = bool(
            ev['q'].get('aaa_pain')
            or ev['q'].get('cea_crescendo')
            or ev['q'].get('abf_wet_infection')
            or (ev['pathway'] in ('abf', 'pad_bypass', 'pad_pta') and ev['q'].get('abf_duration') == '< 2 კვირა')
        )

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
        if med_call['urgent_call'] and verdict['code'] != 'stop':
            med_calls.append(item)
        if sf_info['code'] == 'returned':
            self_returned.append(item)
        if not in_urgent and (ev['over_target_days'] or ev['needs_stale_labs'] or verdict['code'] in ('alert', 'pending')):
            missing_overdue.append(item)

    def _sort_key(it):
        d = it['days_to_op'] if it['days_to_op'] is not None else 9999
        return (it['verdict']['rank'], d, it['p'].pk or 0)

    or_urgent.sort(key=_sort_key)
    med_calls.sort(key=_sort_key)
    self_returned.sort(key=_sort_key)
    missing_overdue.sort(key=_sort_key)
    directory.sort(key=lambda it: (it['days_to_op'] if it['days_to_op'] is not None else 9999, it['verdict']['rank'], it['p'].pk or 0))

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


REGISTER_PROCEDURE_GROUPS = [
    ('საძილე არტერია (CEA)', [
        ('cea_symp', 'საძილე არტერია — სიმპტომური', 'ინსულტი ან გარდამავალი ეპიზოდი · 14 დღე'),
        ('cea_asymp', 'საძილე არტერია — უსიმპტომო', 'გეგმიური შევიწროება'),
    ]),
    ('მუცლის აორტა (AAA)', [
        ('open', 'აორტის ანევრიზმა — ღია ოპერაცია', 'მუცლის აორტის პროთეზირება'),
        ('evar', 'აორტის ანევრიზმა — სტენტი (EVAR)', 'ენდოვასკულური პროთეზი'),
        ('tbd', 'აორტის ანევრიზმა — ტიპი ზუსტდება', 'კტ-ანგიოგრაფიის შემდეგ'),
    ]),
    ('ფეხის არტერიები (შუნტირება / ბალონირება)', [
        ('bypass_clti', 'ფეხის შუნტირება — ტკივილი/წყლული', 'კრიტიკული იშემია (7 დღე)'),
        ('bypass_claud', 'ფეხის შუნტირება — სიარულის ტკივილი', 'გეგმიური (14 დღე)'),
        ('pta_clti', 'ბალონირება / სტენტი — ტკივილი/წყლული', 'ენდოვასკულური (7 დღე)'),
        ('pta_claud', 'ბალონირება / სტენტი — სიარულის ტკივილი', 'ენდოვასკულური (14 დღე)'),
        ('abf_clti', 'აორტო-ბიფემორალური შუნტი — ტკივილი/წყლული', 'კრიტიკული იშემია (7 დღე)'),
        ('abf_claud', 'აორტო-ბიფემორალური შუნტი — სიარულის ტკივილი', 'გეგმიური (14 დღე)'),
    ]),
    ('ვენების ოპერაცია', [
        ('evla', 'ვენების ლაზერული ოპერაცია (EVLA)', 'ადგილობრივი გაუტკივარება'),
        ('phlebectomy', 'ვარიკოზული ვენების ამოკვეთა', 'სპინალური ან ადგილობრივი'),
    ]),
]


def plain_risk_badges(ev):
    """Translate technical scores (RCRI, ARISCAT, eGFR/CrCl, ASA) into plain Georgian badges."""
    badges = []
    rcri_sc = ev['rcri']['score']
    cardio_lbl = 'დაბალი' if rcri_sc <= 1 else ('საშუალო' if rcri_sc == 2 else 'მაღალი')
    cardio_tone = 'ok' if rcri_sc <= 1 else ('warn' if rcri_sc == 2 else 'danger')
    badges.append({'label': 'გულის რისკი', 'value': cardio_lbl, 'tone': cardio_tone})

    ari_sc = ev['ariscat']['score']
    pulm_lbl = 'დაბალი' if ari_sc < 26 else ('საშუალო' if ari_sc < 45 else 'მაღალი')
    pulm_tone = 'ok' if ari_sc < 26 else ('warn' if ari_sc < 45 else 'danger')
    badges.append({'label': 'ფილტვების რისკი', 'value': pulm_lbl, 'tone': pulm_tone})

    renal_vals = [v for v in (ev.get('crcl'), ev.get('egfr')) if v is not None]
    if ev['q'].get('on_dialysis'):
        badges.append({'label': 'თირკმლის ფუნქცია', 'value': 'დიალიზი', 'tone': 'danger'})
    elif renal_vals:
        r_min = min(renal_vals)
        r_tone = 'danger' if r_min < 30 else ('warn' if r_min < 60 else 'ok')
        badges.append({'label': 'თირკმლის გაფილტვრა', 'value': f'{r_min} მლ/წთ', 'tone': r_tone})
    else:
        badges.append({'label': 'თირკმლის გაფილტვრა', 'value': 'კრეატინინი აკლია', 'tone': 'muted'})

    asa = ev['effective_asa']
    asa_tone = 'danger' if 'IV' in asa or '-E' in asa else ('warn' if 'III' in asa else 'ok')
    badges.append({'label': 'ზოგადი სიმძიმე', 'value': asa, 'tone': asa_tone})
    return badges


def build_nurse_patient_context(request, patient, ev):
    """Build Nurse Workspace context for patient_new.html (plain Georgian, zero jargon)."""
    from urllib.parse import quote

    from django.utils import timezone

    today = timezone.localdate()
    verdict = get_nurse_readiness_verdict(patient, ev)
    sf_info = self_fill_status_info(ev)
    op_dt = _date(ev['s'].get('planned_surgery_date'))
    self_path = request.build_absolute_uri(ev.get('self_fill_path') or '') if ev.get('self_fill_path') else ''

    phone_digits = re.sub(r'[^0-9]', '', patient.phone or '')
    if len(phone_digits) == 9 and phone_digits.startswith('5'):
        wa_phone = '995' + phone_digits
    else:
        wa_phone = phone_digits

    share_text = (
        f'გამარჯობა, {patient.patient_name}. გთხოვთ შეავსოთ წინასაოპერაციო კითხვარი ამ ბმულზე: {self_path}'
        if self_path else ''
    )
    wa_url = f'https://wa.me/{wa_phone}?text={quote(share_text)}' if (wa_phone and self_path) else f'https://wa.me/?text={quote(share_text)}'
    sms_url = f'sms:{patient.phone}?body={quote(share_text)}' if (patient.phone and self_path) else f'sms:?body={quote(share_text)}'

    from .nurse_plan import (
        build_phone_med_script,
        build_plain_georgian_missing_items,
        detect_postponement_impacts,
    )

    return {
        'nw_verdict': verdict,
        'nw_procedure_ka': PLAIN_SUBTYPE_KA.get(patient.op_subtype, PLAIN_PATHWAY_KA.get(ev['pathway'], patient.planned_operation_label)),
        'nw_side_ka': PLAIN_SIDE_KA.get(patient.side or '', ''),
        'nw_op_label': _relative_op_label(op_dt, today),
        'nw_risk_badges': plain_risk_badges(ev),
        'nw_self_fill': sf_info,
        'nw_self_fill_abs': self_path,
        'nw_wa_url': wa_url,
        'nw_sms_url': sms_url,
        'nw_med_call': _med_call_status(ev, today),
        'nw_missing_list': build_plain_georgian_missing_items(patient, ev, today),
        'nw_med_script': build_phone_med_script(patient, ev, today),
        'nw_postponement': detect_postponement_impacts(patient, ev, today),
    }

