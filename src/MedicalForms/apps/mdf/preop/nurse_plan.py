"""Track B3/B5 plain-Georgian missing-item checklist, phone med-call script, and postponement detector."""
from .constants import STATUS_NO_Q
from .nurse_ui import _med_call_status, med_schedule_signature
from .utils import _date, _fmt_ka_date, is_self_filled_q

_DRUG_PLAIN_PREFIX = {
    'SGLT2 (ჯარდიანსი / ფორსიგა)': 'შაქრის წამალი — ჯარდიანსი / ფორსიგა (SGLT2)',
    'DOAC (ქსარელტო / ელიქვისი)': 'სისხლის გამათხელებელი — ქსარელტო / ელიქვისი (DOAC)',
    'ქსარელტო 2.5 მგ x2': 'სისხლის გამათხელებელი — რივაროქსაბანი (დაბალი დოზა)',
    'დაბიგატრანი (პრადაქსა)': 'სისხლის გამათხელებელი — პრადაქსა (დაბიგატრანი)',
    'ვარფარინი': 'სისხლის გამათხელებელი — ვარფარინი',
    'კლოპიდოგრელი / P2Y12': 'თრომბის საწინააღმდეგო — კლოპიდოგრელი (პლავიქსი)',
    'ასპირინი (75–100 მგ)': 'ასპირინი (კარდიომაგნილი / თრომბო-ასი)',
    'GLP-1 (ოზემპიკი / მუნჯარო / საქსენდა)': 'წონის/შაქრის ნემსი — ოზემპიკი / მუნჯარო (GLP-1)',
    'ACEi / ARB (პრილი/სარტანი)': 'წნევის წამალი (პრილი / სარტანი)',
}

_FIELD_TO_INPUT_ID = {
    'bp_sys': 'nw-bp-sys', 'bp_dia': 'nw-bp-dia', 'hr': 'nw-hr', 'spo2': 'nw-spo2',
    'weight_kg': 'nw-wt', 'height_cm': 'nw-ht', 'hb': 'nw-hb', 'plt': 'nw-plt',
    'creat': 'nw-creat', 'inr': 'nw-inr', 'k': 'nw-k', 'glucose': 'nw-gl',
    'labs_date': 'nw-labs-date',
}


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
            'kind': 'missing', 'label': f"აკლია: {m['label']}",
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
            'kind': 'missing', 'label': f"დასაზუსტებელია: {u['label']}",
            'target_section': sec_u, 'target_input': ufld_inp, 'action_label': 'შესწორება',
        })
    return seen_s2


