"""Track B3/B5 plain-Georgian missing-item checklist, phone med-call script, and postponement detector."""
from .constants import STATUS_NO_Q
from .nurse_ui import _med_call_status, med_schedule_signature
from .plain_ka import (
    _CLINICIAN_JARGON_TOKENS,
    plain_drug_name as _plain_drug_name,
    scrub_plain_ka,
)
from .utils import _date, _fmt_ka_date, is_self_filled_q

_FIELD_TO_INPUT_ID = {
    'bp_sys': 'nw-bp-sys', 'bp_dia': 'nw-bp-dia', 'hr': 'nw-hr', 'spo2': 'nw-spo2',
    'weight_kg': 'nw-wt', 'height_cm': 'nw-ht', 'hb': 'nw-hb', 'plt': 'nw-plt',
    'creat': 'nw-creat', 'inr': 'nw-inr', 'k': 'nw-k', 'glucose': 'nw-gl',
    'labs_date': 'nw-labs-date',
}


def _scrub_plain_text(txt, second_person=True, strip_quoted_ft=False):
    return scrub_plain_ka(txt, second_person=second_person, strip_quoted_ft=strip_quoted_ft)


def _append_missing_and_unknown_items(items, ev, has_q_banner):
    seen_s2, seen_fields = set(), set()
    for m in ev['missing_items']:
        if m.get('tab') == 'q' and has_q_banner:
            continue
        fld = m.get('field') or ''
        if fld:
            seen_fields.add(fld)
        if m.get('tab') == 's2':
            seen_s2.add(fld or m.get('label'))
        inp_id = _FIELD_TO_INPUT_ID.get(fld, '')
        mtab = m.get('tab') or ''
        sec = {
            'q': 'nw-sec-q', 'header': 'nw-sec-header',
            's2': 'nw-vitals-card' if inp_id else 'nw-sec-s2',
            's1': 'nw-vitals-card' if inp_id else 'nw-sec-s1',
        }.get(mtab, 'nw-vitals-card')
        items.append({
            'kind': 'missing',
            'label': scrub_plain_ka(f"აკლია: {m['label']}", second_person=False, strip_quoted_ft=True),
            'target_section': sec, 'target_input': inp_id, 'action_label': 'შევსება',
        })

    for u in (ev.get('unknown_state_flags') or []):
        if not u.get('blocking'):
            continue
        ukey = u.get('key') or ''
        if ukey == 'unverified_self_fill' and has_q_banner:
            continue
        ufld = u.get('field') or ''
        if ufld and ufld in seen_fields and ukey not in ('implausible_values', 'renal_drug_no_crcl', 'ambiguous_creat_unit'):
            continue
        ufld_inp = _FIELD_TO_INPUT_ID.get(ufld, '')
        sec_u = {
            'dos': 'nw-sec-dos', 'q': 'nw-sec-q', 's2': 'nw-sec-s2',
            'header': 'nw-sec-header',
            's1': 'nw-vitals-card' if ufld_inp else 'nw-sec-s1',
        }.get(u.get('tab') or '', 'nw-vitals-card')
        items.append({
            'kind': 'missing',
            'label': scrub_plain_ka(f"დასაზუსტებელია: {u['label']}", second_person=False, strip_quoted_ft=True),
            'target_section': sec_u, 'target_input': ufld_inp, 'action_label': 'შესწორება',
        })
    return seen_s2


