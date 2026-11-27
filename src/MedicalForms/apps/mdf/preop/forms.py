"""File uploads and isolated POST parsers for Nurse Tab 1 Questionnaire vs Patient Self-Fill."""
import datetime
import os
import uuid

from django.core.files.base import ContentFile
from django.utils import timezone

from ..board import clean_text
from ..cases import MAX_UPLOAD, PhotoError, process_photo
from ..models import PreopFile, PreopPatient
from .constants import CHIP_LABELS
from .utils import _date, _int, _num, cta_live_months_age, merged_q, merged_studies


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


_NURSE_Q_BOOL_FIELDS = (
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
    'allergy_iodine', 'allergy_other', 'anesth_issue', 'pacemaker', 'loose_teeth', 'smoker', 'pregnant_possible',
)

_NURSE_Q_CHIP_FIELDS = (
    'cad_chips', 'med_chips', 'abdo_chips', 'vasc_chips',
    'cea_event_chips', 'cea_neck_chips', 'ulcer_chips',
    'pulm_chips', 'inf_chips', 'allergy_chips',
    'ven_dvt_chips', 'ven_rec_chips',
)

_NURSE_Q_TEXT_LIMITS = (
    ('prev_abdo_text', 250), ('infection_text', 250),
    ('cea_event_text', 250), ('cea_prior_neck_text', 250),
    ('abf_duration', 120), ('abf_ulcer_text', 250), ('abf_prior_vasc_text', 250),
    ('ven_dvt_text', 250), ('ven_recurrent_text', 250),
    ('cad_detail', 250), ('pulm_detail', 250), ('other_meds', 400), ('allergy_text', 250),
)


_VENOUS_ONLY_Q_BOOLS = (
    'ven_thrombophilia', 'ven_acute_svt', 'ven_bleeding',
    'ven_lidocaine_allergy', 'ven_skin_infection', 'ven_recurrent',
    'ven_hormones', 'ven_cancer_immob', 'ven_pregnant',
)
_VENOUS_ONLY_Q_CHIPS = ('ven_dvt_chips', 'ven_rec_chips')


def _apply_q_booleans_and_chips(post_data, q, patient=None):
    is_ven = patient is not None and patient.pathway == PreopPatient.Pathway.VENOUS
    for f in _NURSE_Q_BOOL_FIELDS:
        if f == 'allergy_iodine' and is_ven and 'allergy_iodine_presented' not in post_data and 'allergy_iodine' not in post_data:
            continue
        if f == 'active_infection' and is_ven and 'active_infection_presented' not in post_data and 'active_infection' not in post_data:
            continue
        if not is_ven and f in _VENOUS_ONLY_Q_BOOLS and f not in post_data:
            continue
        q[f] = post_data.get(f) in ('1', 'on', 'true')
    q['mets_ok'] = post_data.get('mets_ok', '1') != '0'
    if post_data.get('mets_limitation') in ('cardiac', 'limb'):
        q['mets_limitation'] = post_data.get('mets_limitation')
    elif q['mets_ok']:
        q['mets_limitation'] = 'cardiac'

    if hasattr(post_data, 'getlist'):
        for chip_field in _NURSE_Q_CHIP_FIELDS:
            if chip_field == 'inf_chips' and is_ven and 'active_infection_presented' not in post_data and 'inf_chips' not in post_data:
                continue
            if not is_ven and chip_field in _VENOUS_ONLY_Q_CHIPS and chip_field not in post_data:
                continue
            valid_keys = CHIP_LABELS[chip_field]
            q[chip_field] = [k for k in post_data.getlist(chip_field) if k in valid_keys]
    if q.get('allergy_chips'):
        q['allergy_other'] = True
    if q.get('cad_chips') or q.get('recent_stent'):
        q['cad'] = True
    if q.get('pulm_chips'):
        q['pulm'] = True
    if q.get('abdo_chips'):
        q['prev_abdo_surg'] = True
    if q.get('vasc_chips'):
        q['abf_prior_vasc'] = True
    if q.get('ulcer_chips'):
        q['abf_ulcer'] = True
    if q.get('inf_chips'):
        q['active_infection'] = True
    if q.get('dm_insulin') or q.get('metformin'):
        q['dm'] = True
    if q.get('ulcer_active_symp'):
        q['ulcer'] = True
    if q.get('cea_neck_chips'):
        q['cea_prior_neck'] = True
    if q.get('cea_event_chips'):
        q['cea_symp_event'] = True
    if q.get('ven_dvt_chips'):
        q['ven_dvt_history'] = True
    if q.get('ven_rec_chips'):
        q['ven_recurrent'] = True


