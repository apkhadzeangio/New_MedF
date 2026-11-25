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


def _apply_q_booleans_and_chips(post_data, q):
    for f in _NURSE_Q_BOOL_FIELDS:
        q[f] = post_data.get(f) in ('1', 'on', 'true')
    q['mets_ok'] = post_data.get('mets_ok', '1') != '0'
    if post_data.get('mets_limitation') in ('cardiac', 'limb'):
        q['mets_limitation'] = post_data.get('mets_limitation')
    elif q['mets_ok']:
        q['mets_limitation'] = 'cardiac'

    if hasattr(post_data, 'getlist'):
        for chip_field in _NURSE_Q_CHIP_FIELDS:
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


def _apply_q_pathway_and_studies(post_data, patient, q, s, today):
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
            d_ev = _int(post_data.get('cea_days_since_event'), lo=0, hi=3650)
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
            patient.approved_at = None
            patient.approved_by = None
            patient.anamnesis = ''
        patient.side = new_side

    for tf, lim in _NURSE_Q_TEXT_LIMITS:
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
    if 'aaa_mm' in post_data:
        s['aaa_mm'] = _num(post_data.get('aaa_mm'), lo=10, hi=200)
    if 'cta_date' in post_data:
        cta_dt = _date(post_data.get('cta_date'))
        if cta_dt and cta_dt <= today:
            s['cta_date'] = cta_dt.isoformat()
            s['cta_months'] = cta_live_months_age({'cta_date': s['cta_date']}, today=today)
        else:
            s['cta_date'] = ''
            if 'cta_months' in post_data:
                s['cta_months'] = _int(post_data.get('cta_months'), lo=0, hi=120)
    elif 'cta_months' in post_data:
        s['cta_months'] = _int(post_data.get('cta_months'), lo=0, hi=120)
    if 'cta_disc' in post_data or patient.pathway not in (PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA):
        s['cta_disc'] = post_data.get('cta_disc') in ('1', 'on', 'true')


def apply_questionnaire_post(post_data, patient, filled_by_label):
    """Full nurse/staff Tab 1 save (marks nurse_verified=True)."""
    q = merged_q(patient)
    s = merged_studies(patient)
    today = timezone.localdate()
    _apply_q_booleans_and_chips(post_data, q)
    _apply_q_pathway_and_studies(post_data, patient, q, s, today)

    q['filled'] = True
    q['filled_at'] = timezone.localtime().strftime('%d.%m.%Y %H:%M')
    q['filled_by'] = filled_by_label
    q['nurse_verified'] = True
    q['nurse_verified_by'] = filled_by_label
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


def _apply_sf_pathway_fields(post_data, pw, q, today):
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
                d_ev = _int(post_data.get('cea_days_since_event'), lo=0, hi=3650)
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


def _apply_sf_chips_and_meta(post_data, patient, pw, q, s, today):
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


def apply_self_fill_post(post_data, patient):
    """Isolated patient Self-Fill parser: NEVER wipes nurse-entered fields (cta_disc, pacemaker, dm_insulin, etc.)."""
    q = merged_q(patient)
    s = merged_studies(patient)
    today = timezone.localdate()
    pw = PreopPatient.SUBTYPE_PATHWAY.get(patient.op_subtype, patient.pathway)

    # Common patient-facing switches rendered on self_fill.html across ALL 6 pathways (including active_infection!)
    for f in _SELF_FILL_COMMON_BOOLS:
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

    _apply_sf_pathway_fields(post_data, pw, q, today)
    _apply_sf_chips_and_meta(post_data, patient, pw, q, s, today)
    patient.questionnaire = q
    patient.studies = s