def build_plain_georgian_missing_items(patient, ev, today):
    """Numbered plain-Georgian checklist of everything missing before surgeon approval."""
    if patient.archived or (ev.get('clearance_seal') or {}).get('code') == 'inactive':
        return []
    items = []
    q = ev['q']
    is_stop = bool(
        ev['has_hard_contraindication']
        or ev['clearance_seal']['code'] == 'stop'
        or ev.get('dos_hold_danger')
        or ev.get('solo_neuraxial_anatomic_mismatch')
    )

    if is_stop:
        if ev.get('solo_neuraxial_anatomic_mismatch'):
            an_plan = (ev.get('s') or {}).get('anesthesia_plan') or ''
            if ev.get('local_plexus_anatomic_mismatch'):
                an_lbl = 'STOP — შეცვალეთ ანესთეზიის გეგმა: არჩეული ადგილობრივი ან წნულის გაუტკივარება ამ ოპერაციისთვის ანატომიურად შეუსაბამოა!'
            elif ev.get('epi_anatomic_mismatch') and an_plan != 'neuraxial':
                an_lbl = 'STOP — მოხსენით ეპიდურული კათეტერის მონიშვნა: ეპიდურული გაუტკივარება ამ ოპერაციისთვის ანატომიურად შეუსაბამოა!'
            else:
                an_lbl = 'STOP — შეცვალეთ ანესთეზიის გეგმა: მარტო ნეირაქსიული ბლოკადა ამ ოპერაციისთვის ანატომიურად შეუსაბამოა!'
            items.append({
                'kind': 'stop',
                'label': an_lbl,
                'target_section': 'nw-sec-s2', 'target_input': '', 'action_label': 'ანესთეზიის შეცვლა',
            })
        for dw in (ev.get('dos_warnings') or ()):
            items.append({
                'kind': 'stop',
                'label': scrub_plain_ka(f'STOP — აცნობეთ ქირურგს: {dw}', second_person=False, strip_quoted_ft=True),
                'target_section': 'nw-sec-dos', 'target_input': '', 'action_label': 'დილის შემოწმება',
            })
        for rf in (ev.get('red_flags') or ()):
            items.append({
                'kind': 'stop',
                'label': scrub_plain_ka(f'STOP — აცნობეთ ქირურგს: {rf}', second_person=False, strip_quoted_ft=True),
                'target_section': 'nw-sec-doc', 'target_input': '', 'action_label': 'ექიმის სექცია',
            })
        if not items:
            items.append({
                'kind': 'stop', 'label': 'STOP — აცნობეთ ქირურგს: ოპერაციაზე დაშვება შეჩერებულია',
                'target_section': 'nw-sec-doc', 'target_input': '', 'action_label': 'ექიმის სექცია',
            })
    elif ev['has_unacked_red']:
        for rf in (ev.get('red_flags') or ()):
            items.append({
                'kind': 'alert',
                'label': scrub_plain_ka(f'ექიმს უნდა ეცნობოს: {rf}', second_person=False, strip_quoted_ft=True),
                'target_section': 'nw-sec-doc', 'target_input': '', 'action_label': 'ქირურგის ნახვა',
            })
    if ev.get('neuraxial_hard_blocked') and (not ev.get('solo_neuraxial_anatomic_mismatch') or ev.get('has_explicit_nx_blockers')):
        items.append({
            'kind': 'alert',
            'label': 'ანესთეზიის გეგმა შესაცვლელია: არჩეული ნეირაქსიული ბლოკადა უკუნაჩვენებია!',
            'target_section': 'nw-sec-s2', 'target_input': '', 'action_label': 'ანესთეზიის შეცვლა',
        })

    has_q_banner = False
    if not q.get('filled') or ev['status'] == STATUS_NO_Q:
        has_q_banner = True
        items.append({
            'kind': 'q', 'label': 'შესავსებია პაციენტის წინასაოპერაციო კითხვარი (ან გაუგზავნეთ ბმული)',
            'target_section': 'nw-sec-q', 'target_input': '', 'action_label': 'კითხვარის გახსნა',
        })
    elif is_self_filled_q(q) and not q.get('nurse_verified'):
        has_q_banner = True
        items.append({
            'kind': 'q', 'label': 'პაციენტმა შეავსო კითხვარი — საჭიროა ექთნის გადამოწმება',
            'target_section': 'nw-sec-q', 'target_input': '', 'action_label': 'გადამოწმება',
        })

    seen_s2 = _append_missing_and_unknown_items(items, ev, has_q_banner)
    for t in ev['tasks']:
        if not t['done'] and t.get('key') not in ('q', 's1') and not seen_s2:
            items.append({
                'kind': 'task',
                'label': scrub_plain_ka(t['label'], second_person=False, strip_quoted_ft=True),
                'target_section': 'nw-sec-s2', 'target_input': '', 'action_label': 'მონიშვნა',
            })

    med_call = _med_call_status(ev, today)
    if med_call['needs_call'] and ev['clearance_seal']['code'] != 'stop':
        lbl = (
            'თარიღი შეიცვალა — დაურეკეთ პაციენტს წამლების ახალი თარიღებით!'
            if med_call['outdated_call']
            else 'დაურეკეთ პაციენტს წამლის შეჩერების ზუსტ თარიღებზე და მონიშნეთ „დავურეკე ✓“'
        )
        items.append({
            'kind': 'med_call', 'label': lbl,
            'target_section': 'nw-med-call-card', 'target_input': '', 'action_label': 'დარეკვის ბარათი',
        })

    return items


def _spoken_med_phrase(row, op_ka):
    """Turn a technical med_schedule row into one clear spoken Georgian sentence for phone calls."""
    plan = (row.get('date_plan') or '').strip()
    has_skip_or_warn = any(k in plan for k in ('გამოტოვეთ', 'არ მიიღოთ', 'შეწყვიტ', 'შეჩერ', 'შეათანხმეთ', 'STOP'))
    if not row.get('hold_or_skip') and row.get('tone') == 'ok' and not has_skip_or_warn:
        return f'არ შეწყვიტოს! მიიღოს ჩვეულებრივად{f" ({op_ka}-ის დილასაც ყლუპი წყლით)" if op_ka else ""}.'
    if plan:
        raw_sp = plan.replace('ბოლო:', 'ბოლო აბი მიიღოს:').replace('არ მიიღოთ', 'აღარ მიიღოს')
    else:
        raw_sp = row.get('rule') or ''
        if any(k in raw_sp for k in _CLINICIAN_JARGON_TOKENS):
            raw_sp = _plain_undated_patient_med({
                'raw_drug': row.get('drug') or '',
                'hold_or_skip': row.get('hold_or_skip'),
                'tone': row.get('tone'),
            })
    return scrub_plain_ka(raw_sp, second_person=False, strip_quoted_ft=True)