def _parse_inv_field(post_data, target, key, parser, inv, keep_valid=False):
    if key not in post_data:
        return
    raw_v = (post_data.get(key) or '').strip()
    val = parser(raw_v)
    if val is not None or not keep_valid:
        target[key] = val
    if raw_v and val is None:
        inv[key] = clean_text(raw_v, 24)
    else:
        inv.pop(key, None)


def _apply_q_numeric_studies(post_data, patient, s, inv, today):
    _parse_inv_field(post_data, s, 'weight_kg', lambda r: _num(r, lo=25, hi=260), inv, keep_valid=True)
    _parse_inv_field(post_data, s, 'height_cm', lambda r: _num(r, lo=100, hi=230), inv, keep_valid=True)
    _parse_inv_field(post_data, s, 'aaa_mm', lambda r: _num(r, lo=10, hi=200), inv, keep_valid=False)
    if 'cta_date' in post_data:
        raw_cd = (post_data.get('cta_date') or '').strip()
        cta_dt = _date(raw_cd)
        if cta_dt and cta_dt <= today:
            s['cta_date'] = cta_dt.isoformat()
            s['cta_months'] = cta_live_months_age({'cta_date': s['cta_date']}, today=today)
            inv.pop('cta_date', None)
            inv.pop('cta_months', None)
        else:
            s['cta_date'] = ''
            if raw_cd:
                inv['cta_date'] = clean_text(raw_cd, 24)
            else:
                inv.pop('cta_date', None)
            _parse_inv_field(post_data, s, 'cta_months', lambda r: _int(r, lo=0, hi=240), inv, keep_valid=False)
    else:
        _parse_inv_field(post_data, s, 'cta_months', lambda r: _int(r, lo=0, hi=240), inv, keep_valid=False)
    if inv:
        s['_invalid_inputs'] = inv
    else:
        s.pop('_invalid_inputs', None)
    if 'cta_disc' in post_data or patient.pathway not in (PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA):
        s['cta_disc'] = post_data.get('cta_disc') in ('1', 'on', 'true')


def _apply_q_pathway_and_studies(post_data, patient, q, s, today):
    if post_data.get('cfs_level') in ('1_3', '4', '5_plus'):
        q['cfs_level'] = post_data.get('cfs_level')
    if post_data.get('aaa_pedal_pulse') in ('intact', 'reduced'):
        q['aaa_pedal_pulse'] = post_data.get('aaa_pedal_pulse')
    if post_data.get('ven_ceap') in ('C2', 'C3', 'C4', 'C5', 'C6'):
        q['ven_ceap'] = post_data.get('ven_ceap')

    raw_inv = s.get('_invalid_inputs')
    inv = dict(raw_inv) if isinstance(raw_inv, dict) else {}
    if q.get('cea_symp_event') or q.get('cea_crescendo') or q.get('cea_thrombolysis') or patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP:
        raw_ev = (post_data.get('cea_event_date') or '').strip() if 'cea_event_date' in post_data else ''
        ev_dt = _date(raw_ev) if raw_ev else None
        if raw_ev:
            if ev_dt and ev_dt <= today:
                q['cea_event_date'] = ev_dt.isoformat()
                q['cea_days_since_event'] = (today - ev_dt).days
                inv.pop('cea_event_date', None)
            else:
                q['cea_event_date'] = ''
                q['cea_days_since_event'] = None
                inv['cea_event_date'] = clean_text(raw_ev, 24)
        elif 'cea_days_since_event' in post_data:
            inv.pop('cea_event_date', None)
            d_ev = _int(post_data.get('cea_days_since_event'), lo=0, hi=3650)
            q['cea_days_since_event'] = d_ev
            try:
                q['cea_event_date'] = (today - datetime.timedelta(days=d_ev)).isoformat() if d_ev is not None else ''
            except OverflowError:
                q['cea_event_date'] = ''
        elif 'cea_event_date' in post_data:
            inv.pop('cea_event_date', None)
    else:
        q['cea_event_date'] = ''
        q['cea_days_since_event'] = None
        q['cea_event_chips'] = []
        q['cea_crescendo'] = False
        q['cea_thrombolysis'] = False
        inv.pop('cea_event_date', None)

    _parse_inv_field(post_data, q, 'abf_claud_m', lambda r: _int(r, lo=0, hi=5000), inv, keep_valid=False)
    if 'side' in post_data and post_data.get('side') in ('', 'R', 'L', 'B'):
        new_side = post_data.get('side')
        is_cea_pw = (
            patient.pathway == PreopPatient.Pathway.CEA
            or (isinstance(patient.op_subtype, str) and patient.op_subtype.startswith('cea_'))
        )
        if not (is_cea_pw and new_side == 'B'):
            if new_side != patient.side:
                s['dos_site_marked'] = False
                patient.anamnesis = ''
            patient.side = new_side

    for tf, lim in _NURSE_Q_TEXT_LIMITS:
        if tf in post_data:
            q[tf] = clean_text(post_data.get(tf), lim).replace('\n', ' ')
    if (q.get('allergy_text') or '').strip():
        q['allergy_other'] = True

    _apply_q_numeric_studies(post_data, patient, s, inv, today)


