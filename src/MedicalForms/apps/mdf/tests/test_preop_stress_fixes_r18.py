"""Regression tests for Stage-4 Round-18 (v1.2.17) clinical, pharmacology, NLP, RBAC, and plain-Georgian fixes."""
import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.mdf.models import Case, PreopFile, PreopPatient, StaffProfile
from apps.mdf.preop import (
    build_med_schedule,
    build_self_fill_highlights,
    evaluate,
    generate_anamnesis,
    make_self_fill_token,
)
from apps.mdf.preop.calculators import normalize_albumin_gl
from apps.mdf.preop.meds_freetext import _detect_freetext_high_risk_meds, _positive_clauses_text
from apps.mdf.preop.nurse_plan import (
    build_patient_plain_explanation,
    build_phone_med_script,
    detect_postponement_impacts,
)
from apps.mdf.preop.nurse_ui import PREOP_UI_NEW, PREOP_UI_OLD, _med_call_status, get_nurse_readiness_verdict
from apps.mdf.preop.plain_ka import scrub_plain_ka
from apps.mdf.preop.registry import RULES_BY_ID, match_rule_for_text
from apps.mdf.preop.views.patient import PreopPatientView

TODAY = datetime.date(2026, 11, 27)
OP_DATE = '2026-12-07'
User = get_user_model()


def _mk(subtype='open', age=68, sex='M', q=None, s=None, side='', archived=False, approved=False):
    pw_map = {
        'open': PreopPatient.Pathway.AAA_INFRA,
        'evar': PreopPatient.Pathway.AAA_INFRA,
        'cea_symp': PreopPatient.Pathway.CEA,
        'cea_asymp': PreopPatient.Pathway.CEA,
        'abf_claud': PreopPatient.Pathway.ABF,
        'abf_clti': PreopPatient.Pathway.ABF,
        'bypass_claud': PreopPatient.Pathway.PAD_BYPASS,
        'bypass_clti': PreopPatient.Pathway.PAD_BYPASS,
        'pta_claud': PreopPatient.Pathway.PAD_PTA,
        'pta_clti': PreopPatient.Pathway.PAD_PTA,
        'evla': PreopPatient.Pathway.VENOUS,
        'phlebectomy': PreopPatient.Pathway.VENOUS,
    }
    base_q = {'filled': True, 'mets_ok': True}
    if q:
        base_q.update(q)
    base_s = {
        'planned_surgery_date': OP_DATE,
        'labs_date': TODAY.isoformat(),
        'weight_kg': 75,
        'height_cm': 175,
        'bp_sys': 125,
        'bp_dia': 76,
        'hr': 72,
        'spo2': 98,
        'hb': 135,
        'plt': 230,
        'inr': 1.0,
        'creat': 82,
        'creat_unit': 'umol',
        'k': 4.2,
        'glucose': 5.4,
        'ecg': 'normal',
    }
    if subtype in ('open', 'evar'):
        base_s['aaa_mm'] = 58
    if s:
        base_s.update(s)
    return PreopPatient(
        patient_name='ტესტ R18 პაციენტი',
        age=age,
        sex=sex,
        pathway=pw_map[subtype],
        op_subtype=subtype,
        side=side or ('R' if subtype in ('cea_symp', 'cea_asymp', 'evla', 'phlebectomy') else ''),
        visit_date=TODAY,
        started_on=TODAY,
        questionnaire=base_q,
        studies=base_s,
        archived=archived,
        approved_at=timezone.now() if approved else None,
    )