def build_phone_med_script(patient, ev, today):
    """Build nurse phone-call medication-hold script with strict stop/caution/continue separation."""
    from django.utils import timezone
    today = _date(today) or timezone.localdate()
    s = ev['s']
    is_inactive = bool(patient.archived) or (ev.get('case_outcome') not in (None, '', 'scheduled'))
    is_rup = bool(ev.get('is_rupture_suspect'))
    op_dt = None if (is_inactive or is_rup) else _date(s.get('planned_surgery_date'))
    op_ka = _fmt_ka_date(op_dt, short=True) if op_dt else ''
    stop_locked = (not is_inactive) and (ev['clearance_seal']['code'] == 'stop' or ev['has_hard_contraindication'])
    med_call = _med_call_status(ev, today)

    stop_rows, caution_rows, keep_rows = [], [], []
    if not is_inactive:
        for row in ev['med_schedule']:
            plain_drug = _plain_drug_name(row['drug'])
            plan = (row.get('date_plan') or '').strip()
            is_glp1_diet = ('GLP-1' in row['drug']) and ('დიეტა' in plan or 'დიეტა' in (row.get('rule') or ''))
            is_non_stop_plan = any(
                kw in plan for kw in ('შეიყვანეთ', 'არ მოხსნათ', 'შეათანხმეთ', 'დააზუსტეთ', 'VTE შეფასება')
            )
            is_reconcile_only = bool(row.get('reconcile_only'))
            is_truly_urgent = bool(row.get('acute_hold') or row.get('contraindicated') or row.get('unstructured'))
            is_contra_or_reconcile = bool(row.get('contraindicated') or row.get('unstructured') or is_reconcile_only)
            is_acute_or_contra = bool(is_truly_urgent or is_reconcile_only)
            is_skip_text = ('გამოტოვეთ' in plan) or ('არ მიიღოთ' in plan)
            is_planned_stop = (
                (bool(row.get('hold_or_skip')) or is_skip_text)
                and not is_contra_or_reconcile
                and not is_glp1_diet
                and not is_non_stop_plan
            )
            is_urgent = is_truly_urgent or bool(row.get('tone') == 'red' and not stop_locked and not is_reconcile_only)
            spoken_txt = (
                'არ შეწყვიტოთ თვითნებურად — ოპერაცია შეჩერებულია (STOP)!'
                if (stop_locked and is_planned_stop and not is_truly_urgent)
                else _spoken_med_phrase(row, op_ka)
            )
            rule_txt = (
                'ოპერაცია შეჩერებულია (STOP) — ექიმის დადასტურებამდე თვითნებურად არ შეწყვიტოთ!'
                if (stop_locked and not is_truly_urgent)
                else scrub_plain_ka(row['rule'], second_person=False, strip_quoted_ft=True)
            )
            entry = {
                'drug': plain_drug,
                'raw_drug': row['drug'],
                'spoken': spoken_txt,
                'rule': rule_txt,
                'tone': row['tone'],
                'date_plan': scrub_plain_ka(plan, second_person=False, strip_quoted_ft=True),
                'raw_date_plan': plan,
                'hold_or_skip': is_planned_stop,
                'urgent_contact': is_urgent,
            }
            if is_planned_stop:
                stop_rows.append(entry)
            elif is_acute_or_contra or is_glp1_diet or is_non_stop_plan or row.get('tone') in ('red', 'amber'):
                caution_rows.append(entry)
            else:
                keep_rows.append(entry)

    return {
        'has_meds': bool(ev['med_schedule']) if is_inactive else bool(stop_rows or caution_rows or keep_rows),
        'stop_rows': stop_rows,
        'caution_rows': caution_rows,
        'urgent_rows': [r for r in (stop_rows + caution_rows) if r['urgent_contact']],
        'keep_rows': keep_rows,
        'stop_locked': stop_locked,
        'inactive_locked': is_inactive,
        'needs_op_date': bool(any(r['hold_or_skip'] for r in stop_rows) and not op_dt and not stop_locked and not is_inactive and not is_rup),
        'op_date_ka': _fmt_ka_date(op_dt) if op_dt else '',
        'called_valid': med_call['called_valid'],
        'outdated_call': med_call['outdated_call'],
        'called_at': s.get('nurse_med_called_at') or '',
        'called_by': s.get('nurse_med_called_by') or '',
        'current_sig': med_schedule_signature(ev),
    }


