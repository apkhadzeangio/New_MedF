"""Track B3/B5 plain-Georgian missing-item checklist, phone med-call script, and postponement detector."""
from .constants import STATUS_NO_Q
from .nurse_ui import _med_call_status, med_schedule_signature
from .utils import _date, _fmt_ka_date

_DRUG_PLAIN_PREFIX = {
    'SGLT2 (ჯარდიანსი / ფორსიგა)': 'შაქრის წამალი — ჯარდიანსი / ფორსიგა (SGLT2)',
    'DOAC (ქსარელტო / ელიქვისი)': 'სისხლის გამათხელებელი — ქსარელტო / ელიქვისი (DOAC)',
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


def build_plain_georgian_missing_items(patient, ev, today):
    """Numbered plain-Georgian checklist of everything missing before surgeon approval."""
    items = []
    q = ev['q']

    if ev['has_hard_contraindication'] or ev['clearance_seal']['code'] == 'stop':
        reason = ev['red_flags'][0] if ev['red_flags'] else 'ოპერაციაზე დაშვება შეჩერებულია'
        items.append({
            'kind': 'stop', 'label': f'STOP — აცნობეთ ქირურგს: {reason}',
            'target_section': 'nw-sec-doc', 'target_input': '', 'action_label': 'ექიმის სექცია',
        })
    elif ev['has_unacked_red']:
        for rf in ev['red_flags'][:3]:
            items.append({
                'kind': 'alert', 'label': f'ექიმს უნდა ეცნობოს: {rf}',
                'target_section': 'nw-sec-doc', 'target_input': '', 'action_label': 'ქირურგის ნახვა',
            })

    if not q.get('filled') or ev['status'] == STATUS_NO_Q:
        items.append({
            'kind': 'q', 'label': 'შესავსებია პაციენტის წინასაოპერაციო კითხვარი (ან გაუგზავნეთ ბმული)',
            'target_section': 'nw-sec-q', 'target_input': '', 'action_label': 'კითხვარის გახსნა',
        })
    elif q.get('self_fill_submitted') and not q.get('nurse_verified'):
        items.append({
            'kind': 'q', 'label': 'პაციენტმა შეავსო კითხვარი — საჭიროა ექთნის გადამოწმება',
            'target_section': 'nw-sec-q', 'target_input': '', 'action_label': 'გადამოწმება',
        })

    seen_s2 = set()
    for m in ev['missing_items']:
        fld = m.get('field') or ''
        if m.get('tab') == 's2':
            seen_s2.add(fld or m.get('label'))
        inp_id = _FIELD_TO_INPUT_ID.get(fld, '')
        sec = 'nw-vitals-card' if inp_id else ('nw-sec-s2' if m.get('tab') == 's2' else 'nw-vitals-card')
        items.append({
            'kind': 'missing', 'label': f"აკლია: {m['label']}",
            'target_section': sec, 'target_input': inp_id, 'action_label': 'შევსება',
        })

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
    if not row.get('hold_or_skip') and row.get('tone') == 'ok':
        return f'არ შეწყვიტოს! მიიღოს ჩვეულებრივად{f" ({op_ka}-ის დილასაც ყლუპი წყლით)" if op_ka else ""}.'
    if plan:
        return plan.replace('ბოლო:', 'ბოლო აბი მიიღოს:').replace('არ მიიღოთ', 'აღარ მიიღოს')
    return row.get('rule') or ''


def build_phone_med_script(patient, ev, today):
    """Build nurse phone-call medication-hold script with stop/continue groups and call-stamp state."""
    s = ev['s']
    op_dt = _date(s.get('planned_surgery_date'))
    op_ka = _fmt_ka_date(op_dt, short=True) if op_dt else ''
    stop_locked = (ev['clearance_seal']['code'] == 'stop' or ev['has_hard_contraindication'])
    med_call = _med_call_status(ev, today)

    stop_rows, keep_rows = [], []
    for row in ev['med_schedule']:
        plain_drug = _DRUG_PLAIN_PREFIX.get(row['drug'], row['drug'])
        is_hold = bool(row.get('hold_or_skip'))
        preserve_stop = bool(row.get('acute_hold') or row.get('contraindicated') or row.get('glp1_diet'))
        spoken_txt = (
            'არ შეწყვიტოთ თვითნებურად — ოპერაცია შეჩერებულია (STOP)!'
            if (stop_locked and is_hold and not preserve_stop)
            else _spoken_med_phrase(row, op_ka)
        )
        entry = {
            'drug': plain_drug,
            'raw_drug': row['drug'],
            'spoken': spoken_txt,
            'rule': row['rule'],
            'tone': row['tone'],
            'date_plan': row.get('date_plan') or '',
            'hold_or_skip': is_hold,
        }
        # Only rows that actually hold/skip/change (or contraindicated/unstructured) belong in stop_rows;
        # red "DO NOT STOP!" rows (e.g. recent stent P2Y12) stay in keep_rows!
        if is_hold or row.get('contraindicated') or row.get('unstructured'):
            stop_rows.append(entry)
        else:
            keep_rows.append(entry)

    return {
        'has_meds': bool(stop_rows or keep_rows),
        'stop_rows': stop_rows,
        'keep_rows': keep_rows,
        'stop_locked': stop_locked,
        'needs_op_date': bool(stop_rows and not op_dt),
        'op_date_ka': _fmt_ka_date(op_dt) if op_dt else '',
        'called_valid': med_call['called_valid'],
        'outdated_call': med_call['outdated_call'],
        'called_at': s.get('nurse_med_called_at') or '',
        'called_by': s.get('nurse_med_called_by') or '',
        'current_sig': med_schedule_signature(ev),
    }


def detect_postponement_impacts(patient, ev, today):
    """Compute exact re-check items when planned_surgery_date was changed / postponed."""
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
    if labs_dt and (cur_dt - labs_dt).days > 14:
        age_at_op = (cur_dt - labs_dt).days
        impacts.append({
            'tone': 'danger',
            'text': f'ანალიზებს ახალ თარიღამდე ({_fmt_ka_date(cur_dt)}) გაუვა 14-დღიანი ვადა (იქნება {age_at_op} დღის) — ანალიზები თავიდან უნდა ჩაბარდეს!',
        })
    elif not labs_dt and shift_days > 0:
        impacts.append({
            'tone': 'warn',
            'text': 'შეამოწმეთ ანალიზების თარიღი — ახალ ოპერაციის დღემდე არ უნდა იყოს 14 დღეზე ძველი.',
        })

    med_call = _med_call_status(ev, today)
    hold_drugs = [r['drug'] for r in ev['med_schedule'] if r.get('hold_or_skip')]
    if hold_drugs:
        if med_call['outdated_call']:
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
    if ev.get('is_symp_cea') and cea_ev_dt and (cur_dt - cea_ev_dt).days > 14:
        impacts.append({
            'tone': 'danger',
            'text': f'სიმპტომური საძილე არტერიის (CEA) 14-დღიანი ფანჯარა ირღვევა (სიმპტომიდან იქნება {(cur_dt - cea_ev_dt).days} დღე) — აცნობეთ ქირურგს!',
        })
    if ev.get('target_days', 14) <= 7 and patient.started_on and (cur_dt - patient.started_on).days > ev['target_days']:
        impacts.append({
            'tone': 'warn',
            'text': f'ოპერაციის ახალი თარიღი ცდება {ev["target_days"]}-დღიან სამიზნე მომზადების ვადას — გადაამოწმეთ ქირურგთან.',
        })

    cta_dt = _date(s.get('cta_date'))
    if cta_dt and (cur_dt - cta_dt).days > 183:
        impacts.append({
            'tone': 'warn',
            'text': 'კტ-ანგიოგრაფია (CTA) ახალ თარიღზე 6 თვეზე ძველი იქნება — დააზუსტეთ ქირურგთან განახლების საჭიროება.',
        })

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


def build_patient_plain_explanation(patient, ev, today):
    """Build plain-Georgian explanation card for the nurse to walk through with the patient."""
    q, s, pw = ev['q'], ev['s'], ev['pathway']
    op_dt = _date(s.get('planned_surgery_date'))
    med_script = build_phone_med_script(patient, ev, today)

    arrival_items = [
        f'დაგეგმილი ოპერაციის თარიღი: {_fmt_ka_date(op_dt)}' if op_dt else 'ოპერაციის ზუსტ თარიღს მკურნალი ექიმი შეგატყობინებთ.',
        'თან იქონიეთ პირადობის მოწმობა და ბოლო ანალიზების ფურცლები.',
    ]
    if pw in ('aaa_infra', 'cea', 'abf', 'pad_bypass'):
        arrival_items.append('აუცილებლად წამოიღეთ კტ-ანგიოგრაფიის (CTA) დისკი და დასკვნა.')
    if pw == 'venous' and not ev.get('compression_contra'):
        arrival_items.append('თან იქონიეთ ექიმის შერჩეული კომპრესიული წინდა (თუ დანიშნულია).')
    if q.get('pulm') or 'osa' in (q.get('pulm_chips') or []):
        arrival_items.append('წამოიღეთ თქვენი ყოველდღიური ინჰალატორი ან ძილის აპარატი (CPAP).')

    anesth_plan = s.get('anesthesia_plan') or 'auto'
    is_local_evla = (
        patient.op_subtype == 'evla'
        and not ev.get('has_neuraxial_plan')
        and anesth_plan in ('auto', 'local')
    )
    if q.get('glp1'):
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
        'stop_meds': med_script['stop_rows'],
        'keep_meds': med_script['keep_rows'],
        'stop_locked': med_script['stop_locked'],
        'emergency_sign': _PATHWAY_EMERGENCY_SIGNS_KA.get(pw, 'უეცარი ძლიერი ტკივილი, სიცხე ან სუნთქვის გაძნელება — დაუყოვნებლივ დარეკეთ კლინიკაში.'),
    }