def apply_questionnaire_post(post_data, patient, filled_by_label):
    """Full nurse/staff Tab 1 save (marks nurse_verified=True)."""
    q = merged_q(patient)
    s = merged_studies(patient)
    today = timezone.localdate()
    _apply_q_booleans_and_chips(post_data, q, patient=patient)
    _apply_q_pathway_and_studies(post_data, patient, q, s, today)

    q['filled'] = True
    q['filled_at'] = timezone.localtime().strftime('%d.%m.%Y %H:%M')
    q['filled_by'] = filled_by_label
    q['nurse_verified'] = True
    q['nurse_verified_by'] = filled_by_label
    if patient.pathway != PreopPatient.Pathway.VENOUS or 'allergy_iodine_presented' in post_data or 'allergy_iodine' in post_data:
        q['allergy_iodine_asked'] = True

    if not patient.started_on:
        patient.started_on = patient.visit_date or today
    patient.questionnaire = q
    patient.studies = s


_SELF_FILL_COMMON_BOOLS = (
    'htn', 'cad', 'recent_stent', 'dm', 'metformin', 'sglt2', 'glp1', 'on_dialysis',
    'pulm', 'thyroid_disease', 'neuro', 'ulcer', 'ulcer_active_symp', 'dental_infection',
    'active_infection', 'pregnant_possible',
    'med_aspirin', 'med_clopidogrel', 'med_ticagrelor', 'med_prasugrel',
    'med_doac', 'med_dabigatran', 'med_warfarin',
    'allergy_iodine', 'allergy_other', 'anesth_issue', 'smoker',
)

_SELF_FILL_PRESERVE_BOOLS = _SELF_FILL_COMMON_BOOLS + (
    'dm_insulin', 'pacemaker', 'aaa_pain', 'claudication', 'prev_abdo_surg',
    'cea_symp_event', 'cea_crescendo', 'cea_prior_neck', 'cea_hoarseness',
    'abf_buttock_pain', 'abf_rest_pain', 'abf_ulcer', 'abf_wet_infection',
    'abf_prior_vasc', 'ic_bmt_3m', 'pta_cant_lie_flat',
    'ven_dvt_history', 'ven_thrombophilia', 'ven_acute_svt', 'ven_bleeding',
    'ven_lidocaine_allergy', 'ven_skin_infection', 'ven_recurrent',
    'ven_hormones', 'ven_cancer_immob', 'ven_pregnant',
)