def _append_postponement_clinical_impacts(impacts, patient, ev, cur_dt, today, cea_win_d, cta_cfg, shift_days=0):
    from .registry import rule_value
    from .utils import cta_live_months_age

    cta_std_m, cta_giant_m, cta_giant_mm = cta_cfg
    s = ev['s']
    med_call = _med_call_status(ev, today)
    contra_drugs = [
        _plain_drug_name(r['drug']) for r in ev['med_schedule']
        if r.get('contraindicated') or r.get('acute_hold') or r.get('unstructured')
    ]
    hold_drugs = [
        _plain_drug_name(r['drug']) for r in ev['med_schedule']
        if not (r.get('contraindicated') or r.get('acute_hold') or r.get('unstructured'))
        and (r.get('hold_or_skip') or (ev.get('is_evla_warfarin_only') and 'ვარფარინ' in (r.get('drug') or '')))
    ]
    stop_locked = (ev['clearance_seal']['code'] == 'stop' or ev.get('has_hard_contraindication'))
    if stop_locked:
        if contra_drugs:
            impacts.append({
                'tone': 'danger',
                'text': f'ოპერაციაზე დაშვება დაბლოკილია (STOP) — გეგმური წამლები არ შეწყვიტოთ, მაგრამ უკუნაჩვენები წამლის გამო ({", ".join(contra_drugs[:3])}) დაუყოვნებლივ დაუკავშირდით ექიმს!',
            })
        else:
            impacts.append({
                'tone': 'danger',
                'text': (
                    'ოპერაციაზე დაშვება დაბლოკილია (STOP) — წამლები არ შეწყვიტოთ და შეჩერების თარიღს ნუ მისცემთ ქირურგის გადაწყვეტილებამდე!'
                    if hold_drugs
                    else 'ოპერაციაზე დაშვება დაბლოკილია (STOP) — თარიღის გადატანის მიუხედავად ოპერაცია ვერ დაიგეგმება ქირურგის გადაწყვეტილებამდე!'
                ),
            })
    else:
        if contra_drugs:
            impacts.append({
                'tone': 'danger',
                'text': f'ყურადღება! ფიქსირდება უკუნაჩვენები ან დაუყოვნებლივ შესათანხმებელი წამალი ({", ".join(contra_drugs[:3])}) — ახალი თარიღის მიუხედავად სასწრაფოდ დაუკავშირდით მკურნალ ექიმს!',
            })
        if hold_drugs:
            if ev.get('has_elapsed_hold_window'):
                impacts.append({'tone': 'danger', 'text': f'ყურადღება! ახალ თარიღზე წამლის შეჩერების სავალდებულო ფანჯარა უკვე ამოწურულია ({", ".join(hold_drugs[:3])}) — დაუყოვნებლივ აცნობეთ ქირურგს!'})
            elif med_call['outdated_call']:
                impacts.append({'tone': 'danger', 'text': f'წამლების შეჩერების თარიღები შეიცვალა ({", ".join(hold_drugs[:3])}) — წინა დარეკვის მონიშვნა გაუქმდა! აუცილებლად დაურეკეთ პაციენტს თავიდან ახალი თარიღებით.'})
            else:
                impacts.append({'tone': 'warn', 'text': f'გადაამოწმეთ და აუხსენით წამლების შეჩერების ახალი თარიღები ({", ".join(hold_drugs[:3])}).'})

    aaa_mm = ev.get('aaa_mm')
    if shift_days > 0 and ev.get('pathway') == 'aaa_infra' and s.get('aaa_morphology') == 'inflammatory_mycotic':
        impacts.append({'tone': 'danger', 'text': 'ინფიცირებული / მიკოზური ანევრიზმა — ოპერაციის გადადება მაღალი რისკია! დაუყოვნებლივ შეათანხმეთ ქირურგთან.'})
    elif shift_days > 0 and ev.get('pathway') == 'aaa_infra' and aaa_mm is not None and aaa_mm >= rule_value('AAA_GIANT_MM') and (cur_dt - today).days > rule_value('AAA_WAIT_TARGET_DAYS')[0]:
        impacts.append({'tone': 'danger', 'text': f'გიგანტური ანევრიზმა ({aaa_mm} მმ) — გადადება ცდება {rule_value("AAA_WAIT_TARGET_DAYS")[0]}-დღიან ვადას (გასკდომის მაღალი რისკი)! დაუყოვნებლივ შეათანხმეთ ქირურგთან.'})
    cea_ev_dt = _date(ev['q'].get('cea_event_date'))
    if ev.get('is_symp_cea'):
        cea_days_at_op = None
        if ev['q'].get('cea_crescendo'):
            cea_days_at_op = (cur_dt - today).days
        elif cea_ev_dt:
            cea_days_at_op = (cur_dt - cea_ev_dt).days
        elif ev.get('days_since_ev') is not None:
            cea_days_at_op = int(ev['days_since_ev']) + (cur_dt - today).days
        if cea_days_at_op is not None and cea_days_at_op > cea_win_d:
            impacts.append({'tone': 'danger', 'text': f'სიმპტომური საძილე არტერიის ოპერაციის {cea_win_d}-დღიანი ფანჯარა ირღვევა (სიმპტომიდან იქნება {cea_days_at_op} დღე) — აცნობეთ ქირურგს!'})
    started_dt = _date(patient.started_on)
    if ev.get('target_days', 14) <= 7 and started_dt and (cur_dt - started_dt).days > ev['target_days']:
        impacts.append({'tone': 'warn', 'text': f'ოპერაციის ახალი თარიღი ცდება {ev["target_days"]}-დღიან სამიზნე მომზადების ვადას — გადაამოწმეთ ქირურგთან.'})

    cta_dt = _date(s.get('cta_date'))
    if cta_dt and cta_dt > cur_dt:
        impacts.append({'tone': 'danger', 'text': 'კტ-ანგიოგრაფიის თარიღი ახალ ოპერაციის თარიღზე გვიანაა — გადაამოწმეთ თარიღები!'})
    elif cta_dt:
        is_giant_aaa = (ev.get('pathway') == 'aaa_infra' and aaa_mm is not None and aaa_mm >= cta_giant_mm)
        max_cta_m = cta_giant_m if is_giant_aaa else cta_std_m
        cta_m = cta_live_months_age({'cta_date': cta_dt.isoformat()}, today=cur_dt)
        if cta_m is not None and cta_m > max_cta_m:
            impacts.append({'tone': 'danger' if is_giant_aaa else 'warn', 'text': f'კტ-ანგიოგრაფია ახალ თარიღზე {max_cta_m} თვეზე ძველი იქნება — დააზუსტეთ ქირურგთან განახლების საჭიროება.'})