class Round18StressFixEngineTests(TestCase):
    def test_crash_resilience_huge_int_invalid_scalar_and_standalone_clear(self):
        p_inv = _mk('open', s={'_invalid_inputs': 123})
        ev_inv = evaluate(p_inv, today=TODAY)
        self.assertIn('clearance_seal', ev_inv)

        p_huge = _mk('open', age=10**5000, q={'other_meds': 'ვარფარინი'}, s={'creat': 10**5000, 'inr': 10**5000})
        ev_huge = evaluate(p_huge, today=TODAY)
        self.assertEqual(ev_huge['clearance_seal']['code'], 'pending')
        self.assertIsInstance(build_med_schedule(p_huge, p_huge.questionnaire, p_huge.studies, None, today=TODAY), list)

        # Standalone build_med_schedule must NOT resurrect cleared caller s_mod=None from patient.studies
        p_clr = _mk('open', q={'med_doac': True}, s={'creat': 280, 'inr': 2.5, 'anesthesia_plan': 'neuraxial'})
        s_clr = {**p_clr.studies, 'creat': None, 'inr': None, 'anesthesia_plan': 'general'}
        rows_cleared = build_med_schedule(p_clr, p_clr.questionnaire, s_clr, 70, today=TODAY)
        doac_r = next(r for r in rows_cleared if 'DOAC' in r['drug'])
        self.assertNotIn('96 სთ', doac_r['rule'])

        # Zero-value raw scalar triggers ASRA/DoS fail-safe (not silently treated as missing)
        ev_zero = evaluate(_mk('open', s={'anesthesia_plan': 'neuraxial', 'plt': 0, 'dos_inr': 0}), today=TODAY)
        self.assertTrue(ev_zero['neuraxial_hard_blocked'])

    def test_nlp_double_negation_gareshe_ufh_and_english_inline_splits(self):
        for txt, expected_key in (
            ('ვარფარინი არ შეუწყვეტია', 'warfarin'),
            ('ქსარელტო არაა შეწყვეტილი', 'doac'),
            ('did not stop apixaban', 'doac'),
            ('ელიქვისი შეწყვეტის გარეშე', 'doac'),
            ('warfarin stopped started apixaban', 'doac'),
        ):
            res = _detect_freetext_high_risk_meds(txt)
            self.assertIn(expected_key, res['matched_keys'], txt)
        self.assertNotIn('warfarin', _detect_freetext_high_risk_meds('warfarin stopped started apixaban')['matched_keys'])
        self.assertEqual(_detect_freetext_high_risk_meds('ვარფარინის გარეშე')['matched_keys'], set())
        self.assertEqual(_detect_freetext_high_risk_meds('ვარფარინი 5მგ მოხსნილია')['matched_keys'], set())
        self.assertIn('ელიქვისი', _positive_clauses_text('ვარფარინი მოხსნა დაიწყო ელიქვისი'))

        # UFH on dialysis is NOT contraindicated; LMWH IS contraindicated; Argatroban + liver_3x warns 75%
        ev_ufh = evaluate(_mk('bypass_clti', q={'on_dialysis': True, 'other_meds': 'ჰეპარინი 5000'}, s={'creat': 600}), today=TODAY)
        ufh_row = next(r for r in ev_ufh['med_schedule'] if 'UFH' in r['drug'])
        self.assertFalse(ufh_row.get('contraindicated'))
        self.assertFalse(any(r.get('unstructured') for r in ev_ufh['med_schedule']))

        ev_lmwh = evaluate(_mk('bypass_clti', q={'on_dialysis': True, 'other_meds': 'კლექსანი 40მგ'}, s={'creat': 600}), today=TODAY)
        lmwh_row = next(r for r in ev_lmwh['med_schedule'] if 'პარენტერული' in r['drug'])
        self.assertTrue(lmwh_row['contraindicated'])

        ev_arg = evaluate(_mk('open', q={'allergy_chips': ['hit_heparin'], 'other_meds': 'არგატრობანი'}, s={'liver_status': '3x'}), today=TODAY)
        self.assertTrue(any('75%' in rf and 'არგატრობან' in rf for rf in ev_arg['red_flags']))
        self.assertTrue(any('75%' in r['rule'] for r in ev_arg['med_schedule']))

    def test_pharmacology_freetext_parity_amio_dabi_isth_nsaid_p2y12_and_preg(self):
        # Dual P2Y12 across structured + free-text
        ev_dup = evaluate(_mk('pta_clti', q={'med_clopidogrel': True, 'other_meds': 'ბრილინტა 90 მგ'}), today=TODAY)
        self.assertTrue(any('P2Y12' in rf for rf in ev_dup['red_flags']))
        self.assertTrue(all(r['contraindicated'] for r in ev_dup['med_schedule'] if 'კლოპიდოგრელი' in r['drug'] or 'ტიკაგრელორი' in r['drug']))

        # Amiodarone + free-text Warfarin (CYP2C9, NOT DOAC P-gp) vs free-text DOAC (EHRA +24h)
        ev_aw = evaluate(_mk('open', q={'med_chips': ['amiodarone'], 'other_meds': 'ვარფარინი 5 მგ'}, s={'creat': 140, 'weight_kg': 70, 'inr': 2.5}), today=TODAY)
        amio_w = next(r for r in ev_aw['med_schedule'] if 'ამიოდარონი' in r['drug'])
        self.assertIn('CYP2C9', amio_w['rule'])
        self.assertNotIn('DOAC/დაბიგატრან', amio_w['rule'])
        ev_ad = evaluate(_mk('open', q={'med_chips': ['amiodarone'], 'other_meds': 'ელიქვისი 5 მგ'}, s={'creat': 140, 'weight_kg': 70}), today=TODAY)
        self.assertTrue(any('ამიოდარონი + DOAC' in af for af in ev_ad['amber_flags']))

        # Dabigatran ISTH 2021 obesity (>40 BMI / >120 kg) cleanly attributed
        ev_dabi = evaluate(_mk('open', q={'med_dabigatran': True}, s={'weight_kg': 138, 'height_cm': 175}), today=TODAY)
        dabi_af = next(af for af in ev_dabi['amber_flags'] if 'ISTH 2021' in af)
        self.assertEqual(match_rule_for_text(dabi_af).id, 'MED_DABIGATRAN_HOLD_INTERVALS')

        # Free-text NSAID + OAC triggers red flag + attributed med_schedule row
        ev_ns = evaluate(_mk('open', q={'med_doac': True, 'other_meds': 'იბუპროფენი 400 მგ'}), today=TODAY)
        ns_row = next(r for r in ev_ns['med_schedule'] if 'აასს' in r['drug'])
        self.assertTrue(ns_row['contraindicated'])
        self.assertIsNotNone(match_rule_for_text(ns_row['rule']))

        # Prasugrel in pregnancy & age=130 saturated + free-text cilostazol CrCl<=25 / HR>100 parity
        ev_pp = evaluate(_mk('pta_clti', sex='F', age=32, q={'pregnant_possible': True, 'med_prasugrel': True}), today=TODAY)
        self.assertTrue(next(r for r in ev_pp['med_schedule'] if 'პრასუგრელი' in r['drug'])['contraindicated'])
        self.assertTrue(any(match_rule_for_text(rf) is not None for rf in ev_pp['red_flags'] if 'პრასუგრელი' in rf))
        ev_p130 = evaluate(_mk('pta_clti', age=130, q={'med_prasugrel': True}), today=TODAY)
        self.assertIn('≥75 წ.', next(r for r in ev_p130['med_schedule'] if 'პრასუგრელი' in r['drug'])['rule'])
        ev_cilo_hr = evaluate(_mk('abf_claud', q={'other_meds': 'პლეტალი 100 მგ'}, s={'hr': 120}), today=TODAY)
        self.assertTrue(next(r for r in ev_cilo_hr['med_schedule'] if 'ცილოსტაზოლ' in r['drug'])['contraindicated'])

    def test_clinical_combos_anesthesia_evla_mycotic_venous_and_postponement(self):
        # Explicit DAPT + rivaroxaban_2_5 when voyager_safe=False & mech_valve + active stent partner
        ev_dr = evaluate(_mk('pta_clti', sex='F', age=32, q={'pregnant_possible': True, 'med_chips': ['rivaroxaban_2_5']}, s={'post_antithrombotic_plan': 'dapt'}), today=TODAY)
        self.assertIn('უკუნაჩვენებია', ev_dr['post_antithrombotic_label'])
        ev_mvs = evaluate(_mk('pta_clti', q={'cad_chips': ['mech_valve'], 'recent_stent': True, 'med_warfarin': True, 'med_clopidogrel': True}), today=TODAY)
        self.assertIn('კორონარული სტენტის დაცვა', ev_mvs['post_antithrombotic_label'])

        # Anatomic anesthesia mismatch hard STOP
        self.assertEqual(evaluate(_mk('open', s={'anesthesia_plan': 'local'}), today=TODAY)['clearance_seal']['code'], 'stop')
        self.assertEqual(evaluate(_mk('evla', s={'anesthesia_plan': 'neuraxial'}), today=TODAY)['clearance_seal']['code'], 'stop')
        self.assertEqual(evaluate(_mk('evla', s={'anesth_lines_chips': ['epidural']}), today=TODAY)['clearance_seal']['code'], 'stop')

        # EVLA uninterrupted warfarin checked before HIT/MV; subtherapeutic MV disqualifies uninterrupted VKA
        ev_evla_hit = evaluate(_mk('evla', q={'med_warfarin': True, 'allergy_chips': ['hit_heparin']}, s={'inr': 2.5, 'ven_gsv_mm': 8}), today=TODAY)
        self.assertIn('მოუხსნელად', ev_evla_hit['caprini_advice'])
        ev_evla_sub = evaluate(_mk('evla', q={'med_warfarin': True, 'cad_chips': ['mech_valve']}, s={'inr': 1.5, 'ven_gsv_mm': 8}), today=TODAY)
        self.assertTrue(any('სუბთერაპიულია' in rf for rf in ev_evla_sub['red_flags']))

        # Mycotic AAA -> ASA IV-E, broad-spectrum empirical ABX, urgent postponement danger
        p_myc = _mk('open', s={'aaa_morphology': 'inflammatory_mycotic', 'prev_planned_surgery_date': '2026-12-01', 'planned_surgery_date': '2026-12-15'})
        ev_myc = evaluate(p_myc, today=TODAY)
        self.assertEqual(ev_myc['effective_asa'], 'ASA IV-E')
        self.assertIn('მიკოზური AAA', ev_myc['surgical_brief']['abx_regimen'])
        imp_myc = detect_postponement_impacts(p_myc, ev_myc, TODAY)['impacts']
        self.assertTrue(any(i['tone'] == 'danger' and 'მიკოზური' in i['text'] for i in imp_myc))

        # Postponement with contraindicated drug never emits green ok
        p_contra = _mk('open', q={'cad_chips': ['mech_valve'], 'med_doac': True}, s={'prev_planned_surgery_date': '2026-12-07', 'planned_surgery_date': '2026-12-10'})
        ev_contra = evaluate(p_contra, today=TODAY)
        imp_c = detect_postponement_impacts(p_contra, ev_contra, TODAY)['impacts']
        self.assertEqual(imp_c[0]['tone'], 'danger')

        # Venous eff_cardiac & anamnesis + dm=False GLP-1 amber + Caprini severe_renal anti-Xa guard
        p_ven = _mk('evla', age=38, q={'cad': False, 'htn': False}, s={'ecg': 'afib_controlled', 'echo_valve_issue': True, 'ven_gsv_mm': 7})
        ev_ven = evaluate(p_ven, today=TODAY)
        self.assertTrue(ev_ven['needs_ecg_venous'])
        self.assertNotIn('მნიშვნელოვანი ქრონიკული დაავადებების გარეშე', generate_anamnesis(p_ven, ev_ven))
        ev_glp = evaluate(_mk('open', q={'dm': False, 'glp1': True}), today=TODAY)
        self.assertTrue(any('GLP-1' in af and 'დიაბეტის მითითების გარეშე' in af for af in ev_glp['amber_flags']))
        ev_cap = evaluate(_mk('open', age=55, s={'creat': 260, 'weight_kg': 65}), today=TODAY)
        self.assertIn('anti-Xa', ev_cap['caprini_advice'])

    def test_inactive_seal_plain_georgian_and_live_registry_mutations(self):
        # Archived & operated cards -> clearance_seal='inactive', workup_ready=False, guide is_draft + inactive_banner
        p_arch = _mk('evla', s={'ven_gsv_mm': 7}, archived=True, approved=True)
        ev_arch = evaluate(p_arch, today=TODAY)
        self.assertEqual(ev_arch['clearance_seal']['code'], 'inactive')
        self.assertFalse(ev_arch['workup_ready'])
        self.assertEqual(get_nurse_readiness_verdict(p_arch, ev_arch)['badge'], 'არააქტიური ბარათი')
        guide_arch = build_patient_plain_explanation(p_arch, ev_arch, TODAY)
        self.assertTrue(guide_arch['is_draft'])
        self.assertIn('დაარქივებულია', guide_arch['inactive_banner'])

        # STOP fasting without GLP-1 never mentions 24-hour liquid diet
        p_stop = _mk('open', q={'aaa_pain': True, 'glp1': False})
        guide_stop = build_patient_plain_explanation(p_stop, evaluate(p_stop, today=TODAY), TODAY)
        self.assertNotIn('24-საათიანი', guide_stop['fasting_plain'])

        # Plain-Georgian scrub & highlights & med-call script
        self.assertNotIn('4.6', scrub_plain_ka('ორსულობა (EMA SmPC 4.6) — ტიკაგრელორი'))
        p_sf = _mk('cea_symp', q={'cea_crescendo': True, 'med_doac': True, 'sglt2': True, 'allergy_chips': ['hit_heparin', 'nsaid']})
        ev_sf = evaluate(p_sf, today=TODAY)
        hl_txt = ' '.join(t for _, t in build_self_fill_highlights(p_sf.questionnaire))
        for tok in ('CEA', 'TIA', 'SGLT2', 'HIT', 'NSAID'):
            self.assertNotIn(tok, hl_txt)
        mc = _med_call_status(ev_sf, TODAY)
        for d in mc['drugs']:
            self.assertNotIn('DOAC', d)
            self.assertNotIn('SGLT2', d)
        script = build_phone_med_script(p_sf, ev_sf, TODAY)
        for bucket in ('stop_rows', 'caution_rows', 'urgent_rows', 'keep_rows'):
            for row in script[bucket]:
                self.assertNotIn('DOAC', row['date_plan'])
                self.assertNotIn('SGLT2', row['date_plan'])

        # Live registry mutation: match_rule_for_text returns live entry + dynamic {val[crcl_low]} + alb_gdl_lo
        patched = dict(RULES_BY_ID['MED_DOAC_HOLD_INTERVALS'].value)
        patched['crcl_low'] = 40
        live_doac = RULES_BY_ID['MED_DOAC_HOLD_INTERVALS'].__class__(**{**RULES_BY_ID['MED_DOAC_HOLD_INTERVALS'].__dict__, 'value': patched})
        with patch.dict(RULES_BY_ID, {'MED_DOAC_HOLD_INTERVALS': live_doac}):
            ev_m = evaluate(_mk('open', q={'med_doac': True}, s={'creat': 160, 'weight_kg': 60}), today=TODAY)
            rf_m = next(rf for rf in ev_m['red_flags'] if 'DOAC (CrCl <40)' in rf)
            matched = match_rule_for_text(rf_m)
            self.assertEqual(matched.id, 'MED_DOAC_HOLD_INTERVALS')
            self.assertEqual(matched.value['crcl_low'], 40)

        patched_alb = dict(RULES_BY_ID['LAB_UNIT_AUTO_CONVERSION'].value)
        patched_alb['alb_gdl_lo'] = 2.0
        live_alb = RULES_BY_ID['LAB_UNIT_AUTO_CONVERSION'].__class__(**{**RULES_BY_ID['LAB_UNIT_AUTO_CONVERSION'].__dict__, 'value': patched_alb})
        with patch.dict(RULES_BY_ID, {'LAB_UNIT_AUTO_CONVERSION': live_alb}):
            self.assertIsNone(normalize_albumin_gl(1.5))