_SELF_FILL_PRESERVE_CHIPS = (
    'cad_chips', 'med_chips', 'allergy_chips', 'pulm_chips', 'inf_chips',
    'abdo_chips', 'cea_event_chips', 'cea_neck_chips', 'ulcer_chips',
    'vasc_chips', 'ven_dvt_chips', 'ven_rec_chips',
)

_CEAP_RANK = {'C2': 2, 'C3': 3, 'C4': 4, 'C5': 5, 'C6': 6}


def _apply_sf_pathway_fields(post_data, pw, q, today, inv):
    if pw == PreopPatient.Pathway.AAA_INFRA:
        for f in ('aaa_pain', 'claudication', 'prev_abdo_surg'):
            q[f] = post_data.get(f) in ('1', 'on', 'true')
    elif pw == PreopPatient.Pathway.CEA:
        posted_ev_chips = bool(post_data.getlist('cea_event_chips')) if hasattr(post_data, 'getlist') else False
        for f in ('cea_symp_event', 'cea_crescendo', 'cea_prior_neck', 'cea_hoarseness'):
            if f == 'cea_symp_event' and (q.get('cea_event_chips') or posted_ev_chips):
                q[f] = True
            else:
                q[f] = post_data.get(f) in ('1', 'on', 'true')
        if q.get('cea_symp_event') or q.get('cea_crescendo'):
            raw_ev = (post_data.get('cea_event_date') or '').strip() if 'cea_event_date' in post_data else ''
            ev_dt = _date(raw_ev) if raw_ev else None
            if raw_ev:
                if ev_dt and ev_dt <= today:
                    q['cea_event_date'] = ev_dt.isoformat()
                    q['cea_days_since_event'] = (today - ev_dt).days
                    inv.pop('cea_event_date', None)
                else:
                    q['cea_event_date'] = ''
                    q['cea_days_since_event'] = None
                    inv['cea_event_date'] = clean_text(raw_ev, 24)
            elif 'cea_days_since_event' in post_data and post_data.get('cea_days_since_event'):
                d_ev = _int(post_data.get('cea_days_since_event'), lo=0, hi=3650)
                if d_ev is not None:
                    q['cea_days_since_event'] = d_ev
                    try:
                        q['cea_event_date'] = (today - datetime.timedelta(days=d_ev)).isoformat()
                    except OverflowError:
                        q['cea_event_date'] = ''
    elif pw in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA):
        for f in ('abf_buttock_pain', 'abf_rest_pain', 'abf_ulcer', 'abf_wet_infection'):
            q[f] = post_data.get(f) in ('1', 'on', 'true')
        for opt_f in ('abf_prior_vasc', 'ic_bmt_3m', 'pta_cant_lie_flat'):
            if opt_f in post_data or (opt_f == 'pta_cant_lie_flat' and pw == PreopPatient.Pathway.PAD_PTA):
                q[opt_f] = post_data.get(opt_f) in ('1', 'on', 'true')
        _parse_inv_field(post_data, q, 'abf_claud_m', lambda r: _int(r, lo=0, hi=5000), inv, keep_valid=False)
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