def detect_postponement_impacts(patient, ev, today):
    """Compute exact re-check items when planned_surgery_date was changed / postponed."""
    from django.utils import timezone

    from .registry import rule_value
    if bool(patient.archived) or bool(ev.get('is_rupture_suspect')) or (ev.get('case_outcome') not in (None, '', 'scheduled')) or (ev.get('clearance_seal') or {}).get('code') == 'inactive':
        return None
    today = _date(today) or timezone.localdate()
    stale_lab_d = rule_value('INF_STALE_LABS_DAYS')
    cea_win_d = rule_value('CEA_SYMP_WINDOW_DAYS')[0]
    cta_cfg = rule_value('AAA_CTA_STALE_MONTHS')
    s = ev['s']
    prev_dt = _date(s.get('prev_planned_surgery_date'))
    cur_dt = _date(s.get('planned_surgery_date'))
    if not prev_dt or not cur_dt or prev_dt == cur_dt:
        return None
    if s.get('postpone_ack_sig') == f'{prev_dt.isoformat()}->{cur_dt.isoformat()}':
        return None

    shift_days = (cur_dt - prev_dt).days
    impacts = []

    labs_dt = _date(s.get('labs_date'))
    if labs_dt and (cur_dt - labs_dt).days > stale_lab_d:
        age_at_op = (cur_dt - labs_dt).days
        impacts.append({
            'tone': 'danger',
            'text': f'ანალიზებს ახალ თარიღამდე ({_fmt_ka_date(cur_dt)}) გაუვა {stale_lab_d}-დღიანი ვადა (იქნება {age_at_op} დღის) — ანალიზები თავიდან უნდა ჩაბარდეს!',
        })
    elif not labs_dt:
        impacts.append({
            'tone': 'warn',
            'text': f'შეამოწმეთ ანალიზების თარიღი — ახალ ოპერაციის დღემდე არ უნდა იყოს {stale_lab_d} დღეზე ძველი.',
        })

    _append_postponement_clinical_impacts(impacts, patient, ev, cur_dt, today, cea_win_d, cta_cfg, shift_days=shift_days)

    if not impacts:
        impacts.append({
            'tone': 'ok',
            'text': 'ანალიზების ვადა და წამლების სქემა ახალ თარიღზე წესრიგშია — უბრალოდ აცნობეთ პაციენტს ახალი თარიღი.',
        })

    return {
        'prev_ka': _fmt_ka_date(prev_dt),
        'cur_ka': _fmt_ka_date(cur_dt),
        'shift_days': shift_days,
        'sig': f'{prev_dt.isoformat()}->{cur_dt.isoformat()}',
        'impacts': impacts,
    }


def summarize_stage1_clinical_delta(ev_before, ev_after):
    """Describe clinically meaningful recalculations after correcting vitals/labs (e.g. weight or creatinine)."""
    notes = []
    r_before = min([v for v in (ev_before.get('crcl'), ev_before.get('egfr')) if v is not None], default=None)
    r_after = min([v for v in (ev_after.get('crcl'), ev_after.get('egfr')) if v is not None], default=None)
    if r_before is not None and r_after is not None and r_before != r_after:
        notes.append(f'თირკმლის გაფილტვრა გადაითვალა: {r_before} → {r_after} მლ/წთ')
    elif r_before is None and r_after is not None:
        notes.append(f'გამოითვალა თირკმლის გაფილტვრა: {r_after} მლ/წთ')

    if med_schedule_signature(ev_before) != med_schedule_signature(ev_after):
        changed_drugs = []
        before_map = {r['drug']: (r.get('rule'), r.get('date_plan')) for r in ev_before['med_schedule']}
        for r in ev_after['med_schedule']:
            if before_map.get(r['drug']) != (r.get('rule'), r.get('date_plan')):
                changed_drugs.append(_plain_drug_name(r['drug']))
        if changed_drugs:
            notes.append(f'შეიცვალა წამლის შეჩერების სქემა ({", ".join(changed_drugs[:3])}) — გადაამოწმეთ დარეკვის ბარათი!')

    if ev_before['clearance_seal']['code'] != ev_after['clearance_seal']['code']:
        seal_txt = ev_after['clearance_seal'].get('badge') or ev_after['clearance_seal']['code']
        notes.append(f'ოპერაციაზე დაშვების სტატუსი შეიცვალა: {seal_txt}')
    return notes