class Round18StressFixWorkflowTests(TestCase):
    def setUp(self):
        self.doc = User.objects.create_user('r18_doc', password='pw')
        StaffProfile.objects.create(user=self.doc, display_name='ტესტ R18 ქირურგი')
        self.nurse = User.objects.create_user('r18_nurse', password='pw')
        self.c_doc, self.c_nurse = Client(), Client()
        self.c_doc.force_login(self.doc)
        self.c_nurse.force_login(self.nurse)

    def test_inactive_locks_nurse_anamnesis_rbac_parsers_and_plain_templates(self):
        p = _mk('bypass_clti', q={'neuro': False, 'ven_pregnant': True}, s={'carotid_symp': True}, approved=True)
        p.save()

        # 1. Plain nurse cannot overwrite/regenerate signed anamnesis on approved card
        p.anamnesis = 'ქირურგის ხელმოწერილი ანამნეზი'
        p.save(update_fields=['anamnesis'])
        self.c_nurse.post(reverse('preop-patient', args=[p.pk]), {'section': 'signoff', 'action': 'save_anamnesis', 'anamnesis': 'შეცვლილი'})
        p.refresh_from_db()
        self.assertEqual(p.anamnesis, 'ქირურგის ხელმოწერილი ანამნეზი')
        self.assertIsNotNone(p.approved_at)

        # 2. Decimal preservation on Stage 1/2 HTTP POST for spiro_fev1 and tp_r/tp_l
        self.c_doc.post(reverse('preop-patient', args=[p.pk]), {'section': 'stage1', 's1_partial': '1', 'tp_r': '29.9', 'tp_l': '29.5'})
        self.c_doc.post(reverse('preop-patient', args=[p.pk]), {'section': 'stage2', 'spiro_fev1': '49.9'})
        p.refresh_from_db()
        self.assertAlmostEqual(float(p.studies['tp_r']), 29.9, places=1)
        self.assertAlmostEqual(float(p.studies['spiro_fev1']), 49.9, places=1)

        # 3. Pathway migration clears ven_pregnant & migrates carotid_symp -> q['neuro'] (Catch-22 #1/#2)
        s_m = dict(p.studies)
        PreopPatientView._migrate_header_pathway_flags(p, PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA, s_m, [])
        self.assertFalse(p.questionnaire.get('ven_pregnant'))
        PreopPatientView._migrate_header_pathway_flags(p, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA, s_m, [])
        self.assertTrue(p.questionnaire.get('neuro'))
        self.assertFalse(s_m.get('carotid_symp'))

        # 4. Inactive outcome (operated) locks patient POST, file upload, file delete, and case sync
        f = PreopFile.objects.create(patient=p, category='labs', file=SimpleUploadedFile('l.pdf', b'%PDF-1.4\n%%EOF\n'), is_pdf=True)
        case_obj = Case.objects.create(patient_name=p.patient_name, diagnosis='PAD CLTI', side='R', doctor=self.doc)
        p.case = case_obj
        p.studies = {**p.studies, 'case_outcome_status': 'operated'}
        p.save()
        self.c_doc.post(reverse('preop-patient', args=[p.pk]), {'section': 'header', 'patient_name': 'შეცვლილი'})
        p.refresh_from_db()
        self.assertNotEqual(p.patient_name, 'შეცვლილი')
        self.c_doc.post(reverse('preop-files', args=[p.pk]), {'category': 'labs', 'file': [SimpleUploadedFile('x.pdf', b'%PDF-1.4\n%%EOF\n', content_type='application/pdf')]})
        self.assertEqual(PreopFile.objects.filter(patient=p).count(), 1)
        self.c_nurse.post(reverse('preop-file-delete', args=[f.pk]))
        self.assertTrue(PreopFile.objects.filter(pk=f.pk).exists())
        self.c_doc.post(reverse('preop-new'), {'case_id': case_obj.pk})
        self.assertEqual(PreopPatient.objects.filter(case=case_obj).count(), 1)

        # 5. Plain-Georgian self_fill.html & register_new.html have zero clinical abbreviation leaks
        p_sf = _mk('cea_symp')
        p_sf.save()
        sf_html = Client().get(reverse('preop-self-fill', args=[make_self_fill_token(p_sf)])).content.decode('utf-8')
        for bad_tok in ('CABG', 'Amaurosis fugax', '(CEA)', '(HIT)', 'NSAID'):
            self.assertNotIn(bad_tok, sf_html)
        s = self.c_nurse.session
        s['preop_ui_mode'] = PREOP_UI_NEW
        s.save()
        reg_html = self.c_nurse.get(reverse('preop-nurse-register')).content.decode('utf-8')
        for bad_tok in ('(CEA)', '(AAA)', '(EVAR)', '(EVLA)'):
            self.assertNotIn(bad_tok, reg_html)
        s['preop_ui_mode'] = PREOP_UI_OLD
        s.save()