def _apply_sf_chips_and_meta(post_data, patient, pw, q, s, today, inv):
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

    for ch_k, parent_k in (
        ('allergy_chips', 'allergy_other'), ('cad_chips', 'cad'), ('pulm_chips', 'pulm'),
        ('abdo_chips', 'prev_abdo_surg'), ('vasc_chips', 'abf_prior_vasc'), ('ulcer_chips', 'abf_ulcer'),
        ('inf_chips', 'active_infection'), ('cea_neck_chips', 'cea_prior_neck'),
        ('cea_event_chips', 'cea_symp_event'), ('ven_dvt_chips', 'ven_dvt_history'), ('ven_rec_chips', 'ven_recurrent'),
    ):
        if q.get(ch_k):
            q[parent_k] = True
    if q.get('metformin') or q.get('dm_insulin'):
        q['dm'] = True
    if q.get('recent_stent'):
        q['cad'] = True

    # Patient may fill an empty surgical side, NEVER overwrite an already-set surgeon side (and never side='B' on CEA)!
    valid_sf_sides = ('R', 'L') if pw == PreopPatient.Pathway.CEA else ('R', 'L', 'B')
    if not patient.side and 'side' in post_data and post_data.get('side') in valid_sf_sides:
        patient.side = post_data.get('side')

    for tf, lim in (('other_meds', 400), ('allergy_text', 250)):
        if tf in post_data:
            q[tf] = clean_text(post_data.get(tf), lim).replace('\n', ' ')
    if (q.get('allergy_text') or '').strip():
        q['allergy_other'] = True

    _parse_inv_field(post_data, s, 'weight_kg', lambda r: _num(r, lo=25, hi=260), inv, keep_valid=True)
    _parse_inv_field(post_data, s, 'height_cm', lambda r: _num(r, lo=100, hi=230), inv, keep_valid=True)

    q['filled'] = True
    q['self_fill_submitted'] = True
    q['filled_at'] = timezone.localtime().strftime('%d.%m.%Y %H:%M')
    q['filled_by'] = 'პაციენტი (ბმულით)'
    if pw != PreopPatient.Pathway.VENOUS or 'allergy_iodine' in post_data or 'allergy_iodine_presented' in post_data:
        q['allergy_iodine_asked'] = True

    if not patient.started_on:
        patient.started_on = patient.visit_date or today


def _preserve_prior_sf_risks(old_q, q):
    """When a previously filled card is reopened via a blank bearer link, never silently wipe prior risk/med/allergy flags."""
    for bf in _SELF_FILL_PRESERVE_BOOLS:
        if old_q.get(bf):
            q[bf] = True
    if not old_q.get('mets_ok', True):
        q['mets_ok'] = False
        if old_q.get('mets_limitation') in ('cardiac', 'limb'):
            q['mets_limitation'] = old_q['mets_limitation']
    for cf in _SELF_FILL_PRESERVE_CHIPS:
        old_c = old_q.get(cf) or []
        if old_c:
            q[cf] = sorted(set(old_c) | set(q.get(cf) or []))
    for tf, lim in (('other_meds', 400), ('allergy_text', 250)):
        old_t = (old_q.get(tf) or '').strip()
        new_t = (q.get(tf) or '').strip()
        if old_t and not new_t:
            q[tf] = old_t
        elif old_t and new_t and old_t.lower() not in new_t.lower():
            q[tf] = f'{old_t}; {new_t}'[:lim]
    for sk in ('abf_duration', 'cea_event_date'):
        if old_q.get(sk) and not q.get(sk):
            q[sk] = old_q[sk]
    if _CEAP_RANK.get(old_q.get('ven_ceap'), 0) > _CEAP_RANK.get(q.get('ven_ceap'), 0):
        q['ven_ceap'] = old_q['ven_ceap']
    for nk in ('cea_days_since_event', 'abf_claud_m'):
        if old_q.get(nk) is not None and q.get(nk) is None:
            q[nk] = old_q[nk]


def apply_self_fill_post(post_data, patient):
    """Isolated patient Self-Fill parser: NEVER wipes nurse-entered fields (cta_disc, pacemaker, dm_insulin, etc.)."""
    q = merged_q(patient)
    old_q = dict(q)
    was_filled = bool(old_q.get('filled'))
    s = merged_studies(patient)
    today = timezone.localdate()
    pw = PreopPatient.SUBTYPE_PATHWAY.get(patient.op_subtype, patient.pathway)

    # Common patient-facing switches rendered on self_fill.html across ALL 6 pathways (including active_infection!)
    for f in _SELF_FILL_COMMON_BOOLS:
        if f == 'allergy_iodine' and pw == PreopPatient.Pathway.VENOUS and 'allergy_iodine' not in post_data:
            continue
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

    raw_inv = s.get('_invalid_inputs')
    inv = dict(raw_inv) if isinstance(raw_inv, dict) else {}
    _apply_sf_pathway_fields(post_data, pw, q, today, inv)
    _apply_sf_chips_and_meta(post_data, patient, pw, q, s, today, inv)
    if was_filled:
        _preserve_prior_sf_risks(old_q, q)
    if inv:
        s['_invalid_inputs'] = inv
    else:
        s.pop('_invalid_inputs', None)
    patient.questionnaire = q
    patient.studies = s