_PATHWAY_EMERGENCY_SIGNS_KA = {
    'aaa_infra': 'მუცლის ან წელის უეცარი ძლიერი ტკივილი, თავბრუსხვევა ან გონების დაკარგვა.',
    'cea': 'ხელის/ფეხის ან სახის უეცარი დაბუჟება ან სისუსტე, მეტყველების არევა ან ცალი თვალით მხედველობის დაკარგვა.',
    'abf': 'ფეხის უეცარი გაციება, გაფერმკრთალება, დაბუჟება, ძლიერი ტკივილი ან სიცხე >38°C.',
    'pad_bypass': 'ფეხის უეცარი გაციება, ძლიერი ტკივილი, ფერის შეცვლა ან ჭრილობის გაწითლება/სიცხე.',
    'pad_pta': 'ფეხის უეცარი გაციება, ძლიერი ტკივილი ან ჭრილობის გაუარესება/სიცხე.',
    'venous': 'ფეხის უეცარი ძლიერი შეშუპება, გაწითლება/ტკივილი, სუნთქვის გაძნელება ან სისხლდენა ვენიდან.',
}


def _plain_undated_patient_med(item):
    from .registry import rule_value
    raw_d = item.get('raw_drug') or ''
    if 'GLP-1' in raw_d:
        glp_liq = rule_value('MED_GLP1_FASTING_PROTOCOL')['liquid_hours']
        return f'ოპერაციამდე {glp_liq} საათით ადრე დაგჭირდებათ მხოლოდ სუფთა თხევადი დიეტა; ზუსტ დღეს ექიმი შეგატყობინებთ თარიღის დანიშნის შემდეგ.'
    if 'ვარფარინ' in raw_d:
        return 'ოპერაციის თარიღის დანიშვნის შემდეგ ექიმი მოგცემთ წამლის დროებით შეჩერების ზუსტ დღეს და გეტყვით, დაგჭირდებათ თუ არა დროებითი დამცავი ნემსები ან კლინიკაში გადასხმა.'
    if 'DOAC' in raw_d or 'დაბიგატრან' in raw_d or 'რივაროქსაბან' in raw_d or 'ქსარელტო 2.5' in raw_d:
        return 'ოპერაციის თარიღის დანიშვნის შემდეგ ექიმი მოგცემთ წამლის დროებით შეჩერების ზუსტ დღეს თირკმლის ანალიზის მიხედვით — თვითნებურად არ შეწყვიტოთ!'
    if any(k in raw_d for k in ('კლოპიდოგრელ', 'P2Y12', 'ტიკაგრელორ', 'პრასუგრელ', 'ცილოსტაზოლ')):
        return 'ოპერაციის თარიღის დანიშვნის შემდეგ ექიმი მოგცემთ სისხლის გამათხელებელი ამ წამლის დროებით შეჩერების ან ჩანაცვლების ზუსტ დღეს — თვითნებურად არ შეწყვიტოთ!'
    if 'SGLT2' in raw_d:
        sglt_cfg = rule_value('MED_SGLT2_HOLD_DAYS')
        return f"ოპერაციამდე {sglt_cfg['std_days']}–{sglt_cfg['ertugliflozin_days']} დღით ადრე დროებით შესაჩერებელია — ზუსტ დღეს ექიმი შეგატყობინებთ თარიღის დანიშნის შემდეგ."
    if 'მეტფორმინ' in raw_d:
        return 'ოპერაციის დილას დროებით გამოსატოვებელია ან დასაზუსტებელია თირკმლის ანალიზის მიხედვით — ზუსტ წესს ექიმი გეტყვით.'
    if 'ინსულინ' in raw_d:
        return 'ოპერაციის წინა საღამოსა და დილის ინსულინის ზუსტ დოზას ექიმი/ენდოკრინოლოგი შეგითანხმებთ — თვითნებურად სრულად არ შეწყვიტოთ!'
    if 'სტეროიდ' in raw_d or 'პრედნიზოლონ' in raw_d:
        return 'არ შეწყვიტოთ თვითნებურად! ოპერაციის დილას მიიღეთ ჩვეული დოზა; საჭიროებისას ექიმი დაგიმატებთ დამცავ დოზას.'
    if 'სტატინ' in raw_d:
        return 'ამ წამლის მიღების წესი შეათანხმეთ ექიმთან ღვიძლის ანალიზის მიხედვით.'
    if 'ACEi' in raw_d or 'პრილი' in raw_d:
        return 'ოპერაციის დილას წნევის ამ წამლის მიღების ან გამოტოვების წესს ექიმი დაგიზუსტებთ.'
    if 'ჰორმონ' in raw_d:
        return 'ჰორმონული წამლის გამო ოპერაციის წინ თრომბის პრევენციის წესი შეათანხმეთ ექიმთან.'
    if 'პანტოპრაზოლ' in raw_d or 'ომეპრაზოლ' in raw_d or 'PPI' in raw_d:
        return 'კუჭის დამცავი წამალი მიიღეთ ექიმის დანიშნულებით — ომეპრაზოლი საჭიროებისას შეცვალეთ პანტოპრაზოლით ექიმთან შეთანხმებით.'
    if 'ამიოდარონ' in raw_d:
        return 'გულის რიტმის წამალი თვითნებურად არ შეწყვიტოთ — მიიღეთ ექიმის დანიშნულებით და გადაამოწმეთ სისხლის შედედების ანალიზი.'
    if item.get('hold_or_skip'):
        return 'ოპერაციის თარიღის დანიშვნის შემდეგ ექიმი მოგცემთ ამ წამლის დროებით შეჩერების ზუსტ დღეს — თვითნებურად არ შეწყვიტოთ!'
    if item.get('tone') == 'ok':
        return 'არ შეწყვიტოთ! მიიღეთ ჩვეულებრივად ექიმის დანიშნულებით.'
    return 'ამ წამლის მიღების ან დროებით შეჩერების წესი შეათანხმეთ მკურნალ ექიმთან — თვითნებურად არ შეწყვიტოთ!'