def build_plain_georgian_missing_items(patient, ev, today):
    """Numbered plain-Georgian checklist of everything missing before surgeon approval."""
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
            items.append({
                'kind': 'stop',
                'label': 'STOP — შეცვალეთ ანესთეზიის გეგმა: მარტო ნეირაქსიული ბლოკადა ამ ოპერაციისთვის ანატომიურად შეუსაბამოა!',
                'target_section': 'nw-sec-s2', 'target_input': '', 'action_label': 'ანესთეზიის შეცვლა',
            })
        for dw in (ev.get('dos_warnings') or ()):
            items.append({
                'kind': 'stop', 'label': f'STOP — აცნობეთ ქირურგს: {dw}',
                'target_section': 'nw-sec-dos', 'target_input': '', 'action_label': 'დილის შემოწმება',
            })
        for rf in (ev.get('red_flags') or ()):
            items.append({
                'kind': 'stop', 'label': f'STOP — აცნობეთ ქირურგს: {rf}',
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
                'kind': 'alert', 'label': f'ექიმს უნდა ეცნობოს: {rf}',
                'target_section': 'nw-sec-doc', 'target_input': '', 'action_label': 'ქირურგის ნახვა',
            })
    if ev.get('neuraxial_hard_blocked') and (not ev.get('solo_neuraxial_anatomic_mismatch') or ev.get('has_explicit_nx_blockers')):
        items.append({
            'kind': 'alert',
            'label': 'ანესთეზიის გეგმა შესაცვლელია: არჩეული ნეირაქსიული ბლოკადა უკუნაჩვენებია (ASRA 5th Ed)!',
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
                'kind': 'task', 'label': t['label'],
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
        return plan.replace('ბოლო:', 'ბოლო აბი მიიღოს:').replace('არ მიიღოთ', 'აღარ მიიღოს')
    return row.get('rule') or ''


def build_phone_med_script(patient, ev, today):
    """Build nurse phone-call medication-hold script with strict stop/caution/continue separation."""
    from django.utils import timezone
    today = _date(today) or timezone.localdate()
    s = ev['s']
    op_dt = _date(s.get('planned_surgery_date'))
    op_ka = _fmt_ka_date(op_dt, short=True) if op_dt else ''
    stop_locked = (ev['clearance_seal']['code'] == 'stop' or ev['has_hard_contraindication'])
    med_call = _med_call_status(ev, today)

    stop_rows, caution_rows, keep_rows = [], [], []
    for row in ev['med_schedule']:
        plain_drug = _DRUG_PLAIN_PREFIX.get(row['drug'], row['drug'])
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
        entry = {
            'drug': plain_drug,
            'raw_drug': row['drug'],
            'spoken': spoken_txt,
            'rule': row['rule'],
            'tone': row['tone'],
            'date_plan': plan,
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
        'has_meds': bool(stop_rows or caution_rows or keep_rows),
        'stop_rows': stop_rows,
        'caution_rows': caution_rows,
        'urgent_rows': [r for r in (stop_rows + caution_rows) if r['urgent_contact']],
        'keep_rows': keep_rows,
        'stop_locked': stop_locked,
        'needs_op_date': bool(any(r['hold_or_skip'] for r in stop_rows) and not op_dt and not stop_locked),
        'op_date_ka': _fmt_ka_date(op_dt) if op_dt else '',
        'called_valid': med_call['called_valid'],
        'outdated_call': med_call['outdated_call'],
        'called_at': s.get('nurse_med_called_at') or '',
        'called_by': s.get('nurse_med_called_by') or '',
        'current_sig': med_schedule_signature(ev),
    }


def _append_postponement_clinical_impacts(impacts, patient, ev, cur_dt, today, cea_win_d, cta_cfg):
    from .utils import cta_live_months_age

    cta_std_m, cta_giant_m, cta_giant_mm = cta_cfg
    s = ev['s']
    med_call = _med_call_status(ev, today)
    hold_drugs = [
        r['drug'] for r in ev['med_schedule']
        if r.get('hold_or_skip') or (ev.get('is_evla_warfarin_only') and 'ვარფარინ' in (r.get('drug') or ''))
    ]
    stop_locked = (ev['clearance_seal']['code'] == 'stop' or ev.get('has_hard_contraindication'))
    if hold_drugs and stop_locked:
        impacts.append({
            'tone': 'danger',
            'text': 'ოპერაციაზე დაშვება დაბლოკილია (STOP) — წამლები არ შეწყვიტოთ და შეჩერების თარიღს ნუ მისცემთ ქირურგის გადაწყვეტილებამდე!',
        })
    elif hold_drugs:
        if ev.get('has_elapsed_hold_window'):
            impacts.append({
                'tone': 'danger',
                'text': f'ყურადღება! ახალ თარიღზე წამლის შეჩერების სავალდებულო ფანჯარა უკვე ამოწურულია ({", ".join(hold_drugs[:3])}) — დაუყოვნებლივ აცნობეთ ქირურგს!',
            })
        elif med_call['outdated_call']:
            impacts.append({
                'tone': 'danger',
                'text': f'წამლების შეჩერების თარიღები შეიცვალა ({", ".join(hold_drugs[:3])}) — წინა დარეკვის მონიშვნა გაუქმდა! აუცილებლად დაურეკეთ პაციენტს თავიდან ახალი თარიღებით.',
            })
        else:
            impacts.append({
                'tone': 'warn',
                'text': f'გადაამოწმეთ და აუხსენით წამლების შეჩერების ახალი თარიღები ({", ".join(hold_drugs[:3])}).',
            })

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
            impacts.append({
                'tone': 'danger',
                'text': f'სიმპტომური საძილე არტერიის (CEA) {cea_win_d}-დღიანი ფანჯარა ირღვევა (სიმპტომიდან იქნება {cea_days_at_op} დღე) — აცნობეთ ქირურგს!',
            })
    started_dt = _date(patient.started_on)
    if ev.get('target_days', 14) <= 7 and started_dt and (cur_dt - started_dt).days > ev['target_days']:
        impacts.append({
            'tone': 'warn',
            'text': f'ოპერაციის ახალი თარიღი ცდება {ev["target_days"]}-დღიან სამიზნე მომზადების ვადას — გადაამოწმეთ ქირურგთან.',
        })

    cta_dt = _date(s.get('cta_date'))
    if cta_dt and cta_dt > cur_dt:
        impacts.append({
            'tone': 'danger',
            'text': 'კტ-ანგიოგრაფიის (CTA) თარიღი ახალ ოპერაციის თარიღზე გვიანაა — გადაამოწმეთ თარიღები!',
        })
    elif cta_dt:
        aaa_mm = ev.get('aaa_mm')
        is_giant_aaa = (ev.get('pathway') == 'aaa_infra' and aaa_mm is not None and aaa_mm >= cta_giant_mm)
        max_cta_m = cta_giant_m if is_giant_aaa else cta_std_m
        cta_m = cta_live_months_age({'cta_date': cta_dt.isoformat()}, today=cur_dt)
        if cta_m is not None and cta_m > max_cta_m:
            impacts.append({
                'tone': 'danger' if is_giant_aaa else 'warn',
                'text': f'კტ-ანგიოგრაფია (CTA) ახალ თარიღზე {max_cta_m} თვეზე ძველი იქნება — დააზუსტეთ ქირურგთან განახლების საჭიროება.',
            })


def detect_postponement_impacts(patient, ev, today):
    """Compute exact re-check items when planned_surgery_date was changed / postponed."""
    from django.utils import timezone

    from .registry import rule_value
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
    elif not labs_dt and shift_days > 0:
        impacts.append({
            'tone': 'warn',
            'text': f'შეამოწმეთ ანალიზების თარიღი — ახალ ოპერაციის დღემდე არ უნდა იყოს {stale_lab_d} დღეზე ძველი.',
        })

    _append_postponement_clinical_impacts(impacts, patient, ev, cur_dt, today, cea_win_d, cta_cfg)

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
                changed_drugs.append(r['drug'])
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
    raw_d = item.get('raw_drug') or ''
    if 'GLP-1' in raw_d:
        return 'ოპერაციამდე 24 საათით ადრე დაგჭირდებათ მხოლოდ სუფთა თხევადი დიეტა; ზუსტ დღეს ექიმი შეგატყობინებთ თარიღის დანიშნის შემდეგ.'
    if 'ვარფარინ' in raw_d:
        return 'ოპერაციის თარიღის დანიშვნის შემდეგ ექიმი მოგცემთ წამლის დროებით შეჩერების ზუსტ დღეს და გეტყვით, დაგჭირდებათ თუ არა დროებითი დამცავი ნემსები ან კლინიკაში გადასხმა.'
    if 'DOAC' in raw_d or 'დაბიგატრან' in raw_d or 'რივაროქსაბან' in raw_d or 'ქსარელტო 2.5' in raw_d:
        return 'ოპერაციის თარიღის დანიშვნის შემდეგ ექიმი მოგცემთ წამლის დროებით შეჩერების ზუსტ დღეს თირკმლის ანალიზის მიხედვით — თვითნებურად არ შეწყვიტოთ!'
    if any(k in raw_d for k in ('კლოპიდოგრელ', 'P2Y12', 'ტიკაგრელორ', 'პრასუგრელ', 'ცილოსტაზოლ')):
        return 'ოპერაციის თარიღის დანიშვნის შემდეგ ექიმი მოგცემთ სისხლის გამათხელებელი ამ წამლის დროებით შეჩერების ან ჩანაცვლების ზუსტ დღეს — თვითნებურად არ შეწყვიტოთ!'
    if 'SGLT2' in raw_d:
        return 'ოპერაციამდე 3–4 დღით ადრე დროებით შესაჩერებელია — ზუსტ დღეს ექიმი შეგატყობინებთ თარიღის დანიშნის შემდეგ.'
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
    if item.get('hold_or_skip'):
        return 'ოპერაციის თარიღის დანიშვნის შემდეგ ექიმი მოგცემთ ამ წამლის დროებით შეჩერების ზუსტ დღეს — თვითნებურად არ შეწყვიტოთ!'
    if item.get('tone') == 'ok':
        return 'არ შეწყვიტოთ! მიიღეთ ჩვეულებრივად ექიმის დანიშნულებით.'
    return 'ამ წამლის მიღების ან დროებით შეჩერების წესი შეათანხმეთ მკურნალ ექიმთან — თვითნებურად არ შეწყვიტოთ!'


_CLINICIAN_JARGON_TOKENS = (
    'EHRA', 'PAUSE', 'CrCl', 'eGFR', 'ESUR', 'FDA', 'Assoc.', 'Multi-Society', 'ESAIC',
    'ESVS', 'ESC', 'ASRA', 'D+1', 'LMWH', 'VKA', 'UFH', 'HIT', 'DKA', 'NPH', 'POISE-3',
    'HFpEF', 'HFrEF', 'ULN', 'EVLA', 'DOAC', 'SGLT2', 'GLP-1', 'VTE შეფასება',
    'ჰიდროკორტიზონ', 'ეტომიდატ', 'უანტიაგრეგანტოდ', 'სავალდებულოა ჩანაცვლება',
    'CYP2C9', '≥3x', 'DAPT', 'დე-ესკალაცი', 'მექ. სარქვ',
)
_PLAIN_TERM_REPLACEMENTS = (
    ('შეიყვანეთ კრეატინინი თარიღისთვის', 'ექიმი დაგიზუსტებთ შეჩერების ზუსტ დღეს თირკმლის ანალიზის მიხედვით — თვითნებურად არ შეწყვიტოთ'),
    ('შეიყვანეთ CrCl თარიღისთვის', 'ექიმი დაგიზუსტებთ შეჩერების ზუსტ დღეს თირკმლის ანალიზის მიხედვით — თვითნებურად არ შეწყვიტოთ'),
    ('შეიყვანეთ კრეატინინი რეჟიმისთვის', 'ექიმი დაგიზუსტებთ მიღების წესს თირკმლის ანალიზის მიხედვით — თვითნებურად არ შეწყვიტოთ'),
    ('ერთეული დასაზუსტებელია', 'ექიმი გადაამოწმებს თირკმლის ანალიზს და გეტყვით ზუსტ დღეს — თვითნებურად არ შეწყვიტოთ'),
    ('სავალდებულოა სტაციონარული IV ხიდი (ბივალირუდინი/არგატრობანი — HIT: LMWH/ჰეპარინი არა!)', 'სავალდებულოა კლინიკაში წინასწარ წოლა და ვენაში დამცავი წამალი ექიმის მეთვალყურეობით (თვითნებურად არ შეწყვიტოთ!)'),
    ('მაღალი თრომბორისკი + HIT: არაჰეპარინული ხიდი ჰემატოლოგთან (LMWH/ჰეპარინი არა!)', 'თრომბის მაღალი რისკის გამო საჭიროა ექიმის შერჩეული სპეციალური დამცავი წამალი (თვითნებურად არ შეწყვიტოთ!)'),
    ('სავალდებულოა სტაციონარული IV UFH ხიდი (CrCl<30/დიალიზი: თვითნებურად არ შეწყვიტოთ!)', 'სავალდებულოა კლინიკაში წინასწარ წოლა და ვენაში დამცავი გადასხმა ექიმის მეთვალყურეობით (თვითნებურად არ შეწყვიტოთ!)'),
    ('ორსულობა + მექანიკური სარქველი + HIT — ჰემატოლოგის, კარდიოლოგისა და მეანის კონსილიუმი (UFH/LMWH აკრძალულია)!', 'ორსულობის, გულის სარქვლისა და წამალზე ალერგიის გამო თვითნებურად არ შეწყვიტოთ — სასწრაფოდ დაუკავშირდით ექიმებს (ჰემატოლოგს, კარდიოლოგსა და მეანს)!'),
    ('ორსულობა + მექანიკური სარქველი — კონსილიუმი!', 'ორსულობისა და გულის სარქვლის გამო თვითნებურად არ შეწყვიტოთ — სასწრაფოდ შეათანხმეთ მეანთან და კარდიოლოგთან!'),
    ('ორსულობა + მექ. სარქველი — კონსილიუმი!', 'ორსულობისა და გულის სარქვლის გამო თვითნებურად არ შეწყვიტოთ — სასწრაფოდ შეათანხმეთ მეანთან და კარდიოლოგთან!'),
    (' [CYP2C9: ვარფარინთან აძლიერებს ანტიკოაგულაციას — გადაამოწმეთ INR!]', ' (ვარფარინთან ერთად მიღებისას აუცილებელია სისხლის შედედების ანალიზის გადამოწმება ექიმთან)'),
    ('[CYP2C9: ვარფარინთან აძლიერებს ანტიკოაგულაციას — გადაამოწმეთ INR!]', '(ვარფარინთან ერთად მიღებისას აუცილებელია სისხლის შედედების ანალიზის გადამოწმება ექიმთან)'),
    ('ღვიძლი ≥3x — შეათანხმეთ ექიმთან!', 'ღვიძლის ანალიზის გამო მიღება ან შეწყვეტა აუცილებლად შეათანხმეთ ექიმთან!'),
    (' [≥3 ანტითრომბოზული — შეათანხმეთ დე-ესკალაცია ქირურგთან!]', ' (რამდენიმე გამათხელებელი წამლის გამო სქემა აუცილებლად შეათანხმეთ ქირურგთან!)'),
    ('(DAPT რეჟიმი სტენტირებისთვის)', '(ორმაგი დამცავი რეჟიმი სტენტირებისთვის)'),
    ('DAPT', 'ორმაგი გამათხელებელი სქემა'),
    ('სავალდებულო LMWH ხიდით', 'სავალდებულო დროებითი დამცავი ნემსებით'),
    (' (ტექსტიდან)', ''),
    ('ACEi / ARB', 'წნევის წამალი (პრილი / სარტანი)'),
    ('ამლოდიპინი / CCB', 'ამლოდიპინი (წნევის წამალი)'),
    (' (PPI)', ''),
    (' (DOAC)', ''),
    (' (SGLT2)', ''),
    (' (GLP-1)', ''),
    (' (VKA)', ''),
    (' / P2Y12', ''),
    (' (P2Y12)', ''),
    ('არ შეწყვიტოს! მიიღოს', 'არ შეწყვიტოთ! მიიღეთ'),
    ('ბოლო აბი მიიღოს:', 'ბოლო აბი მიიღეთ:'),
    ('აღარ მიიღოს', 'აღარ მიიღოთ'),
    ('კონსილიუმის გარეშე', 'ექიმებთან შეთანხმების გარეშე'),
    ('კონსილიუმით', 'ექიმებთან შეთანხმებით'),
    ('კონსილიუმი!', 'ექიმებთან სასწრაფო შეთანხმება!'),
    ('კონსილიუმი', 'ექიმებთან შეთანხმება'),
    ('მექ. სარქველი', 'გულის მექანიკური სარქველი'),
    ('მექ. სარქველზე', 'გულის მექანიკურ სარქველზე'),
    ('LMWH ხიდი', 'დროებითი ნემსების სქემა'),
    ('LMWH', 'დროებითი ნემსი'),
    ('UFH', 'ვენაში დამცავი წამალი'),
    ('D+1', 'ოპერაციის მეორე დღიდან'),
    ('CrCl', 'თირკმლის მაჩვენებლით'),
    ('eGFR', 'თირკმლის ანალიზით'),
    ('VTE შეფასება', 'თრომბის რისკის შეფასება ექიმთან'),
    ('ეუგლიკემიური DKA', 'სისხლში მჟავიანობის (კეტოაციდოზის)'),
    ('DKA', 'კეტოაციდოზის'),
    ('NPH', 'საშუალო ხანგრძლივობის ინსულინი'),
    ('POISE-3', 'ექიმის სქემით'),
    ('HFpEF', 'გულის სტაბილურ ფონზე'),
    ('HFrEF-ზე', 'გულის უკმარისობის გამო ექიმთან'),
    ('HFrEF', 'გულის უკმარისობის გამო'),
    ('ULN', 'ნორმის ზედა ზღვარზე'),
    ('(SBP<100 ან HR<55)', '(დაბალი წნევის ან ნელი პულსის გამო)'),
    ('(SBP<100 / K+≥5.2)', '(დაბალი წნევის ან მომატებული კალიუმის გამო)'),
    ('(თუ SBP≥100, HR≥55)', '(თუ წნევა და პულსი ნორმალურია)'),
    ('(SBP<110)', '(დაბალი წნევის გამო)'),
    ('(SBP≥110)', '(თუ წნევა ნორმალურია)'),
    ('(SBP≤DBP)', '(წნევის გადამოწმების შემდეგ)'),
    ('IV სტრეს-დოზა', 'ვენაში დამცავი დოზა'),
    ('Xa-DOAC', 'სისხლის გამათხელებელი'),
    ('DOAC', 'სისხლის გამათხელებელი'),
    ('GLP-1 აგონისტი', 'წონის/შაქრის ინექცია'),
    ('GLP-1', 'წონის/შაქრის წამალი'),
    ('SGLT2', 'შაქრის წამალი'),
    ('VKA', 'ვარფარინი'),
    ('EVLA-ზე', 'ლაზერულ პროცედურაზე'),
    ('EVLA-ს', 'ლაზერული პროცედურის'),
    ('EVLA', 'ლაზერული პროცედურა'),
    ('INR კონტროლი', 'სისხლის შედედების ანალიზი'),
    ('INR ', 'სისხლის შედედების ანალიზი '),
    ('არასტრუქტურირებულია', 'დასაზუსტებელია'),
    ('არასტრუქტურირებული ჩანაწერი', 'დასაზუსტებელი წამალი'),
)


def _patient_2nd_person(entries):
    out = []
    for e in entries:
        sp = e.get('spoken') or ''
        if not (e.get('date_plan') or '').strip() and any(k in sp for k in _CLINICIAN_JARGON_TOKENS):
            sp = _plain_undated_patient_med(e)
        dr = e.get('drug') or ''
        for old, new in _PLAIN_TERM_REPLACEMENTS:
            sp = sp.replace(old, new)
            dr = dr.replace(old, new)
        out.append({
            'drug': dr,
            'raw_drug': e['raw_drug'],
            'spoken': sp,
            'tone': e['tone'],
            'hold_or_skip': e['hold_or_skip'],
            'urgent_contact': e['urgent_contact'],
        })
    return out


def build_patient_plain_explanation(patient, ev, today):
    """Build plain-Georgian explanation card for the nurse to walk through with the patient."""
    from django.utils import timezone

    from .meds import _detect_freetext_high_risk_meds
    today = _date(today) or timezone.localdate()
    q, s, pw = ev['q'], ev['s'], ev['pathway']
    op_dt = _date(s.get('planned_surgery_date'))
    med_script = build_phone_med_script(patient, ev, today)

    if med_script['stop_locked']:
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
    if med_script['stop_locked']:
        fasting_plain = 'ოპერაცია შეჩერებულია დაზუსტებამდე — 24-საათიანი დიეტა ან უზმოდ რეჟიმი არ დაიწყოთ ექიმის დადასტურებამდე!'
    elif has_glp1:
        fasting_plain = (
            'ოპერაციამდე 24 საათით ადრე გადადით მხოლოდ სუფთა თხევად დიეტაზე (წყალი, ჩაი რძის გარეშე, გამჭვირვალე ბულიონი). '
            'მყარი საკვები შეწყვიტეთ მინიმუმ 8 საათით ადრე, 2 საათით ადრე — სუფთა წყალიც შეწყვიტეთ.'
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
        not bool(patient.approved_at)
        or seal_code in ('stop', 'pending')
        or bool(ev.get('has_blocking_unknown_state'))
    )
    return {
        'is_draft': is_draft,
        'arrival_items': arrival_items,
        'fasting_plain': fasting_plain,
        'stop_meds': _patient_2nd_person(med_script['stop_rows']),
        'caution_meds': _patient_2nd_person(med_script['caution_rows']),
        'urgent_meds': _patient_2nd_person(med_script['urgent_rows']),
        'keep_meds': _patient_2nd_person(med_script['keep_rows']),
        'stop_locked': med_script['stop_locked'],
        'emergency_sign': _PATHWAY_EMERGENCY_SIGNS_KA.get(pw, 'უეცარი ძლიერი ტკივილი, სიცხე ან სუნთქვის გაძნელება — დაუყოვნებლივ დარეკეთ კლინიკაში.'),
    }