def _patient_2nd_person(entries):
    out = []
    for e in entries:
        sp = e.get('spoken') or ''
        if not (e.get('raw_date_plan') or e.get('date_plan') or '').strip() and any(k in sp for k in _CLINICIAN_JARGON_TOKENS):
            sp = _plain_undated_patient_med(e)
        out.append({
            'drug': scrub_plain_ka(e.get('drug') or '', second_person=True, strip_quoted_ft=True),
            'raw_drug': e['raw_drug'],
            'spoken': scrub_plain_ka(sp, second_person=True, strip_quoted_ft=True),
            'tone': e['tone'],
            'hold_or_skip': e['hold_or_skip'],
            'urgent_contact': e['urgent_contact'],
        })
    return out


def build_patient_plain_explanation(patient, ev, today):
    """Build plain-Georgian explanation card for the nurse to walk through with the patient."""
    from django.utils import timezone

    from .meds import _detect_freetext_high_risk_meds
    from .registry import rule_value
    today = _date(today) or timezone.localdate()
    glp_cfg = rule_value('MED_GLP1_FASTING_PROTOCOL')
    q, s, pw = ev['q'], ev['s'], ev['pathway']
    is_inactive = bool(patient.archived) or (ev.get('case_outcome') not in (None, '', 'scheduled'))
    is_rup = bool(ev.get('is_rupture_suspect'))
    op_dt = None if (is_inactive or is_rup) else _date(s.get('planned_surgery_date'))
    med_script = build_phone_med_script(patient, ev, today)
    if patient.archived:
        inactive_banner = 'ეს ბარათი დაარქივებულია — ძველი თარიღები და დაშვება აღარ მოქმედებს. ახალი დანიშნულებისთვის დაუკავშირდით მკურნალ ექიმს.'
    elif is_inactive:
        plain_outcome = scrub_plain_ka(ev.get('case_outcome_label') or 'არააქტიური', second_person=False)
        inactive_banner = f'ოპერაციის სტატუსი შეცვლილია ({plain_outcome}) — დაგეგმილი მომზადების ბარათი აღარ მოქმედებს.'
    else:
        inactive_banner = ''

    if is_inactive:
        first_arrival = 'ეს ბარათი აღარ არის აქტიური — დაგეგმილი ოპერაციის თარიღი გადაამოწმეთ მკურნალ ექიმთან.'
    elif is_rup:
        first_arrival = 'გადაუდებელი ოპერაცია (საეჭვო რუპტურა) — სასწრაფოდ დაუკავშირდით ქირურგს / მიემართეთ გადაუდებელი დახმარების განყოფილებას!'
    elif med_script['stop_locked']:
        first_arrival = 'ოპერაციის თარიღი დროებით შეჩერებულია — ზუსტ თარიღს მკურნალი ექიმი შეგატყობინებთ.'
    elif op_dt:
        first_arrival = f'დაგეგმილი ოპერაციის თარიღი: {_fmt_ka_date(op_dt)}'
    else:
        first_arrival = 'ოპერაციის ზუსტ თარიღს მკურნალი ექიმი შეგატყობინებთ.'
    arrival_items = [
        first_arrival,
        'თან იქონიეთ პირადობის მოწმობა და ბოლო ანალიზების ფურცლები.',
    ]
    if pw in ('aaa_infra', 'cea', 'abf', 'pad_bypass'):
        arrival_items.append('აუცილებლად წამოიღეთ კომპიუტერული ტომოგრაფიის დისკი და დასკვნა.')
    if pw == 'venous' and not ev.get('compression_contra'):
        arrival_items.append('თან იქონიეთ ექიმის შერჩეული კომპრესიული წინდა (თუ დანიშნულია).')
    if q.get('pulm') or bool(q.get('pulm_chips')):
        arrival_items.append('წამოიღეთ თქვენი ყოველდღიური ინჰალატორი ან ძილის სასუნთქი აპარატი.')

    anesth_plan = s.get('anesthesia_plan') or 'auto'
    lido_allergy = bool(q.get('ven_lidocaine_allergy')) or ('lidocaine' in (ev.get('eff_allergy_chips') or ()))
    has_glp1 = bool(q.get('glp1') or _detect_freetext_high_risk_meds(q.get('other_meds') or '', q=q)['has_glp1'])
    is_local_evla = (
        patient.op_subtype == 'evla'
        and not ev.get('has_neuraxial_plan')
        and anesth_plan in ('auto', 'local')
        and not lido_allergy
        and not med_script['stop_locked']
    )
    if is_inactive:
        fasting_plain = 'ბარათი არააქტიურია — უზმოდ ყოფნის რეჟიმი დააზუსტეთ მკურნალ ექიმთან.'
    elif is_rup:
        fasting_plain = f"გადაუდებელი ოპერაცია (საეჭვო რუპტურა) — უზმოდ ყოფნის ან {glp_cfg['liquid_hours']}-საათიანი თხევადი დიეტის გამო ოპერაცია არ გადაიდება! ნურაფერს მიირთმევთ და სასწრაფოდ მიემართეთ საოპერაციოს / ქირურგს."
    elif med_script['stop_locked']:
        if has_glp1:
            fasting_plain = f"ოპერაცია შეჩერებულია დაზუსტებამდე — {glp_cfg['liquid_hours']}-საათიანი დიეტა ან უზმოდ რეჟიმი არ დაიწყოთ ექიმის დადასტურებამდე!"
        else:
            fasting_plain = 'ოპერაცია შეჩერებულია დაზუსტებამდე — უზმოდ ყოფნის რეჟიმი არ დაიწყოთ ექიმის დადასტურებამდე!'
    elif has_glp1:
        fasting_plain = (
            f"ოპერაციამდე {glp_cfg['liquid_hours']} საათით ადრე გადადით მხოლოდ სუფთა თხევად დიეტაზე "
            '(წყალი, ჩაი რძის გარეშე, გამჭვირვალე ბულიონი). '
            f"მყარი საკვები შეწყვიტეთ მინიმუმ {glp_cfg['solid_hours']} საათით ადრე, "
            f"{glp_cfg['water_hours']} საათით ადრე — სუფთა წყალიც შეწყვიტეთ."
        )
    elif is_local_evla:
        fasting_plain = 'ადგილობრივი გაუტკივარებით ლაზერული პროცედურის დილას მსუბუქი საუზმე დაშვებულია (თუ ექიმმა უზმოზე მოსვლა არ გთხოვათ).'
    else:
        fasting_plain = (
            'ოპერაციამდე 6 საათით ადრე შეწყვიტეთ ნებისმიერი საკვები და რძიანი სასმელი. '
            'სუფთა წყლის დალევა შეგიძლიათ ოპერაციამდე 2 საათით ადრე; დილის დაშვებულ წამლებს დააყოლეთ მხოლოდ ერთი ყლუპი წყალი.'
        )

    seal_code = ev['clearance_seal']['code']
    is_draft = (
        is_inactive
        or not bool(patient.approved_at)
        or seal_code in ('stop', 'pending', 'conditional', 'inactive')
        or not bool(ev.get('workup_ready'))
        or bool(ev.get('has_blocking_unknown_state'))
    )
    return {
        'is_draft': is_draft,
        'inactive_banner': inactive_banner,
        'arrival_items': arrival_items,
        'fasting_plain': fasting_plain,
        'stop_meds': _patient_2nd_person(med_script['stop_rows']),
        'caution_meds': _patient_2nd_person(med_script['caution_rows']),
        'urgent_meds': _patient_2nd_person(med_script['urgent_rows']),
        'keep_meds': _patient_2nd_person(med_script['keep_rows']),
        'stop_locked': med_script['stop_locked'],
        'emergency_sign': _PATHWAY_EMERGENCY_SIGNS_KA.get(pw, 'უეცარი ძლიერი ტკივილი, სიცხე ან სუნთქვის გაძნელება — დაუყოვნებლივ დარეკეთ კლინიკაში.'),
    }



